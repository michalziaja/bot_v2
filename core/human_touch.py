"""HumanTouch — tapy i swipey z jitterem i pauzami z humanizera.

Ta warstwa jest nad `UiAuto2Device` — plugin woła `ht.tap(x, y)` a nie
`device.tap(x, y)`, żeby dostał automatycznie:
  - jitter współrzędnych (±kilka px),
  - jitter czasu trwania swipe'a,
  - pauzę pre/post z humanizera.
"""
from __future__ import annotations
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from v2.core.device import UiAuto2Device
    from v2.core.humanizer import Humanizer


@dataclass
class HumanTouch:
    device: "UiAuto2Device"
    humanizer: "Humanizer"
    jitter_px: int = 6
    _rng: random.Random = None  # type: ignore

    def __post_init__(self):
        if self._rng is None:
            self._rng = random.Random()

    def _jit(self, v: int) -> int:
        return v + self._rng.randint(-self.jitter_px, self.jitter_px)

    def tap(self, x: int, y: int, pause_kind: str = "tap"):
        self.device.tap(self._jit(x), self._jit(y))
        self.humanizer.pause(pause_kind)

    def firm_tap(self, x: int, y: int, hold_ms: int = 140,
                 pause_kind: str = "tap"):
        """Dłuższy tap (przytrzymanie ~hold_ms) — otwieranie kafelków z siatki."""
        # lekki jitter czasu, żeby nie wyglądało jak bot-clock
        ms = int(hold_ms * self._rng.uniform(0.9, 1.15))
        self.device.firm_tap(self._jit(x), self._jit(y), hold_ms=ms)
        self.humanizer.pause(pause_kind)

    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              duration_ms: int | None = None, pause_kind: str = "swipe"):
        # duration z jitterem ±20%
        if duration_ms is None:
            duration_ms = int(self._rng.uniform(280, 420))
        else:
            duration_ms = int(duration_ms * self._rng.uniform(0.85, 1.15))
        self.device.swipe(self._jit(x1), self._jit(y1),
                          self._jit(x2), self._jit(y2), duration_ms)
        self.humanizer.pause(pause_kind)

    def swipe_up(self, x: int = 540, y_from: int = 1600, y_to: int = 800, **kw):
        self.swipe(x, y_from, x, y_to, **kw)

    def swipe_down(self, x: int = 540, y_from: int = 700, y_to: int = 1500, **kw):
        self.swipe(x, y_from, x, y_to, **kw)

    def press_back(self, pause_kind: str = "tap"):
        self.device.press_back()
        self.humanizer.pause(pause_kind)

    def press_home(self, pause_kind: str = "tap"):
        self.device.press_home()
        self.humanizer.pause(pause_kind)
