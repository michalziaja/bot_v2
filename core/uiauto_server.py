"""Zarządzanie subprocessem `python -m uiautodev`.

Autostart przy uruchomieniu bota + graceful shutdown przy zakończeniu.
Jeśli serwer już działa (np. odpalony ręcznie w innym terminalu), NIE ubijamy
go przy exit — user może z niego korzystać.
"""
from __future__ import annotations
import atexit
import signal
import subprocess
import sys
import time
import urllib.request

_STARTED_PROC: subprocess.Popen | None = None  # None gdy nie my odpaliliśmy


def _is_up(base_url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/info", timeout=timeout) as r:
            r.read()
        return True
    except Exception:
        return False


def ensure_running(base_url: str = "http://127.0.0.1:20242",
                   max_wait_s: float = 12.0,
                   quiet: bool = False) -> None:
    """Zapewnij że uiautodev działa. Jeśli już wstał — nic nie rób.
    Jeśli nie — odpal jako subprocess, poczekaj aż odpowie."""
    global _STARTED_PROC
    if _is_up(base_url):
        if not quiet:
            print("[uiauto] serwer już działa — używam istniejącego")
        return

    if not quiet:
        print("[uiauto] uruchamiam `python -m uiautodev` w tle...")

    # Windows: bez okienka konsoli
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    _STARTED_PROC = subprocess.Popen(
        [sys.executable, "-m", "uiautodev", "server", "--no-browser", "--offline"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
                print(f"[uiauto] gotowy po {dt:.1f}s")
            return
        # sprawdź czy subprocess nie padł
        if _STARTED_PROC.poll() is not None:
            code = _STARTED_PROC.returncode
            _STARTED_PROC = None
            raise SystemExit(f"[uiauto] proces zakończył się z kodem {code} przed startem")
        time.sleep(0.4)

    # timeout — sprzątamy
    _shutdown()
    raise SystemExit(f"[uiauto] serwer nie wstał w {max_wait_s}s — sprawdź `python -m uiautodev` ręcznie")


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
