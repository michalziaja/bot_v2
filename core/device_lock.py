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
from typing import Callable


# Komentarz: restore MAC przy puszczeniu locka (koniec sesji / atexit)
_release_callbacks: list[Callable[[str], None]] = []


def add_release_callback(cb: Callable[[str], None]) -> None:
    """Zarejestruj hak wołany z DeviceLock.release(serial)."""
    if cb not in _release_callbacks:
        _release_callbacks.append(cb)


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
        for cb in list(_release_callbacks):
            try:
                cb(self.serial)
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


def _lock_path(data_root: Path, serial: str) -> Path:
    """Ta sama ścieżka co DeviceLock — bez otwierania deskryptora."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in serial)
    return Path(data_root) / "locks" / f"device_{safe}.lock"


def _proc_locks_holds(path: Path) -> bool | None:
    """Czy /proc/locks pokazuje exclusive lock na tym inode.

    None = /proc niedostępny (nie zgadujemy). Nie wołamy flock —
    drugi deskryptor w tym samym procesie mógłby puścić lock holdera.
    """
    try:
        st = path.stat()
        raw = Path("/proc/locks").read_text(encoding="utf-8")
    except OSError:
        return None
    ino = str(st.st_ino)
    maj, mino = os.major(st.st_dev), os.minor(st.st_dev)
    # kernel bywa z/bez wiodącego zera w major:minor
    tokens = {
        f"{maj:x}:{mino:x}:{ino}",
        f"{maj:02x}:{mino:02x}:{ino}",
    }
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        if parts[5] in tokens or parts[5].endswith(":" + ino):
            return True
    return False


def _pid_alive(pid: int) -> bool:
    """Czy proces jeszcze żyje (signal 0) — fallback gdy brak /proc/locks."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def peek_device_lock(data_root: Path, serial: str) -> dict | None:
    """Holder flocka albo None gdy serial wolny.

    Plik `device_<serial>.lock` zostaje po release — samo istnienie
    nie znaczy, że telefon jest zajęty. Najpierw /proc/locks, inaczej
    PID z metadanych (mniej pewne po reuse PID).
    """
    if not serial:
        return None
    path = _lock_path(data_root, serial)
    if not path.exists():
        return None
    held = _proc_locks_holds(path)
    holder = _read_holder(path)
    meta = holder if isinstance(holder, dict) else {"owner": str(holder)}
    if held is True:
        return meta
    if held is False:
        return None
    # bez /proc — best-effort: żywy PID z pliku
    if _pid_alive(int(meta.get("pid") or 0)):
        return meta
    return None
