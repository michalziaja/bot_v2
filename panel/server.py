"""Dashboard panel — FastAPI + prosty HTML.

Uruchomienie: `python -m v2.cli panel` → localhost:8765

Endpointy:
  GET  /                     — HTML dashboard (klasyczny panel)
  GET  /app                  — nowy panel operatora (Pulpit); ?view=owner → /share/schedule
  GET  /share                — alias readonly właściciela → /share/schedule
  GET  /share/schedule       — tydzień osi, tylko odczyt (bez auth)
  GET  /share/stats          — KPI 24h, tylko odczyt (bez auth)
  GET  /api/accounts         — lista kont [{alias, device_alias}, ...]
  GET  /api/devices          — lista urządzeń (config/devices/*.json) + klony
  POST /api/devices          — zarejestruj urządzenie (alias, adb_id; ADB sam zczyta hardware)
  PATCH /api/devices/{alias} — subset: scheduler_enabled, scrcpy_max_fps / scrcpy_max_size
  GET  /api/devices/{alias}/preview — JPEG z FB sesji (`preview.jpg`); 204 bez sesji
  GET  /api/adb-devices      — podłączone serials z `adb devices -l`
  POST /api/devices/{alias}/hydrate — ponownie zczytaj hardware z ADB (409 gdy TEN serial jedzie)
  POST /api/devices/{alias}/calibrate — start kalibracji (cli calibrate; 409 gdy TEN serial)
  GET  /api/devices/{alias}/calibrate — stan (running / needs_agent / done)
  POST /api/devices/{alias}/calibrate/reply — odpowiedź agenta (ok/skip/retry/abort)
  GET  /api/devices/{alias}/calibrate/shot — JPEG/PNG gdy krok nie przeszedł
  POST /api/accounts         — utwórz konto (alias, device_alias, app_clone_package; ig_username opcjonalny)
  POST /api/accounts/{acc}/clone — ustaw/wyczyść device.app_clone_package (pusty string = clear)
  POST /api/clones/preview    — {device_alias} — tożsamość + wolne APPID (bez APK)
  POST /api/clones/create     — {device_alias, identity?} — generate+build+install+launch (202)
  GET  /api/clones/status     — stan joba klona + log
  POST /api/clones/cancel     — przerwij subprocess appclonera
  POST /api/clones/scan       — {device_alias} — pm list → dociągnij clones[]
  GET  /api/clones/apks       — lokalne APK; ?device_alias= → stock IG z telefonu
  POST /api/clones/apks/fetch — {app_id} — legacy Aptoide (panel nie woła)
  GET  /api/clones/apks/status — stan pobierania APK
  POST /api/clones/apks/activate — {filename|path} — ustaw instagram.apk
  POST /api/clones/apks/from-device — {device_alias} — adb pull stock IG
  DELETE /api/accounts/{acc} — usuń konto + uninstall klona (nie stock)
  DELETE /api/timeline/{dev} — wyczyść całą oś urządzenia (cycle + schedule)
  GET  /api/accounts/{acc}/credentials  — status danych logowania (bez hasła)
  POST /api/accounts/{acc}/credentials  — zapisz ig_username/ig_password (+ opcjonalny signup)
  POST /api/signup           — {account} — krótki flow zakładania konta IG
  GET  /api/accounts/{acc}/avatar — czy jest zdjęcie profilowe
  POST /api/accounts/{acc}/avatar — upload zdjęcia (multipart file / JSON b64)
  GET  /api/accounts/{acc}/avatar/file — blob awatara (podgląd w ustawieniach)
  GET  /api/accounts/{acc}/profile — bio + status zdjęcia
  POST /api/accounts/{acc}/profile — {bio} — zapis biogramu na PC
  POST /api/accounts/{acc}/profile/apply — wstaw bio + avatar na IG
  POST /api/login            — {account} — krótki flow logowania w subprocess
  GET  /api/verification-code/{acc} — czy bot czeka na kod (i o co prosi)
  POST /api/verification-code/{acc} — {code} — wyślij kod do czekającego bota
  GET  /api/schedule/{acc}   — sekcja `schedule` configu konta
  POST /api/schedule/{acc}   — zapis sekcji `schedule`
  GET  /api/scheduler        — master switch (czy plan jest egzekwowany)
  POST /api/scheduler        — {enabled} — START/STOP harmonogramu
  GET  /api/timeline-devices — urządzenia + konta uczestniczące w planie
  GET  /api/timeline/{dev}?days=N&date= — plan urządzenia (bloki z czasem trwania)
  POST /api/timeline/{dev}/regenerate?days=N — ułóż dzień od nowa
  POST /api/timeline/{dev}/{date}/block — dodaj blok ręcznie
  POST /api/timeline/{dev}/{date}/block/{id}/move  — {start,force} — przesuń
  POST /api/timeline/{dev}/{date}/block/{id}/queue — {scenario,queue_override}
  POST /api/timeline/{dev}/day/{i}/block/{id}/lock — {locked} — kłódka inspectora
  DELETE /api/timeline/{dev}/{date}/block/{id}     — usuń blok
  POST /api/timeline/{dev}/{date}/autospread — rozsuń kolidujące bloki
  POST /api/estimate         — {account,scenario,queue_override} — wycena czasu
  POST /api/estimate-actions — {account,scenario,queue_override} — szacunek lajków/follow + ETA z tempa sesji
  GET  /api/queue-presets/{acc} — nazwy zapisanych scenariuszy kolejki
  GET  /api/queue/{acc}?name=X — kolejka (domyślna gdy name pominięte)
  POST /api/queue/{acc}?name=X — zapis kolejki (domyślnej albo nazwanego scenariusza)
  DELETE /api/queue/{acc}/{name} — usuń nazwany scenariusz
  GET  /api/accounts/{acc}/actions — lista zapisanych akcji (presety kroku, nie runy)
  GET  /api/accounts/{acc}/actions/{id} — jedna zapisana akcja
  POST /api/accounts/{acc}/actions — utwórz {name, comment, plugin, overrides}
  PUT  /api/accounts/{acc}/actions/{id} — aktualizuj nazwę/komentarz/overrides
  DELETE /api/accounts/{acc}/actions/{id} — usuń zapisaną akcję
  GET  /api/flows            — lista flow-ów
  GET  /api/known_steps      — spis pluginów
  GET  /api/raw-profiles/{acc}?limit=&unique= — podgląd raw_profiles.jsonl (panel)
  GET  /api/target-candidates/{acc}?limit= — podgląd target_candidates.jsonl
  GET  /api/visited-profiles/{acc}?limit= — visited_profiles.jsonl, newest first (1–500)
  GET  /api/profile-filter/{acc} — config + status filtra HTTP
  POST /api/profile-filter/{acc} — zapis sekcji profile_filter w account.json
  POST /api/profile-filter/{acc}/run — uruchom filtr teraz (202, tło)
  POST /api/profile-filter/{acc}/stop — ręcznie zatrzymaj subprocess filtra
  GET  /api/profile-filter/{acc}/status — last run + liczniki + busy + job.terminal_tail/log
  GET  /api/profile-filter/{acc}/log — pełny stdout/stderr ostatniego joba filtra
  GET  /api/filter-proxies — URL-e filter_proxies (edycja w Lejku)
  POST /api/filter-proxies — zapis URL-i do secrets.json
  GET  /api/bot-config       — globalne limity/humanizer/kroki (config/bot.json)
  POST /api/bot-config       — zapis tych samych sekcji (walidacja jak fragment account.json)
  GET  /api/config/{acc}     — pełen config konta
  POST /api/config/{acc}     — zapis (z walidacją load_account)
  DELETE /api/niche/{acc}    — wyczyść account.niche (UI: „Usuń dane niszy”)
  GET  /api/status           — sesje per device + running=running_any (klasyk)
  POST /api/start            — {account, flow} — start w subprocess (409 tylko ten telefon)
  POST /api/stop             — {device_alias}|{account}; bez body = slot focus
  POST /api/where {acc}      — cmd_where jednorazowo
  POST /api/recover {acc,t}  — cmd_recover
  GET  /api/events/stream    — SSE tail events.jsonl (+stdout) ?account= | ?device=
  GET  /stats                — HTML panel statystyk sesji
  GET  /api/stats/{acc}      — live session_stats.json (+meta + account_stats)
  GET  /api/stats/{acc}/sessions — lista snapshotów sessions/*.json
  GET  /api/stats/{acc}/sessions/{sid} — jeden snapshot sesji (+ account_stats)
  GET  /api/stats/{acc}/aggregate?range=24h — suma counters/by_plugin/ai + series
                                 (+ account_stats z events: pierwszy/ostatni odczyt)
  GET  /api/stats/{acc}/aggregate?from=&to= — suma w zakresie dat (YYYY-MM-DD)
  GET  /api/publish?device=&account=&kind=&status= — kolejka Treści
  POST /api/publish — upload (multipart: account, kind, caption, file) — bez account → 400
  GET  /api/publish/{acc}/{id}/file — blob (miniatura / podgląd)
  GET  /pro-stats-preview — karta Insights (nie rail, nie #statystyki)
  GET  /api/pro-dash/{acc} — store pulpitu profesjonalnego (auto-seed gdy pusty)
  GET  /api/pro-dash/{acc}/item/{id} — wiersz + historia / seria pozycji
  GET  /api/pro-dash/{acc}/thumb/{id} — miniatura JPEG
  POST /api/pro-dash/{acc}/seed — wlej recon_pro_dash + TRIAL_FETCH
  POST /api/pro-dash/{acc}/fetch — live gdy telefon wolny; inaczej seed
  PATCH /api/publish/{acc}/{id} — caption, kind (nie gdy published)
  DELETE /api/publish/{acc}/{id} — blob + opcjonalnie plik na telefonie
  POST /api/publish/{acc}/{id}/push — adb push + skan MediaStore (dozwolone w sesji)
"""
from __future__ import annotations
import asyncio
import base64
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, JSONResponse, Response, RedirectResponse

from v2.core.adb_probe import (
    hardware_gaps,
    hydrate_device_doc,
    hydrate_device_file,
    list_adb_devices,
)
from v2.cloner.apk_store import (
    ApkStoreError,
    activate as activate_source_apk,
    active_apk,
    fetch_job_snapshot,
    list_local as list_local_apks,
    list_remote as list_remote_apks,
    pull_from_device as pull_apk_from_device,
    rank_remote_versions,
    read_installed_version,
    resolve_stored_apk,
    start_fetch_thread,
)
from v2.cloner.job import (
    CloneBusy,
    CloneJobError,
    appcloner_root,
    job_snapshot,
    list_installed_packages,
    load_device,
    packages_held_on_device,
    preview_clone,
    prune_stale_clone_records,
    save_device,
    request_cancel,
    start_job_thread,
    uninstall_clone_for_deleted_account,
)
from v2.core.device_lock import peek_device_lock
from v2.ai.vision import empty_usage_totals, merge_usage_totals
from v2.data.stores import VisitedProfilesStore, visit_has_interaction
from v2.data.pro_dash_fetch import ensure_store as pro_dash_ensure, fetch_into as pro_dash_fetch, seed_into as pro_dash_seed
from v2.data.pro_dash_store import ProDashStore
from v2.data.account_avatar import avatar_path, save_avatar
from v2.data.account_profile import profile_status, set_bio
from v2.data.publish_store import (
    PublishStore,
    delete_on_phone,
    list_publish,
    parse_multipart,
    push_to_phone,
)
from v2.panel.preview import get_device_preview

V2_ROOT     = Path(__file__).resolve().parent.parent
CONFIG_ROOT = V2_ROOT / "config"
FLOWS_DIR   = V2_ROOT / "flows"
DATA_ROOT   = V2_ROOT / "data"

app = FastAPI(title="Instagram Bot v2 — Dashboard")


# ============================================================
# session manager — mapa device_alias → Popen (flota, nie jeden globalny proc)
# ============================================================
def _account_device_alias(account: str, config_root: Path) -> str | None:
    """Alias telefonu z account.json — bez load_account (to ruszałoby ADB)."""
    path = Path(config_root) / "accounts" / f"{account}.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    alias = (cfg.get("device") or {}).get("device_alias")
    return alias.strip() if isinstance(alias, str) and alias.strip() else None


def _device_adb_id(device_alias: str, config_root: Path) -> str | None:
    """Serial z config/devices/<alias>.json."""
    path = Path(config_root) / "devices" / f"{device_alias}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    serial = (doc.get("adb_id") or "").strip()
    return serial or None


class _SessionSlot:
    """Jeden proces CLI na telefonie — watchdog i STOP nie ruszają sąsiada."""

    def __init__(self, *, device_alias: str, account: str, flow: str | None,
                 proc: subprocess.Popen, started_at: float,
                 stdout_path: Path, stderr_path: Path,
                 deadline: float | None):
        self.device_alias = device_alias
        self.account = account
        self.flow = flow
        self.proc = proc
        self.started_at = started_at
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.deadline = deadline
        self.stopping_since: float | None = None

    def is_alive(self) -> bool:
        return self.proc.poll() is None


class SessionManager:
    # Ile ponad zarezerwowane okno bloku czekamy, zanim sami wyślemy STOP.
    # JobRunner sam pilnuje ramy, ale robi to MIĘDZY jednostkami pracy —
    # jedna rolka z AI i harvestem może się przeciągnąć. To jest siatka
    # bezpieczeństwa dla harmonogramu, nie podstawowy mechanizm.
    WATCHDOG_GRACE_S = 180

    def __init__(self, config_root: Path | None = None,
                 data_root: Path | None = None):
        # Testy wstrzykują tmp — produkcja bierze stałe modułu.
        self._config_root = config_root or CONFIG_ROOT
        self._data_root = data_root or DATA_ROOT
        self._slots: dict[str, _SessionSlot] = {}
        self._focus: str | None = None  # ostatni start / SSE — stop bez body
        self._lock = threading.Lock()

    def is_running_on(self, device_alias: str) -> bool:
        """Czy na tym telefonie jedzie żywy Popen."""
        with self._lock:
            slot = self._slots.get(device_alias)
            return bool(slot and slot.is_alive())

    def set_focus(self, device_alias: str | None) -> None:
        """Focus UI Na żywo — stop bez body gasi ten slot, nie całą flotę."""
        if not device_alias:
            return
        with self._lock:
            self._focus = device_alias

    def start(self, account: str, flow: str | None = None,
              mode: str = "flow", **opts) -> dict:
        """mode='flow' → `cli start --flow=…`; mode='queue' → `cli run-queue`;
        mode='login' → `cli login`; mode='signup' → `cli signup`;
        mode='profile' → `cli profile` (bio + avatar na IG);
        mode='calibrate' → `cli calibrate` (nowy telefon, dump + agent).

        `max_duration_s` (tylko dla 'queue') to rama czasowa bloku
        harmonogramu — trafia do JobRunnera i uzbraja watchdoga.

        409 (RuntimeError) tylko gdy TEN SAM telefon jest zajęty albo brak ADB.
        Inny slot może jechać równolegle.
        """
        device_alias = _account_device_alias(account, self._config_root)
        if not device_alias:
            raise RuntimeError(f"konto '{account}' nie ma device_alias w configu")
        self._assert_device_online(device_alias)

        with self._lock:
            existing = self._slots.get(device_alias)
            if existing and existing.is_alive():
                raise RuntimeError(
                    f"sesja już działa na {device_alias}: account={existing.account} "
                    f"tryb={existing.flow}"
                )
            # zaległy STOP z poprzedniego przebiegu ubiłby nowy od razu
            stop = self._data_root / "accounts" / account / "STOP"
            if stop.exists():
                stop.unlink()
            log_dir = self._data_root / "accounts" / account / "sessions"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            stdout_path = log_dir / f"{ts}-stdout.log"
            stderr_path = log_dir / f"{ts}-stderr.log"

            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore

            if mode == "queue":
                cmd = [sys.executable, "-u", "-m", "v2.cli", "run-queue",
                       f"--account={account}"]
                if opts.get("only"):
                    cmd.append(f"--only={opts['only']}")
                if opts.get("max_items"):
                    cmd.append(f"--max-items={int(opts['max_items'])}")
                if opts.get("queue_name"):
                    cmd.append(f"--queue={opts['queue_name']}")
                if opts.get("max_duration_s"):
                    cmd.append(f"--max-duration-s={float(opts['max_duration_s']):.0f}")
                if opts.get("plugin_oneshot"):
                    cmd.append(f"--plugin={opts['plugin_oneshot']}")
            elif mode == "login":
                cmd = [sys.executable, "-u", "-m", "v2.cli", "login",
                       f"--account={account}"]
            elif mode == "signup":
                cmd = [sys.executable, "-u", "-m", "v2.cli", "signup",
                       f"--account={account}"]
            elif mode == "calibrate":
                cmd = [sys.executable, "-u", "-m", "v2.cli", "calibrate",
                       f"--account={account}"]
            elif mode == "profile":
                cmd = [sys.executable, "-u", "-m", "v2.cli", "profile",
                       f"--account={account}"]
            else:
                cmd = [sys.executable, "-u", "-m", "v2.cli", "start",
                       f"--account={account}", f"--flow={flow}"]

            # Zamykamy uchwyty po Popen — child ma skopiowany fd.
            out_f = stdout_path.open("wb")
            err_f = stderr_path.open("wb")
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(V2_ROOT.parent),
                    stdout=out_f,
                    stderr=err_f,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    creationflags=creationflags,
                )
            finally:
                out_f.close()
                err_f.close()
            if mode == "flow":
                flow_label = flow
            elif mode == "login":
                flow_label = "logowanie"
            elif mode == "signup":
                flow_label = "zakładanie konta"
            elif mode == "calibrate":
                flow_label = "kalibracja"
            elif mode == "profile":
                flow_label = "profil IG"
            elif opts.get("plugin_oneshot"):
                flow_label = f"one-shot [{opts['plugin_oneshot']}]"
            else:
                flow_label = (
                    f"kolejka [{opts['queue_name']}]" if opts.get("queue_name")
                    else "kolejka"
                )
            started_at = time.time()
            max_duration_s = opts.get("max_duration_s")
            deadline = (started_at + float(max_duration_s)) if max_duration_s else None
            slot = _SessionSlot(
                device_alias=device_alias, account=account, flow=flow_label,
                proc=proc, started_at=started_at,
                stdout_path=stdout_path, stderr_path=stderr_path,
                deadline=deadline,
            )
            self._slots[device_alias] = slot
            self._focus = device_alias
            if deadline:
                self._arm_watchdog(slot)
            return self._status_unlocked()

    def _assert_device_online(self, device_alias: str) -> None:
        """409 gdy serial nie jest w `adb devices` w stanie device."""
        serial = _device_adb_id(device_alias, self._config_root)
        if not serial:
            raise RuntimeError(f"urządzenie '{device_alias}' nie ma adb_id")
        live = [
            r for r in list_adb_devices()
            if r.get("serial") == serial and r.get("state") == "device"
        ]
        if not live:
            raise RuntimeError(
                f"brak ADB: serial {serial} ({device_alias}) nie jest w stanie device"
            )

    def _arm_watchdog(self, slot: _SessionSlot) -> None:
        """Siatka bezpieczeństwa: STOP tylko tego slotu, nigdy sąsiada."""
        proc, account, device_alias, deadline = (
            slot.proc, slot.account, slot.device_alias, slot.deadline,
        )

        def _watch():
            timeout = max((deadline or 0) + self.WATCHDOG_GRACE_S - time.time(), 0)
            try:
                proc.wait(timeout=timeout)
                return          # skończyła się sama — nic do roboty
            except subprocess.TimeoutExpired:
                pass
            with self._lock:
                current = self._slots.get(device_alias)
                still_ours = current is slot and proc.poll() is None
            if still_ours:
                print(f"[watchdog] {account}@{device_alias}: sesja przekroczyła "
                      f"okno bloku o {self.WATCHDOG_GRACE_S}s — wysyłam STOP")
                self.stop(device_alias=device_alias)

        threading.Thread(target=_watch, daemon=True).start()

    def _resolve_stop_alias(self, device_alias: str | None,
                            account: str | None) -> str | None:
        """Który slot gasić: jawny alias, konto, albo focus (nie cała flota)."""
        if device_alias:
            return device_alias
        if account:
            return _account_device_alias(account, self._config_root)
        return self._focus

    def stop(self, grace_s: int = 30, *,
             device_alias: str | None = None,
             account: str | None = None) -> dict:
        """Miękkie zatrzymanie JEDNEGO slotu.

        Body `{device_alias}` albo `{account}`. Bez body = focus (ostatni
        start / SSE), nie cała flota. Plik STOP + terminate tylko tego Popen.
        """
        with self._lock:
            alias = self._resolve_stop_alias(device_alias, account)
            slot = self._slots.get(alias) if alias else None
            if not slot or not slot.is_alive():
                return self._status_unlocked()

            # snapshot: wątek ubijający nie może sięgnąć po mapę po nazwie,
            # bo do czasu jego wybudzenia może tam być już NASTĘPNA sesja
            proc, acc = slot.proc, slot.account
            if acc:
                stop = self._data_root / "accounts" / acc / "STOP"
                stop.parent.mkdir(parents=True, exist_ok=True)
                stop.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
            slot.stopping_since = time.time()

            def _hard_kill_later():
                try:
                    proc.wait(timeout=grace_s)
                except subprocess.TimeoutExpired:
                    try:
                        if sys.platform == "win32":
                            proc.send_signal(signal.CTRL_BREAK_EVENT)
                        else:
                            proc.terminate()
                        proc.wait(timeout=6)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                finally:
                    with self._lock:
                        current = self._slots.get(slot.device_alias)
                        if current is slot:
                            slot.stopping_since = None
                    self._clear_stop_file(acc)

            threading.Thread(target=_hard_kill_later, daemon=True).start()
            return self._status_unlocked()

    def force_stop(self, wait_s: float = 3.0) -> None:
        """Natychmiastowe zatrzymanie WSZYSTKICH slotów przy wyjściu panelu.

        `stop()` odpala daemon-thread z grace — przy exit procesu wątek
        znika, a child (CREATE_NEW_PROCESS_GROUP) zostaje orphanem.
        Tu ubijamy synchronicznie, żeby port 8765 i child nie wisiały."""
        with self._lock:
            slots = list(self._slots.values())
        for slot in slots:
            self._force_stop_slot(slot, wait_s=wait_s)

    def _force_stop_slot(self, slot: _SessionSlot, wait_s: float) -> None:
        proc, account = slot.proc, slot.account
        if not slot.is_alive():
            slot.stopping_since = None
            return
        if account:
            stop = self._data_root / "accounts" / account / "STOP"
            try:
                stop.parent.mkdir(parents=True, exist_ok=True)
                stop.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
            except OSError:
                pass
        slot.stopping_since = time.time()
        try:
            if sys.platform == "win32":
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    proc.terminate()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=wait_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            slot.stopping_since = None
            self._clear_stop_file(account)

    def _clear_stop_file(self, account: str | None = None):
        if not account:
            return
        p = self._data_root / "accounts" / account / "STOP"
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    def _slot_public(self, slot: _SessionSlot) -> dict:
        running = slot.is_alive()
        queue = None
        flow = slot.flow or ""
        if flow.startswith("kolejka [") and flow.endswith("]"):
            queue = flow[len("kolejka ["):-1]
        return {
            "device_alias": slot.device_alias,
            "account": slot.account,
            "running": running,
            "phase": slot.flow,
            "queue": queue,
            "started_at": slot.started_at,
            "eta": slot.deadline if running else None,
            "stopping": slot.stopping_since is not None,
            "pid": slot.proc.pid if slot.proc else None,
        }

    def _focus_slot(self) -> _SessionSlot | None:
        if self._focus and self._focus in self._slots:
            return self._slots[self._focus]
        for slot in self._slots.values():
            if slot.is_alive():
                return slot
        return next(iter(self._slots.values()), None)

    def slot_for_device(self, device_alias: str) -> _SessionSlot | None:
        with self._lock:
            return self._slots.get(device_alias)

    def slot_for_account(self, account: str) -> _SessionSlot | None:
        with self._lock:
            for slot in self._slots.values():
                if slot.account == account:
                    return slot
        return None

    def _status_unlocked(self) -> dict:
        """Odczyt statusu — wołający już trzyma `_lock` (start/stop)."""
        sessions = [
            self._slot_public(s)
            for s in self._slots.values()
            if s.is_alive() or s.stopping_since is not None
        ]
        running_any = any(s["running"] for s in sessions)
        focus = self._focus_slot()
        running_focus = bool(focus and focus.is_alive())
        classic = {
            "account": focus.account if focus else None,
            "flow": focus.flow if focus else None,
            "pid": focus.proc.pid if focus and focus.proc else None,
            "since": focus.started_at if focus else None,
            "stopping": bool(focus and focus.stopping_since is not None),
            "deadline": (focus.deadline if focus and running_focus else None),
            "stdout": str(focus.stdout_path) if focus and focus.stdout_path else None,
            "returncode": (
                None if running_focus
                else (focus.proc.returncode if focus and focus.proc else None)
            ),
        }
        return {
            "sessions": sessions,
            "running_any": running_any,
            "running": running_any,  # klasyk: ten sam bool
            **classic,
        }

    def status(self) -> dict:
        """Kontrakt floty + pola klasyka (running bool, account/flow focus)."""
        with self._lock:
            return self._status_unlocked()

    # Pola, które stary kod czytał z jednego slota (SSE / stats / delete).
    @property
    def account(self) -> str | None:
        slot = self._focus_slot()
        return slot.account if slot else None

    @property
    def stdout_path(self) -> Path | None:
        slot = self._focus_slot()
        return slot.stdout_path if slot else None

    def active_events_path(self) -> Path | None:
        if not self.account:
            return None
        return self._data_root / "accounts" / self.account / "events.jsonl"


SESSION = SessionManager()

# Flaga shutdown — SSE / cleanup wychodzą z pętli zamiast trzymać port
_panel_shutting_down = False


def _deny_if_scheduler_enabled(account: str | None = None,
                               device_alias: str | None = None):
    """423 tylko gdy global ON i scheduler_enabled TEGO telefonu.

    Globalny `enabled` zostaje bezpiecznikiem. Device z `scheduler_enabled=false`
    można startować ręcznie, nawet gdy master switch jest włączony.
    """
    from v2.config.schema import device_scheduler_enabled
    from v2.scheduler.state import is_enabled
    if not is_enabled(DATA_ROOT):
        return
    alias = device_alias or (
        _account_device_alias(account, CONFIG_ROOT) if account else None
    )
    if alias and not device_scheduler_enabled(alias, CONFIG_ROOT):
        return
    raise HTTPException(423, "harmonogram jest uruchomiony — zatrzymaj go "
                             "(zakładka Harmonogram), żeby sterować ręcznie")


# ============================================================
# API — konta / flowy / kroki
# ============================================================
@app.get("/api/accounts")
def list_accounts():
    """Konta = <alias>.json + device_alias z configu.

    Zwraca listę obiektów `{alias, device_alias}`, żeby panel mógł filtrować
    konta po urządzeniu wybranym w sidebarze. Pomijamy pliki towarzyszące
    (`*.queue.json`, `*.bak`)."""
    d = CONFIG_ROOT / "accounts"
    out = []
    for p in sorted(d.glob("*.json")):
        if p.stem.endswith(".queue"):
            continue
        device_alias = None
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            device_alias = (cfg.get("device") or {}).get("device_alias")
        except Exception:
            pass
        out.append({"alias": p.stem, "device_alias": device_alias})
    return out


@app.get("/api/flows")
def list_flows():
    return sorted([p.stem for p in FLOWS_DIR.glob("*.yml")])


def _claimed_clone_packages() -> dict:
    """Mapa device_alias -> {app_clone_package: account_alias} — żeby formularz
    tworzenia konta mógł ostrzec, że dany klon jest już przypisany."""
    claimed: dict = {}
    for p in (CONFIG_ROOT / "accounts").glob("*.json"):
        if p.stem.endswith(".queue"):
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        dev = cfg.get("device") or {}
        alias, pkg = dev.get("device_alias"), dev.get("app_clone_package")
        if alias and pkg:
            claimed.setdefault(alias, {})[pkg] = p.stem
    return claimed


def _accounts_by_device() -> dict[str, list[str]]:
    """Konta przypięte do aliasu telefonu — kolejność alfabetyczna."""
    by: dict[str, list[str]] = {}
    for p in (CONFIG_ROOT / "accounts").glob("*.json"):
        if p.stem.endswith(".queue"):
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        alias = (cfg.get("device") or {}).get("device_alias")
        if alias:
            by.setdefault(alias, []).append(p.stem)
    for alias in by:
        by[alias].sort()
    return by


def _scrcpy_int(value, lo: int, hi: int, default: int) -> int:
    """Int w [lo, hi]; śmieci → default (scrcpy.max_fps / max_size)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _uiauto_port(dev: dict) -> int | None:
    """Port z uiautodev.base_url — nagłówek Floty, nie diagnostyka ekranu."""
    url = ((dev.get("uiautodev") or {}).get("base_url") or "").strip()
    if not url:
        return None
    try:
        host = url.rsplit("/", 1)[-1]
        port = int(host.rsplit(":", 1)[-1])
        return port if port > 0 else None
    except (TypeError, ValueError):
        return None


def _calibrate_public(alias: str) -> dict:
    """Stan kalibracji z data/devices/<alias>/calibrate.json."""
    from v2.core import calibrate_state
    return calibrate_state.public(calibrate_state.load(DATA_ROOT, alias))


def _calibration_info(model: str | None) -> dict:
    """Plik kalibracji tego modelu. Brak nie blokuje Floty (fallback w CLI)."""
    if not model:
        return {"exists": False, "file": None}
    name = f"{str(model).replace('-', '_')}_draft.json"
    path = V2_ROOT / "calibration" / name
    if path.exists():
        return {"exists": True, "file": name}
    return {"exists": False, "file": None}


@app.get("/api/devices")
def list_devices():
    """Urządzenia + klony IG na każdym, z oznaczeniem które klony są już
    przypisane do konta — formularz tworzenia profilu wybiera z tej listy.

    Nieuzupełnione wpisy (np. nowy telefon bez resolution) bot sam zczytuje
    z ADB, jeśli serial jest podłączony.
    """
    claimed = _claimed_clone_packages()
    accounts_by = _accounts_by_device()
    live = {d["serial"]: d for d in list_adb_devices()}
    out = []
    for p in sorted((CONFIG_ROOT / "devices").glob("*.json")):
        try:
            dev = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if hardware_gaps(dev):
            try:
                result = hydrate_device_file(p, config_root=CONFIG_ROOT)
                dev = result.get("doc") or json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        alias = dev.get("alias", p.stem)
        clones = []
        for c in dev.get("clones") or []:
            clones.append({**c, "claimed_by": claimed.get(alias, {}).get(c.get("package"))})
        serial = dev.get("adb_id")
        live_row = live.get(serial) if serial else None
        scrcpy = dev.get("scrcpy") if isinstance(dev.get("scrcpy"), dict) else {}
        scrcpy_port = scrcpy.get("port")
        try:
            scrcpy_port = int(scrcpy_port) if scrcpy_port is not None else None
        except (TypeError, ValueError):
            scrcpy_port = None
        # flock sesji (nie claimed_by na klonie) — plik locka zostaje po release
        lock_holder = peek_device_lock(DATA_ROOT, serial) if serial else None
        calib = _calibration_info(dev.get("model"))
        online = bool(live_row and live_row.get("state") == "device")
        out.append({
            "alias": alias, "model": dev.get("model"), "adb_id": serial,
            "manufacturer": dev.get("manufacturer"), "clones": clones,
            "resolution": dev.get("resolution"),
            "dpi": dev.get("dpi"),
            "android_version": dev.get("android_version"),
            "complete": not hardware_gaps(dev),
            "connected": online,
            "online": online,
            # Brak klucza w JSON = włączony (dziedziczy globalny bezpiecznik).
            "scheduler_enabled": bool(dev.get("scheduler_enabled", True)),
            "scrcpy_port": scrcpy_port,
            "scrcpy_max_fps": _scrcpy_int(scrcpy.get("max_fps"), 1, 60, 15),
            "scrcpy_max_size": _scrcpy_int(scrcpy.get("max_size"), 240, 1920, 900),
            "locked": bool(lock_holder),
            "claimed_by": (lock_holder or {}).get("owner"),
            "lock": lock_holder,
            "accounts": accounts_by.get(alias) or [],
            "calibration_exists": bool(calib["exists"]),
            "calibration_file": calib["file"],
            # Komentarz: stan walkera (idle / running / needs_agent) — Flota
            "calibrate": _calibrate_public(alias),
            "uiautodev_port": _uiauto_port(dev),
        })
    return out


@app.get("/api/adb-devices")
def api_adb_devices():
    """Live `adb devices -l` + czy serial już ma alias w configu."""
    registered = {}
    for p in (CONFIG_ROOT / "devices").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        serial = d.get("adb_id")
        if serial:
            registered[serial] = d.get("alias") or p.stem
    rows = []
    for d in list_adb_devices():
        rows.append({
            **d,
            "registered_as": registered.get(d["serial"]),
        })
    return rows


@app.post("/api/devices/{alias}/hydrate")
def api_hydrate_device(alias: str):
    """Ponownie zczytaj model/resolution/dpi/klony z podłączonego telefonu.

    409 tylko gdy TEN serial jedzie — drugi telefon można hydrować.
    """
    if any(c in alias for c in "/\\") or ".." in alias or " " in alias:
        raise HTTPException(400, "nieprawidłowy alias")
    path = CONFIG_ROOT / "devices" / f"{alias}.json"
    if not path.exists():
        raise HTTPException(404, f"urządzenie '{alias}' nie istnieje")
    _deny_if_session_running(device_alias=alias)
    result = hydrate_device_file(path, config_root=CONFIG_ROOT, force=True)
    if result.get("error"):
        raise HTTPException(400, f"nie udało się zczytać z ADB: {result['error']}")
    return {
        "ok": True,
        "alias": alias,
        "changed": result.get("changed"),
        "probed": result.get("probed"),
        "doc": result.get("doc"),
    }


def _alias_ok(alias: str) -> str:
    """Alias bez path traversal — ten sam check co hydrate."""
    if any(c in alias for c in "/\\") or ".." in alias or " " in alias:
        raise HTTPException(400, "nieprawidłowy alias")
    return alias


@app.post("/api/devices/{alias}/calibrate")
async def api_calibrate_start(alias: str, request: Request):
    """Odpal `cli calibrate` na pierwszym koncie telefonu (albo body.account).

    Harmonogram NIE blokuje (423) — inny telefon może jechać plan.
    409 tylko gdy TEN serial jest zajęty. 400 bez konta na urządzeniu.
    """
    _alias_ok(alias)
    _require_device(alias)
    acc = ""
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw)
            if isinstance(body, dict):
                acc = (body.get("account") or "").strip()
    except Exception:
        acc = ""
    known = _accounts_by_device().get(alias) or []
    if acc and acc not in known:
        raise HTTPException(400, f"konto '{acc}' nie jest na {alias}")
    if not acc:
        if not known:
            raise HTTPException(
                400, "brak konta na tym urządzeniu — utwórz je w Konta")
        acc = known[0]
    from v2.core import calibrate_state
    # Komentarz: status od razu — walker nadpisze po starcie
    calibrate_state.save(DATA_ROOT, alias, {
        "status": "running", "account": acc, "device_alias": alias, "step": "queued",
    })
    try:
        return SESSION.start(acc, mode="calibrate")
    except RuntimeError as e:
        calibrate_state.save(DATA_ROOT, alias, {
            "status": "failed", "account": acc, "device_alias": alias,
            "note": str(e),
        })
        raise HTTPException(409, str(e))


@app.get("/api/devices/{alias}/calibrate")
def api_calibrate_status(alias: str):
    """Postęp walkera — panel polluje, gdy Flota jest otwarta."""
    _alias_ok(alias)
    _require_device(alias)
    return _calibrate_public(alias)


@app.post("/api/devices/{alias}/calibrate/reply")
async def api_calibrate_reply(alias: str, request: Request):
    """Agent / operator: ok | skip | retry | abort — odblokuj czekający krok."""
    from v2.core import calibrate_state
    _alias_ok(alias)
    _require_device(alias)
    body = await request.json()
    action = (body.get("action") if isinstance(body, dict) else "") or ""
    note = (body.get("note") if isinstance(body, dict) else "") or ""
    try:
        doc = calibrate_state.set_reply(DATA_ROOT, alias, action, note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "kalibracja nie czeka")
    return {"ok": True, **calibrate_state.public(doc)}


@app.get("/api/devices/{alias}/calibrate/shot")
def api_calibrate_shot(alias: str):
    """Screen z kroku, który nie przeszedł — tylko z data/devices/<alias>/."""
    from v2.core import calibrate_state
    _alias_ok(alias)
    _require_device(alias)
    doc = calibrate_state.load(DATA_ROOT, alias)
    raw = doc.get("shot_path") or ""
    if not raw:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    path = Path(raw)
    try:
        resolved = path.resolve()
        root = (DATA_ROOT / "devices" / alias).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise HTTPException(400, "screen poza katalogiem urządzenia")
    if not resolved.is_file():
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    mime = "image/png" if resolved.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(resolved, media_type=mime,
                        headers={"Cache-Control": "no-store"})


@app.get("/api/devices/{alias}/preview")
def api_device_preview(alias: str):
    """JPEG z pliku FB sesji (`data/devices/<alias>/preview.jpg`).

    200 tylko gdy na tym aliasie jedzie sesja i plik jest świeży
    (albo rzadki fallback uiautodev, gdy FB nie zapisał klatki).
    Bez sesji: 204 ``no_session`` — nie uiautodev, nie drugi scrcpy.

    Statusy:
      200 image/jpeg — klatka
      204 — brak klatki; nagłówek ``X-Preview-Reason``: ``offline`` |
            ``no_session`` (panel pokazuje placeholder, nie 500)
      404 — alias nie istnieje w config/devices/
    """
    _require_device(alias)
    try:
        result = get_device_preview(
            alias, CONFIG_ROOT,
            session_running=SESSION.is_running_on(alias),
            data_root=DATA_ROOT,
        )
    except Exception:
        # cokolwiek padło w capture — dashboard zostaje żywy
        return Response(
            status_code=204,
            headers={
                "Cache-Control": "no-store",
                "X-Preview-Reason": "offline",
            },
        )
    if result.jpeg:
        headers = {"Cache-Control": "no-store"}
        if result.cached:
            headers["X-Preview-Cached"] = "1"
        return Response(
            content=result.jpeg,
            media_type="image/jpeg",
            headers=headers,
        )
    return Response(
        status_code=204,
        headers={
            "Cache-Control": "no-store",
            "X-Preview-Reason": result.reason or "no_session",
        },
    )


@app.post("/api/devices")
async def create_device(request: Request):
    """Rejestracja urządzenia — tworzy `config/devices/<alias>.json`.

    Wymagane: alias, adb_id. Opcjonalnie: model, manufacturer, product,
    android_version, resolution, dpi, root_method, clones, uiautodev."""
    body = await request.json()
    alias = (body.get("alias") or "").strip()
    adb_id = (body.get("adb_id") or "").strip()
    if not alias or not adb_id:
        raise HTTPException(400, "wymagane: alias, adb_id")
    # prosta walidacja nazwy pliku — bez ścieżek i spacji
    if any(c in alias for c in "/\\..") or " " in alias:
        raise HTTPException(400, "alias nie może zawierać spacji ani znaków ścieżki")

    devices_dir = CONFIG_ROOT / "devices"
    devices_dir.mkdir(parents=True, exist_ok=True)
    path = devices_dir / f"{alias}.json"
    if path.exists():
        raise HTTPException(409, f"urządzenie '{alias}' już istnieje")

    clones = body.get("clones")
    if clones is None:
        # domyślnie jeden slot z oryginalnym pakietem IG
        clones = [{"slot": 1, "package": "com.instagram.android",
                   "note": "oryginalna instalacja"}]
    if not isinstance(clones, list):
        raise HTTPException(400, "clones musi być listą")

    doc = {
        "alias": alias,
        "adb_id": adb_id,
        "model": body.get("model"),
        "product": body.get("product"),
        "manufacturer": body.get("manufacturer"),
        "android_version": body.get("android_version"),
        "resolution": body.get("resolution"),
        "dpi": body.get("dpi"),
        "root_method": body.get("root_method") or "magisk",
        "clones": clones,
        "uiautodev": body.get("uiautodev") or {"base_url": "http://127.0.0.1:20242"},
        "scheduler_enabled": True,
    }
    # Zczytaj hardware z podłączonego telefonu — resolution/model/dpi nie z formularza.
    probe = hydrate_device_doc(doc, persist_path=None, config_root=CONFIG_ROOT, force=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return {
        "ok": True,
        "alias": alias,
        "path": str(path.relative_to(CONFIG_ROOT)),
        "hydrated": bool(probe.get("hydrated")),
        "probed": probe.get("probed"),
        "warning": None if probe.get("hydrated") else (
            probe.get("error") or "telefon nieodpowiada — podłącz i odśwież z ADB"
        ),
    }


@app.patch("/api/devices/{alias}")
async def patch_device(alias: str, request: Request):
    """Zapisz subset pól urządzenia — Harmonogram + scrcpy FPS/rozmiar.

    Nie nadpisujemy całego JSON-a (hydrate / klony zostają). Brak klucza
    w body = nie ruszaj. Nieznane pola → 400, żeby UI nie udawał zapisu.
    `scrcpy_max_fps` / `scrcpy_max_size`: serwer scrcpy od następnej sesji;
    interwał JPEG i poll panelu (1–15 Hz) od razu — writer czyta JSON.
    """
    _require_device(alias)
    if any(c in alias for c in "/\\") or ".." in alias or " " in alias:
        raise HTTPException(400, "nieprawidłowy alias")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    allowed = {"scheduler_enabled", "scrcpy_max_fps", "scrcpy_max_size"}
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(400, f"nieznane pola: {sorted(unknown)}")
    if not (allowed & set(body)):
        raise HTTPException(400, f"podaj przynajmniej jedno z: {sorted(allowed)}")
    path = CONFIG_ROOT / "devices" / f"{alias}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(400, f"uszkodzony plik urządzenia '{alias}'")
    if not isinstance(doc, dict):
        raise HTTPException(400, f"uszkodzony plik urządzenia '{alias}'")
    if "scheduler_enabled" in body:
        doc["scheduler_enabled"] = bool(body["scheduler_enabled"])
    if "scrcpy_max_fps" in body or "scrcpy_max_size" in body:
        block = doc.get("scrcpy") if isinstance(doc.get("scrcpy"), dict) else {}
        if "scrcpy_max_fps" in body:
            block["max_fps"] = _scrcpy_int(body["scrcpy_max_fps"], 1, 60, 15)
        if "scrcpy_max_size" in body:
            block["max_size"] = _scrcpy_int(body["scrcpy_max_size"], 240, 1920, 900)
        doc["scrcpy"] = block
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    scrcpy = doc.get("scrcpy") if isinstance(doc.get("scrcpy"), dict) else {}
    return {
        "ok": True,
        "alias": alias,
        "scheduler_enabled": bool(doc.get("scheduler_enabled", True)),
        "scrcpy_max_fps": _scrcpy_int(scrcpy.get("max_fps"), 1, 60, 15),
        "scrcpy_max_size": _scrcpy_int(scrcpy.get("max_size"), 240, 1920, 900),
    }


def _ensure_clone_on_device(device_alias: str, package: str,
                            identity: dict | None = None) -> None:
    """Dopisz package do devices/<alias>.json → clones, jeśli jeszcze go nie ma.

    Panel pozwala wpisać APPID ręcznie — lista klonów urządzenia zostaje
    podpowiedzią (hint), nie bramką walidacji. `identity` z joba klona
    nadpisuje istniejący wpis (MAC / IMEI / android_id).
    """
    if not device_alias or not package:
        return
    dev_path = CONFIG_ROOT / "devices" / f"{device_alias}.json"
    if not dev_path.exists():
        return
    try:
        dev = json.loads(dev_path.read_text(encoding="utf-8"))
    except Exception:
        return
    clones = list(dev.get("clones") or [])
    for c in clones:
        if c.get("package") == package:
            if identity:
                c["identity"] = identity
                dev["clones"] = clones
                dev_path.write_text(json.dumps(dev, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
            return
    next_slot = max((int(c.get("slot") or 0) for c in clones), default=0) + 1
    rec = {"slot": next_slot, "package": package, "note": "dodany z panelu"}
    if identity:
        rec["identity"] = identity
    clones.append(rec)
    dev["clones"] = clones
    dev_path.write_text(json.dumps(dev, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")


@app.post("/api/accounts")
async def create_account(request: Request):
    body = await request.json()
    alias   = (body.get("alias") or "").strip()
    device_alias = (body.get("device_alias") or "").strip()
    app_clone_package = (body.get("app_clone_package") or "").strip()
    # Komentarz: login IG można dopisać później — bez niego alias idzie w meta.ig_username
    ig_user = (body.get("ig_username") or "").strip() or alias
    if not alias or not device_alias or not app_clone_package:
        raise HTTPException(400, "wymagane: alias, device_alias, app_clone_package")

    dev_path = CONFIG_ROOT / "devices" / f"{device_alias}.json"
    if not dev_path.exists():
        raise HTTPException(404, f"urządzenie '{device_alias}' nie istnieje")

    # ręczny APPID OK — dopisz do listy klonów urządzenia (podpowiedzi w UI)
    _ensure_clone_on_device(device_alias, app_clone_package)

    from v2.config.schema import new_account_from_template, ConfigError
    try:
        new_account_from_template(alias, ig_user, device_alias,
                                  app_clone_package=app_clone_package,
                                  config_root=CONFIG_ROOT)
    except ConfigError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "alias": alias}


def _safe_account_alias(account: str) -> str:
    """Alias bez path traversal — ten sam kontrakt co przy tworzeniu konta."""
    alias = (account or "").strip()
    if not alias or any(c in alias for c in "/\\") or ".." in alias or " " in alias:
        raise HTTPException(400, "nieprawidłowy alias konta")
    return alias


@app.post("/api/accounts/{account}/clone")
async def set_account_clone(account: str, request: Request):
    """Ustaw lub wyczyść `device.app_clone_package` na koncie.

    Pusty string / null → clear (zwalnia „zajętość” w UI). Niepusty → zapis
    + dopisanie do devices/<alias>.clones gdy brakuje w podpowiedziach.
    """
    alias = _safe_account_alias(account)
    body = await request.json()
    if "app_clone_package" not in body:
        raise HTTPException(400, "wymagane: app_clone_package (string; pusty = clear)")
    pkg = "" if body.get("app_clone_package") is None else str(body.get("app_clone_package")).strip()

    path = CONFIG_ROOT / "accounts" / f"{alias}.json"
    if not path.exists():
        raise HTTPException(404, f"konto nie istnieje: {alias}")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(400, f"nieczytelny config: {e}")

    cfg.setdefault("device", {})["app_clone_package"] = pkg
    device_alias = (cfg.get("device") or {}).get("device_alias") or ""
    if pkg:
        _ensure_clone_on_device(device_alias, pkg)

    backup = path.with_suffix(".json.bak")
    backup.write_bytes(path.read_bytes())
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        from v2.config.schema import load_account
        load_account(alias)
    except Exception as e:
        path.write_bytes(backup.read_bytes())
        raise HTTPException(400, f"walidacja padła — przywrócono backup: {e}")
    return {"ok": True, "alias": alias, "app_clone_package": pkg}


@app.post("/api/clones/preview")
async def api_clones_preview(request: Request):
    """Losowa tożsamość + wolne APPID — bez APK / JDK / appclonera.

    200 gdy jest devices/<alias>.json; ADB może paść — identity i tak wraca.
    404 tylko gdy alias nie istnieje. Nie wołać create (409 JDK) zamiast tego.
    """
    body = await request.json()
    device_alias = (body.get("device_alias") or "").strip()
    if not device_alias:
        raise HTTPException(400, "wymagane: device_alias")
    # Komentarz: operator wybrał model — preview nie rotuje na inny profil
    preferred = (body.get("model") or "").strip() or None
    ident_in = body.get("identity")
    if not preferred and isinstance(ident_in, dict):
        preferred = str(ident_in.get("model") or "").strip() or None
    try:
        return preview_clone(
            device_alias, config_root=CONFIG_ROOT, preferred_model=preferred)
    except CloneJobError as e:
        raise HTTPException(404, str(e))


@app.post("/api/clones/create")
async def api_clones_create(request: Request):
    """Jedna akcja panelu: tożsamość → APK → adb install → launch (202)."""
    body = await request.json()
    device_alias = (body.get("device_alias") or "").strip()
    if not device_alias:
        raise HTTPException(400, "wymagane: device_alias")
    raw_ident = body.get("identity")
    if raw_ident is not None and not isinstance(raw_ident, dict):
        raise HTTPException(400, "identity musi być obiektem")
    try:
        snap = start_job_thread(
            device_alias, config_root=CONFIG_ROOT, data_root=DATA_ROOT,
            identity=raw_ident)
    except CloneBusy as e:
        raise HTTPException(409, str(e))
    except CloneJobError as e:
        # Komentarz: 409 przy busy / brak device / apk / JDK / lock — UI pokazuje detail
        raise HTTPException(409, str(e))
    return JSONResponse(snap, status_code=202)


@app.get("/api/clones/apks")
def api_clones_apks(remote: int = Query(0), device_alias: str = Query("")):
    """Lokalne APK z `apks/` (źródło klona); `device_alias` → info o stock IG.

    Panel tylko pokazuje versionName z telefonu — wersję wybierasz z dysku.
    `?remote=1` (Aptoide) zostaje w API, dialog Utwórz go nie woła.
    """
    try:
        root = appcloner_root(create=True)
    except CloneJobError as e:
        raise HTTPException(409, str(e))
    active = active_apk(root)
    out = {
        "ok": True,
        "root": str(root),
        "local": list_local_apks(root),
        "active": str(active) if active.is_file() else None,
        "remote": [],
    }
    # Komentarz: stock z ADB niezależnie od remote — UI pokazuje wersję z telefonu
    alias = (device_alias or "").strip()
    stock = None
    if alias:
        try:
            _path, dev = load_device(CONFIG_ROOT, alias)
            serial = (dev.get("adb_id") or "").strip()
            if not serial or serial == "REPLACE":
                raise CloneJobError("brak adb_id")
            rec = read_installed_version(serial)
            stock = {
                "package": rec.get("package"),
                "version": rec.get("version") or "",
                "version_code": rec.get("version_code"),
                "device_alias": alias,
            }
            out["stock"] = stock
        except Exception as e:
            out["stock_error"] = str(e)
    if not int(remote or 0):
        return out
    try:
        rows = list_remote_apks(
            want_version=(stock or {}).get("version") or None,
            want_version_code=(stock or {}).get("version_code"),
        )
        if stock and (stock.get("version") or stock.get("version_code") is not None):
            rows = rank_remote_versions(
                rows,
                stock_version=stock.get("version") or "",
                stock_version_code=stock.get("version_code"),
            )
        out["remote"] = rows
    except ApkStoreError as e:
        out["remote_error"] = str(e)
    return out


@app.post("/api/clones/apks/fetch")
async def api_clones_apks_fetch(request: Request):
    """Pobierz wersję IG z Aptoide (202) → apks/ + instagram.apk."""
    body = await request.json()
    try:
        app_id = int(body.get("app_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "wymagane: app_id")
    try:
        root = appcloner_root(create=True)
        snap = start_fetch_thread(app_id, root=root)
    except ApkStoreError as e:
        raise HTTPException(409, str(e))
    except CloneJobError as e:
        raise HTTPException(409, str(e))
    return JSONResponse(snap, status_code=202)


@app.get("/api/clones/apks/status")
def api_clones_apks_status():
    """Stan pobierania bazowego APK."""
    return fetch_job_snapshot()


@app.post("/api/clones/apks/activate")
async def api_clones_apks_activate(request: Request):
    """Ustaw wybrany plik jako instagram.apk (źródło appclonera)."""
    body = await request.json()
    try:
        root = appcloner_root(create=True)
        src = resolve_stored_apk(
            root,
            filename=str(body.get("filename") or ""),
            path=str(body.get("path") or ""),
        )
        return activate_source_apk(root, src)
    except (ApkStoreError, CloneJobError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/clones/apks/from-device")
async def api_clones_apks_from_device(request: Request):
    """Zciągnij zainstalowany stock IG z telefonu (`pm path` + adb pull)."""
    body = await request.json()
    device_alias = (body.get("device_alias") or "").strip()
    if not device_alias:
        raise HTTPException(400, "wymagane: device_alias")
    try:
        _path, dev = load_device(CONFIG_ROOT, device_alias)
        serial = (dev.get("adb_id") or "").strip()
        if not serial or serial == "REPLACE":
            raise CloneJobError("brak adb_id")
        root = appcloner_root(create=True)
        return pull_apk_from_device(root, serial)
    except (ApkStoreError, CloneJobError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/clones/status")
def api_clones_status():
    """Stan joba klona + ogon logu."""
    return JSONResponse(job_snapshot())


@app.post("/api/clones/cancel")
def api_clones_cancel():
    """Ubij subprocess appclonera jeśli jeszcze przed install."""
    request_cancel()
    return JSONResponse(job_snapshot())


@app.post("/api/clones/scan")
async def api_clones_scan(request: Request):
    """pm list packages → dopisz brakujące com.instagram.* do clones[]."""
    body = await request.json()
    device_alias = (body.get("device_alias") or "").strip()
    if not device_alias:
        raise HTTPException(400, "wymagane: device_alias")
    try:
        _path, dev = load_device(CONFIG_ROOT, device_alias)
    except CloneJobError as e:
        raise HTTPException(404, str(e))
    serial = (dev.get("adb_id") or "").strip()
    if not serial:
        raise HTTPException(400, "brak adb_id")
    try:
        pkgs = list_installed_packages(serial)
    except CloneJobError as e:
        raise HTTPException(400, str(e))
    added = []
    for pkg in sorted(pkgs):
        if not pkg.startswith("com.instagram."):
            continue
        before = {c.get("package") for c in (dev.get("clones") or [])}
        _ensure_clone_on_device(device_alias, pkg)
        if pkg not in before:
            added.append(pkg)
    _path, dev = load_device(CONFIG_ROOT, device_alias)
    held = packages_held_on_device(CONFIG_ROOT, device_alias)
    dropped: list[str] = []
    # Komentarz: puste pkgs = ADB padł — nie czyść clones[] jakby telefon był pusty
    if pkgs:
        dropped = prune_stale_clone_records(dev, pkgs, held)
        if dropped:
            save_device(_path, dev)
            _path, dev = load_device(CONFIG_ROOT, device_alias)
    return {
        "ok": True,
        "added": added,
        "dropped": dropped,
        "clones": dev.get("clones") or [],
    }


@app.delete("/api/accounts/{account}")
def delete_account(account: str):
    """Usuń konto z panelu — config + credentials + klon APK; data ostrożnie.

    Bezpieczeństwo danych:
    - NIE kasujemy ślepo całego `data/accounts/<alias>/` (sesje, logi, raw).
    - Preferencja: usuń config (`*.json` + `.bak` + katalog `.queues` + `.actions`),
      a folder data przenieś na `data/accounts/<alias>.deleted-<ts>` (trash).
    - Jeśli rename się nie uda — zostaw data na miejscu i zgłoś w odpowiedzi
      (`data_kept=True`), żeby operator mógł posprzątać ręcznie.
    - Aktywna sesja tego konta → 409 (najpierw Stop).
    - Klon (`com.instagram.androi*`, nie stock) schodzi z telefonu, o ile
      żadne inne konto nie trzyma tego APPID. Błąd ADB nie cofa skasowania.
    """
    alias = _safe_account_alias(account)
    cfg_path = CONFIG_ROOT / "accounts" / f"{alias}.json"
    if not cfg_path.exists():
        raise HTTPException(404, f"konto nie istnieje: {alias}")

    # Zablokuj kasowanie gdy sesja leci na tym koncie — unikamy crasha / orphanów
    slot = SESSION.slot_for_account(alias)
    if slot and slot.is_alive():
        raise HTTPException(
            409,
            f"konto '{alias}' ma aktywną sesję — zatrzymaj ją (Stop) przed usunięciem",
        )

    # Komentarz: pakiet i telefon zczytaj PRZED unlink — potem uninstall klona
    clone_device = ""
    clone_pkg = ""
    try:
        cfg_preview = json.loads(cfg_path.read_text(encoding="utf-8"))
        clone_device = ((cfg_preview.get("device") or {}).get("device_alias") or "").strip()
        clone_pkg = ((cfg_preview.get("device") or {}).get("app_clone_package") or "").strip()
    except Exception:
        clone_device, clone_pkg = "", ""

    removed = {"config": False, "config_bak": False, "queues": False,
               "actions": False, "legacy_queue": False, "credentials": False,
               "data": None, "clone": None}
    # 1) config konta
    try:
        cfg_path.unlink()
        removed["config"] = True
    except OSError as e:
        raise HTTPException(500, f"nie udało się usunąć configu: {e}")

    bak = CONFIG_ROOT / "accounts" / f"{alias}.json.bak"
    if bak.exists():
        try:
            bak.unlink()
            removed["config_bak"] = True
        except OSError:
            pass

    # 2) scenariusze kolejek (katalog + legacy plik-rodzeństwo)
    queues_dir = CONFIG_ROOT / "accounts" / f"{alias}.queues"
    if queues_dir.is_dir():
        try:
            shutil.rmtree(queues_dir)
            removed["queues"] = True
        except OSError:
            pass
    # 2b) zapisane akcje (presety kroku — ten sam wzorzec co .queues)
    actions_dir = CONFIG_ROOT / "accounts" / f"{alias}.actions"
    if actions_dir.is_dir():
        try:
            shutil.rmtree(actions_dir)
            removed["actions"] = True
        except OSError:
            pass
    legacy_q = CONFIG_ROOT / "accounts" / f"{alias}.queue.json"
    if legacy_q.exists():
        try:
            legacy_q.unlink()
            removed["legacy_queue"] = True
        except OSError:
            pass

    # 3) credentials — wyczyść wpis (nie zostawiaj hasła po usuniętym aliasie)
    try:
        from v2.config import credentials
        credentials.set(alias, None, None)
        removed["credentials"] = True
    except Exception:
        pass

    # 4) data → trash (rename), NIE rmtree
    data_dir = DATA_ROOT / "accounts" / alias
    data_kept = False
    data_trash = None
    if data_dir.is_dir():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        trash = DATA_ROOT / "accounts" / f"{alias}.deleted-{ts}"
        # kolizja ekstremalnie rzadka — doklej licznik
        n = 0
        while trash.exists():
            n += 1
            trash = DATA_ROOT / "accounts" / f"{alias}.deleted-{ts}-{n}"
        try:
            data_dir.rename(trash)
            # as_posix — czytelna ścieżka w odpowiedzi API (także na Windows)
            removed["data"] = trash.relative_to(DATA_ROOT).as_posix()
            data_trash = removed["data"]
        except OSError:
            # bezpieczniej zostawić niż kasować na ślepo
            data_kept = True
            removed["data"] = "kept"
    else:
        removed["data"] = None

    # 5) bloki osi / planu dnia — inaczej orphan alias zostaje w cycle.json
    #    (locked) i psuje limity oraz slots regeneracji
    timeline_purge = None
    try:
        from v2.scheduler import device_planner as dp
        timeline_purge = dp.purge_account_from_plans(alias, DATA_ROOT, CONFIG_ROOT)
        removed["timeline"] = {
            "devices": timeline_purge.get("devices") or [],
            "removed_cycle": timeline_purge.get("removed_cycle", 0),
            "removed_plans": timeline_purge.get("removed_plans", 0),
        }
    except Exception as e:
        removed["timeline"] = {"error": str(e)}

    # 6) klon APK z telefonu — nigdy stock; ADB fail nie cofa skasowania konta
    removed["clone"] = uninstall_clone_for_deleted_account(
        config_root=CONFIG_ROOT,
        device_alias=clone_device,
        package=clone_pkg,
        account_alias=alias,
    )

    return {
        "ok": True,
        "alias": alias,
        "removed": removed,
        "data_kept": data_kept,
        "data_trash": data_trash,
        "timeline_purge": timeline_purge,
        "clone": removed["clone"],
    }


@app.get("/api/accounts/{account}/credentials")
def get_account_credentials(account: str):
    from v2.config import credentials
    return credentials.status(account)


@app.post("/api/accounts/{account}/credentials")
async def set_account_credentials(account: str, request: Request):
    alias = _safe_account_alias(account)
    cfg_path = CONFIG_ROOT / "accounts" / f"{alias}.json"
    if not cfg_path.exists():
        raise HTTPException(404, f"konto nie istnieje: {alias}")
    body = await request.json()
    from v2.config import credentials
    ig_user = body.get("ig_username")
    signup = body.get("signup") if "signup" in body else None
    credentials.set(alias, ig_user, body.get("ig_password"), signup=signup)
    # trzymaj meta.ig_username w sync z Login IG (to samo pole w UI)
    user = (ig_user or "").strip()
    if user:
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg.setdefault("meta", {})["ig_username"] = user
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        except Exception:
            pass
    return credentials.status(alias)


@app.get("/api/accounts/{account}/avatar")
def get_account_avatar(account: str):
    """Czy na dysku jest zdjęcie profilowe do wrzucenia po logowaniu."""
    alias = _safe_account_alias(account)
    path = avatar_path(DATA_ROOT, alias)
    return {
        "ok": True,
        "has_avatar": bool(path),
        "filename": path.name if path else None,
    }


@app.post("/api/accounts/{account}/avatar")
async def set_account_avatar(account: str, request: Request):
    """Upload awatara (multipart `file` albo JSON file_b64)."""
    alias = _safe_account_alias(account)
    cfg_path = CONFIG_ROOT / "accounts" / f"{alias}.json"
    if not cfg_path.exists():
        raise HTTPException(404, f"konto nie istnieje: {alias}")
    filename = "avatar.jpg"
    blob = b""
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            body = await request.json()
        except Exception:
            body = {}
        raw_b64 = (body or {}).get("file_b64") or (body or {}).get("content_b64") or ""
        if raw_b64:
            try:
                blob = base64.b64decode(raw_b64)
            except Exception:
                blob = b""
        filename = (body or {}).get("filename") or filename
    elif "multipart/" in ctype:
        raw = await request.body()
        _fields, upload = parse_multipart(request.headers.get("content-type") or "", raw)
        if upload:
            filename = upload.get("filename") or filename
            blob = upload.get("blob") or b""
    if not blob:
        raise HTTPException(400, "brak pliku zdjęcia")
    try:
        dest = save_avatar(DATA_ROOT, alias, blob, filename)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "has_avatar": True, "filename": dest.name}


@app.get("/api/accounts/{account}/avatar/file")
def get_account_avatar_file(account: str):
    """Blob awatara do podglądu w ustawieniach konta."""
    alias = _safe_account_alias(account)
    path = avatar_path(DATA_ROOT, alias)
    if not path:
        raise HTTPException(404, "brak zdjęcia profilowego")
    ext = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=mime)


@app.get("/api/accounts/{account}/profile")
def get_account_profile(account: str):
    """Bio + czy leży avatar — do formularza w ustawieniach konta."""
    alias = _safe_account_alias(account)
    return profile_status(DATA_ROOT, alias)


@app.post("/api/accounts/{account}/profile")
async def set_account_profile(account: str, request: Request):
    """Zapisz biogram na PC (bot wstawi na IG przy apply / login / signup)."""
    alias = _safe_account_alias(account)
    cfg_path = CONFIG_ROOT / "accounts" / f"{alias}.json"
    if not cfg_path.exists():
        raise HTTPException(404, f"konto nie istnieje: {alias}")
    try:
        body = await request.json()
    except Exception:
        body = {}
    bio = set_bio(DATA_ROOT, alias, str((body or {}).get("bio") or ""))
    st = profile_status(DATA_ROOT, alias)
    st["bio"] = bio
    return st


@app.post("/api/accounts/{account}/profile/apply")
async def apply_account_profile(account: str):
    """Krótki flow: wake → IG → bio + zdjęcie profilowe."""
    alias = _safe_account_alias(account)
    _deny_if_scheduler_enabled(account=alias)
    st = profile_status(DATA_ROOT, alias)
    if not st.get("bio") and not st.get("has_avatar"):
        raise HTTPException(400, "najpierw zapisz bio albo zdjęcie profilowe")
    try:
        return SESSION.start(alias, mode="profile")
    except RuntimeError as e:
        raise HTTPException(409, str(e))


# ============================================================
# API — zapisane akcje (named preset kroku, nie nowy typ runu)
# ============================================================
@app.get("/api/accounts/{account}/actions")
def list_saved_actions(account: str):
    """Lista presetów kroku (`<alias>.actions/*.json`) — pełne obiekty."""
    alias = _safe_account_alias(account)
    from v2.plugins import store
    try:
        return store.list_actions(alias)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.get("/api/accounts/{account}/actions/{slug}")
def get_saved_action(account: str, slug: str):
    alias = _safe_account_alias(account)
    from v2.plugins import store
    try:
        return JSONResponse(store.read_action(alias, slug))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/accounts/{account}/actions")
async def create_saved_action(account: str, request: Request):
    alias = _safe_account_alias(account)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    from v2.plugins import store
    try:
        return store.create_action(alias, body)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/accounts/{account}/actions/{slug}")
async def update_saved_action(account: str, slug: str, request: Request):
    alias = _safe_account_alias(account)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    from v2.plugins import store
    try:
        return store.update_action(alias, slug, body)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/accounts/{account}/actions/{slug}")
def delete_saved_action(account: str, slug: str):
    alias = _safe_account_alias(account)
    from v2.plugins import store
    try:
        store.delete_action(alias, slug)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/humanizer_profiles")
def humanizer_profiles():
    """Profile pauz obecne NA DYSKU.

    Lista w panelu była zapisana na sztywno i zawierała nazwy bez plików —
    wybranie takiej kończyło sesję twardym błędem przy starcie."""
    d = V2_ROOT / "humanizer" / "profiles"
    return sorted(p.stem for p in d.glob("*.yaml")) or ["default"]


@app.get("/api/humanizer_profiles/{name}")
def humanizer_profile(name: str):
    """Treść profilu YAML (resolved ranges) — do podpowiedzi defaultów w UI.

    Panel pokazuje te wartości w polach override, gdy konto nie ma własnych
    nadpisań; runtime i tak bierze je z Humanizer.load(profile_name).
    """
    # prosta walidacja nazwy — bez path traversal
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, f"nieprawidłowa nazwa profilu: {name!r}")
    path = V2_ROOT / "humanizer" / "profiles" / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(404, f"profil nie istnieje: {name}")
    from v2.core.humanizer import Humanizer, HumanizerError
    try:
        return Humanizer.load(name).profile
    except HumanizerError as e:
        raise HTTPException(400, str(e))


@app.get("/api/known_steps")
def known_steps():
    from v2.steps.registry import load_all, known
    load_all()
    return known()


# ============================================================
# API — klucz do Anthropic API
# ============================================================
@app.get("/api/secrets")
def get_secrets():
    """Sam STATUS klucza — pełna wartość nigdy nie wraca do przeglądarki."""
    from v2.config.secrets import status
    return status()


@app.post("/api/secrets")
async def set_secrets(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "oczekiwany JSON")
    has_key = "anthropic_api_key" in body
    has_ws = "anthropic_workspace_id" in body
    if not has_key and not has_ws:
        raise HTTPException(
            400, "wymagane pole anthropic_api_key lub anthropic_workspace_id")
    from v2.config.secrets import set_api_key, set_workspace_id, status
    if has_key:
        set_api_key(body["anthropic_api_key"])
    if has_ws:
        set_workspace_id(body.get("anthropic_workspace_id"))
    return status()


# ============================================================
# API — pluginy (katalog + ustawienia per konto)
# ============================================================
@app.get("/api/plugins")
def plugins_catalog():
    """Katalog pluginów ze schematami — dashboard generuje z tego formularze."""
    from v2.plugins.catalog import PLUGINS
    return PLUGINS


@app.get("/api/plugins/{account}")
def get_plugin_settings(account: str):
    """Ustawienia pluginów dla konta: defaults katalogu ← config konta."""
    from v2.plugins import store
    try:
        return JSONResponse(store.read_settings(account))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.post("/api/plugins/{account}")
async def set_plugin_settings(account: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object {plugin_id: settings}")
    from v2.plugins import store
    try:
        return store.write_settings(account, body)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ============================================================
# API — Treści (kolejka publikacji z tożsamością pliku)
# ============================================================
def _account_exists(account: str) -> bool:
    """Czy jest config/accounts/<alias>.json (nie *.queue)."""
    p = CONFIG_ROOT / "accounts" / f"{account}.json"
    return p.is_file() and not p.stem.endswith(".queue")


def _adb_serial_state(serial: str) -> str | None:
    """Stan ADB dla seriala albo None gdy brak na liście."""
    if not serial:
        return None
    for row in list_adb_devices():
        if row.get("serial") == serial:
            return row.get("state")
    return None


def _publish_item_public(rec: dict) -> dict:
    """Rekord kolejki + URL miniatury — bez ścieżek absolutnych hosta."""
    acc = rec.get("account") or ""
    iid = rec.get("id") or ""
    files = rec.get("files") or []
    mime = (files[0] or {}).get("mime") if files else ""
    return {
        **rec,
        "file_url": f"/api/publish/{acc}/{iid}/file" if acc and iid else None,
        "is_image": str(mime).startswith("image/"),
        "is_video": str(mime).startswith("video/"),
    }


@app.get("/api/publish")
def api_publish_list(
    device: str = "",
    account: str = "",
    kind: str = "",
    status: str = "",
):
    """Kolejka Treści — filtry urządzenie / konto / typ / status."""
    rows = list_publish(
        DATA_ROOT,
        account=account or None,
        device=device or None,
        kind=kind or None,
        status=status or None,
        device_of=lambda acc: _account_device_alias(acc, CONFIG_ROOT),
    )
    return {"items": [_publish_item_public(r) for r in rows]}


@app.post("/api/publish")
async def api_publish_upload(request: Request):
    """Upload + tożsamość. Bez `account` → 400 (nie wolno sieroty).

    Multipart (`account`, `kind`, `caption`, `file`) albo JSON — JSON bez
    pliku i tak padnie na „brak pliku”, ale brak konta jest pierwszy.
    """
    account = None
    kind = None
    caption = ""
    filename = "plik"
    mime = None
    blob = b""
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            account = body.get("account")
            kind = body.get("kind")
            caption = body.get("caption") or ""
            # Testy / klienci bez multipart: opcjonalny base64.
            raw_b64 = body.get("file_b64") or body.get("content_b64")
            if raw_b64:
                try:
                    blob = base64.b64decode(raw_b64)
                except Exception:
                    blob = b""
                filename = body.get("filename") or "plik"
                mime = body.get("mime")
    elif "multipart/" in ctype:
        raw = await request.body()
        fields, upload = parse_multipart(request.headers.get("content-type") or "", raw)
        account = fields.get("account")
        kind = fields.get("kind")
        caption = fields.get("caption") or ""
        if upload:
            filename = upload.get("filename") or "plik"
            mime = upload.get("mime")
            blob = upload.get("blob") or b""
    if not account or not str(account).strip():
        raise HTTPException(400, "account jest wymagane — bez konta nie wolno wrzucić sieroty")
    account = str(account).strip()
    if not _account_exists(account):
        raise HTTPException(404, f"nieznane konto: {account}")
    if kind not in ("post", "story"):
        raise HTTPException(400, "kind musi być post albo story")
    if not blob:
        raise HTTPException(400, "brak pliku")
    store = PublishStore(account, DATA_ROOT)
    try:
        rec = store.add(
            kind=kind,
            filename=filename,
            blob=blob,
            mime=mime,
            caption=caption or "",
            device_alias=_account_device_alias(account, CONFIG_ROOT),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _publish_item_public(rec)


@app.get("/api/publish/{account}/{item_id}/file")
def api_publish_file(account: str, item_id: str):
    """Blob na PC — miniatura karty. 404 gdy brak."""
    store = PublishStore(account, DATA_ROOT)
    rec = store.get(item_id)
    if rec is None:
        raise HTTPException(404, "brak pozycji")
    path = store.blob_path(rec)
    if path is None or not path.exists():
        raise HTTPException(404, "brak pliku")
    mime = ((rec.get("files") or [{}])[0] or {}).get("mime") or "application/octet-stream"
    return FileResponse(path, media_type=mime)


@app.patch("/api/publish/{account}/{item_id}")
async def api_publish_patch(account: str, item_id: str, request: Request):
    """Caption / kind — tylko dopóki nie published. Retry: status=ready."""
    if not _account_exists(account):
        raise HTTPException(404, f"nieznane konto: {account}")
    store = PublishStore(account, DATA_ROOT)
    if store.get(item_id) is None:
        raise HTTPException(404, "brak pozycji")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    fields = {}
    if "caption" in body:
        fields["caption"] = body.get("caption") or ""
    if "kind" in body:
        fields["kind"] = body["kind"]
    if "status" in body:
        fields["status"] = body["status"]
        if body["status"] == "ready":
            fields["error"] = None
    if not fields:
        raise HTTPException(400, "nic do zmiany")
    try:
        rec = store.update(item_id, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "brak pozycji")
    return _publish_item_public(rec)


@app.delete("/api/publish/{account}/{item_id}")
def api_publish_delete(account: str, item_id: str):
    """Kasuje blob na PC i opcjonalnie plik na telefonie."""
    store = PublishStore(account, DATA_ROOT)
    try:
        rec = store.delete(item_id)
    except KeyError:
        raise HTTPException(404, "brak pozycji")
    device_alias = rec.get("device_alias") or _account_device_alias(account, CONFIG_ROOT)
    serial = _device_adb_id(device_alias, CONFIG_ROOT) if device_alias else None
    if serial and rec.get("phone_path") and _adb_serial_state(serial) == "device":
        delete_on_phone(rec, serial=serial)
    return {"ok": True, "id": item_id}


@app.post("/api/publish/{account}/{item_id}/push")
def api_publish_push(account: str, item_id: str):
    """adb push + skan MediaStore. Sesja na tym serialu NIE blokuje (to tylko pliki)."""
    if not _account_exists(account):
        raise HTTPException(404, f"nieznane konto: {account}")
    store = PublishStore(account, DATA_ROOT)
    rec = store.get(item_id)
    if rec is None:
        raise HTTPException(404, "brak pozycji")
    device_alias = rec.get("device_alias") or _account_device_alias(account, CONFIG_ROOT)
    if not device_alias:
        raise HTTPException(400, "konto nie ma device_alias")
    serial = _device_adb_id(device_alias, CONFIG_ROOT)
    if not serial:
        raise HTTPException(400, f"urządzenie '{device_alias}' nie ma adb_id")
    if _adb_serial_state(serial) != "device":
        raise HTTPException(
            409, f"brak ADB: serial {serial} ({device_alias}) nie jest w stanie device",
        )
    blob = store.blob_path(rec)
    if blob is None:
        raise HTTPException(404, "brak pliku na PC")
    rec = dict(rec)
    rec.setdefault("account", account)
    try:
        pushed = push_to_phone(rec, serial=serial, blob_path=blob)
    except (RuntimeError, ValueError) as e:
        store.update(item_id, status="failed", error=str(e))
        raise HTTPException(502, str(e))
    rec = store.update(
        item_id,
        status="on_device",
        phone_path=pushed["phone_path"],
        content_uri=pushed.get("content_uri"),
        device_alias=device_alias,
        error=None,
    )
    return _publish_item_public(rec)


@app.get("/api/phrase-usage/{account}")
def api_phrase_usage(account: str):
    """Last-checked hashtagów / keywords / lokalizacji / znaczników.

    Czyta `data/accounts/<alias>/phrase_usage.json`. Brak pliku = puste buckety.
    """
    from v2.data.stores import PhraseUsageStore
    acc_path = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not acc_path.exists():
        raise HTTPException(404, f"brak konta: {account}")
    return JSONResponse(PhraseUsageStore(account, DATA_ROOT).read())


@app.get("/api/raw-profiles/{account}")
def api_raw_profiles(account: str, limit: int = 50, unique: int = 1):
    """Podgląd raw_profiles.jsonl dla panelu (profile_interact).

    Query: `limit` (1–500), `unique=1` — po jednym wierszu na username (najnowszy).
    Nie zmienia pliku ani ustawień pluginów.
    """
    from v2.data.stores import RawProfilesStore
    # Konto musi istnieć w configu — inaczej 404 jak przy innych endpointach
    acc_path = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not acc_path.exists():
        raise HTTPException(404, f"brak konta: {account}")
    store = RawProfilesStore(account, DATA_ROOT)
    lim = max(1, min(int(limit or 50), 500))
    return JSONResponse(store.preview(limit=lim, unique=bool(int(unique))))


@app.get("/api/target-candidates/{account}")
def api_target_candidates(account: str, limit: int = 50):
    """Podgląd target_candidates.jsonl (po filtrze HTTP)."""
    from v2.data.stores import TargetCandidatesStore
    acc_path = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not acc_path.exists():
        raise HTTPException(404, f"brak konta: {account}")
    store = TargetCandidatesStore(account, DATA_ROOT)
    lim = max(1, min(int(limit or 50), 500))
    return JSONResponse(store.preview(limit=lim))


@app.get("/api/visited-profiles/{account}")
def api_visited_profiles(account: str, limit: int = 50):
    """Ostatnio obejrzani — `visited_profiles.jsonl`, newest first, bez mutacji.

    Query `limit` 1–500. Pola: username, ts, action, reason, tiles_liked,
    followed, header, interacted, outcome. `today` = KPI obejrzani/interakcja.
    """
    acc_path = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not acc_path.exists():
        raise HTTPException(404, f"brak konta: {account}")
    store = VisitedProfilesStore(account, DATA_ROOT)
    lim = max(1, min(int(limit or 50), 500))
    payload = store.preview(limit=lim)
    today = datetime.now().strftime("%Y-%m-%d")
    viewed = 0
    interacted = 0
    for row in store.read_all():
        ts = str(row.get("ts") or "")
        if not ts.startswith(today):
            continue
        viewed += 1
        if visit_has_interaction(row):
            interacted += 1
    payload["today"] = {"viewed": viewed, "interacted": interacted}
    return JSONResponse(payload)


# ============================================================
# API — filtr profili (HTTP / proxy, bez telefonu)
# ============================================================
_filter_job_lock = threading.Lock()
# Popen trzymamy osobno — nie wolno wrzucać do JSON statusu
_filter_proc: subprocess.Popen | None = None
_filter_job: dict = {
    "running": False,
    "stopping": False,
    "cancel": False,
    "account": None,
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
    "stdout_path": None,
    "stderr_path": None,
    "pid": None,
}


def _tail_log_lines(path: Optional[str | Path], n: int = 8) -> list[str]:
    """Ostatnie n niepustych linii z logu CLI (stdout/stderr filtra)."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return lines[-n:] if lines else []


def _read_log_text(path: Optional[str | Path],
                   max_chars: int = 400_000) -> str:
    """Pełna treść logu (z bezpiecznym limitem znaków dla API)."""
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def _signal_filter_proc(proc: subprocess.Popen, *, hard: bool) -> None:
    """SIGTERM/CTRL_BREAK albo SIGKILL — filtr nie rusza telefonu."""
    if sys.platform == "win32":
        if hard:
            proc.kill()
            return
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception:
            proc.terminate()
        return
    sig = signal.SIGKILL if hard else signal.SIGTERM
    try:
        os.killpg(proc.pid, sig)
    except (OSError, ProcessLookupError):
        if hard:
            proc.kill()
        else:
            proc.terminate()


def _terminate_filter_proc(proc: subprocess.Popen | None,
                           wait_s: float = 5.0) -> None:
    """Miękki stop, potem kill — czekamy poza lockiem joba."""
    if proc is None or proc.poll() is not None:
        return
    try:
        _signal_filter_proc(proc, hard=False)
        try:
            proc.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            _signal_filter_proc(proc, hard=True)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def stop_filter_job(account: str) -> dict:
    """Zatrzymaj subprocess filtra. ValueError gdy nic nie leci / inne konto."""
    global _filter_proc
    with _filter_job_lock:
        if not _filter_job.get("running"):
            raise ValueError("filtr nie działa")
        if _filter_job.get("account") != account:
            raise ValueError(
                f"filtr działa na innym koncie: {_filter_job.get('account')}")
        _filter_job["stopping"] = True
        _filter_job["cancel"] = True
        proc = _filter_proc
    _terminate_filter_proc(proc)
    # Jeśli proces już nie żyje (albo nie zdążył wstać) — od razu zdejmij
    # RUNNING, żeby UI nie czekało na worker, który padł / nie wystartował.
    with _filter_job_lock:
        dead = proc is None or proc.poll() is not None
        if dead:
            if _filter_proc is proc:
                _filter_proc = None
            _filter_job.update({
                "running": False,
                "stopping": False,
                "cancel": False,
                "finished_at": time.time(),
                "error": "zatrzymany",
                "pid": None,
            })
    return {"ok": True, "stopped": True, "account": account,
            "job": _filter_job_status()}


def _filter_job_status() -> dict:
    # Compact: ostatnie 8 linii; full: całość do scrollowanego panelu UI
    st = dict(_filter_job)
    st.pop("cancel", None)  # flaga wewnętrzna, nie do UI
    st["terminal_tail"] = _tail_log_lines(st.get("stdout_path"), 8)
    if not st["terminal_tail"]:
        st["terminal_tail"] = _tail_log_lines(st.get("stderr_path"), 8)
    stdout_full = _read_log_text(st.get("stdout_path"))
    stderr_full = _read_log_text(st.get("stderr_path"))
    # Preferuj stdout; dołącz stderr gdy coś tam jest
    if stdout_full and stderr_full:
        st["terminal_log"] = stdout_full.rstrip() + "\n--- stderr ---\n" + stderr_full
    else:
        st["terminal_log"] = stdout_full or stderr_full
    return st


def _looks_like_proxy_url(url: str) -> bool:
    """http(s)://… — bez zgadywania schematu socks."""
    u = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


@app.get("/api/filter-proxies")
def api_filter_proxies_get():
    """URL-e filter_proxies do edycji w Lejku (lokalny panel)."""
    from v2.config import secrets
    st = secrets.filter_proxies_status()
    st["proxies"] = secrets.filter_proxies_for_edit()
    return JSONResponse(st)


@app.post("/api/filter-proxies")
async def api_filter_proxies_set(request: Request):
    """Zapisz mapę {nazwa: url|linie} do secrets.json → filter_proxies."""
    from v2.config import secrets
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    raw = body.get("proxies")
    if raw is None:
        raw = {k: v for k, v in body.items() if k != "proxies"}
    if not isinstance(raw, dict):
        raise HTTPException(400, "proxies musi być obiektem")
    cleaned: dict[str, str | list[str]] = {}
    for name, val in raw.items():
        key = str(name or "").strip()
        if not key:
            continue
        parsed = secrets.parse_filter_proxy_text(
            val if isinstance(val, str) else "\n".join(
                str(x) for x in (val or [])))
        if parsed is None:
            continue
        urls = parsed if isinstance(parsed, list) else [parsed]
        for u in urls:
            if not _looks_like_proxy_url(u):
                raise HTTPException(
                    400, f"{key}: URL musi zaczynać się od http:// lub https://")
        cleaned[key] = parsed
    secrets.set_filter_proxies(cleaned or None)
    st = secrets.filter_proxies_status()
    st["proxies"] = secrets.filter_proxies_for_edit()
    return JSONResponse({"ok": True, **st})


@app.get("/api/profile-filter/{account}")
def api_profile_filter_get(account: str):
    """Config + status (jak GET status, plus pełny config)."""
    from v2.profile_filter.pipeline import filter_status
    from v2.config import secrets
    try:
        st = filter_status(account, DATA_ROOT, CONFIG_ROOT)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    st["job"] = _filter_job_status()
    st["proxies"] = secrets.filter_proxies_status()
    return JSONResponse(st)


@app.post("/api/profile-filter/{account}")
async def api_profile_filter_set(account: str, request: Request):
    """Zapisz sekcję `profile_filter` w account.json."""
    from v2.profile_filter.pipeline import (
        DEFAULT_PROFILE_FILTER, merge_profile_filter_cfg)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    p = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not p.exists():
        raise HTTPException(404, f"brak konta: {account}")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    merged = merge_profile_filter_cfg({**(cfg.get("profile_filter") or {}),
                                       **body})
    # Walidacja godzin HH:MM
    times = []
    for t in merged.get("times") or []:
        parts = str(t).strip().split(":")
        if len(parts) != 2:
            raise HTTPException(400, f"zła godzina: {t}")
        try:
            h, m = int(parts[0]), int(parts[1])
        except ValueError:
            raise HTTPException(400, f"zła godzina: {t}")
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise HTTPException(400, f"zła godzina: {t}")
        times.append(f"{h:02d}:{m:02d}")
    merged["times"] = times or list(DEFAULT_PROFILE_FILTER["times"])
    merged["enabled"] = bool(merged.get("enabled"))
    merged["limit_per_run"] = max(1, int(merged.get("limit_per_run") or 100))
    merged["followers_min"] = int(merged.get("followers_min") or 0)
    merged["followers_max"] = int(merged.get("followers_max") or 20000)
    merged["min_posts"] = max(0, int(merged.get("min_posts") or 0))
    # Pauzy: 0–120 s (0 = bez czekania)
    try:
        between = float(merged.get("sleep_between_s")
                        if merged.get("sleep_between_s") is not None
                        else DEFAULT_PROFILE_FILTER["sleep_between_s"])
    except (TypeError, ValueError):
        raise HTTPException(400, "sleep_between_s musi być liczbą")
    try:
        req_s = float(merged.get("sleep_request_s")
                      if merged.get("sleep_request_s") is not None
                      else DEFAULT_PROFILE_FILTER["sleep_request_s"])
    except (TypeError, ValueError):
        raise HTTPException(400, "sleep_request_s musi być liczbą")
    try:
        backoff_base = float(merged.get("retry_backoff_base_s")
                             if merged.get("retry_backoff_base_s") is not None
                             else DEFAULT_PROFILE_FILTER["retry_backoff_base_s"])
    except (TypeError, ValueError):
        raise HTTPException(400, "retry_backoff_base_s musi być liczbą")
    try:
        backoff_max = float(merged.get("retry_backoff_max_s")
                            if merged.get("retry_backoff_max_s") is not None
                            else DEFAULT_PROFILE_FILTER["retry_backoff_max_s"])
    except (TypeError, ValueError):
        raise HTTPException(400, "retry_backoff_max_s musi być liczbą")
    merged["sleep_between_s"] = max(0.0, min(120.0, between))
    merged["sleep_request_s"] = max(0.0, min(60.0, req_s))
    merged["retry_backoff_base_s"] = max(0.1, min(60.0, backoff_base))
    merged["retry_backoff_max_s"] = max(
        merged["retry_backoff_base_s"], min(300.0, backoff_max))
    try:
        timeout_s = float(merged.get("request_timeout_s")
                          if merged.get("request_timeout_s") is not None
                          else DEFAULT_PROFILE_FILTER["request_timeout_s"])
    except (TypeError, ValueError):
        raise HTTPException(400, "request_timeout_s musi być liczbą")
    merged["request_timeout_s"] = max(5.0, min(120.0, timeout_s))
    proxy = (merged.get("proxy") or "losowo").strip()
    known_proxy = {"losowo", "webshare", "rayobyte"}
    # Akceptuj też własne nazwy z secrets
    from v2.config import secrets
    known_proxy |= set(secrets.get_filter_proxies().keys())
    if proxy not in known_proxy:
        raise HTTPException(400, f"proxy musi być jednym z: {sorted(known_proxy)}")
    merged["proxy"] = proxy
    cfg["profile_filter"] = merged
    backup = p.with_suffix(".json.bak")
    backup.write_bytes(p.read_bytes())
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from v2.config.schema import load_account
        load_account(account, config_root=CONFIG_ROOT)
    except Exception as e:
        p.write_bytes(backup.read_bytes())
        raise HTTPException(400, f"walidacja padła — przywrócono backup: {e}")
    return {"ok": True, "config": merged}


@app.get("/api/profile-filter/{account}/status")
def api_profile_filter_status(account: str):
    from v2.profile_filter.pipeline import filter_status
    try:
        st = filter_status(account, DATA_ROOT, CONFIG_ROOT)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    st["job"] = _filter_job_status()
    return JSONResponse(st)


@app.get("/api/profile-filter/{account}/log")
def api_profile_filter_log(account: str):
    """Pełny log stdout/stderr ostatniego (lub trwającego) joba filtra."""
    acc_path = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not acc_path.exists():
        raise HTTPException(404, f"brak konta: {account}")
    st = _filter_job_status()
    # Jeśli job dotyczy innego konta — spróbuj najnowszy plik *-filter-stdout.log
    if st.get("account") and st.get("account") != account:
        st = {"account": account, "terminal_log": "", "terminal_tail": [],
              "stdout_path": None, "stderr_path": None}
    if not st.get("terminal_log"):
        log_dir = DATA_ROOT / "accounts" / account / "sessions"
        outs = sorted(log_dir.glob("*-filter-stdout.log"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        if outs:
            out_p = outs[0]
            err_p = out_p.with_name(
                out_p.name.replace("-filter-stdout.log", "-filter-stderr.log"))
            st["stdout_path"] = str(out_p)
            st["stderr_path"] = str(err_p) if err_p.exists() else None
            st["terminal_tail"] = _tail_log_lines(out_p, 8)
            stdout_full = _read_log_text(out_p)
            stderr_full = _read_log_text(err_p if err_p.exists() else None)
            if stdout_full and stderr_full:
                st["terminal_log"] = (
                    stdout_full.rstrip() + "\n--- stderr ---\n" + stderr_full)
            else:
                st["terminal_log"] = stdout_full or stderr_full
    return JSONResponse({
        "account": account,
        "stdout_path": st.get("stdout_path"),
        "stderr_path": st.get("stderr_path"),
        "terminal_tail": st.get("terminal_tail") or [],
        "terminal_log": st.get("terminal_log") or "",
        "running": bool(_filter_job.get("running")
                        and _filter_job.get("account") == account),
    })


@app.post("/api/profile-filter/{account}/run")
def api_profile_filter_run(account: str, limit: Optional[int] = None):
    """Uruchom filtr w tle (subprocess CLI) — 202 Accepted."""
    from v2.scheduler import profile_filter_tick as pft
    acc_path = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not acc_path.exists():
        raise HTTPException(404, f"brak konta: {account}")
    if pft.is_filter_busy():
        raise HTTPException(
            409, f"filtr już działa: account={pft.current_filter_account()}")
    with _filter_job_lock:
        if _filter_job.get("running"):
            raise HTTPException(
                409,
                f"filtr już działa: account={_filter_job.get('account')}")
        _filter_job.update({
            "running": True,
            "stopping": False,
            "cancel": False,
            "account": account,
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
            "stdout_path": None,
            "stderr_path": None,
            "pid": None,
        })

    def _worker():
        global _filter_proc
        cmd = [sys.executable, "-u", "-m", "v2.cli", "filter-profiles",
               f"--account={account}"]
        if limit is not None:
            cmd.append(f"--limit={int(limit)}")
        log_dir = DATA_ROOT / "accounts" / account / "sessions"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = log_dir / f"{ts}-filter-stdout.log"
        err_path = log_dir / f"{ts}-filter-stderr.log"
        # Ścieżki od razu w jobie — UI może czytać ogon terminala w trakcie
        with _filter_job_lock:
            _filter_job["stdout_path"] = str(out_path)
            _filter_job["stderr_path"] = str(err_path)
        proc: subprocess.Popen | None = None
        rc = 1
        try:
            creationflags = 0
            extra: dict = {}
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore
            else:
                # Własna grupa procesów — stop ubija też dzieci (curl_cffi)
                extra["start_new_session"] = True
            with out_path.open("wb") as out_f, err_path.open("wb") as err_f:
                with _filter_job_lock:
                    if _filter_job.get("cancel"):
                        rc = -1
                    else:
                        proc = subprocess.Popen(
                            cmd,
                            cwd=str(V2_ROOT.parent),
                            stdout=out_f,
                            stderr=err_f,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            creationflags=creationflags,
                            **extra,
                        )
                        _filter_proc = proc
                        _filter_job["pid"] = proc.pid
                        if _filter_job.get("cancel"):
                            # Stop wciśnięty między check a Popen
                            pass
                if proc is not None:
                    if _filter_job.get("cancel"):
                        _terminate_filter_proc(proc)
                    rc = proc.wait()
            from v2.profile_filter.pipeline import read_last_run
            last = read_last_run(account, DATA_ROOT)
            with _filter_job_lock:
                already = _filter_job.get("error") == "zatrzymany"
                stopped = already or bool(_filter_job.get("cancel")
                                          or _filter_job.get("stopping"))
                err = None
                if stopped:
                    err = "zatrzymany"
                elif rc != 0:
                    err = f"exit={rc}"
                if _filter_proc is proc:
                    _filter_proc = None
                _filter_job.update({
                    "running": False,
                    "stopping": False,
                    "cancel": False,
                    "finished_at": time.time(),
                    "result": last,
                    "error": err,
                    "stdout_path": str(out_path),
                    "stderr_path": str(err_path),
                    "pid": None,
                })
        except Exception as e:
            with _filter_job_lock:
                if _filter_proc is proc:
                    _filter_proc = None
                _filter_job.update({
                    "running": False,
                    "stopping": False,
                    "cancel": False,
                    "finished_at": time.time(),
                    "error": str(e),
                    "pid": None,
                })

    threading.Thread(target=_worker, name=f"filter-{account}",
                     daemon=True).start()
    return JSONResponse({"ok": True, "accepted": True, "account": account,
                         "job": _filter_job_status()}, status_code=202)


@app.post("/api/profile-filter/{account}/stop")
def api_profile_filter_stop(account: str):
    """Ręczne zatrzymanie subprocessu filtra (panel — przycisk Zatrzymaj)."""
    acc_path = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not acc_path.exists():
        raise HTTPException(404, f"brak konta: {account}")
    try:
        return JSONResponse(stop_filter_job(account))
    except ValueError as e:
        raise HTTPException(409, str(e))


# ============================================================
# API — kolejka zadań
# ============================================================
@app.get("/api/queue/{account}")
def get_queue(account: str, name: Optional[str] = None):
    """`name` = scenariusz (`<alias>.queues/<name>.json`); pominięte =
    scenariusz domyślny (`<alias>.queues/default.json`)."""
    from v2.plugins import store
    try:
        return JSONResponse(store.read_queue(account, name=name))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.post("/api/queue/{account}")
async def set_queue(account: str, request: Request, name: Optional[str] = None):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    from v2.plugins import store
    try:
        return store.write_queue(account, body, name=name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/queue/{account}/resolved")
def get_queue_resolved(account: str, name: Optional[str] = None):
    """Podgląd kolejki z rozwiązanymi ustawieniami (defaults ← config ← override).

    Odpowiednik `--dry-run` — pozwala sprawdzić, co faktycznie dostanie plugin,
    zanim cokolwiek dotknie telefonu.
    """
    from v2.plugins import store
    try:
        return JSONResponse(store.resolve_queue(account, name=name))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.get("/api/queue-presets/{account}")
def list_queue_presets(account: str):
    """Nazwy zapisanych scenariuszy (`<alias>.queues/*.json`), bez zawartości —
    do wypełnienia selektora w panelu."""
    from v2.plugins import store
    try:
        return store.list_queue_presets(account)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/queue/{account}/{name}")
def delete_queue_preset(account: str, name: str):
    from v2.plugins import store
    try:
        store.delete_queue_preset(account, name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/run-queue")
async def api_run_queue(request: Request):
    """Uruchom kolejkę (domyślną albo nazwany scenariusz) zadań konta w
    osobnym procesie."""
    body = await request.json()
    acc = body.get("account")
    queue_name = body.get("queue_name")
    _deny_if_scheduler_enabled(account=acc)
    if not acc:
        raise HTTPException(400, "account wymagane")
    from v2.plugins import store
    try:
        q = store.read_queue(acc, name=queue_name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    if not [t for t in q["queue"] if t.get("enabled", True)]:
        raise HTTPException(400, "kolejka pusta — dodaj i zapisz zadania "
                                 "w zakładce Kolejka")
    # Start z panelu = decyzja operatora; włącz pluginy z runy, zanim
    # JobRunner skipnie je jako plugin_disabled_for_account (stare runy
    # zapisane zanim write_queue zaczął to robić).
    store.enable_queued_plugins(acc, q["queue"])
    try:
        return SESSION.start(acc, mode="queue",
                             only=body.get("only"),
                             queue_name=queue_name,
                             max_items=body.get("max_items"))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/run-plugin")
async def api_run_plugin(request: Request):
    """Test / one-shot pluginu.

    Zwykłe pluginy: uruchamia zadanie z kolejki (wymaga wpisu w scenariuszu).
    `niche_discover`: poza kolejkami — one-shot z Ustawień konta
    (`cli run-queue --plugin=niche_discover`)."""
    body = await request.json()
    acc, pid = body.get("account"), body.get("plugin")
    _deny_if_scheduler_enabled(account=acc)
    if not acc or not pid:
        raise HTTPException(400, "account + plugin wymagane")
    from v2.plugins import store
    from v2.plugins.catalog import get_plugin
    from v2.plugins.registry import load_all as load_plugins, is_implemented
    load_plugins()

    # discovery niszy — nie trafia do scenariuszy; osobny entrypoint z ustawień
    if pid == "niche_discover":
        try:
            get_plugin(pid)
        except KeyError:
            raise HTTPException(404, f"plugin '{pid}' nieznany w katalogu")
        if not is_implemented(pid):
            raise HTTPException(501, f"plugin '{pid}' nie ma jeszcze implementacji")
        try:
            return SESSION.start(
                acc, mode="queue", plugin_oneshot="niche_discover",
                max_items=body.get("max_items"))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    if not is_implemented(pid):
        raise HTTPException(501, f"plugin '{pid}' nie ma jeszcze implementacji")
    q = store.read_queue(acc)
    task = next((t for t in q["queue"] if t["plugin"] == pid), None)
    if not task:
        raise HTTPException(400, f"brak zadania z pluginem '{pid}' w kolejce — "
                                 "dodaj je w zakładce Planowanie → Scenariusze")
    try:
        return SESSION.start(acc, mode="queue", only=task["id"],
                             max_items=body.get("max_items") or 3)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


def _account_session_running(account: str) -> bool:
    """Czy to konto ma żywy slot — nie mylić z running_any floty."""
    slot = SESSION.slot_for_account(account)
    return bool(slot and slot.is_alive() and slot.account == account)


@app.get("/api/bot-config")
def get_bot_config():
    """Globalne limity / humanizer / kroki z `config/bot.json` (effective)."""
    from v2.config.schema import load_bot
    return load_bot(CONFIG_ROOT)


@app.post("/api/bot-config")
async def set_bot_config(request: Request):
    """Zapis globali do `config/bot.json` — merge sekcji, nie echo sekretów.

    Body: {limits? , humanizer? , toggles?} jak fragment account.json.
    Konto bez własnego klucza dziedziczy; override w pliku konta wygrywa.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    known = {"limits", "humanizer", "toggles"}
    if not known & set(body):
        raise HTTPException(400, f"podaj przynajmniej jedno z: {sorted(known)}")
    from v2.config.schema import ConfigError, save_bot
    try:
        return save_bot(body, CONFIG_ROOT)
    except ConfigError as e:
        raise HTTPException(400, str(e))


@app.get("/api/config/{account}")
def get_config(account: str):
    p = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not p.exists():
        raise HTTPException(404, f"config nie istnieje: {account}")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


@app.post("/api/config/{account}")
async def set_config(account: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    p = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not p.exists():
        raise HTTPException(404, f"config nie istnieje: {account}")
    # backup, walidacja, zapis
    backup = p.with_suffix(".json.bak")
    prev_raw = p.read_bytes()
    backup.write_bytes(prev_raw)
    # ręczna edycja keywords/hashtags w panelu → rozdziel na manual/discovered
    if isinstance(body.get("niche"), dict):
        try:
            prev_cfg = json.loads(prev_raw.decode("utf-8"))
        except Exception:
            prev_cfg = {}
        from v2.plugins.store import reconcile_niche_phrases
        body["niche"] = reconcile_niche_phrases(
            (prev_cfg or {}).get("niche") or {},
            body["niche"],
        )
    p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from v2.config.schema import load_account
        load_account(account)
    except Exception as e:
        p.write_bytes(backup.read_bytes())  # rollback
        raise HTTPException(400, f"walidacja padła — przywrócono backup: {e}")
    return {"ok": True, "backup": str(backup)}


@app.delete("/api/niche/{account}")
def delete_niche(account: str):
    """Wyczyść account.niche — pusty szkielet; .bak przed zapisem.

    Kasuje description/audience/keywords/hashtags/exclusions, źródła
    manual/discovered, tag_variants oraz accepted/discovered_at. Ustawienia
    discovery (plugin niche_discover / konkurencja) zostają w configu —
    to osobna sekcja.
    """
    # Jawny import funkcji — unikamy NameError gdy ktoś zapomni `import store`
    # (wzorzec lokalnych importów w tym pliku; panel nie ładuje store na starcie).
    from v2.plugins.store import clear_niche
    try:
        cleared = clear_niche(account)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "niche": cleared}


# ============================================================
# API — sterowanie sesją
# ============================================================
@app.get("/api/status")
def get_status():
    return SESSION.status()


@app.post("/api/start")
async def api_start(request: Request):
    body = await request.json()
    acc = body.get("account"); flow = body.get("flow")
    _deny_if_scheduler_enabled(account=acc)
    if not acc or not flow:
        raise HTTPException(400, "account + flow wymagane")
    try:
        return SESSION.start(acc, flow)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/stop")
async def api_stop(request: Request):
    """Stop jednego slotu: `{device_alias}` albo `{account}`.

    Bez body (klasyk) = focus / ostatni start, nie cała flota.
    """
    device_alias = None
    account = None
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw)
            if isinstance(body, dict):
                device_alias = body.get("device_alias")
                account = body.get("account")
    except Exception:
        pass
    return SESSION.stop(device_alias=device_alias, account=account)


@app.post("/api/login")
async def api_login(request: Request):
    """Krótki flow: wake → unlock → uruchom klon → wpisz login/hasło z
    config/credentials.py. 2FA/potwierdzenia kończysz ręcznie na telefonie —
    obserwuj przez /api/events/stream, potem POST /api/where żeby sprawdzić,
    czy dotarliśmy do home_feed."""
    body = await request.json()
    acc = body.get("account")
    _deny_if_scheduler_enabled(account=acc)
    if not acc:
        raise HTTPException(400, "account wymagane")
    from v2.config import credentials
    if not credentials.get(acc):
        raise HTTPException(400, "brak zapisanych danych logowania dla tego konta — "
                                 "zapisz je najpierw przez POST /api/accounts/{account}/credentials")
    try:
        return SESSION.start(acc, mode="login")
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/signup")
async def api_signup(request: Request):
    """Krótki flow: wake → unlock → klon → rejestracja IG z credentials.signup."""
    body = await request.json()
    acc = body.get("account")
    _deny_if_scheduler_enabled(account=acc)
    if not acc:
        raise HTTPException(400, "account wymagane")
    from v2.config import credentials
    if not credentials.get_signup(acc):
        raise HTTPException(400, "brak danych do założenia konta — "
                                 "zapisz signup przez POST /api/accounts/{account}/credentials")
    try:
        return SESSION.start(acc, mode="signup")
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/verification-code/{account}")
def get_verification_code_request(account: str):
    """Czy bot aktualnie czeka na kod (email/SMS) i o co prosi — bez
    ujawniania samego kodu (go jeszcze nie ma, dopiero go zbieramy)."""
    from v2.core import verification_code as vc
    req = vc.get_request(DATA_ROOT, account)
    return {"waiting": bool(req), "prompt": (req or {}).get("prompt", "")}


@app.post("/api/verification-code/{account}")
async def submit_verification_code(account: str, request: Request):
    body = await request.json()
    code = (body.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "code wymagany")
    from v2.core import verification_code as vc
    vc.submit_code(DATA_ROOT, account, code)
    return {"ok": True}


def _deny_if_session_running(account: str | None = None,
                             device_alias: str | None = None):
    """Diagnostyka / hydrate: 409 tylko gdy TEN serial jedzie.

    Bez aliasu nie blokujemy całej floty — inny telefon zostaje wolny.
    """
    alias = device_alias or (
        _account_device_alias(account, CONFIG_ROOT) if account else None
    )
    if not alias:
        return
    if SESSION.is_running_on(alias):
        raise HTTPException(409, "sesja w toku — diagnostyka zakłóciłaby ją; "
                                 "zatrzymaj sesję albo poczekaj")


@app.post("/api/where")
async def api_where(request: Request):
    body = await request.json()
    acc = body.get("account")
    if not acc: raise HTTPException(400, "account wymagane")
    _deny_if_session_running(account=acc)
    out = _run_cli_sync(["where", f"--account={acc}"], timeout=15)
    return {"output": out}


@app.post("/api/recover")
async def api_recover(request: Request):
    body = await request.json()
    acc = body.get("account"); target = body.get("target", "home_feed")
    if not acc: raise HTTPException(400, "account wymagane")
    _deny_if_session_running(account=acc)
    out = _run_cli_sync(["recover", f"--account={acc}", f"--target={target}"], timeout=45)
    return {"output": out}


from v2.scheduler.device_planner import DEFAULT_SCHEDULE


def _account_config_path(account: str) -> Path:
    p = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not p.exists():
        raise HTTPException(404, f"konto nie istnieje: {account}")
    return p


@app.get("/api/schedule/{account}")
def get_schedule(account: str):
    p = _account_config_path(account)
    cfg = json.loads(p.read_text(encoding="utf-8"))
    return {**DEFAULT_SCHEDULE, **(cfg.get("schedule") or {})}


@app.post("/api/schedule/{account}")
async def set_schedule(account: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    p = _account_config_path(account)
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg["schedule"] = {**DEFAULT_SCHEDULE, **body}

    backup = p.with_suffix(".json.bak")
    backup.write_bytes(p.read_bytes())
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from v2.config.schema import load_account
        load_account(account, config_root=CONFIG_ROOT)
    except Exception as e:
        p.write_bytes(backup.read_bytes())
        raise HTTPException(400, f"walidacja padła — przywrócono backup: {e}")
    return {"ok": True}


# ============================================================
# API — master switch harmonogramu
# ============================================================
@app.get("/api/scheduler")
def get_scheduler_state():
    from v2.scheduler.state import CYCLE_CHOICES, read
    return {**read(DATA_ROOT), "cycle_choices": list(CYCLE_CHOICES)}


@app.post("/api/scheduler")
async def set_scheduler_state(request: Request):
    """Ustawienia wspólne dla całej instalacji: master switch, godziny
    działania i długość powtarzalnego cyklu. Włączony switch = planem
    dysponuje scheduler i ręczne uruchomienia są odmawiane (423)."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body musi być JSON object")
    known = {"enabled", "active_hours", "cycle_days"}
    if not known & set(body):
        raise HTTPException(400, f"podaj przynajmniej jedno z: {sorted(known)}")
    from v2.scheduler.state import CYCLE_CHOICES, update
    if body.get("cycle_days") is not None and int(body["cycle_days"]) not in CYCLE_CHOICES:
        raise HTTPException(400, f"cycle_days musi być jednym z {list(CYCLE_CHOICES)}")
    return {**update(DATA_ROOT, **{k: body[k] for k in known & set(body)}),
            "cycle_choices": list(CYCLE_CHOICES)}


# ============================================================
# API — konta uczestniczące w planie (zbiorczo, per urządzenie)
# ============================================================
@app.get("/api/schedule-accounts/{device}")
def get_schedule_accounts(device: str):
    """Wszystkie konta urządzenia z ich udziałem w planie i wyceną scenariuszy.

    Zbiorczo, żeby zapisanie planu dla trzech kont nie wymagało trzykrotnego
    przełączania kontekstu konta w panelu.
    """
    _require_device(device)
    from v2.plugins import store
    from v2.scheduler import device_planner as dp
    from v2.scheduler.estimator import estimate_queue

    out = []
    for entry in dp.accounts_for_device(CONFIG_ROOT, device, only_enabled=False):
        acc, sch = entry["account"], entry["schedule"]
        try:
            presets = store.list_queue_presets(acc)
        except Exception:
            presets = []
        estimates = {}
        for name in presets:
            try:
                e = estimate_queue(acc, name)
                estimates[name] = {"total_s": e["total_s"], "warnings": e["warnings"]}
            except Exception as ex:
                estimates[name] = {"total_s": None, "warnings": [str(ex)]}
        # limity dzienne — potrzebne do pasków wykorzystania na osi czasu
        try:
            cfg = json.loads(
                (CONFIG_ROOT / "accounts" / f"{acc}.json").read_text(encoding="utf-8"))
            limits = cfg.get("limits") or {}
        except Exception:
            limits = {}
        out.append({"account": acc, "enabled": bool(sch.get("enabled")),
                    "sessions_per_day": sch.get("sessions_per_day"),
                    "scenarios": sch.get("scenarios") or [],
                    "presets": presets, "estimates": estimates,
                    "limits": limits})
    return out


@app.post("/api/schedule-accounts/{device}")
async def set_schedule_accounts(device: str, request: Request):
    """Zapisz udział w planie dla wielu kont naraz. Zaznaczone konto od razu
    uczestniczy w harmonogramie — nie ma osobnego kroku „dodaj do planu".

    `sessions_per_day` edytuje się na osi czasu; tu pole jest opcjonalne —
    brak w body = zostaw obecną wartość (nie kasuj przy zapisie enabled/scenariuszy).

    Po zapisie układamy wzorzec od nowa (bloki `locked` zostają), żeby oś
    od razu pokazywała skutek zaznaczenia, a nie stary plan sprzed zmiany.
    """
    _require_device(device)
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(400, "body musi być listą {account, enabled, ...}")

    from v2.scheduler import device_planner as dp
    saved = []
    for row in body:
        acc = row.get("account")
        if not acc:
            raise HTTPException(400, "każdy wpis musi mieć pole account")
        if dp.device_for_account(CONFIG_ROOT, acc) != device:
            raise HTTPException(400, f"konto '{acc}' nie jest przypisane do '{device}'")
        p = _account_config_path(acc)
        cfg = json.loads(p.read_text(encoding="utf-8"))
        prev = {**DEFAULT_SCHEDULE, **(cfg.get("schedule") or {})}
        # sesje/dzień: jawna wartość z UI osi albo dotychczasowa z konta
        if "sessions_per_day" in row and row["sessions_per_day"]:
            spd = row["sessions_per_day"]
        else:
            spd = prev.get("sessions_per_day") or DEFAULT_SCHEDULE["sessions_per_day"]
        # scenariusze: brak klucza = nie ruszaj; pustą listę uznajemy za celowy clear
        if "scenarios" in row:
            scenarios = row.get("scenarios") or []
        else:
            scenarios = prev.get("scenarios") or []
        enabled = bool(row["enabled"]) if "enabled" in row else bool(prev.get("enabled"))
        cfg["schedule"] = {**prev,
                           "enabled": enabled,
                           "sessions_per_day": spd,
                           "scenarios": scenarios}
        # `active_hours` jest teraz odgórne (GET/POST /api/scheduler) — zostawione
        # w configu tylko po to, żeby stare pliki nie wyglądały na uszkodzone
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append(acc)

    cycle = _timeline_call(dp.build_cycle, device, DATA_ROOT, CONFIG_ROOT)
    dp.resync_date(device, DATA_ROOT, CONFIG_ROOT)
    return {"ok": True, "saved": saved, "cycle": dp.annotate_cycle(cycle)}


# ============================================================
# API — oś czasu urządzenia (bloki z policzonym czasem trwania)
# ============================================================
def _require_device(device: str) -> str:
    """Bez tego literówka w URL-u zakładałaby katalog planu dla nieistniejącego
    urządzenia i cicho zwracała pusty dzień."""
    if not (CONFIG_ROOT / "devices" / f"{device}.json").exists():
        raise HTTPException(404, f"urządzenie '{device}' nie istnieje w config/devices/")
    return device


def _timeline_call(fn, *args, **kwargs):
    """Operacje na planie zgłaszają `PlanError` dla rzeczy, które użytkownik
    może naprawić (kolizja, brak bloku) — to 409, nie 500."""
    from v2.scheduler.device_planner import PlanError
    try:
        return fn(*args, **kwargs)
    except PlanError as e:
        raise HTTPException(409, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/timeline/{device}")
def get_timeline(device: str):
    """Wzorzec cyklu urządzenia (1, 3 albo 7 powtarzalnych dni) plus statusy
    dzisiejszego, zmaterializowanego dnia.

    Edytuje się wzorzec, nie kalendarz — dlatego oś zwraca `days` po indeksie
    cyklu, a nie po dacie. `runtime` służy tylko do pokazania, co z dzisiejszego
    dnia już poszło.
    """
    _require_device(device)
    from datetime import date as date_cls
    from v2.scheduler import device_planner as dp

    cycle = _timeline_call(dp.load_or_build_cycle, device, DATA_ROOT, CONFIG_ROOT)
    today = date_cls.today().isoformat()
    plan = dp.read_plan(device, DATA_ROOT, today)
    return {
        **cycle,
        "today": today,
        "today_index": dp.cycle_index(today, cycle["cycle_days"]),
        "runtime": {b["id"]: {"status": b.get("status"),
                              "started_at": b.get("started_at"),
                              "ended_at": b.get("ended_at")}
                    for b in (plan or {}).get("blocks", [])},
    }


@app.get("/api/timeline-devices")
def list_timeline_devices():
    """Urządzenia z liczbą kont uczestniczących w planie — selektor osi czasu."""
    from v2.scheduler import device_planner as dp
    out = []
    for d in dp.list_devices(CONFIG_ROOT):
        out.append({
            "device": d,
            "accounts": [e["account"] for e in dp.accounts_for_device(CONFIG_ROOT, d)],
            "accounts_total": len(dp.accounts_for_device(CONFIG_ROOT, d, only_enabled=False)),
        })
    return out


@app.post("/api/timeline/{device}/regenerate")
def regenerate_timeline(device: str):
    """Ułóż cały wzorzec od nowa. Bloki oznaczone `locked` (ręcznie przesunięte
    lub edytowane) zostają na miejscu."""
    _require_device(device)
    from v2.scheduler import device_planner as dp
    cycle = _timeline_call(dp.build_cycle, device, DATA_ROOT, CONFIG_ROOT)
    dp.resync_date(device, DATA_ROOT, CONFIG_ROOT)
    return dp.annotate_cycle(cycle)


@app.delete("/api/timeline/{device}")
def clear_timeline(device: str):
    """Wyczyść całą oś urządzenia: cycle.json + zmaterializowane dni w schedule/.

    Po clear panel pokazuje pusty wzorzec — użytkownik może kliknąć
    „Ułóż wzorzec od nowa” albo zapisać sesje/dzień.
    """
    _require_device(device)
    from v2.scheduler import device_planner as dp
    return dp.clear_device_timeline(device, DATA_ROOT)


@app.post("/api/timeline/{device}/day/{day_index}/block")
async def add_timeline_block(device: str, day_index: int, request: Request):
    """Ręczny slot na osi: konto + scenariusz (kolejka) + godzina startu.

    Blok dostaje `locked=True`, więc regeneracja wzorca go nie rusza, a auto-plan
    odejmuje go od puli `sessions_per_day` tego konta.
    """
    _require_device(device)
    body = await request.json()
    acc = body.get("account")
    if not acc:
        raise HTTPException(400, "account wymagane")
    from v2.scheduler import device_planner as dp
    if dp.device_for_account(CONFIG_ROOT, acc) != device:
        raise HTTPException(400, f"konto '{acc}' nie jest przypisane do '{device}'")
    return _timeline_call(dp.add_block, device, DATA_ROOT, CONFIG_ROOT, day_index,
                          acc, body.get("scenario"), body.get("start") or "12:00")


@app.post("/api/timeline/{device}/day/{day_index}/block/{block_id}/move")
async def move_timeline_block(device: str, day_index: int, block_id: str,
                              request: Request):
    _require_device(device)
    body = await request.json()
    start = body.get("start")
    if not start:
        raise HTTPException(400, "start wymagany (HH:MM)")
    from v2.scheduler import device_planner as dp
    return _timeline_call(dp.move_block, device, DATA_ROOT, CONFIG_ROOT, day_index,
                          block_id, start, force=bool(body.get("force")))


@app.post("/api/timeline/{device}/day/{day_index}/block/{block_id}/queue")
async def set_timeline_block_queue(device: str, day_index: int, block_id: str,
                                   request: Request):
    """Zawartość bloku — override per slot. `queue_override: null` odpina
    kopię i wraca do scenariusza bazowego; słownik zapisuje własną wersję
    zadań, nie ruszając pliku scenariusza ani innych bloków."""
    _require_device(device)
    body = await request.json()
    from v2.scheduler import device_planner as dp
    return _timeline_call(dp.set_block_queue, device, DATA_ROOT, CONFIG_ROOT,
                          day_index, block_id, scenario=body.get("scenario"),
                          queue_override=body.get("queue_override"),
                          account=body.get("account"))


@app.delete("/api/timeline/{device}/day/{day_index}/block/{block_id}")
def delete_timeline_block(device: str, day_index: int, block_id: str):
    _require_device(device)
    from v2.scheduler import device_planner as dp
    return _timeline_call(dp.delete_block, device, DATA_ROOT, CONFIG_ROOT,
                          day_index, block_id)


@app.post("/api/timeline/{device}/day/{day_index}/block/{block_id}/lock")
async def lock_timeline_block(device: str, day_index: int, block_id: str,
                              request: Request):
    """Kłódka inspectora — `locked` przeżywa `regenerate`. Bez drugiego plannera."""
    _require_device(device)
    body = await request.json()
    if not isinstance(body, dict) or "locked" not in body:
        raise HTTPException(400, "locked wymagane (bool)")
    from v2.scheduler import device_planner as dp
    return _timeline_call(dp.set_block_locked, device, DATA_ROOT, CONFIG_ROOT,
                          day_index, block_id, bool(body.get("locked")))


@app.post("/api/timeline/{device}/day/{day_index}/autospread")
def autospread_timeline(device: str, day_index: int):
    _require_device(device)
    from v2.scheduler import device_planner as dp
    return _timeline_call(dp.autospread, device, DATA_ROOT, CONFIG_ROOT, day_index)


@app.post("/api/estimate")
async def api_estimate(request: Request):
    """Ile potrwa taka kolejka — żywy podgląd w edytorze bloku.

    `queue_override` pozwala wycenić listę zadań, która jeszcze nie została
    nigdzie zapisana, żeby blok na osi zmieniał szerokość w trakcie edycji.
    """
    body = await request.json()
    acc = body.get("account")
    if not acc:
        raise HTTPException(400, "account wymagane")
    from v2.scheduler.estimator import estimate_queue
    try:
        return estimate_queue(acc, body.get("scenario"),
                              queue_data=body.get("queue_override"))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/estimate-actions")
async def api_estimate_actions(request: Request):
    """Szacunek potencjalnych lajków / follow / komentarzy dla scenariusza.

    Ta sama semantyka co `/api/estimate`: `queue_override` = żywy podgląd
    niezapisanej kolejki z edytora Scenariuszy. Zwraca zakresy min–max oraz
    porównanie z limitami dziennymi konta (`utilization` + `used_today`).

    `eta` — czas na te akcje z historycznego tempa sesji (sec/like, sec/follow).
    """
    body = await request.json()
    acc = body.get("account")
    if not acc:
        raise HTTPException(400, "account wymagane")
    from v2.scheduler.action_estimator import (count_used_today,
                                               estimate_queue_actions,
                                               utilization_vs_limits)
    from v2.data.pace import account_pace, estimate_actions_eta
    try:
        est = estimate_queue_actions(
            acc, body.get("scenario"),
            queue_data=body.get("queue_override"))
        # limity z configu konta
        try:
            cfg = json.loads(
                (CONFIG_ROOT / "accounts" / f"{acc}.json").read_text(encoding="utf-8"))
            limits = cfg.get("limits") or {}
        except Exception:
            limits = {}
        # dzisiejsze zużycie — ta sama classify_action co limiter
        used = count_used_today(acc, DATA_ROOT)
        est["limits"] = limits
        est["used_today"] = used
        est["utilization"] = utilization_vs_limits(
            est.get("actions"), limits, used_today=used)
        # ETA z tempa ostatnich kolejek (albo defaultów)
        pace = account_pace(acc, DATA_ROOT, last_n=30)
        est["eta"] = estimate_actions_eta(est.get("actions"), pace)
        return est
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/stats/{account}/pace")
def api_account_pace(account: str, last_n: int = 30):
    """Historyczne tempo akcji (sec/like…) z snapshotów sesji konta."""
    _require_account(account)
    from v2.data.pace import account_pace, load_recent_session_rates
    pace = account_pace(account, DATA_ROOT, last_n=last_n)
    return {
        "account": account,
        "pace": pace,
        "recent": load_recent_session_rates(
            account, DATA_ROOT, last_n=min(10, max(1, int(last_n)))),
    }


def _run_cli_sync(args: list, timeout: int) -> str:
    r = subprocess.run(
        [sys.executable, "-u", "-m", "v2.cli", *args],
        cwd=str(V2_ROOT.parent),
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return (r.stdout or "") + (r.stderr or "")


# ============================================================
# API — SSE tail events.jsonl + stdout
# ============================================================
@app.get("/api/events/stream")
async def stream_events(account: Optional[str] = None, device: Optional[str] = None):
    """SSE: tail events.jsonl + stdout TEGO slotu.

    `?account=` zostaje (klasyk). `?device=` ustawia focus i bierze konto
    z jadącego slotu. Bez żadnego parametru — 400.
    """
    if device:
        SESSION.set_focus(device)
        slot = SESSION.slot_for_device(device)
        if not account and slot:
            account = slot.account
    elif account:
        alias = _account_device_alias(account, CONFIG_ROOT)
        if alias:
            SESSION.set_focus(alias)
    if not account:
        raise HTTPException(400, "account albo device wymagane")
    events_path = DATA_ROOT / "accounts" / account / "events.jsonl"

    async def _gen():
        # zacznij od końca — nowe eventy tylko
        last_events_pos = events_path.stat().st_size if events_path.exists() else 0
        last_stdout_pos = 0

        # heartbeat + tail — wychodzimy przy shutdown panelu (zwolnienie portu)
        while not _panel_shutting_down:
            payloads = []

            # events.jsonl
            if events_path.exists():
                sz = events_path.stat().st_size
                if sz < last_events_pos:  # plik został przycięty/zresetowany
                    last_events_pos = 0
                if sz > last_events_pos:
                    with events_path.open("rb") as f:
                        f.seek(last_events_pos)
                        chunk = f.read(sz - last_events_pos).decode("utf-8", errors="replace")
                        last_events_pos = sz
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line: continue
                        try:
                            payloads.append({"src": "event", "data": json.loads(line)})
                        except Exception:
                            payloads.append({"src": "event_raw", "data": line})

            # stdout TEGO slotu (device albo konto), nie sąsiada floty
            slot = (
                SESSION.slot_for_device(device) if device
                else SESSION.slot_for_account(account)
            )
            sp = slot.stdout_path if slot and slot.is_alive() else None
            if sp and sp.exists():
                sz = sp.stat().st_size
                if sz > last_stdout_pos:
                    with sp.open("rb") as f:
                        f.seek(last_stdout_pos)
                        chunk = f.read(sz - last_stdout_pos).decode("utf-8", errors="replace")
                        last_stdout_pos = sz
                    for line in chunk.splitlines():
                        line = line.rstrip()
                        if line:
                            payloads.append({"src": "stdout", "data": line})

            if payloads:
                for p in payloads:
                    yield f"data: {json.dumps(p, ensure_ascii=False)}\n\n"
            else:
                yield ": ping\n\n"      # SSE keep-alive

            # 0.2s — krótszy poll = mniejsza latencja logów w panelu
            # (wcześniej 0.5s dokładało do opóźnienia stdout)
            await asyncio.sleep(0.2)

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ============================================================
# API — live session_stats (panel /stats)
# ============================================================
def _require_account(account: str) -> str:
    """Konto musi istnieć w configu — inaczej 404 jak przy innych endpointach."""
    if not (CONFIG_ROOT / "accounts" / f"{account}.json").exists():
        raise HTTPException(404, f"brak konta: {account}")
    return account


def _account_data_dir(account: str) -> Path:
    return DATA_ROOT / "accounts" / account


def _read_json_file(path: Path) -> dict | None:
    """Odczyt JSON; None gdy brak pliku / uszkodzony (UI pokaże pusty stan)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _list_session_snapshots(account: str, limit: int = 50) -> list[dict]:
    """Snapshoty `sessions/<session_id>.json` (bez logów *-stdout/-stderr).

    Sortowane od najnowszego (mtime). Zwraca meta bez pełnych counters —
    pełny dump idzie przez GET .../sessions/{sid}.
    """
    sess_dir = _account_data_dir(account) / "sessions"
    if not sess_dir.is_dir():
        return []
    rows: list[dict] = []
    for p in sess_dir.glob("*.json"):
        # pomiń ewentualne śmieci / bak
        if p.name.endswith(".bak") or ".tmp" in p.name:
            continue
        data = _read_json_file(p)
        if not isinstance(data, dict):
            continue
        # Snapshot session_stats ma session_id; inne JSON w katalogu pomijamy
        sid = data.get("session_id") or p.stem
        if not sid:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0
        rows.append({
            "session_id": sid,
            "started_at": data.get("started_at"),
            "ended_at": data.get("ended_at"),
            "updated_at": data.get("updated_at"),
            "plugin": data.get("plugin"),
            "queue": data.get("queue"),
            "summary": data.get("summary"),
            "mtime": mtime,
            "filename": p.name,
        })
    rows.sort(key=lambda r: r.get("mtime") or 0, reverse=True)
    return rows[: max(1, min(int(limit or 50), 200))]


def _account_stats_for_session(account: str, session_id: str | None) -> dict:
    """followers/following z events.jsonl dla sesji (start ± end + delta).

    Brak session_id → pusty summarize (UI pokaże hint).
    """
    from v2.data.stats import load_account_stats, summarize_account_stats

    if not session_id:
        return summarize_account_stats([])
    # Sesje bywają starsze niż domyślne okno — przy znanym id skanujemy cały plik
    return load_account_stats(
        account, DATA_ROOT, session_id=str(session_id), since_days=None,
    )


@app.get("/api/stats/{account}")
def api_session_stats(account: str):
    """Live `session_stats.json` konta + lista ostatnich snapshotów (meta).

    Gdy pliku live jeszcze nie ma — `live: null` (konto OK, brak sesji).
    ``account_stats`` = odczyt followers/following ze `stats_snapshot` dla
    bieżącej sesji live (events.jsonl).
    """
    _require_account(account)
    live_path = _account_data_dir(account) / "session_stats.json"
    live = _read_json_file(live_path)
    snapshots = _list_session_snapshots(account, limit=30)
    sid = (live or {}).get("session_id") if isinstance(live, dict) else None
    return {
        "account": account,
        "live_path": str(live_path.relative_to(DATA_ROOT)) if live_path.exists() else None,
        "live": live,
        "sessions": snapshots,
        "session_running": _account_session_running(account),
        # Obserwujący / obserwowani na starcie (i ewentualnie na końcu) sesji
        "account_stats": _account_stats_for_session(account, sid),
    }


@app.get("/api/stats/{account}/sessions")
def api_session_stats_list(account: str, limit: int = 50):
    """Lista snapshotów sesji na dysku (meta, bez pełnych counters)."""
    _require_account(account)
    return {
        "account": account,
        "sessions": _list_session_snapshots(account, limit=limit),
    }


@app.get("/api/stats/{account}/sessions/{session_id}")
def api_session_stats_snapshot(account: str, session_id: str):
    """Jeden snapshot `sessions/<session_id>.json` + account_stats z events."""
    _require_account(account)
    # Bez path traversal — session_id to sama nazwa pliku
    if not session_id or "/" in session_id or "\\" in session_id or ".." in session_id:
        raise HTTPException(400, f"nieprawidłowe session_id: {session_id!r}")
    path = _account_data_dir(account) / "sessions" / f"{session_id}.json"
    data = _read_json_file(path)
    if data is None:
        raise HTTPException(404, f"brak snapshotu sesji: {session_id}")
    return {
        "account": account,
        "session_id": session_id,
        "snapshot": data,
        "account_stats": _account_stats_for_session(account, session_id),
    }


def _parse_iso_ts(value) -> datetime | None:
    """ISO z session_stats (lokalne, bez strefy) → datetime; None przy śmieciach."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Akceptuj też końcówkę Z / offset — normalizuj do naive local-like
    if s.endswith("Z"):
        s = s[:-1]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _session_anchor_ts(data: dict, mtime: float = 0.0) -> datetime | None:
    """Kotwica czasu sesji do filtra okna.

    Priorytet: `started_at` (moment startu kolejki) → `ended_at` → `updated_at`
    → mtime pliku. Dzięki temu „ostatnie 24h” = sesje, które **wystartowały**
    w oknie; mtime tylko ratuje stare/uszkodzone JSON bez timestampów.
    """
    for key in ("started_at", "ended_at", "updated_at"):
        dt = _parse_iso_ts(data.get(key))
        if dt is not None:
            return dt
    if mtime and mtime > 0:
        try:
            return datetime.fromtimestamp(mtime)
        except (OSError, OverflowError, ValueError):
            return None
    return None


def _sum_int_maps(dst: dict, src: dict | None) -> None:
    """Dodaj wartości int z src do dst (pomijaj None / nie-liczby)."""
    if not isinstance(src, dict):
        return
    for k, v in src.items():
        if v is None:
            continue
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        dst[k] = int(dst.get(k) or 0) + iv


def _sum_by_plugin(dst: dict, src: dict | None) -> None:
    """Zsumuj by_plugin[plugin][counter] między sesjami."""
    if not isinstance(src, dict):
        return
    for plugin, counters in src.items():
        if not plugin or not isinstance(counters, dict):
            continue
        bucket = dst.setdefault(str(plugin), {})
        _sum_int_maps(bucket, counters)


def _iter_stats_sources(account: str) -> list[tuple[dict, float, str]]:
    """Źródła do agregacji: snapshoty + live (gdy brak snapshotu tej sesji).

    Zwraca listę (data, mtime, source) gdzie source = 'snapshot'|'live'.
    Live dokładamy tylko gdy `session_id` nie ma jeszcze pliku w sessions/ —
    inaczej po `snapshot_end` sumowalibyśmy dwa razy te same counters.
    """
    out: list[tuple[dict, float, str]] = []
    seen_ids: set[str] = set()
    sess_dir = _account_data_dir(account) / "sessions"
    if sess_dir.is_dir():
        for p in sess_dir.glob("*.json"):
            if p.name.endswith(".bak") or ".tmp" in p.name:
                continue
            data = _read_json_file(p)
            if not isinstance(data, dict):
                continue
            sid = data.get("session_id") or p.stem
            if not sid:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            out.append((data, mtime, "snapshot"))
            seen_ids.add(str(sid))

    live_path = _account_data_dir(account) / "session_stats.json"
    live = _read_json_file(live_path)
    if isinstance(live, dict):
        sid = live.get("session_id")
        if sid and str(sid) not in seen_ids:
            try:
                mtime = live_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            out.append((live, mtime, "live"))
    return out


def _resolve_aggregate_window(
    *,
    range_: str | None,
    from_: str | None,
    to_: str | None,
) -> tuple[datetime, datetime, dict]:
    """Zbuduj [window_start, window_end] z query params.

    - `range=24h` → ostatnie 24 godziny od teraz
    - `from`/`to` (YYYY-MM-DD) → od 00:00:00 from do 23:59:59 to (włącznie)
    Mutually exclusive: przy range ignorujemy from/to.
    """
    now = datetime.now().replace(microsecond=0)
    meta: dict = {"mode": None, "range": None, "from": None, "to": None}

    if range_:
        r = str(range_).strip().lower()
        if r not in ("24h", "1d", "day"):
            raise HTTPException(
                400,
                f"nieobsługiwany range={range_!r} (użyj 24h albo from/to)",
            )
        start = now - timedelta(hours=24)
        meta.update({"mode": "range", "range": "24h",
                     "from": start.isoformat(timespec="seconds"),
                     "to": now.isoformat(timespec="seconds")})
        return start, now, meta

    if not from_ or not to_:
        raise HTTPException(
            400,
            "podaj range=24h albo oba parametry from=YYYY-MM-DD&to=YYYY-MM-DD",
        )
    try:
        d_from = datetime.strptime(str(from_).strip(), "%Y-%m-%d")
        d_to = datetime.strptime(str(to_).strip(), "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(
            400, f"from/to muszą być YYYY-MM-DD ({e})"
        ) from e
    if d_to < d_from:
        raise HTTPException(400, "to nie może być wcześniejsze niż from")
    # Koniec dnia włącznie (23:59:59)
    start = d_from.replace(hour=0, minute=0, second=0)
    end = d_to.replace(hour=23, minute=59, second=59)
    meta.update({
        "mode": "dates",
        "range": None,
        "from": start.date().isoformat(),
        "to": end.date().isoformat(),
    })
    return start, end, meta


def _clone_int_map(src: dict | None) -> dict[str, int]:
    """Płytka kopia mapy liczników → int (śmieci pomijane)."""
    out: dict[str, int] = {}
    _sum_int_maps(out, src)
    return out


def _clone_by_plugin(src: dict | None) -> dict[str, dict[str, int]]:
    """Kopia by_plugin[plugin][counter] bez współdzielenia referencji."""
    out: dict[str, dict[str, int]] = {}
    _sum_by_plugin(out, src)
    return out


@app.get("/api/stats/{account}/aggregate")
def api_session_stats_aggregate(
    account: str,
    range: Optional[str] = None,
    to: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
):
    """Suma `counters` + `by_plugin` + `ai` (tokeny/koszt) z sesji w oknie.

    Query:
      - `range=24h` — ostatnie 24h
      - `from=YYYY-MM-DD&to=YYYY-MM-DD` — zakres dni (alias query: `from`)

    Filtr: kotwica `started_at` (fallback ended/updated/mtime) ∈ [start, end].
    Zwraca też listę sesji weszłych w agregat (id + timestamps) oraz
    `series` — punkty timeline `{t, session_id, counters, by_plugin}`
    (rosnąco po czasie) pod wykresy Chart.js.
    ``account_stats`` — pierwszy/ostatni odczyt followers/following w oknie
    (z events.jsonl / stats_snapshot) + delta.
    """
    _require_account(account)
    window_start, window_end, window_meta = _resolve_aggregate_window(
        range_=range, from_=from_, to_=to,
    )

    counters: dict[str, int] = {}
    by_plugin: dict[str, dict[str, int]] = {}
    # Tokeny IN/OUT + koszt USD (stare snapshoty bez `ai` = 0)
    ai = empty_usage_totals()
    sessions_in: list[dict] = []
    # Punkty serii (później sort rosnąco) — jeden punkt = jedna sesja w oknie
    series_pts: list[dict] = []

    for data, mtime, source in _iter_stats_sources(account):
        anchor = _session_anchor_ts(data, mtime)
        if anchor is None:
            continue
        if anchor < window_start or anchor > window_end:
            continue
        sid = str(data.get("session_id") or "")
        sess_counters = _clone_int_map(data.get("counters"))
        sess_by_plugin = _clone_by_plugin(data.get("by_plugin"))
        _sum_int_maps(counters, sess_counters)
        _sum_by_plugin(by_plugin, sess_by_plugin)
        merge_usage_totals(ai, data.get("ai"))
        sessions_in.append({
            "session_id": sid or None,
            "started_at": data.get("started_at"),
            "ended_at": data.get("ended_at"),
            "updated_at": data.get("updated_at"),
            "anchor_at": anchor.isoformat(timespec="seconds"),
            "source": source,
            "plugin": data.get("plugin"),
            "queue": (data.get("queue") or {}).get("name")
                     if isinstance(data.get("queue"), dict) else None,
        })
        series_pts.append({
            "t": anchor.isoformat(timespec="seconds"),
            "session_id": sid or None,
            "plugin": data.get("plugin"),
            "source": source,
            "counters": sess_counters,
            "by_plugin": sess_by_plugin,
        })

    # Najnowsze pierwsze (po kotwicy) — lista sesji w UI
    sessions_in.sort(
        key=lambda r: r.get("anchor_at") or "",
        reverse=True,
    )
    # Timeline chronologicznie (oś X wykresu)
    series_pts.sort(key=lambda r: r.get("t") or "")

    # Obserwujący/obserwowani w oknie: pierwszy vs ostatni odczyt stats_snapshot
    from v2.data.stats import load_account_stats
    account_stats = load_account_stats(
        account, DATA_ROOT,
        since_days=None,
        window_start=window_start,
        window_end=window_end,
    )

    return {
        "account": account,
        "window": window_meta,
        "window_start": window_start.isoformat(timespec="seconds"),
        "window_end": window_end.isoformat(timespec="seconds"),
        "sessions_count": len(sessions_in),
        "sessions": sessions_in,
        "counters": counters,
        "by_plugin": by_plugin,
        "ai": ai,
        "series": series_pts,
        "account_stats": account_stats,
    }


# ============================================================
# UI
# ============================================================
_scheduler_task: asyncio.Task | None = None


async def _scheduler_loop(interval_s: int = 60):
    """Wątek schedulera w procesie panelu — `python -m v2.cli panel` staje się
    też procesem odpalającym sesje wg planu, bez drugiego procesu do
    pilnowania. `SESSION.is_running_on(device)` blokuje tylko ten telefon;
    drugi serial może dostać due w tym samym ticku.

    Pętla chodzi zawsze, ale `tick_once` nic nie robi, dopóki master switch
    jest wyłączony — dzięki temu przycisk START/STOP w panelu działa
    natychmiast, bez restartu procesu.

    Równolegle ticka filtr profili HTTP (`profile_filter`) — bez device locka.
    """
    from v2.scheduler.tick import finish_running_blocks, tick_once
    from v2.scheduler import profile_filter_tick as pft

    def _start(acc, scenario, max_duration_s=None):
        SESSION.start(acc, mode="queue", queue_name=scenario,
                      max_duration_s=max_duration_s)

    while True:
        try:
            finish_running_blocks(DATA_ROOT, CONFIG_ROOT)
            actions = tick_once(
                DATA_ROOT, CONFIG_ROOT,
                is_busy=lambda device: SESSION.is_running_on(device),
                start=_start,
            )
            for a in actions:
                extra = f"  scenario={a['scenario']}" if a.get("scenario") else ""
                print(f"[scheduler] {a['account']}  blok={a['start']}  {a['result']}{extra}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[scheduler] tick error: {e}")

        # Filtr HTTP — niezależny od master switcha telefonu; gated przez
        # account.profile_filter.enabled + godziny times[]
        try:
            if not _filter_job.get("running") and not pft.is_filter_busy():
                for a in pft.tick_once(DATA_ROOT, CONFIG_ROOT):
                    print(f"[filter-sched] {a}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[filter-sched] tick error: {e}")

        await asyncio.sleep(interval_s)


@app.on_event("startup")
async def _start_scheduler():
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_scheduler_loop())


@app.on_event("shutdown")
async def _shutdown_panel():
    """Graceful shutdown FastAPI: anuluj scheduler + ubij child sesji.

    Bez tego SSE/`uvicorn.run` potrafi wisieć na Windows, a child
    `run-queue` (CREATE_NEW_PROCESS_GROUP) zostaje orphanem — port 8765
    wygląda jak zombie."""
    global _scheduler_task, _panel_shutting_down
    _panel_shutting_down = True
    task = _scheduler_task
    _scheduler_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    SESSION.force_stop()
    try:
        with _filter_job_lock:
            proc = _filter_proc
            if _filter_job.get("running"):
                _filter_job["cancel"] = True
                _filter_job["stopping"] = True
        _terminate_filter_proc(proc, wait_s=2)
    except Exception:
        pass
    print("[panel] shutdown — sesja zatrzymana, zwalnianie portu")


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.get("/stats", response_class=HTMLResponse)
def stats_page():
    """Osobna strona — live counters / by_plugin + wykresy Chart.js."""
    html_path = Path(__file__).parent / "static" / "stats.html"
    return html_path.read_text(encoding="utf-8")


# Nowy panel operatora — osobny shell, klasyczny index.html bez zmian.
_APP_STATIC = Path(__file__).parent / "static"
# Komentarz: Chrome poza Cursorem trzyma /app i konta.js; Simple Browser zwykle nie.
# no-store + ?v=mtime — nowy URL skryptu, bez twardego Ctrl+Shift+R.
_STATIC_NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
_STATIC_HREF = re.compile(
    r'(?P<pre>(?:src|href)=")(?P<url>/static/[^"?]+\.(?:js|css))(?P<post>")',
)


def _static_asset_version() -> str:
    """Najnowszy mtime JS/CSS panelu — cache-bust w HTML."""
    newest = 0
    for name in ("app.js", "konta.js", "app.css", "stats-view.js"):
        p = _APP_STATIC / name
        try:
            newest = max(newest, int(p.stat().st_mtime))
        except OSError:
            pass
    return str(newest or 1)


def _with_static_bust(html: str) -> str:
    """Dopisz ?v=mtime do /static/*.js|css — stary URL w cache Chrome mija."""
    v = _static_asset_version()

    def repl(m: re.Match[str]) -> str:
        return f'{m.group("pre")}{m.group("url")}?v={v}{m.group("post")}'

    return _STATIC_HREF.sub(repl, html)


@app.get("/app")
def operator_app(view: Optional[str] = None):
    """Pulpit v2 (`app.html`). Stary UI zostaje pod GET /.

    `?view=owner` to alias planu — ten sam readonly shell co `/share/schedule`,
    bez auth (token dołożymy później).
    """
    if (view or "").strip().lower() == "owner":
        return RedirectResponse("/share/schedule", status_code=302)
    html = _with_static_bust((_APP_STATIC / "app.html").read_text(encoding="utf-8"))
    return HTMLResponse(html, headers=_STATIC_NO_STORE)


def _pro_dash_ig_user(account: str) -> str:
    """Nick IG z account.json — tylko do etykiety karty."""
    path = CONFIG_ROOT / "accounts" / f"{account}.json"
    try:
        meta = (json.loads(path.read_text(encoding="utf-8")).get("meta") or {})
        return str(meta.get("ig_username") or "")
    except Exception:
        return ""


@app.get("/api/pro-dash/{account}")
def api_pro_dash_get(account: str):
    """Store Insights (okna 7/14/30 + daily + zawartość). Auto-seed gdy pusty.

    Nie mylić z GET /api/stats — to session_stats / #statystyki.
    """
    _require_account(account)
    _store, view = pro_dash_ensure(
        account, DATA_ROOT, ig_username=_pro_dash_ig_user(account), auto_seed=True)
    return view


@app.get("/api/pro-dash/{account}/item/{item_id}")
def api_pro_dash_item(account: str, item_id: str):
    """Szczegół wiersza: snapshot history + ewentualna seria dzienna pozycji."""
    _require_account(account)
    if not item_id or "/" in item_id or "\\" in item_id or ".." in item_id:
        raise HTTPException(400, "nieprawidłowe item_id")
    store = ProDashStore(account, DATA_ROOT)
    rec = store.get_item(item_id)
    if rec is None:
        raise HTTPException(404, f"brak pozycji: {item_id}")
    return {"account": account, "item": rec}


@app.get("/api/pro-dash/{account}/thumb/{item_id}")
def api_pro_dash_thumb(account: str, item_id: str):
    """JPEG miniatury z `pro_dash/thumbs/`."""
    _require_account(account)
    if not item_id or "/" in item_id or "\\" in item_id or ".." in item_id:
        raise HTTPException(400, "nieprawidłowe item_id")
    store = ProDashStore(account, DATA_ROOT)
    path = store.thumb_path(item_id)
    if path is None:
        raise HTTPException(404, "brak miniatury")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/api/pro-dash/{account}/seed")
def api_pro_dash_seed(account: str):
    """Wlej recon + TRIAL_FETCH (gdy telefon niedostępny)."""
    _require_account(account)
    store = ProDashStore(account, DATA_ROOT)
    _store, view = pro_dash_seed(store, ig_username=_pro_dash_ig_user(account))
    return view


@app.post("/api/pro-dash/{account}/fetch")
def api_pro_dash_fetch(account: str, item_id: Optional[str] = None):
    """Live 7/14/30 + filtry zawartości gdy telefon wolny; inaczej seed.

    Nie publikuje. Nie startuje scrcpy. `item_id` = drill-in wiersza
    i odczyt wykresu wyświetleń z overlayu (oś + piksele linii).
    """
    _require_account(account)
    if item_id and ("/" in item_id or "\\" in item_id or ".." in item_id):
        raise HTTPException(400, "nieprawidłowe item_id")
    store = ProDashStore(account, DATA_ROOT)
    return pro_dash_fetch(
        store, account, DATA_ROOT, CONFIG_ROOT,
        item_id=item_id, full_charts=bool(item_id))


@app.get("/pro-stats-preview", response_class=HTMLResponse)
def pro_stats_preview():
    """Karta Insights (zapis + wykresy) — osobna strona, nie rail / nie #statystyki."""
    return HTMLResponse((_APP_STATIC / "pro-stats-preview.html").read_text(encoding="utf-8"))


@app.get("/static/app.css")
def operator_app_css():
    return FileResponse(
        _APP_STATIC / "app.css", media_type="text/css", headers=_STATIC_NO_STORE)


@app.get("/static/app.js")
def operator_app_js():
    return FileResponse(
        _APP_STATIC / "app.js", media_type="text/javascript",
        headers=_STATIC_NO_STORE)


@app.get("/static/stats-view.js")
def operator_stats_view_js():
    """KPI + iframe /stats w shellu operatora — osobno od app.js (Na żywo)."""
    return FileResponse(
        _APP_STATIC / "stats-view.js", media_type="text/javascript",
        headers=_STATIC_NO_STORE)


@app.get("/static/konta.js")
def operator_konta_js():
    """Konta (#konta) — karty + inspector, osobno od app.js."""
    return FileResponse(
        _APP_STATIC / "konta.js", media_type="text/javascript",
        headers=_STATIC_NO_STORE)


def _share_html() -> str:
    """HTML stubu właściciela — recykluje app.css, flaga readonly w body."""
    return (_APP_STATIC / "share.html").read_text(encoding="utf-8")


@app.get("/share", response_class=HTMLResponse)
def owner_share_root():
    """Alias — JS i tak defaultuje na harmonogram."""
    return _share_html()


@app.get("/share/schedule", response_class=HTMLResponse)
def owner_share_schedule():
    """Tydzień osi, tylko odczyt. JS rozróżnia ścieżkę; zero POST sterowania."""
    return _share_html()


@app.get("/share/stats", response_class=HTMLResponse)
def owner_share_stats():
    """KPI 24h, tylko odczyt (bez iframe operatorskiego /stats)."""
    return _share_html()


@app.get("/static/share.js")
def owner_share_js():
    return FileResponse(_APP_STATIC / "share.js", media_type="text/javascript")


def _cleanup_panel_process():
    """atexit / finally — ostatnia linia obrony gdy sygnał ominie lifespan."""
    global _panel_shutting_down
    _panel_shutting_down = True
    try:
        SESSION.force_stop()
    except Exception:
        pass
    try:
        with _filter_job_lock:
            proc = _filter_proc
            if _filter_job.get("running"):
                _filter_job["cancel"] = True
                _filter_job["stopping"] = True
        _terminate_filter_proc(proc, wait_s=2)
    except Exception:
        pass


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True):
    """Start uvicorn w foreground; Ctrl+C / zamknięcie konsoli → wolny port.

    Na Windows samo uvicorn + atexit nie wystarcza przy CLOSE okna konsoli:
    proces potrafi zostać z LISTENING (Errno 10048). Dokładamy
    SetConsoleCtrlHandler + SIGINT/SIGTERM/SIGBREAK → `should_exit`.
    Panel NIE jest spawnowany jako detached child.
    """
    import atexit
    import ctypes
    import uvicorn
    import webbrowser

    atexit.register(_cleanup_panel_process)

    # Timer jako daemon — inaczej nie-daemon wątek trzyma proces przy wyjściu
    if open_browser:
        t = threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}"))
        t.daemon = True
        t.start()

    # timeout_graceful_shutdown: SSE (while True) inaczej blokuje exit na Windows
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)

    def _request_shutdown(*_args):
        # sygnał / konsola — ustaw flagę uvicorna i ubij child sesji
        server.should_exit = True
        _cleanup_panel_process()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _request_shutdown)
        except (ValueError, OSError):
            pass

    if sys.platform == "win32":
        if hasattr(signal, "SIGBREAK"):
            try:
                signal.signal(signal.SIGBREAK, _request_shutdown)
            except (ValueError, OSError):
                pass

        # CLOSE/LOGOFF/SHUTDOWN okna konsoli (root cause orphan :8765)
        HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        @HandlerRoutine
        def _console_ctrl(ctrl_type: int) -> bool:
            # 0=C, 1=BREAK, 2=CLOSE, 5=LOGOFF, 6=SHUTDOWN
            _request_shutdown()
            return True

        server._win_console_handler = _console_ctrl  # type: ignore[attr-defined]
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_ctrl, True)

    print(f"[panel] PID={os.getpid()}  http://{host}:{port}")
    print(f"[panel] zatrzymaj: Ctrl+C  (port {port} zostanie zwolniony)")
    try:
        server.run()
    except KeyboardInterrupt:
        print("[panel] Ctrl+C — zamykam")
    finally:
        _cleanup_panel_process()
        server.should_exit = True
        print("[panel] zatrzymany — port powinien być wolny")
