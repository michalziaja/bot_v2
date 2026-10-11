"""Navigator — drabinka recovery L0–L6 z fixem L4b.

Jedyne miejsce w kodzie które wie co robić gdy widzimy zły ekran / overlay
/ obcą aplikację / systemui z rozwinięta szufladą.

Wywołuje Device (do gestów) + ScreenMap (do rozpoznawania) + EventLogger
(do audytu). Nie zna pluginów; plugin woła `navigator.ensure(target)`.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from v2.core.screen_map import (ScreenMap, ScreenState,
                                flatten_hierarchy, ids_from_flat, dominant_package,
                                texts_from_flat)

if TYPE_CHECKING:
    from v2.core.device import UiAuto2Device
    from v2.core.events import EventLogger

# ekrany których w ogóle nie próbujemy dismisować
BLOCKING_OVERLAYS = {"rate_limit", "suspicious_login", "challenge_two_factor"}

# Domyślne mapowanie target → kotwica nav_tab. Używane w L5.
DEFAULT_TAB_ANCHOR = {
    "home_feed":     "nav.feed_tab",
    "reels_feed":    "nav.clips_tab",
    "profile_self":  "nav.profile_tab",
    "search_top":    "nav.search_tab",
    "direct_inbox":  "nav.direct_tab",
}

# Ekrany z których wychodzi się przez BACK, nie przez tap w bottom nav.
# - overlaye/bottom sheety (mają w drzewie IDs bazowego ekranu → tap w nav zwykle
#   trafia w link wewnątrz sheeta)
# - pełne ekrany bez bottom nav w tej wersji IG (np. profile_other, story_viewer,
#   reel_detail itp. — tam bottom nav znika, tap w (108, 2277) klika w nic)
BACK_TO_EXIT_SCREENS = {
    # overlays / sheets
    "reel_likers_list",
    "reel_comments_sheet",
    "profile_options_sheet",
    "share_sheet",
    "search_input_active",
    # Wyniki wyszukiwania NIE MAJĄ dolnej nawigacji — siatka sięga dołu ekranu.
    # Tap w miejsce zakładki feedu trafiał w kafelek i otwierał treść, po czym
    # BACK wracał na wyniki i drabinka kręciła się w kółko aż do max_steps.
    "search_results",
    # full screens bez bottom nav w tej wersji IG:
    "profile_other",
    "story_viewer",
}


@dataclass
class Navigator:
    device: "UiAuto2Device"
    screen_map: ScreenMap
    ig_package: str
    events: "EventLogger | None" = None
    settle_after_action_s: float = 1.2

    # ---- utility ----
    def _log(self, kind: str, **kw):
        if self.events:
            self.events.log(kind, **kw)

    # ---- state ----
    def identify(self) -> ScreenState:
        flat = flatten_hierarchy(self.device.hierarchy())
        ids  = ids_from_flat(flat)
        pkg  = dominant_package(flat) or self.device.current_pkg()
        # Komentarz: teksty — login/signup na OnePlus nie mają resource-id
        return self.screen_map.identify(ids, pkg, texts=texts_from_flat(flat))

    # ---- ladder ----
    def ensure(self, target: str, max_steps: int = 6,
               tab_anchor_map: dict | None = None) -> bool:
        """Doprowadź do `target` przez drabinkę L0–L6. Zwraca True gdy dotarto."""
        tabs = tab_anchor_map or DEFAULT_TAB_ANCHOR
        consecutive_back = 0
        # Ekrany, na których tap w zakładkę już raz nie pomógł. Bez tego
        # drabinka potrafiła oscylować: tap otwierał treść, BACK wracał na ten
        # sam ekran, tap znowu ją otwierał — i tak aż do wyczerpania kroków.
        # Zaobserwowane na `search_results`, gdzie pod współrzędnymi zakładki
        # feedu leży kafelek siatki.
        tap_failed_on: set = set()

        for step in range(max_steps):
            s = self.identify()
            self._log("navigator.identify",
                      step=step, target=target, top=s.top, hits=s.hits,
                      overlay=s.overlay, pkg=s.package, ambiguous=s.ambiguous)

            # L0 — clean match
            if s.top == target and not s.overlay:
                return True

            # BLOCKING — never dismiss, abort with alert-worthy event
            if s.overlay in BLOCKING_OVERLAYS:
                self._log("navigator.blocking_overlay", overlay=s.overlay)
                return False

            # L1 — keyboard IME → back to dismiss
            if s.overlay == "keyboard":
                self._log("navigator.L1", action="back_dismiss_ime")
                self.device.press_back(); time.sleep(0.9); consecutive_back = 0; continue

            # L2 — dismissable overlay → back (later: use popups config)
            if s.overlay and not s.overlay.startswith("foreign_app") and s.overlay != "system_ui":
                self._log("navigator.L2", action="back_dismiss_overlay", overlay=s.overlay)
                self.device.press_back(); time.sleep(1.0); consecutive_back = 0; continue

            # L4b — systemui z top=None (drawer / notif tray / recents) → HOME + relaunch
            if s.overlay == "system_ui" or (s.package and "systemui" in s.package and s.top is None):
                self._log("navigator.L4b", action="home_key_and_relaunch")
                self.device.press_home(); time.sleep(0.8)
                self.device.launch_app(self.ig_package); time.sleep(2.5)
                consecutive_back = 0; continue

            # L3 — foreign app foreground
            if s.overlay and s.overlay.startswith("foreign_app"):
                self._log("navigator.L3", action="monkey_relaunch", was=s.overlay)
                self.device.launch_app(self.ig_package); time.sleep(3.0)
                consecutive_back = 0; continue

            # L4a — IG uruchomiony ale ekran nierozpoznany
            if s.top is None:
                if consecutive_back >= 2:
                    # eskalacja: 2× back nie pomogło → relaunch
                    self._log("navigator.L4a_escalate", action="monkey_relaunch")
                    self.device.launch_app(self.ig_package); time.sleep(2.5)
                    consecutive_back = 0; continue
                self._log("navigator.L4a", action="back_probe")
                self.device.press_back(); time.sleep(1.0)
                consecutive_back += 1; continue

            # L4c — ekran-overlay/bez-nav → press_back zamiast tap w nav.
            # Dotyczy: sheetów (reel_likers_list, share_sheet) ORAZ pełnych ekranów
            # bez bottom nav (profile_other, story_viewer). Tap w nav_tab tam nic
            # nie robi (nav nie istnieje) albo trafia w link w sheecie.
            if s.top in BACK_TO_EXIT_SCREENS and s.top != target:
                self._log("navigator.L4c", action="back_dismiss", top=s.top)
                self.device.press_back(); time.sleep(0.9); consecutive_back = 0; continue

            # L5 — znany ekran ale zły → tap nav tab
            anchor_name = tabs.get(target)
            if anchor_name and s.top not in tap_failed_on:
                c = self.screen_map.anchor_center(anchor_name)
                if c:
                    self._log("navigator.L5", action="tap_nav", target=target,
                              anchor=anchor_name, xy=c)
                    tap_failed_on.add(s.top)   # skreślamy z góry; wróci tu = nie zadziałało
                    self.device.tap(*c); time.sleep(self.settle_after_action_s)
                    consecutive_back = 0; continue

            # L5b — tap w zakładkę na tym ekranie już nie zadziałał (wróciliśmy
            # tu ponownie). Zamiast powtarzać ten sam gest, cofamy się — ekran
            # najwyraźniej nie ma dolnej nawigacji, mimo że nie jest na liście.
            if s.top in tap_failed_on:
                self._log("navigator.L5b", action="back_after_failed_tap", top=s.top)
                self.device.press_back(); time.sleep(0.9)
                consecutive_back = 0; continue

            # nothing else — exhaust
            self._log("navigator.exhausted", top=s.top, target=target)
            return False

        self._log("navigator.max_steps", target=target)
        return False

    # ---- wygodne go_* ----
    def go_home(self):    return self.ensure("home_feed")
    def go_profile(self): return self.ensure("profile_self")
    def go_reels(self):   return self.ensure("reels_feed")
    def go_search(self):  return self.ensure("search_top")
    def go_dm(self):      return self.ensure("direct_inbox")
