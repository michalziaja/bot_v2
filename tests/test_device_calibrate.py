"""Kalibracja modelu — harvest kotwic i zapis mapy, bez telefonu."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.device_calibrate import (
    fallback_nav_anchors,
    harvest_anchors,
    step_passed,
    write_model_map,
)


class DeviceCalibrateTests(unittest.TestCase):
    def test_harvest_nav_tab(self):
        flat = [{
            "resource-id": "com.instagram.android:id/feed_tab",
            "bounds": "[0,2120][216,2260]",
            "text": "",
            "content-desc": "Strona Główna",
        }]
        got = harvest_anchors(flat)
        self.assertIn("nav.feed_tab", got)
        self.assertEqual(got["nav.feed_tab"]["bounds"], "[0,2120][216,2260]")

    def test_fallback_nav_nad_inset(self):
        # Komentarz: 1080×2400 — taby nie mogą kończyć się na 2400 (pasek systemowy)
        a = fallback_nav_anchors(1080, 2400, inset=140)
        self.assertEqual(len(a), 5)
        feed = a["nav.feed_tab"]["bounds"]
        self.assertIn("[0,", feed)
        self.assertTrue(feed.endswith("[216,2260]"))

    def test_write_map_nadpisuje_model(self):
        with TemporaryDirectory() as td:
            dest = Path(td) / "KB2003_draft.json"
            write_model_map(
                model="KB2003",
                resolution=[1080, 2400],
                harvested={"nav.feed_tab": {
                    "kind": "resource-id",
                    "value": "com.instagram.android:id/feed_tab",
                    "bounds": "[10,2100][200,2240]",
                }},
                dest=dest,
            )
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["meta"]["device_model"], "KB2003")
            self.assertEqual(data["meta"]["resolution"], [1080, 2400])
            self.assertEqual(
                data["anchors"]["nav.feed_tab"]["bounds"],
                "[10,2100][200,2240]")
            self.assertIn("login_screen", data["fingerprints"])

    def test_step_passed_bez_oczekiwania(self):
        # Komentarz: wake/unlock — dump zawsze OK
        self.assertTrue(step_passed(None, None))
        self.assertTrue(step_passed("lock_screen", None))

    def test_step_passed_identify_musi_trafic(self):
        self.assertTrue(step_passed("home_feed", {"home_feed", "login_screen"}))
        self.assertFalse(step_passed("unknown", {"home_feed"}))
        self.assertFalse(step_passed(None, {"home_feed"}))


if __name__ == "__main__":
    unittest.main()
