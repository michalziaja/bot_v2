"""Zarządzanie subprocessem `python -m uiautodev`.

Autostart przy uruchomieniu bota + graceful shutdown przy zakończeniu.
Jeśli serwer już działa (np. odpalony ręcznie w innym terminalu), NIE ubijamy
go przy exit — user może z niego korzystać.

Panel bywa odpalany systemowym `python` (bez pakietu uiautodev). Wtedy
subprocess musi iść z `.venv` projektu — inaczej pada z kodem 1
(`No module named uiautodev`) i logi sesji są puste, bo stderr szedł do DEVNULL.
"""
from __future__ import annotations
import atexit
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

_STARTED_PROC: subprocess.Popen | None = None  # None gdy nie my odpaliliśmy
_ERR_PATH: str | None = None

_V2_ROOT = Path(__file__).resolve().parent.parent
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _is_up(base_url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/info", timeout=timeout) as r:
            r.read()
        return True
    except Exception:
        return False


def resolve_uiauto_python() -> Path:
    """Python, którym da się odpalić `python -m uiautodev`.

    Kolejność: venv projektu (Linux `bin/` / Windows `Scripts/`), potem
    `sys.executable` jeśli sam ma pakiet.
    """
    names = ("python", "python.exe")
    candidates: list[Path] = []
    for sub in ("bin", "Scripts"):
        for name in names:
            p = _V2_ROOT / ".venv" / sub / name
            if p.is_file():
                candidates.append(p)
    exe = Path(sys.executable)
    if exe not in candidates:
        candidates.append(exe)

    last_err = ""
    for p in candidates:
        if _has_module(p, "uiautodev"):
            return p
        last_err = str(p)
    raise SystemExit(
        "[uiauto] brak pakietu uiautodev — zainstaluj w .venv projektu "
        f"(sprawdzane: {', '.join(str(c) for c in candidates)}; last={last_err})"
    )


def _has_module(python: Path, mod: str) -> bool:
    try:
        r = subprocess.run(
            [str(python), "-c", f"import {mod}"],
            capture_output=True,
            timeout=8,
            creationflags=_NO_WINDOW,
        )
        return r.returncode == 0
    except Exception:
        return False


def ensure_running(base_url: str = "http://127.0.0.1:20242",
                   max_wait_s: float = 12.0,
                   quiet: bool = False) -> None:
    """Zapewnij że uiautodev działa. Jeśli już wstał — nic nie rób.
    Jeśli nie — odpal jako subprocess, poczekaj aż odpowie."""
    global _STARTED_PROC, _ERR_PATH
    if _is_up(base_url):
        if not quiet:
            print("[uiauto] serwer już działa — używam istniejącego")
        return

    py = resolve_uiauto_python()
    if not quiet:
        print(f"[uiauto] uruchamiam `{py} -m uiautodev` w tle...")

    creationflags = _NO_WINDOW

    err = tempfile.NamedTemporaryFile(
        prefix="uiauto_", suffix=".err", delete=False)
    _ERR_PATH = err.name
    err.close()
    err_fh = open(_ERR_PATH, "wb")

    _STARTED_PROC = subprocess.Popen(
        [str(py), "-m", "uiautodev", "server", "--no-browser", "--offline"],
        stdout=subprocess.DEVNULL,
        stderr=err_fh,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    # atexit + sygnały — żeby subprocess zawsze umarł razem z nami
    atexit.register(_shutdown)
    _install_signal_handlers()

    # poll aż wstanie
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        if _is_up(base_url):
            if not quiet:
                dt = max_wait_s - (deadline - time.monotonic())
                print(f"[uiauto] gotowy po {dt:.1f}s  python={py}")
            return
        # sprawdź czy subprocess nie padł
        if _STARTED_PROC.poll() is not None:
            code = _STARTED_PROC.returncode
            _STARTED_PROC = None
            raise SystemExit(
                f"[uiauto] proces zakończył się z kodem {code} przed startem"
                f" (python={py}){_err_tail()}"
            )
        time.sleep(0.4)

    # timeout — sprzątamy
    _shutdown()
    raise SystemExit(
        f"[uiauto] serwer nie wstał w {max_wait_s}s — python={py}"
        f"{_err_tail()} — spróbuj `{py} -m uiautodev` ręcznie"
    )


def _err_tail(limit: int = 800) -> str:
    """Doklej koniec stderr subprocessu, żeby w logu sesji było widać dlaczego padł."""
    if not _ERR_PATH:
        return ""
    try:
        text = Path(_ERR_PATH).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    if len(text) > limit:
        text = "…" + text[-limit:]
    return f"\n{text}"


def _shutdown() -> None:
    """Kończy subprocess (jeśli my go odpaliliśmy). Idempotentne."""
    global _STARTED_PROC
    p = _STARTED_PROC
    if p is None:
        return
    _STARTED_PROC = None
    try:
        if p.poll() is None:   # jeszcze żyje
            p.terminate()
            try:
                p.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                p.kill()
    except Exception:
        pass


def _install_signal_handlers() -> None:
    """Ctrl+C / SIGTERM — czysto ubić subprocess przed exit."""
    def _handler(signum, frame):
        _shutdown()
        # restore default i wywołaj — pozwala Python normalnie zakończyć
        signal.signal(signum, signal.SIG_DFL)
        raise KeyboardInterrupt() if signum == signal.SIGINT else SystemExit(128 + signum)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass  # nie działa w niektórych kontekstach (wątki)
