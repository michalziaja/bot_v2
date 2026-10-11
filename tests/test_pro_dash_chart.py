"""Parser tooltipu wykresu Insights — bez telefonu."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.data.pro_dash_chart import (  # noqa: E402
    axis_from_words,
    date_at_x,
    item_chart_bounds,
    merge_samples,
    parse_tesseract_tsv,
    parse_tooltip_words,
    trace_overlay_jpeg,
)
from v2.data.pro_dash_parse import load_slim  # noqa: E402


class BoundsTests(unittest.TestCase):
    def test_chart_miedzy_filtrami_a_legenda(self):
        nodes = [
            {"text": "Wszyscy", "desc": "Wszyscy",
             "bounds": "[0,1124][291,1224]"},
            {"text": "Ten post", "bounds": "[69,1821][206,1864]"},
            {"text": "Wyświetlenia na przestrzeni czasu",
             "bounds": "[42,1032][775,1090]"},
        ]
        b = item_chart_bounds(nodes)
        self.assertIsNotNone(b)
        self.assertGreater(b[3] - b[1], 80)
        self.assertLess(b[1], 1300)
        self.assertGreater(b[3], 1700)


class TsvParseTests(unittest.TestCase):
    def test_tsv_naglowek_tesseract(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
            "left\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t120\t40\t40\t20\t90\t367\n"
            "5\t1\t1\t1\t2\t1\t200\t180\t30\t16\t88\t2\n"
            "5\t1\t1\t1\t2\t2\t232\t180\t40\t16\t85\twrz\n"
        )
        words = parse_tesseract_tsv(tsv)
        self.assertEqual(len(words), 3)
        hit = parse_tooltip_words(words, tap_x=130, chart_h=200)
        self.assertEqual(hit["this_views"], 367)
        self.assertEqual(hit["date"], "2026-09-02")


class MergeAxisTests(unittest.TestCase):
    def test_interpolacja_daty_na_osi(self):
        axis = [(100, "2026-09-01"), (500, "2026-09-05")]
        self.assertEqual(date_at_x(100, axis), "2026-09-01")
        self.assertEqual(date_at_x(500, axis), "2026-09-05")
        mid = date_at_x(300, axis)
        self.assertEqual(mid, "2026-09-03")

    def test_merge_zostawia_jeden_punkt_na_dzien(self):
        merged = merge_samples([
            {"date": "2026-09-02", "this_views": 360, "tap_x": 200},
            {"date": "2026-09-02", "this_views": 367, "tap_x": 210},
            {"date": "2026-09-03", "this_views": 376, "typical_views": 264,
             "tap_x": 300},
        ], axis=[])
        this = {p["date"]: p["views"] for p in merged["this_post"]}
        self.assertEqual(this["2026-09-03"], 376)
        self.assertIn("2026-09-02", this)
        typ = {p["date"]: p["views"] for p in merged["typical"]}
        self.assertEqual(typ["2026-09-03"], 264)


class OverlayPixelTests(unittest.TestCase):
    """28_typical_post.jpg — te same liczby co oś 0/200/400 i linia magenta."""

    def test_ten_post_z_kadru_osiaga_379(self):
        root = ROOT / "calibration" / "recon_pro_dash"
        jpg = (root / "28_typical_post.jpg").read_bytes()
        nodes = load_slim(root / "28_typical_post.slim.json")
        hit = trace_overlay_jpeg(jpg, nodes, item_total=379)
        self.assertTrue(hit["ok"], hit.get("reason"))
        this = {p["date"]: p["views"] for p in hit["this_post"]}
        self.assertGreaterEqual(len(this), 4)
        self.assertLessEqual(this[min(this)], 20)
        self.assertEqual(this[max(this)], 379)
        # skok 1→2 wrz jak na IG, nie chip 14 ani KPI w środku osi
        if "2026-09-02" in this:
            self.assertGreater(this["2026-09-02"], 300)
        typ = {p["date"]: p["views"] for p in hit["typical"]}
        self.assertGreaterEqual(len(typ), 3)
        last_typ = typ[max(typ)]
        self.assertLess(last_typ, 200)

    def test_post_278_os_150_300_gdy_brak_legendy_w_hier(self):
        root = ROOT / "calibration" / "recon_pro_dash"
        jpg = (root / "41_post_278_30d.jpg").read_bytes()
        nodes = load_slim(root / "41_post_278_30d.slim.json")
        hit = trace_overlay_jpeg(jpg, nodes, item_total=278)
        self.assertTrue(hit["ok"], hit.get("reason"))
        this = {p["date"]: p["views"] for p in hit["this_post"]}
        self.assertEqual(this[max(this)], 278)
        self.assertLessEqual(this[min(this)], 25)


if __name__ == "__main__":
    unittest.main()
