"""Context — jeden obiekt wstrzykiwany do każdego kroku.

Buduje się raz na sesję. Nie zawiera stanu przejściowego per-krok — od tego
jest StepResult.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from v2.core.device import UiAuto2Device
    from v2.core.human_touch import HumanTouch
    from v2.core.navigator import Navigator
    from v2.core.screen_map import ScreenMap
    from v2.core.humanizer import Humanizer
    from v2.core.events import EventLogger
    from v2.core.frame_buffer import FrameBuffer
    from v2.config.schema import AccountConfig
    from v2.data.session_stats import SessionStats


@dataclass
class Context:
    device:     "UiAuto2Device"
    ht:         "HumanTouch"
    navigator:  "Navigator"
    screen_map: "ScreenMap"
    humanizer:  "Humanizer"
    events:     "EventLogger"
    account:    "AccountConfig"
    session_id: str
    ig_package: str
    fb:         "FrameBuffer | None" = None      # scrcpy stream (None = brak, watch spadnie na screenshot HTTP)
    # miejsce na cache per-iterację flow (AI wynik, marker rolki itp.)
    scratch:    dict = field(default_factory=dict)
    # Live postęp sesji → session_stats.json (None w testach / flow bez kolejki)
    session_stats: "SessionStats | None" = None

    def log(self, kind: str, **payload):
        return self.events.log(kind, **payload)
