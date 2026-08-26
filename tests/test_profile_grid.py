"""Testy wykrywania kafelków siatki profilu i podpisu rolki w prompcie AI."""
from __future__ import annotations
import unittest
from unittest import mock
from unittest.mock import MagicMock
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps.profile._grid import (estimate_grid_tiles, find_grid_tiles,
                                    parse_bounds, capture_grid)
from v2.steps.reels.analyze_frames_ai import _ensure_caption, _format_comments
from v2.steps.reels.read_caption_comments import _panel_open

SCREEN_W = 1080
CELL = SCREEN_W // 3
TILE_ID = "com.instagram.android:id/image_button"


def _tile(col, row, top=100, rid=TILE_ID):
    x1, y1 = col * CELL, top + row * CELL
    return {"bounds": f"[{x1},{y1}][{x1 + CELL},{y1 + CELL}]", "resource-id": rid}


def _grid(rows=4):
    return [_tile(c, r) for r in range(rows) for c in range(3)]


class ParseBoundsTests(unittest.TestCase):
    def test_parses(self):
        self.assertEqual(parse_bounds("[1,2][3,4]"), (1, 2, 3, 4))

    def test_garbage_is_none(self):
        self.assertIsNone(parse_bounds("nope"))
        self.assertIsNone(parse_bounds(None))


class FindGridTilesTests(unittest.TestCase):
    def test_reading_order(self):
        tiles = find_grid_tiles(_grid(2), SCREEN_W, 12)
        self.assertEqual(len(tiles), 6)
        # wiersz po wierszu, w wierszu lewo → prawo
        self.assertEqual([t[0] for t in tiles[:3]], [0, CELL, 2 * CELL])
        self.assertLess(tiles[0][1], tiles[3][1])

    def test_respects_tiles_count(self):
        self.assertEqual(len(find_grid_tiles(_grid(4), SCREEN_W, 9)), 9)

    def test_grid_container_rejected(self):
        """Kontener całej siatki ma ID kafelka i wielkość ekranu — nie może
        wejść jako jeden wielki kafelek, bo rozjeżdża numerację."""
        flat = _grid(2) + [{"bounds": "[0,0][1080,2000]", "resource-id": TILE_ID}]
        self.assertEqual(len(find_grid_tiles(flat, SCREEN_W, 12)), 6)

    def test_duplicate_bounds_collapse(self):
        """Kafelek bywa w drzewie dwa razy (kontener + obrazek)."""
        flat = _grid(1) + _grid(1)
        self.assertEqual(len(find_grid_tiles(flat, SCREEN_W, 12)), 3)

    def test_no_tiles_returns_empty(self):
        flat = [{"bounds": "[0,0][1080,120]", "resource-id": "x/action_bar"}]
        self.assertEqual(find_grid_tiles(flat, SCREEN_W, 12), [])


class EstimateGridTests(unittest.TestCase):
    def test_three_columns_in_reading_order(self):
        tiles = estimate_grid_tiles(SCREEN_W, 2400, 6)
        self.assertEqual(len(tiles), 6)
        self.assertEqual([t[0] for t in tiles[:3]], [0, CELL, 2 * CELL])
        self.assertEqual(tiles[0][1], tiles[2][1])

    def test_drops_rows_below_screen(self):
        """Kafelek poza ekranem to kafelek, którego na zrzucie nie ma —
        narysowany numer wskazywałby wtedy pustkę."""
        tiles = estimate_grid_tiles(SCREEN_W, 800, 12)
        self.assertTrue(all(t[3] <= 800 for t in tiles))
        self.assertLess(len(tiles), 12)


class CaptureGridScrollTests(unittest.TestCase):
    """Port starego _capture_grid: wstępny scroll ZAWSZE, potem dociągnięcie."""

    def _ctx(self, flat_fn):
        swipes = []

        class Ht:
            def swipe(self, *a, **k):
                swipes.append((a, k))

        class Dev:
            def hierarchy(self, force=False):
                return flat_fn()

            def screenshot(self):
                return b"fake"

            def wait_for_stable_ui(self, **kw):
                return True

        ctx = MagicMock()
        ctx.ht = Ht()
        ctx.device = Dev()
        ctx.account.device = {"_hardware": {"resolution": (SCREEN_W, 2340)}}
        ctx.log = lambda *a, **k: None
        return ctx, swipes

    def test_preliminary_scroll_always_runs(self):
        """Nawet gdy hierarchy już ma 9 kafelków — 1 scroll jak max_scroll_for_grid."""
        ctx, swipes = self._ctx(lambda: _grid(3))
        with mock.patch("v2.steps.profile._grid.flatten_hierarchy",
                        side_effect=lambda x: x), \
             mock.patch("v2.steps.profile._grid.annotate_grid",
                        return_value="/tmp/g.jpg"), \
             mock.patch("time.sleep", return_value=None):
            shot = capture_grid(ctx, "/tmp/g.jpg", tiles_count=9, scrolls=1,
                                max_extra_scrolls=0)
        self.assertEqual(shot["scrolls"], 1)
        self.assertEqual(len(shot["tiles"]), 9)
        self.assertEqual(len(swipes), 1)
        # Port swipe_scroll_likers_list: ~0.78 → 0.30 ekranu
        (_x0, y0, _x1, y1), _kw = swipes[0]
        self.assertGreater(y0, int(2340 * 0.70))
        self.assertLess(y1, int(2340 * 0.40))

    def test_scrolls_zero_skips_preliminary(self):
        """scrolls=0 = diagnostyka bez wstępu (stary enabled path z max=0)."""
        ctx, swipes = self._ctx(lambda: _grid(3))
        with mock.patch("v2.steps.profile._grid.flatten_hierarchy",
                        side_effect=lambda x: x), \
             mock.patch("v2.steps.profile._grid.annotate_grid",
                        return_value="/tmp/g.jpg"), \
             mock.patch("time.sleep", return_value=None):
            shot = capture_grid(ctx, "/tmp/g.jpg", tiles_count=9, scrolls=0,
                                max_extra_scrolls=0)
        self.assertEqual(shot["scrolls"], 0)
        self.assertEqual(swipes, [])

    def test_extra_scroll_when_short_after_prelim(self):
        """Po wstępie za mało kafelków → dodatkowy scroll (stary attempts < 3)."""
        calls = {"n": 0}

        def flat_fn():
            calls["n"] += 1
            # hierarchy wołane po każdym scrollu; pierwsze odczyty = 3 kafelki
            return _grid(1) if calls["n"] < 3 else _grid(3)

        ctx, swipes = self._ctx(flat_fn)
        with mock.patch("v2.steps.profile._grid.flatten_hierarchy",
                        side_effect=lambda x: x), \
             mock.patch("v2.steps.profile._grid.annotate_grid",
                        return_value="/tmp/g.jpg"), \
             mock.patch("time.sleep", return_value=None):
            shot = capture_grid(ctx, "/tmp/g.jpg", tiles_count=9, scrolls=1,
                                max_extra_scrolls=3)
        self.assertGreaterEqual(shot["scrolls"], 2)
        self.assertEqual(len(shot["tiles"]), 9)
        self.assertGreaterEqual(len(swipes), 2)


class PanelOpenTests(unittest.TestCase):
    def test_detects_comment_composer(self):
        self.assertTrue(_panel_open([
            {"resource-id": "com.instagram.android:id/layout_comment_thread_edittext"}]))

    def test_plain_reel_screen_is_closed(self):
        self.assertFalse(_panel_open([
            {"resource-id": "com.instagram.android:id/clips_video_container"},
            {"resource-id": ""}]))


class FrameBufferLivenessTests(unittest.TestCase):
    """Bufor z martwego strumienia wygląda przez `get_frame()` jak żywy —
    stąd composite z sześcioma kopiami ekranu głównego wysłany do AI jako rolka."""

    def _fb(self):
        from v2.core.frame_buffer import FrameBuffer
        return FrameBuffer(socket_conn=None, codec="h264")

    def test_no_frame_is_infinite_age(self):
        self.assertEqual(self._fb().frame_age_ms(), float("inf"))
        self.assertFalse(self._fb().is_live())

    def test_stale_frame_is_not_live(self):
        import time as _t
        fb = self._fb()
        fb._latest = object()
        fb._frame_time = _t.monotonic() - 30      # sprzed 30 s
        self.assertGreater(fb.frame_age_ms(), 1500)
        self.assertFalse(fb.is_live())

    def test_fresh_frame_still_needs_running_thread(self):
        import time as _t
        fb = self._fb()
        fb._latest = object()
        fb._frame_time = _t.monotonic()
        # wątek nie wystartował → strumień nie żyje, mimo świeżej klatki
        self.assertLess(fb.frame_age_ms(), 100)
        self.assertFalse(fb.is_live())


class CaptionForPromptTests(unittest.TestCase):
    def _ctx(self, scratch, nodes=()):
        ctx = MagicMock()
        ctx.scratch = scratch
        ctx.device.hierarchy.return_value = {
            "properties": {}, "children": [
                {"properties": {"resource-id": n[0], "text": n[1],
                                "content-desc": "", "bounds": "[0,0][1,1]"},
                 "children": []} for n in nodes]}
        return ctx

    def test_uses_caption_from_step(self):
        ctx = self._ctx({"reel_caption": "opis z kroku"})
        self.assertEqual(_ensure_caption(ctx), ("opis z kroku", "step"))

    def test_falls_back_to_hierarchy_dump(self):
        ctx = self._ctx({}, [("com.instagram.android:id/clips_caption_component",
                              "opis z drzewa")])
        caption, source = _ensure_caption(ctx)
        self.assertEqual((caption, source), ("opis z drzewa", "dump"))
        # dobrany opis wraca do scratcha — kolejne kroki nie dublują dumpa
        self.assertEqual(ctx.scratch["reel_caption"], "opis z drzewa")

    def test_missing_is_reported_not_faked(self):
        ctx = self._ctx({}, [("inne/id", "cokolwiek")])
        self.assertEqual(_ensure_caption(ctx), (None, "missing"))

    def test_comments_formatting(self):
        out = _format_comments([{"user": "a", "text": "super"},
                                {"user": "", "text": "bez nicku"},
                                {"user": "c", "text": ""}])
        self.assertEqual(out, "@a: super\nbez nicku")


if __name__ == "__main__":
    unittest.main()
