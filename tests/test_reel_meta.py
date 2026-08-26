"""Testy parsera meta rolki — na próbce reels z kalibracji."""
from __future__ import annotations
import json, unittest
from pathlib import Path
from unittest.mock import MagicMock
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps.reels._reel_meta import read_current_reel, _to_int

SAMPLES = ROOT / "calibration" / "samples"


class ToIntTests(unittest.TestCase):
    def test_plain(self):    self.assertEqual(_to_int("123"), 123)
    def test_spaces(self):   self.assertEqual(_to_int("1 234"), 1234)
    def test_comma(self):    self.assertEqual(_to_int("1,234"), 1234)
    def test_k_suffix(self): self.assertEqual(_to_int("1.2K"), 12000)  # "1.2K" → 12 * 1000
    def test_invalid(self):  self.assertIsNone(_to_int("hello"))


class ReadCurrentReelTests(unittest.TestCase):
    def test_extracts_author_from_reels_dump(self):
        h = json.loads((SAMPLES / "03_reels.hierarchy.json").read_text(encoding="utf-8"))
        dev = MagicMock()
        dev.hierarchy.return_value = h
        r = read_current_reel(dev)
        self.assertEqual(r["author"], "parkowa.na.wsi")
        # Marker to ODCISK rolki, nie sam autor: dwie rolki tego samego twórcy
        # muszą się różnić, inaczej weryfikacja przewinięcia daje fałszywy
        # wynik "to wciąż ta sama rolka". Sprawdzamy właściwości, nie dokładny
        # zapis — jego format może się zmieniać.
        self.assertIn("parkowa.na.wsi", r["marker"])
        self.assertIn(str(r["likes"]), r["marker"])
        self.assertGreater(len(r["marker"]), len(r["author"]))


if __name__ == "__main__":
    unittest.main()
