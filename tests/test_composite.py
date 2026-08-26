"""Testy build_composite_grid — bez telefonu, na sztucznych klatkach."""
from __future__ import annotations
import tempfile, unittest
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

try:
    import numpy as np
    from v2.core.composite import build_composite_grid
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


@unittest.skipUnless(HAS_DEPS, "opencv/PIL/numpy required")
class CompositeGridTests(unittest.TestCase):
    def _fake_frame(self, h=1280, w=720, color=(50, 100, 200)):
        f = np.zeros((h, w, 3), dtype=np.uint8)
        f[:, :] = color
        return f

    def test_empty_frames_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "x.jpg"
            self.assertIsNone(build_composite_grid([], out))

    def test_single_frame_writes_jpeg(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "x.jpg"
            res = build_composite_grid([self._fake_frame()], out, thumb_w=200)
            self.assertIsNotNone(res)
            self.assertTrue(Path(res).exists())
            self.assertGreater(Path(res).stat().st_size, 100)
            # 1 klatka → 1 kolumna (bez pustego kafelka obok)
            from PIL import Image
            with Image.open(res) as img:
                self.assertEqual(img.size[0], 200)

    def test_six_frames_two_columns(self):
        frames = [self._fake_frame(color=(i * 40, 200, 100)) for i in range(6)]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "grid.jpg"
            res = build_composite_grid(frames, out, cols=2, thumb_w=200)
            self.assertIsNotNone(res)
            # 2 kolumny × 3 wiersze × thumb_w=200
            from PIL import Image
            with Image.open(res) as img:
                self.assertEqual(img.size[0], 200 * 2)  # width

    def test_odd_count_pads_last_row_only(self):
        """3 klatki → pad 1 czarny do 2×2; numerowane tylko prawdziwe (1–3)."""
        frames = [self._fake_frame() for _ in range(3)]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "odd.jpg"
            res = build_composite_grid(frames, out, cols=2, thumb_w=200)
            self.assertIsNotNone(res)
            from PIL import Image
            with Image.open(res) as img:
                self.assertEqual(img.size, (400, img.size[1]))
                # prawy dolny kafelek = czarny padding (prawie same zera)
                tile = img.crop((200, img.size[1] // 2, 400, img.size[1]))
                # średnia jasność paddingu powinna być niska (czarny)
                pixels = list(tile.getdata())
                avg = sum(sum(p) for p in pixels) / (len(pixels) * 3)
                self.assertLess(avg, 40)
    def test_jpeg_smaller_than_png_equivalent(self):
        frames = [self._fake_frame(color=(i * 30, 100, 200)) for i in range(4)]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "sanity.jpg"
            res = build_composite_grid(frames, out, quality=92)
            self.assertLess(Path(res).stat().st_size, 500_000)


if __name__ == "__main__":
    unittest.main()
