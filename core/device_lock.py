"""Blokada urządzenia (adb serial) — jeden proces bot na telefon.

Live (2026-08): dwa `cli run-queue` na tym samym a50_clone3
(session 141549 + 143100) wpisywały równolegle `nutrition coaching` i
`polish beauty` w to samo pole → garbled `isnhu tbreiatuitoyn`.

SessionManager w panelu trzyma tylko *jeden* Popen w RAM — restart panelu,
równoległy `cli` albo CLI+scheduler omijają tę barierę. Tu: plikowy lock
po `adb_id`, trzymany przez cały proces (atexit + release).
"""
from __future__ import annotations

import atexit
import json
import os
import sys
import time
from pathlib import Path


class DeviceBusy(RuntimeError):
    """Inna sesja trzyma telefon — nie wolno startować drugiej."""


class DeviceLock:
    """Exclusive lock na `data/locks/device_<serial>.lock`."""

    def __init__(self, data_root: Path, serial: str):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in serial)
        self.path = Path(data_root) / "locks" / f"device_{safe}.lock"
        self.serial = serial
        self._fh = None
        self._registered = False

    def acquire(self, *, owner: str, session_id: str,
                timeout_s: float = 0.0) -> None:
        """Zajmij urządzenie. `timeout_s=0` = fail od razu gdy zajęte."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        last_err: Exception | None = None
        while True:
            try:
                self._try_lock(owner=owner, session_id=session_id)
                return
            except DeviceBusy as e:
                last_err = e
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        if last_err:
            raise last_err

    def _try_lock(self, *, owner: str, session_id: str) -> None:
        # 'a+' żeby plik istniał; potem trunc + zapis metadanych
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            fh.seek(0)
            _lock_file(fh)
        except OSError:
            # zajęte — odczytaj kto trzyma (best-effort)
            holder = _read_holder(self.path)
            fh.close()
            raise DeviceBusy(
                f"urządzenie {self.serial} zajęte przez inną sesję "
                f"(holder={holder!r}) — nie uruchamiaj dwóch jobów na jednym "
                f"telefonie (live: przeplatane input text)"
            ) from None

        meta = {
            "serial": self.serial,
            "pid": os.getpid(),
            "owner": owner,
            "session_id": session_id,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        fh.flush()
        self._fh = fh
        if not self._registered:
            atexit.register(self.release)
            self._registered = True

    def release(self) -> None:
        fh = self._fh
        self._fh = None
        if not fh:
            return
        try:
            _unlock_file(fh)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass
        # nie usuwaj pliku — na Win plik zablokowany może zostać; treść i tak
        # nieważna gdy nikt nie trzyma flocka


def _lock_file(fh) -> None:
    """Non-blocking exclusive lock (Win: msvcrt, Unix: fcntl)."""
    if sys.platform == "win32":
        import msvcrt
        # locking działa od bieżącej pozycji — zajmij 1 bajt
        fh.seek(0)
        if fh.read(1) == "":
            fh.write("\0")
            fh.flush()
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fh) -> None:
    if sys.platform == "win32":
        import msvcrt
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _read_holder(path: Path) -> dict | str:
    try:
        raw = path.read_text(encoding="utf-8").strip().splitlines()
        if not raw:
            return "?"
        return json.loads(raw[0])
    except Exception:
        return "?"
