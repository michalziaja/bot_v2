"""JobRunner — wykonuje kolejkę zadań: prolog → pluginy → epilog.

Odpowiednik `FlowRunner` dla warstwy pluginów. Cienki: nie zna semantyki
żadnego pluginu, nie wie co to rolka. Odpowiada za:
  - doprowadzenie telefonu do stanu wejściowego zadania i powrót po nim,
  - budżet i rozwiązanie ustawień (defaults ← config konta ← override zadania),
  - checkpoint po każdym zadaniu, żeby przerwana kolejka wznawiała się
    od miejsca przerwania, a nie od zera,
  - reakcję na wyczerpane limity dzienne,
  - pilnowanie `max_queue_duration_s` — twardej ramy czasowej całej kolejki.

`max_queue_duration_s` istnieje dla harmonogramu: blok na osi czasu ma
zarezerwowane okno i sesja NIE MOŻE w nie wjechać następnemu kontu (jedno
urządzenie). Sam budżet per zadanie tego nie gwarantuje — suma limitów zadań
może przekroczyć okno, a ostatnie zadanie i tak dobiłoby swój limit. Dlatego
budżet każdego zadania jest przycinany do czasu, jaki realnie został.

`finally: ensure(exit_screen)` po KAŻDYM zadaniu jest krytyczne — bez tego
kolejne zadanie startuje wewnątrz sheetu/story i tapie w losowe miejsca.
To lekcja L4c z ARCHITEKTURA.md §3.4.
"""
from __future__ import annotations

import json
import random
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from v2.plugins import registry as plugin_registry
from v2.plugins import store
from v2.plugins.base import Budget, PluginResult, stop_path_for
from v2.plugins.catalog import get_plugin
from v2.steps import registry as step_registry


# Prolog bazowy: telefon → IG. Migawka profilu jest osobno PO ensure(HOME)
# (jak w flows/smoke_stats.yml) — respektuje toggles.steps.stats_snapshot.
PROLOGUE_STEPS = [
    ("system.wake", {}),
    ("system.unlock", {}),
    ("system.apply_clone_identity", {}),
    ("system.ensure_ig_running", {}),
]
# Epilog: opcjonalna migawka końca (on_session_end, domyślnie false) → close → restore MAC → sleep.
EPILOGUE_STEPS = [
    ("system.stats_snapshot", {"when": "session_end"}),
    ("system.close_ig", {}),
    ("system.restore_clone_identity", {}),
    ("system.sleep_screen", {}),
]
HOME = "home_feed"


@dataclass
class JobRunner:
    ctx: object
    data_root: Path
    only: str | None = None            # uruchom tylko zadanie(a) o tych id (comma-separated)
    queue_name: str | None = None      # nazwany scenariusz (<alias>.queues/<name>.json); None = domyślna kolejka
    max_items_override: int | None = None
    max_queue_duration_s: float | None = None   # twarda rama czasowa całej kolejki (blok harmonogramu)
    # One-shot: syntetyczna 1-elementowa kolejka w pamięci (np. niche_discover
    # z panelu) — bez zapisu do *.queues/*.json.
    plugin_oneshot: str | None = None
    dry_run: bool = False
    rng: random.Random = field(default_factory=random.Random)
    _t_start: float = field(default=0.0, init=False, repr=False)

    # ------------------------------------------------------------------
    # rama czasowa kolejki
    # ------------------------------------------------------------------
    def _time_left(self) -> float | None:
        """Ile sekund zostało do końca okna kolejki. None = brak ramy."""
        if self.max_queue_duration_s is None:
            return None
        return self.max_queue_duration_s - (time.monotonic() - self._t_start)

    # Zadanie, któremu zostałoby mniej niż tyle, nie ma po co startować:
    # sam prolog nawigacyjny i wejście na ekran zjadłyby całe okno.
    MIN_TASK_WINDOW_S = 30

    # ------------------------------------------------------------------
    def run(self, account: str) -> dict:
        self._t_start = time.monotonic()
        # meta kolejki (pauzy / on_error) — oneshot nie ma pliku, więc bezpieczne defaulty
        q: dict = {"between_tasks_s": {}, "on_task_error": "continue"}
        if self.plugin_oneshot:
            # syntetyczna kolejka — discovery / test jednego pluginu bez pliku
            pid = self.plugin_oneshot
            get_plugin(pid)  # walidacja id
            tasks = [{
                "id": f"oneshot_{pid}",
                "plugin": pid,
                "note": "one-shot",
                "budget": {},
                # klik w panelu = jawne uruchomienie; nie blokuj domyślnym enabled=False
                "overrides": {"enabled": True},
                "enabled": True,
                "_oneshot": True,
            }]
            self.ctx.log("queue.oneshot", account=account, plugin=pid,
                         session_id=self.ctx.session_id)
            label = f"  [one-shot: {pid}]"
        else:
            q = store.read_queue(account, name=self.queue_name)
            tasks = [t for t in q["queue"] if t.get("enabled", True)]
            if self.only:
                only_ids = {x.strip() for x in self.only.split(",") if x.strip()}
                tasks = [t for t in tasks if t["id"] in only_ids]
            self.ctx.log("queue.start", account=account, tasks=len(tasks),
                         queue_name=self.queue_name, session_id=self.ctx.session_id)
            label = f"  [scenariusz: {self.queue_name}]" if self.queue_name else ""
        if self.max_queue_duration_s:
            label += f"  [okno: {self.max_queue_duration_s / 60:.0f} min]"
        self._banner(f"KOLEJKA — {len(tasks)} zadań" + label, account)

        results: list[dict] = []
        aborted_by: str | None = None

        if not tasks:
            self.ctx.log("queue.empty", account=account)
            print("   (kolejka pusta — nic do zrobienia)")
            return self._summary(account, results, None, ran_prologue=False)

        # Live session_stats.json — reset na start kolejki (znany queue_name)
        ss = getattr(self.ctx, "session_stats", None)
        if ss is not None:
            try:
                ss.begin(session_id=self.ctx.session_id,
                         queue_name=self.queue_name)
            except Exception:
                pass

        self._prologue()

        for i, task in enumerate(tasks):
            if self._user_stopped(account):
                print("\n   STOP od użytkownika — przerywam kolejkę")
                self.ctx.log("queue.user_stop", after_task=i)
                aborted_by = "user"
                break

            left = self._time_left()
            if left is not None and left < self.MIN_TASK_WINDOW_S:
                print(f"\n   KONIEC OKNA kolejki — pomijam pozostałe {len(tasks) - i} zadań")
                self.ctx.log("queue.duration_abort", after_task=i,
                             remaining_tasks=len(tasks) - i)
                aborted_by = "queue_duration"
                break

            res = self._run_task(account, task, i + 1, len(tasks))
            results.append(res)
            self._checkpoint(account, results)

            if res["stopped_by"] == "limit_daily":
                print("   Limit DZIENNY wyczerpany — przerywam całą kolejkę")
                self.ctx.log("queue.daily_limit_abort", task=task["id"])
                aborted_by = "limit_daily"
                break
            if res["status"] == "failed" and q.get("on_task_error") == "abort":
                print("   STOP — on_task_error=abort")
                aborted_by = "task_error"
                break

            if i < len(tasks) - 1:
                self._pause_between(q)

        self._epilogue()
        return self._summary(account, results, aborted_by, ran_prologue=True)

    # ------------------------------------------------------------------
    def _run_task(self, account: str, task: dict, n: int, total: int) -> dict:
        pid = task["plugin"]
        meta = get_plugin(pid)
        settings = store.resolve(account, pid, task.get("overrides"))

        self._banner(f"ZADANIE {n}/{total} — {meta['label']}",
                     f"{pid}  {task.get('note') or ''}")

        # plugin wyłączony dla konta = świadoma decyzja użytkownika w panelu;
        # one-shot (np. discovery z przycisku) omija ten gate — użytkownik
        # właśnie kliknął „uruchom”
        if not settings.get("enabled") and not (
                self.plugin_oneshot or task.get("_oneshot")):
            print("   POMINIĘTE — plugin wyłączony dla tego konta")
            self.ctx.log("queue.task_skipped", task=task["id"], plugin=pid,
                         why="plugin_disabled_for_account")
            return self._task_row(task, "skipped", 0, "disabled")
        if self.plugin_oneshot or task.get("_oneshot"):
            settings["enabled"] = True

        if not plugin_registry.is_implemented(pid):
            print(f"   POMINIĘTE — plugin '{pid}' nie ma jeszcze implementacji")
            self.ctx.log("queue.task_skipped", task=task["id"], plugin=pid,
                         why="not_implemented")
            return self._task_row(task, "skipped", 0, "not_implemented")

        budget = Budget.from_settings(
            settings, task.get("budget"),
            stop_path=stop_path_for(self.data_root, account),
            rng=self.rng, max_items_override=self.max_items_override,
        )
        # One-shot spoza kolejki (np. discovery niszy): bez limitu treści/czasu —
        # zbieramy tyle, ile w ustawieniach collect; zatrzymuje tylko STOP.
        if self.plugin_oneshot or task.get("_oneshot"):
            budget.max_items = None
            budget.max_duration_s = None
        else:
            # Rama kolejki jest ważniejsza niż limit zadania: bez tego ostatnie
            # zadanie dobiłoby swoje 25 minut i wjechało w slot następnego konta.
            left = self._time_left()
            if left is not None:
                clamped = (min(budget.max_duration_s, left)
                           if budget.max_duration_s else left)
                if clamped != budget.max_duration_s:
                    print(f"   budżet czasu przycięty do okna kolejki: "
                          f"{clamped:.0f}s (zostało {left:.0f}s)")
                budget.max_duration_s = clamped
        print(f"   budżet: max_items={budget.max_items} "
              f"max_duration_s={budget.max_duration_s}")

        plugin = plugin_registry.get(pid)()
        entry, exit_ = meta["entry_screen"], meta["exit_screen"]

        if not self.ctx.navigator.ensure(entry):
            self.ctx.log("queue.entry_failed", task=task["id"], plugin=pid, entry=entry)
            print(f"   FAIL — nie doprowadzono do {entry}")
            return self._task_row(task, "failed", 0, "entry_failed")

        self.ctx.log("queue.task_start", task=task["id"], plugin=pid,
                     budget=budget.as_log())
        # session_stats: task i/N + budżet (plugin zaktualizuje progress/counters)
        ss = getattr(self.ctx, "session_stats", None)
        if ss is not None:
            try:
                ss.set_task(
                    plugin=pid, task_id=task["id"], task_i=n, task_n=total,
                    budget={"items_done": 0, "max_items": budget.max_items})
            except Exception:
                pass
        t0 = time.monotonic()
        try:
            res = plugin.run(self.ctx, settings, budget)
        except Exception as e:
            res = PluginResult.failed(f"{e}\n{traceback.format_exc()}", budget)
            print(f"   EXCEPTION: {e}")
        finally:
            # ZAWSZE — inaczej kolejne zadanie startuje w sheecie/story
            self.ctx.navigator.ensure(exit_)

        dt = time.monotonic() - t0
        # Final sync counters/budget po zadaniu (pluginy bumpują na bieżąco)
        if ss is not None:
            try:
                ss.sync_budget(res.items_done, budget.max_items)
                ss.set_counters(res.stats or {})
                ss.record_task_timing(
                    duration_s=dt,
                    items_done=res.items_done,
                    stats=res.stats or {},
                    status=res.status,
                    stopped_by=res.stopped_by,
                )
            except Exception:
                pass
        self.ctx.log("queue.task_end", task=task["id"], plugin=pid,
                     duration_s=round(dt, 1), **res.as_log())
        print(f"   {res.status.upper()}  items={res.items_done}  "
              f"stopped_by={res.stopped_by}  {dt:.1f}s"
              + (f"\n   error={res.error}" if res.error else ""))
        return self._task_row(task, res.status, res.items_done, res.stopped_by,
                              stats=res.stats, error=res.error)

    # ------------------------------------------------------------------
    def _prologue(self):
        # ensure_ig → home → stats (własny profil) → z powrotem home pod pluginy
        self._banner("PROLOG", "wybudzenie -> odblokowanie -> MAC -> IG -> home -> stats")
        for name, params in PROLOGUE_STEPS:
            self._system_step(name, params)
        self.ctx.navigator.ensure(HOME)
        # followers/following → event stats_snapshot; skip gdy flaga wyłączona
        self._system_step("system.stats_snapshot", {"when": "session_start"})
        # po odczycie jesteśmy na profile_self — pluginy startują z entry_screen
        self.ctx.navigator.ensure(HOME)

    def _epilogue(self):
        # stats_end w EPILOGUE_STEPS; przy on_session_end=false → skipped (OK)
        self._banner("EPILOG", "stats(opcjonalnie) -> zamkniecie IG -> restore MAC -> wygaszenie")
        self.ctx.navigator.ensure(HOME)
        for name, params in EPILOGUE_STEPS:
            self._system_step(name, params)

    def _system_step(self, name: str, params: dict):
        """Kroki systemowe idą przez istniejący rejestr kroków — nie duplikujemy
        logiki prologu, którą FlowRunner już ma sprawdzoną."""
        try:
            step = step_registry.get(name)()
        except KeyError:
            self.ctx.log("queue.unknown_step", step=name)
            print(f"   ! nieznany krok systemowy: {name}")
            return
        try:
            res = step.run(self.ctx, params)
            print(f"   {res.status:8s} {name}")
            self.ctx.log("step", step=name, status=res.status, payload=res.payload)
        except Exception as e:
            self.ctx.log("queue.step_error", step=name, error=str(e))
            print(f"   FAIL     {name} — {e}")

    # ------------------------------------------------------------------
    def _pause_between(self, q: dict):
        # Pauza między zadaniami kolejki pochodzi z centralnego profilu humanizera.
        # Najpierw losujemy czas ludzki, potem sprawdzamy okno kolejki przed snem.
        hum = getattr(self.ctx, "humanizer", None)
        t = hum.draw("between_tasks") if hum else self.rng.uniform(60, 240)
        real = hum.scale(t) if hum else t
        # przerwa nie może zjeść okna kolejki — kolejne zadanie i tak
        # zostanie pominięte, a telefon stałby bezczynnie w cudzym slocie
        left = self._time_left()
        if left is not None and real > max(left - self.MIN_TASK_WINDOW_S, 0):
            print("\n   przerwa skrócona — koniec okna kolejki")
            return
        print(f"\n   przerwa między zadaniami: {real:.0f}s")
        if hum:
            hum.sleep(t, kind="between_tasks")
        else:
            time.sleep(t)

    def _user_stopped(self, account: str) -> bool:
        return stop_path_for(self.data_root, account).exists()

    def _checkpoint(self, account: str, results: list[dict]):
        """Stan kolejki na dysk po każdym zadaniu — przerwana sesja wznawia się
        od miejsca przerwania (wzorzec TaskContext ze starego bota)."""
        p = Path(self.data_root) / "accounts" / account / "queue_state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "session_id": self.ctx.session_id,
            "queue_name": self.queue_name,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tasks": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _task_row(self, task, status, items, stopped_by, stats=None, error=None):
        return {"id": task["id"], "plugin": task["plugin"], "status": status,
                "items_done": items, "stopped_by": stopped_by,
                "stats": stats or {}, "error": error}

    def _summary(self, account, results, aborted_by, ran_prologue):
        # wyjście oknem czasowym albo STOP-em użytkownika to zaplanowany
        # koniec, nie awaria — inaczej harmonogram raportowałby porażkę
        # przy każdym bloku, który wykorzystał cały swój czas
        aborted = aborted_by is not None
        clean_stop = aborted_by in (None, "queue_duration", "user")
        s = {
            "account": account,
            "queue_name": self.queue_name,
            "ok": clean_stop and all(r["status"] != "failed" for r in results),
            "aborted_by": aborted_by,
            "tasks_total":   len(results),
            "tasks_ok":      sum(1 for r in results if r["status"] == "ok"),
            "tasks_skipped": sum(1 for r in results if r["status"] == "skipped"),
            "tasks_failed":  sum(1 for r in results if r["status"] == "failed"),
            "items_done":    sum(r["items_done"] for r in results),
            "aborted": aborted,
            "ran_prologue": ran_prologue,
        }
        # Snapshot końcowy → sessions/<session_id>.json + summary w live JSON
        ss = getattr(self.ctx, "session_stats", None)
        if ss is not None:
            try:
                ss.snapshot_end(summary=s)
            except Exception:
                pass
        self.ctx.log("queue.end", **s)
        return s

    def _banner(self, title, sub=""):
        print()
        print("=" * 64)
        print(f"  {title}")
        if sub:
            print(f"  {sub}")
        print("=" * 64)
