"""Testy wyciągania liczników z content-desc rolki (PL / EN)."""
from __future__ import annotations
import unittest
from unittest.mock import MagicMock
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps.reels._reel_meta import read_current_reel, COUNT_PATTERNS


def _fake_hierarchy(nodes: list[dict]):
    """Zbuduj strukturę uiautodev z listy węzłów (każdy: {resource-id?, text?, content-desc?})."""
    def _mk(p):
        return {"properties": {
            "resource-id":  p.get("rid", ""),
            "text":         p.get("text", ""),
            "content-desc": p.get("desc", ""),
            "class":        p.get("class", "android.widget.Button"),
            "bounds":       p.get("bounds", "[0,0][100,100]"),
            "clickable":    "true" if p.get("clickable", True) else "false",
            "package":      "com.instagram.android",
        }, "children": []}
    return {"properties": {}, "children": [_mk(n) for n in nodes]}


class ExtractLikesCommentsTests(unittest.TestCase):
    def _dev(self, hierarchy):
        d = MagicMock(); d.hierarchy.return_value = hierarchy; return d

    def test_pl_likes_and_comments(self):
        h = _fake_hierarchy([
            {"rid": "com.instagram.android:id/clips_author_username", "text": "adaa_ma_mopa"},
            {"desc": "Liczba polubień: 1090. Wyświetl polubienia."},
            {"desc": "Liczba komentarzy: 23. Wyświetl komentarze."},
            {"desc": "Repostowano 26 razy"},
            {"desc": "Liczba zapisań to 58"},
        ])
        r = read_current_reel(self._dev(h))
        self.assertEqual(r["author"], "adaa_ma_mopa")
        self.assertEqual(r["likes"], 1090)
        self.assertEqual(r["comments"], 23)
        self.assertEqual(r["reposts"], 26)
        self.assertEqual(r["saves"], 58)

    def test_alt_shares_pattern(self):
        h = _fake_hierarchy([
            {"desc": "Liczba ponownych udostępnień: 413"},
        ])
        r = read_current_reel(self._dev(h))
        self.assertEqual(r["reposts"], 413)

    def test_en_likes(self):
        h = _fake_hierarchy([
            {"desc": "1234 likes"},
            {"desc": "56 comments"},
        ])
        r = read_current_reel(self._dev(h))
        self.assertEqual(r["likes"], 1234)
        self.assertEqual(r["comments"], 56)

    def test_missing_counts_return_none(self):
        h = _fake_hierarchy([
            {"rid": "com.instagram.android:id/clips_author_username", "text": "x"},
        ])
        r = read_current_reel(self._dev(h))
        self.assertEqual(r["author"], "x")
        self.assertIsNone(r["likes"])
        self.assertIsNone(r["comments"])

    def test_number_with_thousands_space(self):
        h = _fake_hierarchy([{"desc": "Liczba polubień: 12 345. Wyświetl polubienia."}])
        r = read_current_reel(self._dev(h))
        self.assertEqual(r["likes"], 12345)


if __name__ == "__main__":
    unittest.main()
