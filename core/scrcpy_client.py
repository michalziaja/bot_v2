"""ScrcpyClient — puszcza scrcpy-server na urządzeniu, zwraca FrameBuffer.

Adaptacja proven implementation z instagram_bot (~450 linii → ~250 tu):
  1. Push scrcpy-server.jar do /data/local/tmp na telefonie (dedup po md5)
  2. adb forward tcp:<port> localabstract:scrcpy
  3. adb shell app_process ... com.genymobile.scrcpy.Server (background)
  4. Podłącz socket, wczytaj 68-bajtowy header (width/height)
  5. Zwróć uruchomiony FrameBuffer

Wymagania: adb w PATH, PyAV lub OpenCV (dla h264/mjpeg), scrcpy-server.jar w v2/config_bin/.
"""
from __future__ import annotations
import hashlib
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from v2.core.frame_buffer import FrameBuffer

if TYPE_CHECKING:
    from v2.core.device import UiAuto2Device

logger = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

_SERVER_JAR_LOCAL  = str(Path(__file__).resolve().parent.parent / "config_bin" / "scrcpy-server.jar")
_SERVER_JAR_REMOTE = "/data/local/tmp/scrcpy-server.jar"


class ScrcpyClient:
    def __init__(self, device: "UiAuto2Device",
                 # 900 = poprzednie 720 × 1.25 (discovery / klienci bez override)
                 port: int = 27183, max_size: int = 900, max_fps: int = 15,
                 version: str = "3.3", stability_threshold: float = 8.0):
        self.device   = device
        self.port     = port
        self.max_size = max_size
        self.max_fps  = max_fps
        self.version  = version
        self.stab_thr = stability_threshold
        self._socket:      socket.socket | None = None
        self._server_proc: subprocess.Popen | None = None
        self.frame_buffer: FrameBuffer | None = None
        self.width  = 720
        self.height = 1280

    # ---- lifecycle ----
    def start(self) -> FrameBuffer:
        cap = self._detect_capability()
        logger.debug("Scrcpy capability: %s", cap)

        if cap in ("h264", "mjpeg"):
            try:
                self._push_server()
                self._forward_port()
                self._start_server_process()
                self._socket = self._connect_socket()
                fb = FrameBuffer(self._socket, codec=cap,
                                 stability_threshold=self.stab_thr)
            except Exception as exc:
                logger.warning("Scrcpy start failed, fallback adb screencap: %s", exc)
                self._safe_kill_server()
                fb = FrameBuffer(None, codec="adb", device=self.device,
                                 stability_threshold=self.stab_thr)
        else:
            fb = FrameBuffer(None, codec="adb", device=self.device,
                             stability_threshold=self.stab_thr)

        fb.start()
        self.frame_buffer = fb
        logger.info("Scrcpy stream OK (codec=%s, %dx%d)", fb.codec, self.width, self.height)
        return fb

    def stop(self):
        if self.frame_buffer:
            self.frame_buffer.stop()
        if self._socket:
            try: self._socket.close()
            except OSError: pass
        self._safe_kill_server()

    def _safe_kill_server(self):
        if self._server_proc:
            try:    self._server_proc.terminate()
            except Exception: pass
            self._server_proc = None

    # ---- adb wrappers ----
    def _adb(self, *args, **kwargs):
        cmd = ["adb", "-s", self.device.serial] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True,
                              creationflags=_NO_WINDOW, **kwargs)

    def _adb_shell(self, command: str) -> str:
        r = self._adb("shell", command, timeout=10)
        return r.stdout.strip()

    # ---- setup ----
    def _push_server(self):
        jar = os.path.normpath(_SERVER_JAR_LOCAL)
        if not os.path.exists(jar):
            raise FileNotFoundError(
                f"Brak {jar}. Skopiuj scrcpy-server.jar v{self.version} tam.\n"
                f"  https://github.com/Genymobile/scrcpy/releases")

        local_md5  = hashlib.md5(open(jar, "rb").read()).hexdigest()
        remote_exists = bool(self._adb_shell(f"ls {_SERVER_JAR_REMOTE} 2>/dev/null"))
        remote_md5 = ""
        if remote_exists:
            out = self._adb_shell(f"md5sum {_SERVER_JAR_REMOTE} 2>/dev/null")
            remote_md5 = out.split()[0] if out else ""
        if local_md5 == remote_md5:
            return
        r = self._adb("push", jar, _SERVER_JAR_REMOTE, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"adb push nie zadzialal: {(r.stderr or r.stdout).strip()}")
        verify = self._adb_shell(f"md5sum {_SERVER_JAR_REMOTE} 2>/dev/null")
        verify_md5 = verify.split()[0] if verify else ""
        if verify_md5 != local_md5:
            raise RuntimeError(f"md5 mismatch po push: {verify_md5!r} != {local_md5!r}")

    def _forward_port(self):
        self._adb("forward", f"tcp:{self.port}", "localabstract:scrcpy", timeout=10)

    def _start_server_process(self):
        # Ta konkretna kombinacja flag jest KLUCZOWA — komentarze w oryginale
        # (instagram_bot/core/scrcpy_client.py) opisują każdą; skrócone:
        # - args mają PODKREŚLNIKI (nie myślniki), inaczej "Unknown server option"
        # - tunnel_forward=true — nasłuchuje, my robimy adb forward
        # - send_frame_meta=false — czysty strumień H.264 dla PyAV
        # - control=false — inaczej serwer czeka na drugie połączenie i wisi
        # - send_dummy_byte=false — bez tego przesuwa się parsowanie headera
        cmd = (
            f"CLASSPATH={_SERVER_JAR_REMOTE} app_process / "
            f"com.genymobile.scrcpy.Server {self.version} "
            f"log_level=warn tunnel_forward=true "
            f"max_size={self.max_size} video_codec=h264 "
            f"max_fps={self.max_fps} send_device_meta=true "
            f"send_frame_meta=false audio=false "
            f"control=false send_dummy_byte=false "
            f"video_source=display"
        )
        self._server_proc = subprocess.Popen(
            ["adb", "-s", self.device.serial, "shell", cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            creationflags=_NO_WINDOW,
        )
        time.sleep(1.5)
        if self._server_proc.poll() is not None:
            out = self._server_proc.stdout.read() if self._server_proc.stdout else ""
            logger.error("Scrcpy server failed: %s", out.strip() or "(no output)")
        else:
            self._drain_server_output()

    def _drain_server_output(self):
        def _reader():
            p = self._server_proc
            if not p or not p.stdout: return
            for line in iter(p.stdout.readline, ""):
                if line.strip():
                    logger.debug("scrcpy-server: %s", line.strip())
        threading.Thread(target=_reader, daemon=True, name="ScrcpyServerLog").start()

    def _connect_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(8.0)
        sock.connect(("127.0.0.1", self.port))
        header = b""
        while len(header) < 68:
            chunk = sock.recv(68 - len(header))
            if not chunk:
                sock.close()
                raise ConnectionError("Scrcpy: EOF czytając header")
            header += chunk
        # magic (6) + device_name (64) + w(2) + h(2)
        self.width  = int.from_bytes(header[64:66], "big")
        self.height = int.from_bytes(header[66:68], "big")
        sock.settimeout(None)
        return sock

    def _detect_capability(self) -> str:
        try:
            import av  # noqa: F401
            return "h264"
        except ImportError: pass
        try:
            import cv2  # noqa: F401
            return "mjpeg"
        except ImportError: pass
        return "adb"
