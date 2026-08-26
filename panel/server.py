"""Dashboard panel — FastAPI + prosty HTML.

Uruchomienie: `python -m v2.cli panel` → localhost:8765

Endpointy:
  GET  /                     — HTML dashboard
  GET  /api/accounts         — lista kont [{alias, device_alias}, ...]
  GET  /api/devices          — lista urządzeń (config/devices/*.json) + klony
  POST /api/devices          — zarejestruj urządzenie (alias, adb_id; opcjonalnie model/clones…)
  POST /api/accounts         — utwórz nowe konto (alias, ig_username, device_alias, app_clone_package)
  POST /api/accounts/{acc}/clone — ustaw/wyczyść device.app_clone_package (pusty string = clear)
  DELETE /api/accounts/{acc} — usuń konto (config + credentials; data → *.deleted; bloki z osi)
  DELETE /api/timeline/{dev} — wyczyść całą oś urządzenia (cycle + schedule)
  GET  /api/accounts/{acc}/credentials  — status danych logowania (bez hasła)
  POST /api/accounts/{acc}/credentials  — zapisz ig_username/ig_password
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
  DELETE /api/timeline/{dev}/{date}/block/{id}     — usuń blok
  POST /api/timeline/{dev}/{date}/autospread — rozsuń kolidujące bloki
  POST /api/estimate         — {account,scenario,queue_override} — wycena czasu
  POST /api/estimate-actions — {account,scenario,queue_override} — szacunek lajków/follow + ETA z tempa sesji
  GET  /api/queue-presets/{acc} — nazwy zapisanych scenariuszy kolejki
  GET  /api/queue/{acc}?name=X — kolejka (domyślna gdy name pominięte)
  POST /api/queue/{acc}?name=X — zapis kolejki (domyślnej albo nazwanego scenariusza)
  DELETE /api/queue/{acc}/{name} — usuń nazwany scenariusz
  GET  /api/flows            — lista flow-ów
  GET  /api/known_steps      — spis pluginów
  GET  /api/raw-profiles/{acc}?limit=&unique= — podgląd raw_profiles.jsonl (panel)
  GET  /api/target-candidates/{acc}?limit= — podgląd target_candidates.jsonl
  GET  /api/profile-filter/{acc} — config + status filtra HTTP
  POST /api/profile-filter/{acc} — zapis sekcji profile_filter w account.json
  POST /api/profile-filter/{acc}/run — uruchom filtr teraz (202, tło)
  GET  /api/profile-filter/{acc}/status — last run + liczniki + busy + job.terminal_tail/log
  GET  /api/profile-filter/{acc}/log — pełny stdout/stderr ostatniego joba filtra
  GET  /api/config/{acc}     — pełen config konta
  POST /api/config/{acc}     — zapis (z walidacją load_account)
  DELETE /api/niche/{acc}    — wyczyść account.niche (UI: „Usuń dane niszy”)
  GET  /api/status           — czy działa sesja
  POST /api/start            — {account, flow} — start w subprocess
  POST /api/stop             — terminate
  POST /api/where {acc}      — cmd_where jednorazowo
  POST /api/recover {acc,t}  — cmd_recover
  GET  /api/events/stream    — SSE tail events.jsonl aktualnej sesji (+stdout)
  GET  /stats                — HTML panel statystyk sesji
  GET  /api/stats/{acc}      — live session_stats.json (+meta + account_stats)
  GET  /api/stats/{acc}/sessions — lista snapshotów sessions/*.json
  GET  /api/stats/{acc}/sessions/{sid} — jeden snapshot sesji (+ account_stats)
  GET  /api/stats/{acc}/aggregate?range=24h — suma counters/by_plugin + series
                                 (+ account_stats z events: pierwszy/ostatni odczyt)
  GET  /api/stats/{acc}/aggregate?from=&to= — suma w zakresie dat (YYYY-MM-DD)
"""
from __future__ import annotations
import asyncio
import json
import os
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
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

V2_ROOT     = Path(__file__).resolve().parent.parent
CONFIG_ROOT = V2_ROOT / "config"
FLOWS_DIR   = V2_ROOT / "flows"
DATA_ROOT   = V2_ROOT / "data"

app = FastAPI(title="Instagram Bot v2 — Dashboard")


# ============================================================
# session manager — trzyma aktywny subprocess python -m v2.cli start
# ============================================================
class SessionManager:
    # Ile ponad zarezerwowane okno bloku czekamy, zanim sami wyślemy STOP.
    # JobRunner sam pilnuje ramy, ale robi to MIĘDZY jednostkami pracy —
    # jedna rolka z AI i harvestem może się przeciągnąć. To jest siatka
    # bezpieczeństwa dla harmonogramu, nie podstawowy mechanizm.
    WATCHDOG_GRACE_S = 180

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.account: str | None = None
        self.flow:    str | None = None
        self.started_at: float | None = None
        self.stdout_path: Path | None = None
        self.stderr_path: Path | None = None
        self.stopping_since: float | None = None
        self.deadline: float | None = None      # unix ts końca okna bloku
        self._lock = threading.Lock()

    def start(self, account: str, flow: str | None = None,
              mode: str = "flow", **opts) -> dict:
        """mode='flow' → `cli start --flow=…`; mode='queue' → `cli run-queue`;
        mode='login' → `cli login` (krótki flow wpisania danych logowania).

        `max_duration_s` (tylko dla 'queue') to rama czasowa bloku
        harmonogramu — trafia do JobRunnera i uzbraja watchdoga."""
        with self._lock:
            if self.proc and self.proc.poll() is None:
                raise RuntimeError(f"sesja już działa: account={self.account} "
                                   f"tryb={self.flow}")
            # zaległy STOP z poprzedniego przebiegu ubiłby nowy od razu
            stop = DATA_ROOT / "accounts" / account / "STOP"
            if stop.exists():
                stop.unlink()
            log_dir = DATA_ROOT / "accounts" / account / "sessions"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.stdout_path = log_dir / f"{ts}-stdout.log"
            self.stderr_path = log_dir / f"{ts}-stderr.log"

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
            else:
                cmd = [sys.executable, "-u", "-m", "v2.cli", "start",
                       f"--account={account}", f"--flow={flow}"]

            self.proc = subprocess.Popen(
                cmd,
                cwd=str(V2_ROOT.parent),
                stdout=self.stdout_path.open("wb"),
                stderr=self.stderr_path.open("wb"),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=creationflags,
            )
            self.account = account
            if mode == "flow":
                self.flow = flow
            elif mode == "login":
                self.flow = "logowanie"
            elif opts.get("plugin_oneshot"):
                self.flow = f"one-shot [{opts['plugin_oneshot']}]"
            else:
                self.flow = f"kolejka [{opts['queue_name']}]" if opts.get("queue_name") else "kolejka"
            self.started_at = time.time()
            max_duration_s = opts.get("max_duration_s")
            self.deadline = (self.started_at + float(max_duration_s)) if max_duration_s else None
            if self.deadline:
                self._arm_watchdog(self.proc, account, self.deadline)
            return self.status()

    def _arm_watchdog(self, proc: subprocess.Popen, account: str, deadline: float):
        """Siatka bezpieczeństwa dla harmonogramu: gdy sesja przeżyje swoje
        okno o `WATCHDOG_GRACE_S`, zatrzymujemy ją sami, żeby nie weszła
        w blok następnego konta."""
        def _watch():
            timeout = max(deadline + self.WATCHDOG_GRACE_S - time.time(), 0)
            try:
                proc.wait(timeout=timeout)
                return          # skończyła się sama — nic do roboty
            except subprocess.TimeoutExpired:
                pass
            if self.proc is proc and proc.poll() is None:
                print(f"[watchdog] {account}: sesja przekroczyła okno bloku "
                      f"o {self.WATCHDOG_GRACE_S}s — wysyłam STOP")
                self.stop()

        threading.Thread(target=_watch, daemon=True).start()

    def stop(self, grace_s: int = 30) -> dict:
        """Miękkie zatrzymanie: plik STOP → plugin kończy bieżącą jednostkę
        i wychodzi czysto przez ekran wyjściowy. Twarde ubicie dopiero gdy
        proces nie zareaguje — inaczej telefon zostaje w środku sheetu."""
        with self._lock:
            if not self.proc or self.proc.poll() is not None:
                return self.status()

            # snapshot: wątek ubijający nie może sięgnąć po `self.proc`, bo
            # do czasu jego wybudzenia może tam być już NASTĘPNA sesja
            proc, account = self.proc, self.account
            if account:
                stop = DATA_ROOT / "accounts" / account / "STOP"
                stop.parent.mkdir(parents=True, exist_ok=True)
                stop.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
            self.stopping_since = time.time()

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
                        try: proc.kill()
                        except Exception: pass
                finally:
                    if self.proc is proc:
                        self.stopping_since = None
                    self._clear_stop_file(account)

            threading.Thread(target=_hard_kill_later, daemon=True).start()
            return self.status()

    def force_stop(self, wait_s: float = 3.0) -> None:
        """Natychmiastowe zatrzymanie sesji przy wyjściu panelu.

        `stop()` odpala daemon-thread z grace — przy exit procesu wątek
        znika, a child (CREATE_NEW_PROCESS_GROUP) zostaje orphanem.
        Tu ubijamy synchronicznie, żeby port 8765 i child nie wisiały."""
        with self._lock:
            if not self.proc or self.proc.poll() is not None:
                self.stopping_since = None
                return
            proc, account = self.proc, self.account
            if account:
                stop = DATA_ROOT / "accounts" / account / "STOP"
                try:
                    stop.parent.mkdir(parents=True, exist_ok=True)
                    stop.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
                except OSError:
                    pass
            self.stopping_since = time.time()
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
            self.stopping_since = None
            self._clear_stop_file(account)

    def _clear_stop_file(self, account: str | None = None):
        account = account or self.account
        if not account:
            return
        p = DATA_ROOT / "accounts" / account / "STOP"
        if p.exists():
            try: p.unlink()
            except OSError: pass

    def status(self) -> dict:
        running = bool(self.proc and self.proc.poll() is None)
        return {
            "running":   running,
            "account":   self.account,
            "flow":      self.flow,
            "pid":       self.proc.pid if self.proc else None,
            "since":     self.started_at,
            "stopping":  self.stopping_since is not None,
            "deadline":  self.deadline if running else None,
            "stdout":    str(self.stdout_path) if self.stdout_path else None,
            "returncode": None if running else (self.proc.returncode if self.proc else None),
        }

    def active_events_path(self) -> Path | None:
        if not self.account:
            return None
        return DATA_ROOT / "accounts" / self.account / "events.jsonl"


SESSION = SessionManager()

# Flaga shutdown — SSE / cleanup wychodzą z pętli zamiast trzymać port
_panel_shutting_down = False


def _deny_if_scheduler_enabled():
    """Włączony harmonogram = telefonem dysponuje plan, nie przyciski.

    Bez tej blokady ręczny start mógł zająć urządzenie tuż przed slotem
    i przepalić zaplanowaną sesję (blok trafiał na `device_busy`). 423 Locked,
    bo zasób istnieje i jest sprawny, tylko chwilowo nie należy do użytkownika.
    """
    from v2.scheduler.state import is_enabled
    if is_enabled(DATA_ROOT):
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


@app.get("/api/devices")
def list_devices():
    """Urządzenia + klony IG na każdym, z oznaczeniem które klony są już
    przypisane do konta — formularz tworzenia profilu wybiera z tej listy."""
    claimed = _claimed_clone_packages()
    out = []
    for p in sorted((CONFIG_ROOT / "devices").glob("*.json")):
        try:
            dev = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        alias = dev.get("alias", p.stem)
        clones = []
        for c in dev.get("clones") or []:
            clones.append({**c, "claimed_by": claimed.get(alias, {}).get(c.get("package"))})
        out.append({
            "alias": alias, "model": dev.get("model"), "adb_id": dev.get("adb_id"),
            "manufacturer": dev.get("manufacturer"), "clones": clones,
        })
    return out


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
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return {"ok": True, "alias": alias, "path": str(path.relative_to(CONFIG_ROOT))}


def _ensure_clone_on_device(device_alias: str, package: str) -> None:
    """Dopisz package do devices/<alias>.json → clones, jeśli jeszcze go nie ma.

    Panel pozwala wpisać APPID ręcznie — lista klonów urządzenia zostaje
    podpowiedzią (hint), nie bramką walidacji.
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
    if any(c.get("package") == package for c in clones):
        return
    next_slot = max((int(c.get("slot") or 0) for c in clones), default=0) + 1
    clones.append({"slot": next_slot, "package": package, "note": "dodany z panelu"})
    dev["clones"] = clones
    dev_path.write_text(json.dumps(dev, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")


@app.post("/api/accounts")
async def create_account(request: Request):
    body = await request.json()
    alias   = (body.get("alias") or "").strip()
    ig_user = (body.get("ig_username") or "").strip()
    device_alias = (body.get("device_alias") or "").strip()
    app_clone_package = (body.get("app_clone_package") or "").strip()
    if not alias or not ig_user or not device_alias or not app_clone_package:
        raise HTTPException(400, "wymagane: alias, ig_username, device_alias, app_clone_package")

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


@app.delete("/api/accounts/{account}")
def delete_account(account: str):
    """Usuń konto z panelu — config + credentials; data ostrożnie.

    Bezpieczeństwo danych:
    - NIE kasujemy ślepo całego `data/accounts/<alias>/` (sesje, logi, raw).
    - Preferencja: usuń config (`*.json` + `.bak` + katalog `.queues`),
      a folder data przenieś na `data/accounts/<alias>.deleted-<ts>` (trash).
    - Jeśli rename się nie uda — zostaw data na miejscu i zgłoś w odpowiedzi
      (`data_kept=True`), żeby operator mógł posprzątać ręcznie.
    - Aktywna sesja tego konta → 409 (najpierw Stop).
    """
    alias = _safe_account_alias(account)
    cfg_path = CONFIG_ROOT / "accounts" / f"{alias}.json"
    if not cfg_path.exists():
        raise HTTPException(404, f"konto nie istnieje: {alias}")

    # Zablokuj kasowanie gdy sesja leci na tym koncie — unikamy crasha / orphanów
    st = SESSION.status()
    if st.get("running") and st.get("account") == alias:
        raise HTTPException(
            409,
            f"konto '{alias}' ma aktywną sesję — zatrzymaj ją (Stop) przed usunięciem",
        )

    removed = {"config": False, "config_bak": False, "queues": False,
               "legacy_queue": False, "credentials": False, "data": None}
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

    return {
        "ok": True,
        "alias": alias,
        "removed": removed,
        "data_kept": data_kept,
        "data_trash": data_trash,
        "timeline_purge": timeline_purge,
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
    credentials.set(alias, ig_user, body.get("ig_password"))
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
    if not isinstance(body, dict) or "anthropic_api_key" not in body:
        raise HTTPException(400, "wymagane pole anthropic_api_key (puste = wyczyść)")
    from v2.config.secrets import set_api_key, status
    set_api_key(body["anthropic_api_key"])
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


# ============================================================
# API — filtr profili (HTTP / proxy, bez telefonu)
# ============================================================
_filter_job_lock = threading.Lock()
_filter_job: dict = {
    "running": False,
    "account": None,
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
    "stdout_path": None,
    "stderr_path": None,
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


def _filter_job_status() -> dict:
    # Compact: ostatnie 8 linii; full: całość do scrollowanego panelu UI
    st = dict(_filter_job)
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
            "account": account,
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
            "stdout_path": None,
            "stderr_path": None,
        })

    def _worker():
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
        try:
            with out_path.open("wb") as out_f, err_path.open("wb") as err_f:
                proc = subprocess.run(
                    cmd,
                    cwd=str(V2_ROOT.parent),
                    stdout=out_f,
                    stderr=err_f,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
            from v2.profile_filter.pipeline import read_last_run
            last = read_last_run(account, DATA_ROOT)
            with _filter_job_lock:
                _filter_job.update({
                    "running": False,
                    "finished_at": time.time(),
                    "result": last,
                    "error": None if proc.returncode == 0
                    else f"exit={proc.returncode}",
                    "stdout_path": str(out_path),
                    "stderr_path": str(err_path),
                })
        except Exception as e:
            with _filter_job_lock:
                _filter_job.update({
                    "running": False,
                    "finished_at": time.time(),
                    "error": str(e),
                })

    threading.Thread(target=_worker, name=f"filter-{account}",
                     daemon=True).start()
    return JSONResponse({"ok": True, "accepted": True, "account": account,
                         "job": _filter_job_status()}, status_code=202)


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
    _deny_if_scheduler_enabled()
    body = await request.json()
    acc = body.get("account")
    queue_name = body.get("queue_name")
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
    _deny_if_scheduler_enabled()
    body = await request.json()
    acc, pid = body.get("account"), body.get("plugin")
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
    _deny_if_scheduler_enabled()
    body = await request.json()
    acc = body.get("account"); flow = body.get("flow")
    if not acc or not flow:
        raise HTTPException(400, "account + flow wymagane")
    try:
        return SESSION.start(acc, flow)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/stop")
def api_stop():
    return SESSION.stop()


@app.post("/api/login")
async def api_login(request: Request):
    """Krótki flow: wake → unlock → uruchom klon → wpisz login/hasło z
    config/credentials.py. 2FA/potwierdzenia kończysz ręcznie na telefonie —
    obserwuj przez /api/events/stream, potem POST /api/where żeby sprawdzić,
    czy dotarliśmy do home_feed."""
    _deny_if_scheduler_enabled()
    body = await request.json()
    acc = body.get("account")
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


def _deny_if_session_running():
    """Diagnostyka odpala drugi proces CLI na tym samym `adb_id` — równolegle
    z sesją oznacza dwa procesy walczące o hierarchy i tapy."""
    if SESSION.status()["running"]:
        raise HTTPException(409, "sesja w toku — diagnostyka zakłóciłaby ją; "
                                 "zatrzymaj sesję albo poczekaj")


@app.post("/api/where")
async def api_where(request: Request):
    _deny_if_session_running()
    body = await request.json()
    acc = body.get("account")
    if not acc: raise HTTPException(400, "account wymagane")
    out = _run_cli_sync(["where", f"--account={acc}"], timeout=15)
    return {"output": out}


@app.post("/api/recover")
async def api_recover(request: Request):
    _deny_if_session_running()
    body = await request.json()
    acc = body.get("account"); target = body.get("target", "home_feed")
    if not acc: raise HTTPException(400, "account wymagane")
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
    porównanie z limitami dziennymi konta (`utilization`).

    `eta` — czas na te akcje z historycznego tempa sesji (sec/like, sec/follow).
    """
    body = await request.json()
    acc = body.get("account")
    if not acc:
        raise HTTPException(400, "account wymagane")
    from v2.scheduler.action_estimator import (estimate_queue_actions,
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
        est["limits"] = limits
        est["utilization"] = utilization_vs_limits(est.get("actions"), limits)
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
async def stream_events(account: str):
    """Server-Sent Events z tail -f na events.jsonl konta (+bieżący stdout sesji)."""
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

            # stdout aktywnej sesji
            st = SESSION.status()
            if st["running"] and SESSION.stdout_path and SESSION.stdout_path.exists():
                sp = SESSION.stdout_path
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
        "session_running": bool(
            SESSION.status()["running"] and SESSION.status()["account"] == account
        ),
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
    """Suma `counters` + `by_plugin` z sesji w oknie czasowym.

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
    pilnowania. `SESSION` daje blokadę „jedno urządzenie = jedna aktywna
    sesja"; plan dba o to, żeby bloki w ogóle na siebie nie nachodziły.

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
                is_busy=lambda: SESSION.status()["running"],
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


def _cleanup_panel_process():
    """atexit / finally — ostatnia linia obrony gdy sygnał ominie lifespan."""
    global _panel_shutting_down
    _panel_shutting_down = True
    try:
        SESSION.force_stop()
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
