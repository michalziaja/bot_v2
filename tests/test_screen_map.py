"""Testy ScreenMap na próbkach zebranych podczas kalibracji (bez telefonu)."""
from __future__ import annotations
import json
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.screen_map import ScreenMap, flatten_hierarchy, ids_from_flat, dominant_package

MAP_PATH = ROOT / "calibration" / "SM_A505FN_draft.json"
SAMPLES  = ROOT / "calibration" / "samples"


def _flat(name: str) -> list[dict]:
    p = SAMPLES / f"{name}.flat.json"
    return json.loads(p.read_text(encoding="utf-8"))

def _ids(name: str) -> set:
    return {n["resource-id"] for n in _flat(name) if n.get("resource-id")}


class ScreenMapLoadTests(unittest.TestCase):
    def setUp(self):
        self.mp = ScreenMap.load(MAP_PATH)

    def test_load_meta(self):
        self.assertEqual(self.mp.meta["device_model"], "SM_A505FN")

    def test_load_anchors(self):
        # 5 tabów + notifications + search + profile.options
        for k in ("nav.feed_tab", "nav.clips_tab", "nav.direct_tab",
                  "nav.search_tab", "nav.profile_tab",
                  "home.notifications", "search.input", "profile.options"):
            self.assertIn(k, self.mp.anchors)

    def test_anchor_center(self):
        c = self.mp.anchor_center("nav.feed_tab")
        self.assertIsNotNone(c)
        self.assertEqual(c, (108, 2277))


class RecognizeTests(unittest.TestCase):
    def setUp(self):
        self.mp = ScreenMap.load(MAP_PATH)

    # --- czyste ekrany ---
    def _assert_top(self, sample: str, expected: str, min_hits: int = 1):
        ids = _ids(sample)
        pkg = dominant_package(_flat(sample))
        s = self.mp.identify(ids, pkg)
        self.assertEqual(s.top, expected,
                         f"{sample}: expected {expected}, got {s.top} (matches={s.matches[:3]})")
        self.assertGreaterEqual(s.hits, min_hits)

    def test_home_feed(self):        self._assert_top("01_home", "home_feed", 5)
    def test_reels_feed(self):       self._assert_top("03_reels", "reels_feed", 5)
    def test_profile_self(self):     self._assert_top("13_profile_self", "profile_self", 4)
    def test_direct_inbox(self):     self._assert_top("11_direct_inbox", "direct_inbox", 3)
    def test_search_input(self):     self._assert_top("20_search_input_focused", "search_input_active", 3)
    def test_notifications(self):    self._assert_top("30_notifications", "notifications", 2)

    # --- wins_over ---
    def test_reels_feed_no_overlay_when_bottom_sheet_ids_present(self):
        # W drzewie reels_feed są ID typu bottom_sheet_container — recognizer NIE powinien
        # ich brać jako overlay (POPUP_HINTS to allowlist konkretnych id).
        ids = _ids("03_reels")
        pkg = dominant_package(_flat("03_reels"))
        s = self.mp.identify(ids, pkg)
        self.assertIsNone(s.overlay, f"unexpected overlay: {s.overlay}")


class HierarchyFlattenTests(unittest.TestCase):
    def test_flatten_from_raw_hierarchy(self):
        raw = json.loads((SAMPLES / "01_home.hierarchy.json").read_text(encoding="utf-8"))
        flat = flatten_hierarchy(raw)
        self.assertGreater(len(flat), 20)
        ids = ids_from_flat(flat)
        self.assertIn("com.instagram.android:id/feed_tab", ids)

    def test_dominant_package(self):
        flat = _flat("01_home")
        self.assertEqual(dominant_package(flat), "com.instagram.android")


class CloneNormalizationTests(unittest.TestCase):
    """Dump z klona App Clonera (inny prefiks pakietu) po normalizacji w
    `UiAuto2Device.hierarchy()` musi rozpoznawać się identycznie jak oryginał —
    inaczej bot jest ślepy na każdym ekranie klona (BACKLOG.md §3)."""

    def test_clone_dump_recognized_same_as_original(self):
        from v2.core.device import _normalize_resource_ids

        raw_text = (SAMPLES / "01_home.hierarchy.json").read_text(encoding="utf-8")
        clone_raw = json.loads(raw_text.replace("com.instagram.android:id/",
                                                 "com.instagram.androip:id/"))
        _normalize_resource_ids(clone_raw, "com.instagram.androip")

        flat = flatten_hierarchy(clone_raw)
        ids  = ids_from_flat(flat)
        self.assertIn("com.instagram.android:id/feed_tab", ids)

        mp = ScreenMap.load(MAP_PATH)
        original_ids = _ids("01_home")
        s = mp.identify(ids, dominant_package(flat))
        s_original = mp.identify(original_ids, dominant_package(_flat("01_home")))
        self.assertEqual(s.top, "home_feed")
        self.assertEqual(s.top, s_original.top)
        self.assertEqual(s.hits, s_original.hits)


class RealScreensTests(unittest.TestCase):
    """Każdy zebrany dump musi być rozpoznany jako ten ekran, którym jest.

    Ten test istnieje po realnej wpadce: dołożenie fingerprintu `post_detail`
    zbudowanego z generycznych ID wiersza feedu (`media_group`,
    `row_feed_button_like`) sprawiło, że STRONA GŁÓWNA zaczęła być rozpoznawana
    jako otwarty post — bo te same ID są w obu miejscach, a `wins_over`
    przechylało decyzję. Nowy ekran musi mieć własny, unikalny marker.
    """

    MAP = Path(__file__).resolve().parent.parent / "calibration" / "SM_A505FN_draft.json"
    EXPECTED = {
        "01_home": "home_feed",
        "22_home_final": "home_feed",
        "40_home_tray": "home_feed",
        "41_home_scrolled": "home_feed",
        "42_profile_other": "profile_other",
        "13_profile_self": "profile_self",
        "19_search_top": "search_top",
        "11_direct_inbox": "direct_inbox",
    }

    def test_every_sample_is_recognised(self):
        sm = ScreenMap.load(self.MAP)
        for sample, expected in self.EXPECTED.items():
            with self.subTest(sample=sample):
                flat = _flat(sample)
                ids = ids_from_flat(flat) if isinstance(flat, list) else set()
                self.assertEqual(sm.identify(ids, "com.instagram.android").top,
                                 expected)


if __name__ == "__main__":
    unittest.main()
