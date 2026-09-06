"""Job generate → build → install klona IG (panel + CLI)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from v2.cloner.identity import generate_clone_identity
from v2.core.device_lock import DeviceBusy, DeviceLock

# Komentarz: callbacki testów — bez telefonu / bez apktool
AdbFn = Callable[..., str]
LogFn = Callable[[str], None]


class CloneJobError(RuntimeError):
    """Błąd tworzenia klona (brak APK, adb, lock, appcloner)."""


class CloneBusy(CloneJobError):
    """Inny job klona albo sesja bota trzyma telefon."""


_job_lock = threading.Lock()
_job: dict = {
    "running": False,
    "device_alias": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "state": "idle",  # idle | running | ok | error
    "package": None,
    "identity": None,
    "log_path": None,
    "apk": None,
}
_job_proc: subprocess.Popen | None = None
_job_cancel = threading.Event()


def appcloner_root() -> Path:
    """Katalog repo appcloner: APPCLONER_ROOT albo sibling `../appcloner`."""
    env = (os.environ.get("APPCLONER_ROOT") or "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if not p.is_dir():
            raise CloneJobError(f"APPCLONER_ROOT nie istnieje: {p}")
        return p
    # cloner/job.py → bot_v2 → repos/appcloner
    sibling = Path(__file__).resolve().parents[2] / "appcloner"
    if sibling.is_dir():
        return sibling
    raise CloneJobError(
        "nie znaleziono appcloner — ustaw APPCLONER_ROOT albo trzymaj repo "
        "obok bot_v2 (../appcloner)"
    )


def clone_script(root: Path | None = None) -> Path:
    """Ścieżka do clone_instagram.py."""
    r = root or appcloner_root()
    p = r / "clone_instagram.py"
    if not p.is_file():
        raise CloneJobError(f"brak clone_instagram.py w {r}")
    return p


def require_source_apk(root: Path | None = None) -> Path:
    """Bazowy instagram.apk musi leżeć w appcloner (gitignore)."""
    r = root or appcloner_root()
    apk = r / "instagram.apk"
    if not apk.is_file():
        raise CloneJobError(f"brak {apk} — włóż bazowy APK Instagrama do appcloner/")
    return apk


def require_jdk() -> str:
    """JDK w PATH — apktool i uber-apk-signer idą przez `java -jar`."""
    from shutil import which
    found = which("java")
    if not found:
        raise CloneJobError("brak JDK — `java` nie jest w PATH")
    return found


def device_is_busy(data_root: Path, serial: str) -> bool:
    """True gdy inna sesja trzyma DeviceLock (bot na tym adb_id)."""
    lock = DeviceLock(data_root, serial)
    try:
        lock.acquire(owner="clone-preflight", session_id="probe")
        lock.release()
        return False
    except DeviceBusy:
        return True


def preflight_create(
    device_alias: str,
    *,
    config_root: Path,
    data_root: Path,
) -> dict:
    """Walidacja przed 202: device, adb_id, lock, instagram.apk, JDK.

    Rzucane CloneBusy / CloneJobError → HTTP 409 w panelu.
    """
    _path, dev = load_device(config_root, device_alias)
    serial = (dev.get("adb_id") or "").strip()
    if not serial or serial == "REPLACE":
        raise CloneJobError(f"device:{device_alias}: brak adb_id")
    if device_is_busy(data_root, serial):
        raise CloneBusy(
            f"urządzenie {serial} zajęte przez sesję bota — Stop przed klonem")
    root = appcloner_root()
    require_source_apk(root)
    require_jdk()
    return {"serial": serial, "appcloner": str(root)}


def job_snapshot() -> dict:
    """Status bez Popen — do GET /api/clones/status."""
    with _job_lock:
        st = {k: v for k, v in _job.items()}
    path = st.get("log_path")
    st["log_tail"] = _tail(path, 24)
    st["log"] = _read(path)
    st["running"] = bool(st.get("running"))
    return st


def _tail(path: Optional[str], n: int = 24) -> list[str]:
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


def _read(path: Optional[str], max_chars: int = 400_000) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:] if len(text) > max_chars else text


def _log(log: LogFn | None, msg: str) -> None:
    line = f"[clone-job] {msg}"
    if log:
        log(line)
    else:
        print(line, flush=True)


def default_adb(serial: str, args: list[str], timeout: float = 180) -> str:
    """Surowy adb -s SERIAL … (install / pm list)."""
    r = subprocess.run(
        ["adb", "-s", serial, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        raise CloneJobError(f"adb {' '.join(args)} exit={r.returncode}: {out}")
    return out


def list_installed_packages(serial: str, adb: AdbFn = default_adb) -> set[str]:
    """package:com.foo z `pm list packages` — pusta gdy adb padnie."""
    try:
        out = adb(serial, ["shell", "pm", "list", "packages"])
    except Exception:
        return set()
    found: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            found.add(line.split(":", 1)[1].strip())
    return found


def build_clone_argv(identity: dict, *, script: Path, force: bool = True) -> list[str]:
    """Argv python clone_instagram.py --build --sign …"""
    cmd = [
        sys.executable, "-u", str(script),
        "--package", identity["package"],
        "--android-id", identity["android_id"],
        "--fingerprint", identity["fingerprint"],
        "--model", identity.get("model") or "Samsung Galaxy A50",
        "--gaid", identity.get("gaid") or "",
        "--mac", identity.get("mac") or "",
        "--imei", identity.get("imei") or "",
        "--build", "--sign",
    ]
    if force:
        cmd.append("--force")
    return cmd


def run_appcloner_build(
    identity: dict,
    *,
    script: Path,
    log_file,
    cancel: threading.Event | None = None,
    popen_factory=subprocess.Popen,
) -> None:
    """Subprocess clone_instagram.py; stdout/err → log_file."""
    global _job_proc
    cmd = build_clone_argv(identity, script=script)
    proc = popen_factory(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(script.parent),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    _job_proc = proc
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise CloneJobError("anulowano")
            time.sleep(0.25)
        if proc.returncode != 0:
            raise CloneJobError(f"appcloner exit={proc.returncode}")
    finally:
        _job_proc = None


def signed_apk_path(appcloner: Path, package: str) -> Path:
    """clone/<APPID>/<APPID>.apk po --sign."""
    return appcloner / "clone" / package / f"{package}.apk"


_PKG_SAFE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


def install_apk(serial: str, apk: Path, adb: AdbFn = default_adb) -> None:
    """adb install -r na telefon."""
    if not apk.is_file():
        raise CloneJobError(f"brak podpisanego APK: {apk}")
    adb(serial, ["install", "-r", str(apk)])


def append_clone_record(
    device: dict,
    *,
    package: str,
    identity: dict,
    note: str = "z panelu",
) -> dict:
    """Dopisz / zaktualizuj clones[] na kopii device dict."""
    clones = list(device.get("clones") or [])
    for c in clones:
        if c.get("package") == package:
            c["identity"] = identity
            c["note"] = note
            device["clones"] = clones
            return device
    next_slot = max((int(c.get("slot") or 0) for c in clones), default=0) + 1
    clones.append({
        "slot": next_slot,
        "package": package,
        "note": note,
        "identity": identity,
    })
    device["clones"] = clones
    return device


def save_device(path: Path, device: dict) -> None:
    """Zapis devices/<alias>.json."""
    path.write_text(json.dumps(device, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def load_device(config_root: Path, alias: str) -> tuple[Path, dict]:
    path = Path(config_root) / "devices" / f"{alias}.json"
    if not path.exists():
        raise CloneJobError(f"urządzenie '{alias}' nie istnieje")
    try:
        dev = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise CloneJobError(f"nieczytelny device JSON: {e}") from e
    return path, dev


def spoof_fields_from_identity(identity: dict) -> dict:
    """Pola account.device.spoofed_* kopiowane przy tworzeniu konta."""
    return {
        "spoofed_mac": identity.get("mac") or None,
        "spoofed_imei": identity.get("imei") or None,
        "spoofed_android_id": identity.get("android_id") or None,
    }


def identity_for_package(device: dict, package: str) -> dict | None:
    """identity z clones[] dla APPID albo None."""
    for c in device.get("clones") or []:
        if c.get("package") == package and isinstance(c.get("identity"), dict):
            return c["identity"]
    return None


def create_clone(
    device_alias: str,
    *,
    config_root: Path,
    data_root: Path,
    appcloner: Path | None = None,
    adb: AdbFn = default_adb,
    list_packages: Callable[[str], set[str]] | None = None,
    run_build: Callable | None = None,
    acquire_lock: bool = True,
    log: LogFn | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    """Pełny przebieg: generate → appcloner → adb install → zapis clones[].

    Zwraca identity + apk. Testy podmieniają adb / run_build."""
    path, dev = load_device(config_root, device_alias)
    serial = (dev.get("adb_id") or "").strip()
    if not serial or serial == "REPLACE":
        raise CloneJobError(f"device:{device_alias}: brak adb_id")

    # Komentarz: root musi wskazywać appcloner nawet gdy test/panel podmienia
    # run_build — inaczej install szuka APK w cwd (./clone/…) zamiast clone/<APPID>/
    root = appcloner or appcloner_root()
    script = None
    if run_build is None:
        script = clone_script(root)
        require_source_apk(root)
        require_jdk()

    lock = None
    if acquire_lock:
        lock = DeviceLock(data_root, serial)
        try:
            lock.acquire(owner=f"clone:{device_alias}", session_id="clone-app")
        except DeviceBusy as e:
            raise CloneBusy(str(e)) from e

    try:
        extra: set[str] = set()
        lister = list_packages or (lambda s: list_installed_packages(s, adb=adb))
        extra = lister(serial)
        identity = generate_clone_identity(dev, extra_packages=extra)
        pkg = identity["package"]
        if not _PKG_SAFE.fullmatch(pkg):
            raise CloneJobError(f"zły APPID: {pkg}")
        _log(log, f"tożsamość: {pkg} mac={identity['mac']} imei={identity['imei']}")

        def _default_build(ident, logf) -> None:
            # Komentarz: panel przekazuje otwarty log; CLI pisze na stdout
            target = logf if logf is not None else sys.stdout
            run_appcloner_build(
                ident, script=script, log_file=target, cancel=cancel)

        builder = run_build or _default_build
        builder(identity, None)

        apk = signed_apk_path(root, pkg)
        _log(log, f"instaluję {apk} na {serial}")
        if cancel is not None and cancel.is_set():
            raise CloneJobError("anulowano")
        install_apk(serial, apk, adb=adb)

        # odśwież JSON — mogło się zmienić w międzyczasie
        path, dev = load_device(config_root, device_alias)
        slim_ident = {
            "package": identity["package"],
            "android_id": identity["android_id"],
            "gaid": identity.get("gaid"),
            "fingerprint": identity["fingerprint"],
            "mac": identity["mac"],
            "imei": identity["imei"],
            "model": identity.get("model"),
        }
        append_clone_record(dev, package=pkg, identity=slim_ident, note="z panelu")
        save_device(path, dev)
        _log(log, f"zapisano clones[] + {pkg}")
        return {"identity": slim_ident, "apk": str(apk), "device_alias": device_alias}
    finally:
        if lock is not None:
            lock.release()


def request_cancel() -> bool:
    """Ustaw flagę cancel i terminate subprocess appclonera."""
    _job_cancel.set()
    proc = _job_proc
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
        return True
    return bool(_job.get("running"))


def start_job_thread(
    device_alias: str,
    *,
    config_root: Path,
    data_root: Path,
) -> dict:
    """Start w tle — 202 z panelu.  CloneBusy jeśli już leci."""
    with _job_lock:
        if _job.get("running"):
            raise CloneBusy(
                f"klonowanie już trwa: device={_job.get('device_alias')}")
    # Komentarz: preflight PRZED running=True — 409 zamiast joba w error
    preflight_create(
        device_alias, config_root=config_root, data_root=data_root)
    with _job_lock:
        if _job.get("running"):
            raise CloneBusy(
                f"klonowanie już trwa: device={_job.get('device_alias')}")
        _job_cancel.clear()
        log_dir = Path(data_root) / "clones"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"{ts}-{device_alias}.log"
        _job.update({
            "running": True,
            "device_alias": device_alias,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "state": "running",
            "package": None,
            "identity": None,
            "log_path": str(log_path),
            "apk": None,
        })

    def _worker():
        try:
            with log_path.open("w", encoding="utf-8") as fh:
                def log_line(msg: str) -> None:
                    fh.write(msg.rstrip() + "\n")
                    fh.flush()

                ac_root = appcloner_root()

                def run_build(ident, _unused):
                    run_appcloner_build(
                        ident, script=clone_script(ac_root),
                        log_file=fh, cancel=_job_cancel)

                result = create_clone(
                    device_alias,
                    config_root=config_root,
                    data_root=data_root,
                    appcloner=ac_root,
                    run_build=run_build,
                    log=log_line,
                    cancel=_job_cancel,
                )
            ident = result["identity"]
            with _job_lock:
                _job.update({
                    "running": False,
                    "finished_at": time.time(),
                    "error": None,
                    "state": "ok",
                    "package": ident.get("package"),
                    "identity": ident,
                    "apk": result.get("apk"),
                })
        except Exception as e:
            with _job_lock:
                _job.update({
                    "running": False,
                    "finished_at": time.time(),
                    "error": str(e),
                    "state": "error",
                })
            try:
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"[clone-job] BŁĄD: {e}\n")
            except OSError:
                pass

    threading.Thread(target=_worker, name=f"clone-{device_alias}",
                     daemon=True).start()
    return job_snapshot()
