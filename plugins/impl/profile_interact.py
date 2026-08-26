"""profile_interact — standardowa procedura na profilu.

Plugin jest jednocześnie **podprocedurą** wołaną z innych pluginów: `reels_niche`
używa `run_one()` do interakcji „na gorąco" z listy lajkujących, a `content_search`
będzie używać tego samego wejścia. Dlatego procedura profilowa NIE jest zaszyta
w pętli — `run()` to tylko źródło profili plus `run_one()` na każdym z nich.

Podział ekranów:
  `run()`      startuje na `home_feed` i sam dojeżdża na profil (search),
  `run_one()`  zakłada, że jesteśmy JUŻ na `profile_other`, i po sobie NIE
               wychodzi — wyjście należy do wołającego, bo tylko on wie, dokąd
               wracać (lista lajkujących, wyniki wyszukiwania).

Cała logika UI siedzi w kroku `profile.visit`; tutaj jest tłumaczenie ustawień
z katalogu na jego parametry i decyzja, co robić z wynikiem.
"""
from __future__ import annotations

import random

from v2.plugins import stepcall
from v2.plugins.base import Budget, PluginResult, draw_range
from v2.plugins.profile_log import (
    say, visit_start_line, next_profile_line, search_reuse_line,
    open_failed_line, no_candidates_line, done_line,
)
from v2.plugins.progress_log import announce_progress, sync_plugin_stats
from v2.plugins.registry import register
from v2.steps import registry as steps

HOME = "home_feed"
PROFILE = "profile_other"
# Między profilami zostajemy na search UX — bez pełnego powrotu na home_feed.
SEARCH_SCREENS = ("search_input_active", "search_results", "search_top")

# Ile kolejnych nieudanych wejść na profil tolerujemy. Jedno bywa przypadkiem
# (nick zniknął z wyszukiwarki), trzy z rzędu znaczą, że coś jest nie tak
# z ekranem i dalsze próby tylko mielą budżet.
MAX_CONSECUTIVE_FAILURES = 3


@register
class ProfileInteract:
    id = "profile_interact"
    entry_screen = HOME
    exit_screen = HOME

    # ------------------------------------------------------------------
    def run(self, ctx, s: dict, budget: Budget) -> PluginResult:
        rng = random.Random()
        stats = {"visited": 0, "followed": 0, "skipped": 0, "failed": 0,
                 "tiles_liked": 0, "stories_watched": 0}
        # Flaga dla operator_say (post.like / left_profile) — prefix profile_interact
        ctx.scratch["profile_interact_active"] = True
        try:
            return self._run(ctx, s, budget, stats, rng)
        finally:
            ctx.scratch.pop("profile_interact_active", None)

    def _run(self, ctx, s: dict, budget: Budget, stats: dict, rng) -> PluginResult:
        picked = self._fill_queue(ctx, s, budget)
        if not picked:
            ctx.log("profile_interact.no_candidates")
            say(no_candidates_line())
            return PluginResult.done(budget, "empty", **stats)

        stopped_by = "items"
        failures = 0
        first = True
        while True:
            reason = budget.stop_reason()
            if reason:
                stopped_by = reason
                break

            queue = ctx.scratch.get("profile_queue") or []
            if not queue:
                stopped_by = "empty"
                break

            username = (queue.pop(0) or {}).get("username")
            if not username:
                continue

            # Wizyta @x i/N — ten sam current/total do SessionStats
            cur = int(budget.items_done) + 1
            total = budget.max_items
            if first:
                msg = visit_start_line(username, cur, total)
                first = False
            else:
                msg = next_profile_line(username, cur, total)
            announce_progress(
                ctx, say, msg=msg, phase="visit",
                current=cur, total=total, label="Wizyta",
                detail=f"@{username}")

            outcome = self._visit_from_search(ctx, s, username, stats)
            budget.tick()
            sync_plugin_stats(ctx, stats, items_done=budget.items_done,
                              max_items=budget.max_items)

            if outcome == "limit_daily":
                stopped_by = "limit_daily"
                break
            if outcome == "failed":
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    ctx.log("profile_interact.too_many_failures", failures=failures)
                    stopped_by = "error"
                    break
            else:
                failures = 0

            if budget.stop_reason() is None and (ctx.scratch.get("profile_queue")):
                self._pause_between(ctx, s, rng)

        ctx.log("profile_interact.done", stopped_by=stopped_by, **stats)
        say(done_line(**{k: stats[k] for k in
                         ("visited", "followed", "skipped", "failed", "tiles_liked")}))
        return PluginResult.done(budget, stopped_by, **stats)

    # ------------------------------------------------------------------
    def run_one(self, ctx, s: dict, *, short: bool = False) -> dict:
        """Procedura na profilu, na którym już jesteśmy.

        `short=True` — wariant „na gorąco": nagłówek, sanity i decyzja follow,
        bez relacji i bez analizy siatki. Pełna procedura to 60–90 s na profil,
        czyli przy trzech profilach na rolkę cztery minuty zamiast jednej.

        Zwraca `{status, action, followed, username, stop}`, gdzie `stop` to
        `"limit_daily"` gdy dzienny limit każe skończyć całą sesję.
        """
        # Hot z rolek / content_search: wizyta bez pętli search — krótka linia startu
        # content_search ma własny „Profil @nick" (content_search_log) — bez dubla
        if short or ctx.scratch.get("hot_from_reels"):
            expect = ctx.scratch.get("expected_username")
            if expect and not ctx.scratch.get("content_search_active"):
                say(visit_start_line(expect))

        res = _step(ctx, "profile.visit", visit_params(s, short=short))
        payload = res.payload or {}

        stop = None
        if (payload.get("skip_until") == "session.end"
                or "daily" in str(payload.get("reason") or "")):
            stop = "limit_daily"

        return {
            "status": res.status,
            "action": payload.get("action") or payload.get("reason") or res.status,
            "followed": bool(payload.get("followed")),
            "tiles_liked": int(payload.get("tiles_liked") or 0),
            "story_watched": bool(payload.get("story_watched")),
            "username": payload.get("username"),
            "stop": stop,
        }

    # ------------------------------------------------------------------
    def _fill_queue(self, ctx, s: dict, budget: Budget) -> int:
        """Napełnij `scratch.profile_queue` ze źródła podanego w ustawieniach.

        Domyślnie `target_candidates` (po filtrze HTTP). Fallback: raw_profiles.
        """
        src = s.get("source") or {}
        kind = src.get("kind") or "target_candidates"
        if kind not in ("raw_profiles", "target_candidates"):
            # `source_kind`, nie `kind` — kolizja z parametrem logu
            ctx.log("profile_interact.unsupported_source", source_kind=kind)
            return 0

        want = budget.max_items or draw_range(s.get("budget", {}).get("max_items"),
                                              default=10)
        # raw: bez shuffle — najnowsze pierwsze; candidates: losowo
        shuffle = kind == "target_candidates"
        res = _step(ctx, "profile.pick_from_store", {
            "limit": int(want),
            "source_kind": kind,
            "dedup_days": int(src.get("dedup_days", 30)),
            "min_age_minutes": int(src.get("min_age_minutes", 5)),
            "shuffle": shuffle,
        })
        return int((res.payload or {}).get("picked") or 0)

    def _visit_from_search(self, ctx, s: dict, username: str, stats: dict) -> str:
        """search → profil → procedura → powrót na search (NIE na home).

        Między profilami w tej samej sesji reuse'ujemy zakładkę Szukaj:
        clear pola → nowy nick → pick. `search.open` tylko gdy nie jesteśmy
        już na search UX. Hot z rolek NIE woła tej metody — tam zostaje
        `run_one` + `profile.exit` do sheeta.
        """
        ctx.log("profile_interact.visit_start", username=username)
        try:
            if not _ensure_search_ui(ctx):
                stats["failed"] += 1
                say(open_failed_line(username))
                _mark_raw_outcome(ctx, username, {
                    "status": "failed", "action": "open_failed",
                    "followed": False, "tiles_liked": 0,
                })
                return "failed"

            for name, params in (
                # clear_first=True (domyślne) — czyści poprzedni nick w polu
                ("search.type_query", {"query": f"@{username}", "clear_first": True}),
                ("search.pick_result", {"username": username, "prefer_exact": True}),
            ):
                r = _step(ctx, name, params)
                if r.status == "failed":
                    stats["failed"] += 1
                    say(open_failed_line(username))
                    # Wejście nieudane — oznacz raw, żeby nick nie wracał w kółko
                    _mark_raw_outcome(ctx, username, {
                        "status": "failed", "action": "open_failed",
                        "followed": False, "tiles_liked": 0,
                    })
                    return "failed"

            # Samodzielny przebieg zawsze robi pełną procedurę — skrót jest
            # kompromisem interakcji „na gorąco", gdzie profil jest przystankiem
            # w środku innej pętli.
            out = self.run_one(ctx, s, short=False)
            stats["visited"] += 1
            if out["followed"]:
                stats["followed"] += 1
            elif out["status"] != "ok":
                stats["skipped"] += 1
            stats["tiles_liked"] += out["tiles_liked"]
            stats["stories_watched"] += int(out["story_watched"])

            # Aktualizacja raw_profiles (outcome) — NIE dla hot z rolek
            _mark_raw_outcome(ctx, username, out)

            if out["stop"]:
                return out["stop"]
            return "ok" if out["status"] == "ok" else "skip"
        finally:
            # Zawsze lądujemy na search gotowym na kolejny nick — nie home_feed
            # (JobRunner i tak zrobi exit_screen=home po końcu całego pluginu).
            _return_to_search(ctx)

    def _pause_between(self, ctx, s: dict, rng) -> None:
        # Pauza między profilami pochodzi wyłącznie z centralnego profilu
        # humanizera — nie z pola pluginu (usunięte z katalogu).
        ctx.humanizer.pause("between_profiles")


# ============================================================
# ustawienia → parametry kroku
# ============================================================
def visit_params(s: dict, *, short: bool = False) -> dict:
    """Przetłumacz ustawienia z katalogu na parametry `profile.visit`.

    Katalog i krok mają różne nazwy tych samych rzeczy (`grid_ai` vs `match`),
    bo katalog opisuje POLE W PANELU, a krok — swój kontrakt. Tłumaczenie jest
    tutaj, żeby żadna z tych dwóch stron nie musiała znać nazw drugiej.
    """
    gate = s.get("gate") or {}
    grid = s.get("grid_ai") or {}
    story = s.get("story") or {}
    tiles = s.get("like_tiles") or {}

    out = {
        "sanity": s.get("sanity") or {},
        "follow": s.get("follow") or {},
        "gate": {k: v for k, v in gate.items() if k != "enabled"} if gate.get("enabled") else {},
        "story": {},
        "match": {},
        "like_tiles": {},
    }
    if short:
        # wariant „na gorąco" — bez relacji, siatki AI i lajkowania kafelków
        return out

    if story.get("enabled"):
        out["story"] = {"enabled": True, **(story.get("watch") or {})}
    # Lajki niezależnie od AI — „Tylko %” nie wymaga grid_ai.enabled
    if tiles.get("enabled"):
        out["like_tiles"] = tiles
    if grid.get("enabled"):
        out["match"] = {k: v for k, v in grid.items() if k != "enabled"}
    return out


def settings_for(ctx, *overrides: dict) -> dict:
    """Ustawienia `profile_interact` dla konta + nadpisania wołającego.

    Wołane z innego pluginu (interakcja „na gorąco"), więc ustawień nie ma
    w `s` tamtego pluginu — czytamy je z configu konta tak samo, jak zrobiłby
    to JobRunner. Gdy configu nie da się wczytać, schodzimy do domyślnych
    z katalogu: brak pliku ma zdegradować zachowanie do domyślnego,
    a nie wysypać rolki w połowie.
    """
    from v2.plugins.catalog import all_defaults
    from v2.plugins.store import deep_merge

    base = None
    try:
        from v2.plugins import store
        base = store.read_settings(ctx.account.account_id)["profile_interact"]
    except Exception as e:                       # brak configu / inny katalog
        ctx.log("profile_interact.settings_fallback", error=str(e))
    if base is None:
        # `all_defaults()` zwraca kopię — bez tego nadpisania wołającego
        # wsiąkłyby w katalog i zostały z nami do końca procesu
        base = all_defaults()["profile_interact"]

    out = dict(base)
    for ov in overrides:
        if ov:
            out = deep_merge(out, ov)
    return out


def _step(ctx, name: str, params: dict):
    return stepcall.call(steps, ctx, name, params, source="profile_interact")


def _search_top(ctx) -> str | None:
    try:
        return ctx.navigator.identify().top
    except Exception:
        return None


def _ensure_search_ui(ctx) -> bool:
    """Upewnij się, że jesteśmy na search — `search.open` tylko gdy trzeba.

    Pierwszy profil w sesji: zwykle home → open. Kolejne: reuse search_* .
    """
    top = _search_top(ctx)
    if top in SEARCH_SCREENS:
        ctx.log("profile_interact.search_reuse", top=top)
        say(search_reuse_line())
        return True
    ctx.log("profile_interact.search_open", from_top=top)
    r = _step(ctx, "search.open", {})
    if r.status == "failed":
        say(open_failed_line(None))
    return r.status != "failed"


def _return_to_search(ctx) -> None:
    """Po wizycie / failu: wróć na search UX bez spadania na home_feed.

    `profile.exit` przy złym baseline potrafił zrobić ensure(home) — tu
    celujemy wyłącznie w SEARCH_SCREENS (BACK, potem ewentualnie search.open).
    """
    baseline = ctx.scratch.get("baseline_screen")
    if baseline not in SEARCH_SCREENS:
        ctx.scratch["baseline_screen"] = "search_input_active"

    for _ in range(4):
        top = _search_top(ctx)
        if top in SEARCH_SCREENS:
            ctx.log("profile_interact.back_to_search", top=top, how="back")
            # posprzątaj scratch profilu (jak profile.exit)
            for k in ("profile_header", "profile_ai", "profile_decision",
                      "tiles_liked", "story_watched", "followed",
                      "expected_username"):
                ctx.scratch.pop(k, None)
            return
        # Już na home — nie BACK (wyszlibyśmy z IG); otwórz search tab
        if top == HOME:
            break
        try:
            ctx.device.press_back()
            ctx.humanizer.pause("tap")
        except Exception:
            break

    # Fallback: zakładka Szukaj (jeden tap) — nadal bez prologu home
    ctx.log("profile_interact.back_to_search", top=_search_top(ctx), how="search.open")
    _step(ctx, "search.open", {})


def _map_raw_outcome(out: dict) -> str:
    """Zmapuj wynik wizyty na etykietę `outcome` w raw_profiles."""
    if out.get("followed"):
        return "followed"
    if int(out.get("tiles_liked") or 0) > 0:
        return "liked"
    action = str(out.get("action") or "")
    low = action.lower()
    # sanity: „became_private" / „already_following"
    if "private" in low:
        return "private"
    if action == "gate_failed" or low.startswith("gate"):
        return "gate_failed"
    if action == "match_failed" or low.startswith("match"):
        return "match_failed"
    if action == "open_failed" or out.get("status") == "failed":
        return "open_failed"
    return "skipped"


def _mark_raw_outcome(ctx, username: str, out: dict) -> None:
    """Zapisz interacted_at + outcome w raw — pomijane przy hot z rolek."""
    if not username:
        return
    # Hot z reels_niche: tylko visited, bez mutacji raw
    if ctx.scratch.get("hot_from_reels"):
        return
    events = getattr(ctx, "events", None)
    account = getattr(ctx, "account", None)
    if not events or not account:
        return
    try:
        from v2.data.stores import RawProfilesStore
        root = events.file_path.parent.parent.parent
        outcome = _map_raw_outcome(out)
        ok = RawProfilesStore(account.account_id, root).mark_outcome(
            username, outcome,
            visit_status=out.get("status"),
            visit_action=out.get("action"),
            followed=bool(out.get("followed")),
            tiles_liked=int(out.get("tiles_liked") or 0),
        )
        ctx.log("profile_interact.raw_outcome",
                username=username, outcome=outcome, updated=ok)
    except Exception as e:
        ctx.log("profile_interact.raw_mark_failed",
                username=username, error=str(e))
