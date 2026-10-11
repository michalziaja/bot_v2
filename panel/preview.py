"""Podgląd ekranu telefonu dla panelu — JPEG ~480 px z klatki sesji.

Bot (CLI) dekoduje scrcpy do `FrameBuffer` i zapisuje
`data/devices/<alias>/preview.jpg` (atomowo: tmp + rename). Cadence JPEG
i poll panelu biorą się z `scrcpy.max_fps` (klamra UI 1–15). Sam scrcpy
(1–60) startuje raz na sesję — hot-change tylko JPEG/poll, nie serwer.

Panel TYLKO czyta ten plik, gdy jest świeży i sesja na tym aliasie jedzie.
Bez sesji: 204 (`no_session`) — nie wołamy uiautodev ani screencap
„dla ciekawości”. Drugiego scrcpy stąd nie odpalamy (zabiłby ten z CLI).

Fallback uiautodev tylko W TRAKCIE sesji, gdy FB nie zapisał klatki
(rzadki start z codec=adb / scrcpy padł). Nie jako podgląd idle.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from v2.core.device import DeviceError, UiAuto2Device

# Szerokość podglądu w panelu — nie serwujemy pełnego PNG z telefonu.
PREVIEW_MAX_WIDTH = 480
# Fallback ~1 Hz gdy brak configu; żywy interwał: preview_interval_s(fps).
PREVIEW_INTERVAL_S = 1.0
# Plik z CLI jest „świeży”, gdy mtime nie starszy niż to.
# 8 s: nawet 1 fps + hiccup JPEG/GC nie może dać fałszywego 204.
PREVIEW_FILE_MAX_AGE_S = 8.0
# Legacy — writer czyta fps z JSON; stała zostaje jako fallback 1 Hz.
PREVIEW_WRITE_INTERVAL_S = 0.75
# JPEG/poll: 60 fps stopiłby dysk; scrcpy sam może zostać 1–60.
PREVIEW_UI_FPS_MIN = 1
PREVIEW_UI_FPS_MAX = 15
# Krótki timeout — zwis HTTP nie może zablokować dashboardu.
_UIAUTO_TIMEOUT_S = 2.5
_JPEG_QUALITY = 72

# Powody 204 — frontend pokazuje placeholder zamiast wywalać widok.
REASON_OFFLINE = "offline"
REASON_NO_SESSION = "no_session"

try:
    import numpy as np
except ImportError:  # pragma: no cover — runtime FB i tak wymaga numpy
    np = None  # type: ignore[assignment]


@dataclass(frozen=True)
class PreviewResult:
    """Wynik jednej próby podglądu — albo JPEG, albo powód braku klatki."""
    jpeg: bytes | None
    reason: str | None = None
    cached: bool = False


def preview_jpeg_path(data_root: Path, alias: str) -> Path:
    """Ścieżka klatki sesji: `data/devices/<alias>/preview.jpg`."""
    return Path(data_root) / "devices" / alias / "preview.jpg"


def to_preview_jpeg(raw: bytes, max_width: int = PREVIEW_MAX_WIDTH) -> bytes | None:
    """PNG/JPEG z telefonu → przeskalowany JPEG. None gdy dekodowanie pada."""
    if not raw:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None
    return _pil_to_jpeg(img, max_width=max_width)


def bgr_frame_to_jpeg(frame, max_width: int = PREVIEW_MAX_WIDTH) -> bytes | None:
    """Klatka BGR z `FrameBuffer.get_frame()` → JPEG ~480 px."""
    if frame is None or np is None:
        return None
    try:
        arr = np.asarray(frame)
    except Exception:
        return None
    if arr.ndim != 3 or arr.shape[2] < 3:
        return None
    # OpenCV / FB trzyma BGR — PIL chce RGB.
    rgb = arr[:, :, :3][:, :, ::-1]
    try:
        img = Image.fromarray(rgb)
    except Exception:
        return None
    return _pil_to_jpeg(img, max_width=max_width)


def _pil_to_jpeg(img: Image.Image, max_width: int) -> bytes | None:
    """Wspólne skalowanie + zapis JPEG."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if w > max_width and w > 0:
        new_h = max(1, int(round(h * max_width / w)))
        img = img.resize((max_width, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def write_preview_jpeg(data_root: Path, alias: str, jpeg: bytes) -> Path | None:
    """Atomowy zapis klatki: tmp obok + rename. None gdy alias pusty."""
    if not alias or not jpeg:
        return None
    dest = preview_jpeg_path(data_root, alias)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(jpeg)
    os.replace(tmp, dest)
    return dest


def read_fresh_preview_jpeg(
    data_root: Path,
    alias: str,
    *,
    max_age_s: float = PREVIEW_FILE_MAX_AGE_S,
) -> bytes | None:
    """JPEG z dysku, gdy plik istnieje i mtime jest świeży."""
    path = preview_jpeg_path(data_root, alias)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age > max_age_s:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data.startswith(b"\xff\xd8"):
        return None
    return data


def grab_uiauto_screenshot(serial: str, base_url: str) -> bytes | None:
    """Podłącz się do istniejącego uiautodev — bez startu scrcpy i bez nowego serwera."""
    try:
        dev = UiAuto2Device(
            serial=serial,
            base_url=base_url,
            _http_timeout=_UIAUTO_TIMEOUT_S,
        )
        raw = dev.screenshot()
    except DeviceError:
        return None
    except Exception:
        return None
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        return None
    return bytes(raw)


def _load_device_doc(config_root: Path, alias: str) -> dict | None:
    path = Path(config_root) / "devices" / f"{alias}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def preview_ui_fps(scrcpy_fps: int | float | None) -> int:
    """JPEG i poll panelu: 1–15 (scrcpy może być 1–60)."""
    try:
        n = int(round(float(scrcpy_fps)))
    except (TypeError, ValueError):
        n = PREVIEW_UI_FPS_MIN
    return max(PREVIEW_UI_FPS_MIN, min(PREVIEW_UI_FPS_MAX, n))


def preview_interval_s(scrcpy_fps: int | float | None) -> float:
    """Odstęp zapisu/poll JPEG — wyższe fps → krótszy odstęp (15 < 1 s)."""
    return 1.0 / float(preview_ui_fps(scrcpy_fps))


def read_device_scrcpy_fps(
    config_root: Path | None, alias: str, default: int = 15,
) -> int:
    """`scrcpy.max_fps` z config/devices/<alias>.json, klamra 1–60."""
    if not config_root or not alias:
        return default
    doc = _load_device_doc(config_root, alias)
    if not doc:
        return default
    scrcpy = doc.get("scrcpy")
    if not isinstance(scrcpy, dict):
        return default
    try:
        n = int(round(float(scrcpy.get("max_fps", default))))
    except (TypeError, ValueError):
        return default
    return max(1, min(60, n))


def capture_device_preview(
    alias: str,
    config_root: Path,
    *,
    session_running: bool = False,
    data_root: Path | None = None,
    max_width: int = PREVIEW_MAX_WIDTH,
) -> PreviewResult:
    """Klatka z pliku FB sesji; bez sesji zawsze 204.

    Fallback uiautodev tylko gdy sesja jedzie, a `preview.jpg` nie jest świeży
    (FB nie wstał). Idle / offline bez sesji = `no_session`.
    """
    if not session_running:
        return PreviewResult(jpeg=None, reason=REASON_NO_SESSION)

    root = data_root or Path(config_root).parent / "data"
    fresh = read_fresh_preview_jpeg(root, alias)
    if fresh:
        return PreviewResult(jpeg=fresh, reason=None)

    # Sesja jedzie, ale CLI nie zapisuje klatek — rzadki codec=adb / FB padł.
    doc = _load_device_doc(config_root, alias)
    if not doc:
        return PreviewResult(jpeg=None, reason=REASON_NO_SESSION)
    serial = (doc.get("adb_id") or "").strip()
    if not serial:
        return PreviewResult(jpeg=None, reason=REASON_NO_SESSION)
    uiauto = doc.get("uiautodev") or {}
    base_url = (uiauto.get("base_url") or "http://127.0.0.1:20242").rstrip("/")
    raw = grab_uiauto_screenshot(serial, base_url)
    if not raw:
        return PreviewResult(jpeg=None, reason=REASON_NO_SESSION)
    jpeg = to_preview_jpeg(raw, max_width=max_width)
    if not jpeg:
        return PreviewResult(jpeg=None, reason=REASON_NO_SESSION)
    return PreviewResult(jpeg=jpeg, reason=None)


class _PreviewHub:
    """Cache + throttle per alias — równoległe odświeżenia UI nie walą w dysk."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_at: dict[str, float] = {}
        self._cache: dict[str, PreviewResult] = {}

    def clear(self) -> None:
        with self._lock:
            self._last_at.clear()
            self._cache.clear()

    def get(
        self,
        alias: str,
        config_root: Path,
        *,
        session_running: bool = False,
        data_root: Path | None = None,
    ) -> PreviewResult:
        # Bez sesji nie serwujemy starego JPEG ani fallbacku uiautodev.
        if not session_running:
            return PreviewResult(jpeg=None, reason=REASON_NO_SESSION)
        with self._lock:
            now = time.monotonic()
            last = self._last_at.get(alias, 0.0)
            cached = self._cache.get(alias)
            # Throttle per alias z max_fps (1–15 Hz), nie stała 1 s.
            interval = preview_interval_s(
                read_device_scrcpy_fps(config_root, alias),
            )
            if cached is not None and (now - last) < interval:
                return PreviewResult(
                    jpeg=cached.jpeg, reason=cached.reason, cached=True,
                )
            result = capture_device_preview(
                alias, config_root,
                session_running=True,
                data_root=data_root,
            )
            self._last_at[alias] = time.monotonic()
            self._cache[alias] = result
            return result


_HUB = _PreviewHub()


def get_device_preview(
    alias: str,
    config_root: Path,
    *,
    session_running: bool = False,
    data_root: Path | None = None,
) -> PreviewResult:
    """Wejście dla endpointu panelu — throttle + klatka albo powód 204."""
    return _HUB.get(
        alias, config_root,
        session_running=session_running,
        data_root=data_root,
    )


def reset_preview_cache() -> None:
    """Czyści throttle i ostatnią klatkę — tylko testy."""
    _HUB.clear()


# ---------------------------------------------------------------------------
# Zapis z procesu CLI (żywy FrameBuffer) — nie z panelu.
# ---------------------------------------------------------------------------

class _PreviewWriter:
    """Wątek: get_frame() → JPEG → preview.jpg, dopóki FB żyje.

    Interwał czytamy z JSON przy każdej klatce — PATCH FPS na Na żywo
    skraca zapis od razu, bez restartu sesji (scrcpy zostaje stary).
    """

    def __init__(
        self, fb, alias: str, data_root: Path,
        config_root: Path | None = None,
    ) -> None:
        self._fb = fb
        self._alias = alias
        self._data_root = Path(data_root)
        # data/ i config/ są rodzeństwem pod rootem repo.
        self._config_root = (
            Path(config_root) if config_root
            else Path(data_root).parent / "config"
        )
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"preview-{alias}",
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _interval(self) -> float:
        """Sekundy między JPEG — z aktualnego max_fps (hot-change)."""
        return preview_interval_s(
            read_device_scrcpy_fps(self._config_root, self._alias),
        )

    def _loop(self) -> None:
        while not self._stop.wait(self._interval()):
            try:
                frame = self._fb.get_frame()
            except Exception:
                continue
            jpeg = bgr_frame_to_jpeg(frame)
            if not jpeg:
                continue
            try:
                write_preview_jpeg(self._data_root, self._alias, jpeg)
            except OSError:
                continue


_WRITERS: dict[str, _PreviewWriter] = {}
_WRITERS_LOCK = threading.Lock()


def start_preview_writer(
    fb, alias: str, data_root: Path, config_root: Path | None = None,
) -> None:
    """Start wątku zapisu klatek dla aliasu. Drugie wołanie zastępuje poprzedni."""
    if fb is None or not alias:
        return
    writer = _PreviewWriter(fb, alias, data_root, config_root=config_root)
    with _WRITERS_LOCK:
        old = _WRITERS.pop(alias, None)
        _WRITERS[alias] = writer
    if old is not None:
        old.stop()
    writer.start()


def stop_preview_writer(alias: str) -> None:
    """Zatrzymaj wątek zapisu tego telefonu (atexit / koniec sesji)."""
    with _WRITERS_LOCK:
        writer = _WRITERS.pop(alias, None)
    if writer is not None:
        writer.stop()
