"""Testy niezależnych flag AI: use_frames + read_caption (reels_niche)."""
from __future__ import annotations
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.plugins.catalog import get_plugin
from v2.plugins import reel_log
from v2.plugins.base import Budget
from v2.runner.step import StepResult
from v2.steps.reels.analyze_frames_ai import AnalyzeFramesAi
from v2.tests.test_plugins_runtime import (
    FakeCtx, ScratchStep, fake_registry,
)


class CatalogUseFramesDefaultsTests(unittest.TestCase):
    """Defaulty: use_frames=True, read_caption=True — bez zmiany zachowania."""

    def test_use_frames_default_true(self):
        d = get_plugin("reels_niche")["defaults"]["ai"]
        self.assertTrue(d.get("use_frames"))
        self.assertTrue(d.get("read_caption"))
        self.assertEqual(d.get("frames"), 2)

    def test_catalog_has_use_frames_field(self):
        keys = []
        for sec in get_plugin("reels_niche")["sections"]:
            for f in sec.get("fields") or []:
                keys.append(tuple(f["key"]))
        self.assertIn(("ai", "use_frames"), keys)
        self.assertIn(("ai", "read_caption"), keys)


class AiModeLineTests(unittest.TestCase):
    def test_both_on_silent(self):
        self.assertIsNone(reel_log.ai_mode_line(use_frames=True, read_caption=True))

    def test_frames_only(self):
        self.assertIn("klatki", reel_log.ai_mode_line(
            use_frames=True, read_caption=False).lower())

    def test_caption_only(self):
        self.assertIn("opis", reel_log.ai_mode_line(
            use_frames=False, read_caption=True).lower())

    def test_neither(self):
        self.assertIn("brak", reel_log.ai_mode_line(
            use_frames=False, read_caption=False).lower())

    def test_watch_line_caption_only(self):
        w = reel_log.watch_line(author="x", frames=0, caption_only=True,
                                likes=10, comments=2)
        self.assertIn("bez klatek", w)
        self.assertNotIn("Klatki", w)

    def test_reject_no_ai_inputs(self):
        msg = reel_log.ai_reject_human(why="no_ai_inputs", author="a")
        self.assertIn("brak wejsc", msg.lower())


class AnalyzeFramesAiInputModesTests(unittest.TestCase):
    """Skip bez wejść; text-only gdy jest caption bez composite."""

    def _ctx(self, scratch=None):
        ctx = FakeCtx()
        # Konto ze stub AI + zatwierdzoną niszą
        ctx.account = SimpleNamespace(
            raw={"ai": {"stub": True}},
            niche={"description": "fashion", "accepted": True},
        )
        ctx.scratch = dict(scratch or {})
        return ctx

    def test_skip_when_no_composite_and_no_caption(self):
        ctx = self._ctx({"reel": {"author": "x"}})
        with mock.patch("v2.plugins.store.niche_is_accepted", return_value=True):
            r = AnalyzeFramesAi().run(ctx, {
                "ai": {"use_frames": True, "read_caption": True, "stub": True},
            })
        self.assertEqual(r.status, "skipped")
        self.assertEqual((r.payload or {}).get("reason"), "no_ai_inputs")

    def test_skip_when_both_flags_off(self):
        ctx = self._ctx({
            "reel": {"author": "x"},
            "reel_composite": "/tmp/fake.jpg",
            "reel_caption": "hello #fashion",
        })
        with mock.patch("v2.plugins.store.niche_is_accepted", return_value=True):
            r = AnalyzeFramesAi().run(ctx, {
                "ai": {"use_frames": False, "read_caption": False, "stub": True},
            })
        self.assertEqual(r.status, "skipped")
        self.assertEqual((r.payload or {}).get("reason"), "no_ai_inputs")

    def test_caption_only_calls_vision_without_frames(self):
        ctx = self._ctx({
            "reel": {"author": "x"},
            "reel_caption": "#fashion outfit tip",
        })
        captured = {}

        def fake_run(mode, inputs, opts=None):
            captured["frames"] = list(inputs.get("frames") or [])
            captured["podpis"] = inputs.get("podpis")
            return {"niche_score": 80, "verdict": "match", "reasons": ["#fashion"],
                    "_stub": True}

        with mock.patch("v2.plugins.store.niche_is_accepted", return_value=True), \
             mock.patch("v2.steps.reels.analyze_frames_ai.AiVision") as Vis:
            Vis.return_value.run.side_effect = fake_run
            r = AnalyzeFramesAi().run(ctx, {
                "ai": {"use_frames": False, "read_caption": True},
            })
        self.assertEqual(r.status, "ok")
        self.assertEqual(captured["frames"], [])
        self.assertIn("#fashion", captured["podpis"])
        self.assertEqual((r.payload or {}).get("frames_sent"), 0)

    def test_frames_only_ignores_caption_in_scratch(self):
        """read_caption=false → nie bierz opisu ze scratch (celowe OFF)."""
        ctx = self._ctx({
            "reel": {"author": "x"},
            "reel_composite": "/tmp/grid.jpg",
            "reel_caption": "nie powinienes tego wyslac",
        })
        captured = {}

        def fake_run(mode, inputs, opts=None):
            captured["frames"] = list(inputs.get("frames") or [])
            captured["podpis"] = inputs.get("podpis")
            return {"niche_score": 50, "verdict": "partial", "reasons": ["img"],
                    "_stub": True}

        with mock.patch("v2.plugins.store.niche_is_accepted", return_value=True), \
             mock.patch("v2.steps.reels.analyze_frames_ai.AiVision") as Vis:
            Vis.return_value.run.side_effect = fake_run
            r = AnalyzeFramesAi().run(ctx, {
                "ai": {"use_frames": True, "read_caption": False},
            })
        self.assertEqual(r.status, "ok")
        self.assertEqual(captured["frames"], ["/tmp/grid.jpg"])
        self.assertIn("brak", captured["podpis"].lower())
        self.assertEqual(ctx.scratch.get("reel_caption_source"), "disabled")


class PluginPassesUseFramesTests(unittest.TestCase):
    """Plugin przekazuje use_frames / read_caption do reels.watch."""

    def setUp(self):
        from v2.plugins.impl import reels_niche as mod
        self.mod = mod
        self.recorder = []
        self._orig_get = mod.steps.get

    def tearDown(self):
        self.mod.steps.get = self._orig_get

    BASE = {
        "enabled": True,
        "gate": {"min_likes": 10, "min_comments": 1, "on_unknown": "skip"},
        "like_reel": {"enabled": False},
        "likers": {"harvest": {"enabled": False}},
        "comments": {"harvest": {"enabled": False}, "like_comments": {"enabled": False}},
    }

    def _run(self, settings):
        self.mod.steps.get = fake_registry({
            "reels.open": [],
            "reels.swipe_next": [],
            "reels.watch": [StepResult.ok()],
            "reels.analyze_frames_ai": ScratchStep(
                {"reel_ai": {"niche_score": 90, "verdict": "match"}},
                self.recorder, "x"),
        }, self.recorder)
        ctx = FakeCtx()
        self.mod.ReelsNiche().run(ctx, settings, Budget(max_items=1))
        watch_params = [p for n, p in self.recorder if n == "reels.watch"]
        return watch_params

    def test_default_passes_use_frames_true(self):
        s = {**self.BASE, "ai": {"enabled": True, "mode": "niche_match",
                                 "min_niche_score": 50, "allow_verdict": ["match", "partial"]}}
        params = self._run(s)
        self.assertTrue(params)
        self.assertTrue(params[0].get("use_frames"))
        self.assertTrue(params[0].get("read_caption"))

    def test_caption_only_mode(self):
        s = {**self.BASE, "ai": {
            "enabled": True, "use_frames": False, "read_caption": True,
            "mode": "niche_match", "min_niche_score": 50,
            "allow_verdict": ["match", "partial"],
        }}
        params = self._run(s)
        self.assertFalse(params[0].get("use_frames"))
        self.assertTrue(params[0].get("read_caption"))

    def test_frames_only_mode(self):
        s = {**self.BASE, "ai": {
            "enabled": True, "use_frames": True, "read_caption": False,
            "mode": "niche_match", "min_niche_score": 50,
            "allow_verdict": ["match", "partial"],
        }}
        params = self._run(s)
        self.assertTrue(params[0].get("use_frames"))
        self.assertFalse(params[0].get("read_caption"))


class PromptSupportsTextOnlyTests(unittest.TestCase):
    def test_prompt_mentions_no_image_case(self):
        import yaml
        data = yaml.safe_load(
            (ROOT / "ai" / "prompts" / "reel_niche.yml").read_text(encoding="utf-8"))
        blob = ((data.get("system") or "") + (data.get("user") or "")).lower()
        # Krótka wzmianka o trybie bez obrazu / tylko tekst
        self.assertTrue(
            "sam opis" in blob or "brak obrazu" in blob or "tylko tekst" in blob,
            "prompt powinien obsługiwać przypadek bez obrazu",
        )


if __name__ == "__main__":
    unittest.main()
