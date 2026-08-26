"""Testy detekcji reklam w rolkach i skip guarda."""
from __future__ import annotations
import unittest
from unittest.mock import MagicMock
from types import SimpleNamespace
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps.reels._reel_meta import read_current_reel, AD_MARKERS
from v2.steps.reels._guards import skip_if_ad


def _hier(nodes):
    def _mk(p):
        return {"properties": {
            "resource-id":  p.get("rid", ""), "text": p.get("text", ""),
            "content-desc": p.get("desc", ""), "class": p.get("class", "android.view.View"),
            "bounds": "[0,0][100,100]", "clickable": "false", "package": "com.instagram.android",
        }, "children": []}
    return {"properties": {}, "children": [_mk(n) for n in nodes]}


class AdDetectionTests(unittest.TestCase):
    def _dev(self, h):
        d = MagicMock(); d.hierarchy.return_value = h; return d

    def test_pl_sponsorowane(self):
        h = _hier([
            {"rid": "com.instagram.android:id/clips_author_username", "text": "some.brand"},
            {"text": "Sponsorowane", "desc": "Sponsorowane"},
        ])
        r = read_current_reel(self._dev(h))
        self.assertTrue(r["is_ad"])
        self.assertEqual(r["author"], "some.brand")

    def test_en_sponsored(self):
        h = _hier([{"desc": "Sponsored"}])
        r = read_current_reel(self._dev(h))
        self.assertTrue(r["is_ad"])

    def test_reklama_marker(self):
        h = _hier([{"text": "Reklama"}])
        r = read_current_reel(self._dev(h))
        self.assertTrue(r["is_ad"])

    def test_normal_reel_not_ad(self):
        h = _hier([
            {"rid": "com.instagram.android:id/clips_author_username", "text": "regular.user"},
            {"desc": "Liczba polubień: 500. Wyświetl polubienia."},
        ])
        r = read_current_reel(self._dev(h))
        self.assertFalse(r["is_ad"])
        self.assertEqual(r["likes"], 500)


class GuardTests(unittest.TestCase):
    def _ctx(self, reel_dict):
        ctx = SimpleNamespace(
            scratch={"reel": reel_dict},
            log=lambda *a, **kw: None,
        )
        return ctx

    def test_skip_when_ad(self):
        ctx = self._ctx({"is_ad": True, "author": "brand.x"})
        r = skip_if_ad(ctx)
        self.assertIsNotNone(r)
        self.assertEqual(r.status, "skipped")
        self.assertEqual(r.payload.get("reason"), "ad_detected")

    def test_no_skip_when_not_ad(self):
        ctx = self._ctx({"is_ad": False, "author": "person.y"})
        self.assertIsNone(skip_if_ad(ctx))

    def test_no_skip_when_reel_missing(self):
        ctx = SimpleNamespace(scratch={}, log=lambda *a, **kw: None)
        self.assertIsNone(skip_if_ad(ctx))


if __name__ == "__main__":
    unittest.main()
