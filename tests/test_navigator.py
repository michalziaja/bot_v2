"""Testy Navigatora — mockowany Device, realny ScreenMap z próbek."""
from __future__ import annotations
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.screen_map import ScreenMap
from v2.core.navigator  import Navigator

MAP_PATH = ROOT / "calibration" / "SM_A505FN_draft.json"
SAMPLES  = ROOT / "calibration" / "samples"


def _fake_device_for_sample(sample_name: str):
    """Zwraca MagicMock Device który mockuje hierarchy() dumpem z próbki."""
    p = SAMPLES / f"{sample_name}.hierarchy.json"
    h = json.loads(p.read_text(encoding="utf-8"))
    d = MagicMock()
    d.hierarchy.return_value = h
    d.current_pkg.return_value = "com.instagram.android"
    return d


class NavigatorIdentifyTests(unittest.TestCase):
    def setUp(self):
        self.mp = ScreenMap.load(MAP_PATH)

    def test_identify_home(self):
        dev = _fake_device_for_sample("01_home")
        nav = Navigator(device=dev, screen_map=self.mp, ig_package="com.instagram.android")
        s = nav.identify()
        self.assertEqual(s.top, "home_feed")
        self.assertGreaterEqual(s.hits, 5)

    def test_identify_reels_no_overlay(self):
        dev = _fake_device_for_sample("03_reels")
        nav = Navigator(device=dev, screen_map=self.mp, ig_package="com.instagram.android")
        s = nav.identify()
        self.assertEqual(s.top, "reels_feed")
        self.assertIsNone(s.overlay)


class NavigatorEnsureTests(unittest.TestCase):
    """Testy drabinki — mockujemy device i przechodzimy przez sekwencję identyfikacji."""

    def setUp(self):
        self.mp = ScreenMap.load(MAP_PATH)

    def _seq_device(self, sample_names: list[str]):
        """Device który przy każdym kolejnym hierarchy() zwraca kolejny dump."""
        d = MagicMock()
        dumps = [json.loads((SAMPLES / f"{n}.hierarchy.json").read_text(encoding="utf-8"))
                 for n in sample_names]
        it = iter(dumps)
        # cykliczne fallback do ostatniego
        state = {"cur": next(it)}
        def _hierarchy(force=False):
            return state["cur"]
        def _advance():
            try:    state["cur"] = next(it)
            except StopIteration: pass
        d.hierarchy.side_effect = lambda force=False: state["cur"]
        d.current_pkg.return_value = "com.instagram.android"
        # każdy tap/back/launch przesuwa "gdzie jesteśmy"
        d.tap.side_effect          = lambda x, y: _advance()
        d.press_back.side_effect   = lambda:      _advance()
        d.launch_app.side_effect   = lambda pkg:  _advance()
        d.press_home.side_effect   = lambda:      _advance()
        d.invalidate_hierarchy.side_effect = lambda: None
        return d

    def test_already_on_target(self):
        dev = self._seq_device(["01_home"])
        nav = Navigator(device=dev, screen_map=self.mp, ig_package="com.instagram.android")
        self.assertTrue(nav.ensure("home_feed"))

    def test_wrong_screen_l5_tap_nav(self):
        # start: profile_self  →  po tap w feed_tab → home
        dev = self._seq_device(["13_profile_self", "01_home"])
        nav = Navigator(device=dev, screen_map=self.mp, ig_package="com.instagram.android")
        self.assertTrue(nav.ensure("home_feed"))
        # zweryfikuj że tap został wywołany na kotwicy feed_tab (108, 2277)
        dev.tap.assert_called()

    def test_does_not_repeat_a_tap_that_did_not_help(self):
        """Regresja z żywego przebiegu: na ekranie bez dolnej nawigacji tap
        w miejsce zakładki otwierał treść, BACK wracał na ten sam ekran i tak
        w kółko aż do wyczerpania kroków. Po jednym nieudanym tapie na danym
        ekranie drabinka przechodzi na BACK."""
        dev = self._seq_device(["13_profile_self"] * 6)
        dev.tap.side_effect = lambda x, y: None
        nav = Navigator(device=dev, screen_map=self.mp,
                        ig_package="com.instagram.android")
        nav.ensure("home_feed", max_steps=4)
        self.assertEqual(dev.tap.call_count, 1)      # nie 4
        dev.press_back.assert_called()

    def test_gives_up_after_max_steps(self):
        # nigdy nie osiągniemy home_feed (ciągle profile_self)
        dev = self._seq_device(["13_profile_self", "13_profile_self", "13_profile_self",
                                "13_profile_self", "13_profile_self", "13_profile_self",
                                "13_profile_self"])
        # tap NIE zmienia stanu — nadpisujemy side_effect
        dev.tap.side_effect = lambda x, y: None
        nav = Navigator(device=dev, screen_map=self.mp, ig_package="com.instagram.android")
        self.assertFalse(nav.ensure("home_feed", max_steps=3))


if __name__ == "__main__":
    unittest.main()
