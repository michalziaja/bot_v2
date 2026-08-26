"""FrameBuffer — dekoduje strumień scrcpy w tle, udostępnia najświeższą klatkę.

Trzy tryby (autodetect w ScrcpyClient):
  - h264   — natywnie przez PyAV (najszybszy, wymaga scrcpy H.264 socket)
  - mjpeg  — OpenCV imdecode (fallback gdy H.264 nie działa)
  - adb    — screencap co ~1s (ostatnia deska ratunku, wolne)

API (thread-safe):
  fb.get_frame()               → najświeższa klatka albo None
  fb.get_fresh_frame(max_age_ms=100) → czeka aż będzie świeża klatka
  fb.get_stable_frame(200)     → czeka aż screen przestanie animować
  fb.stop()                    → kończy wątek
"""
from __future__ import annotations
import logging
import socket
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from v2.core.device import UiAuto2Device

logger = logging.getLogger(__name__)

# Domyślny próg "stabilności" — cv2.absdiff mean. Mniej = ostrzejsze.
STABILITY_THRESHOLD = 8.0


class FrameBuffer(threading.Thread):
    def __init__(self, socket_conn: socket.socket | None, codec: str = "h264",
                 device: "UiAuto2Device | None" = None,
                 stability_threshold: float = STABILITY_THRESHOLD):
        super().__init__(daemon=True, name="FrameBuffer")
        self._socket        = socket_conn
        self._codec         = codec
        self._device        = device
        self._stability_thr = stability_threshold
        self._lock          = threading.Lock()
        # `_latest` to np.ndarray (bgr24) — nie ładujemy numpy dopóki nie musimy.
        self._latest        = None
        self._frame_time    = 0.0
        self._stop_event    = threading.Event()

    @property
    def codec(self) -> str:
        return self._codec

    # ---- main loop ----
    def run(self):
        try:
            if self._codec == "h264":
                self._run_h264()
            elif self._codec == "mjpeg":
                self._run_mjpeg()
            else:
                self._run_adb_fallback()
        except Exception as exc:
            logger.error("FrameBuffer thread crashed: %s", exc, exc_info=True)

    def _run_h264(self):
        try:
            import av  # type: ignore
        except ImportError:
            logger.error("PyAV missing — fallback to ADB screencap")
            self._run_adb_fallback()
            return

        try:
            codec_ctx = av.CodecContext.create("h264", "r")
            decoded = 0
            last_log = time.monotonic()
            first_chunk = True
            while not self._stop_event.is_set():
                try:
                    chunk = self._socket.recv(65536)
                    if not chunk:
                        logger.warning("Scrcpy socket: EOF")
                        break
                    if first_chunk:
                        logger.debug("Scrcpy stream: first chunk %d bytes", len(chunk))
                        first_chunk = False
                    for pkt in codec_ctx.parse(chunk):
                        for frame in codec_ctx.decode(pkt):
                            img = frame.to_ndarray(format="bgr24")
                            with self._lock:
                                self._latest = img
                                self._frame_time = time.monotonic()
                            decoded += 1
                    now = time.monotonic()
                    if now - last_log >= 10.0:
                        logger.debug("Scrcpy: %d frames decoded", decoded)
                        last_log = now
                except OSError as exc:
                    logger.warning("Scrcpy socket error: %s", exc)
                    break
        except Exception as exc:
            logger.error("Scrcpy h264 decode error: %s", exc)

    def _run_mjpeg(self):
        import cv2, numpy as np
        buf = b""
        SOI = b"\xff\xd8"; EOI = b"\xff\xd9"
        while not self._stop_event.is_set():
            try:
                chunk = self._socket.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    s = buf.find(SOI); e = buf.find(EOI, s + 2)
                    if s == -1 or e == -1:
                        break
                    jpg = buf[s:e + 2]; buf = buf[e + 2:]
                    img = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        with self._lock:
                            self._latest = img
                            self._frame_time = time.monotonic()
            except OSError:
                break

    def _run_adb_fallback(self):
        import cv2, numpy as np
        while not self._stop_event.is_set():
            if self._device:
                try:
                    raw = self._device.screenshot()
                    if raw:
                        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if img is not None:
                            with self._lock:
                                self._latest = img
                                self._frame_time = time.monotonic()
                except Exception as exc:
                    logger.debug("ADB fallback screencap err: %s", exc)
            time.sleep(1.0)

    # ---- public API ----
    def get_frame(self):
        """Najświeższa klatka lub None. Szybkie (~1ms), tylko kopia z RAM.

        UWAGA: „najświeższa" znaczy „ostatnia zdekodowana", a nie „aktualna".
        Gdy socket scrcpy padnie, wątek kończy się, a ta metoda w nieskończoność
        oddaje ostatnią klatkę sprzed awarii. Wołający, dla którego liczy się
        to, co jest na ekranie TERAZ, musi sprawdzić `frame_age_ms()`.
        """
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    def frame_age_ms(self) -> float:
        """Wiek ostatniej klatki w ms. `inf`, gdy nie ma żadnej."""
        with self._lock:
            if self._latest is None:
                return float("inf")
            return (time.monotonic() - self._frame_time) * 1000

    def is_live(self, max_age_ms: float = 1500) -> bool:
        """Czy bufor faktycznie nadąża za ekranem.

        Trzy warunki naraz, bo każdy z osobna daje się oszukać: wątek musi
        żyć, klatka musi istnieć i musi być świeża. Martwy strumień z zapasem
        starych klatek wygląda przez `get_frame()` identycznie jak żywy —
        i tak powstał composite z sześcioma kopiami ekranu głównego Androida
        zamiast rolki.
        """
        return self.is_alive() and self.frame_age_ms() <= max_age_ms

    def get_fresh_frame(self, max_age_ms: int = 100, timeout_s: float = 3.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if (self._latest is not None and
                        (time.monotonic() - self._frame_time) * 1000 < max_age_ms):
                    return self._latest.copy()
            time.sleep(0.01)
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    def get_stable_frame(self, stability_ms: int = 200, timeout_s: float = 5.0):
        """Czekaj aż screen przestanie animować (cv2.absdiff < threshold przez `stability_ms`)."""
        import cv2, numpy as np
        deadline = time.monotonic() + timeout_s
        stable_since = None
        prev = None
        while time.monotonic() < deadline:
            curr = self.get_frame()
            if curr is None:
                time.sleep(0.05); continue
            if prev is not None:
                diff = cv2.absdiff(prev, curr)
                if float(np.mean(diff)) < self._stability_thr:
                    stable_since = stable_since or time.monotonic()
                    if (time.monotonic() - stable_since) * 1000 >= stability_ms:
                        return curr
                else:
                    stable_since = None
            prev = curr
            time.sleep(0.05)
        return self.get_frame()

    def stop(self):
        self._stop_event.set()
