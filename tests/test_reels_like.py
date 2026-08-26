"""Testy silnika gate/match/chance w pluginie reels.like — bez telefonu."""
from __future__ import annotations
import random, unittest
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps.reels.like import _eval_gate, _eval_match, _eval_chance


class GateTests(unittest.TestCase):
    def test_no_gate_passes(self):
        self.assertTrue(_eval_gate({}, {})["pass"])

    def test_min_likes_pass(self):
        self.assertTrue(_eval_gate({"likes": 200}, {"min_likes": 100})["pass"])

    def test_min_likes_fail(self):
        r = _eval_gate({"likes": 30}, {"min_likes": 100})
        self.assertFalse(r["pass"])
        self.assertIn("likes<100", r["why"])

    def test_unknown_likes_recorded(self):
        r = _eval_gate({"likes": None}, {"min_likes": 100})
        self.assertFalse(r["pass"])
        self.assertIn("likes_unknown", r["why"])

    def test_combined_gate(self):
        r = _eval_gate({"likes": 500, "comments": 40},
                       {"min_likes": 100, "min_comments": 35})
        self.assertTrue(r["pass"])


class MatchTests(unittest.TestCase):
    def test_no_match_passes(self):
        self.assertTrue(_eval_match({}, {})["pass"])

    def test_needs_ai_when_match_configured(self):
        r = _eval_match({}, {"min_niche_score": 55})
        self.assertFalse(r["pass"])
        self.assertEqual(r["why"], "no_ai_analysis")

    def test_verdict_off_blocks(self):
        r = _eval_match({"verdict": "off", "niche_score": 80}, {"min_niche_score": 55})
        self.assertFalse(r["pass"])
        self.assertIn("verdict=off", r["why"])

    def test_score_below_threshold_blocks(self):
        r = _eval_match({"verdict": "match", "niche_score": 40}, {"min_niche_score": 55})
        self.assertFalse(r["pass"])

    def test_score_and_verdict_pass(self):
        r = _eval_match({"verdict": "match", "niche_score": 70}, {"min_niche_score": 55})
        self.assertTrue(r["pass"])


class ChanceTests(unittest.TestCase):
    def test_no_chance_passes(self):
        self.assertTrue(_eval_chance({}, {}, random.Random(0))["pass"])

    def test_static_probability_deterministic(self):
        # seed=0 → random.random() = 0.844... — p=0.5 → miss
        r = _eval_chance({}, {"p": 0.5}, random.Random(0))
        self.assertFalse(r["pass"])
        r2 = _eval_chance({}, {"p": 0.9}, random.Random(0))
        self.assertTrue(r2["pass"])

    def test_formula_score_over_100(self):
        r = _eval_chance({"niche_score": 90}, {"formula": "score/100"}, random.Random(5))
        self.assertEqual(r["p"], 0.9)


class LikeLogPrefixTests(unittest.TestCase):
    """Warmup podaje log_prefix — like ma powiedzieć Polubiono z tym tagiem."""

    def _ctx(self):
        from v2.tests.test_plugins_runtime import FakeCtx
        ctx = FakeCtx()
        ctx.scratch["reel"] = {"author": "bob", "likes": 10}
        ctx.device.tap = lambda *a, **k: None
        # like.py czyta rozdzielczość z account.device
        ctx.account = type("A", (), {
            "device": {"_hardware": {"resolution": [1080, 1920]}},
        })()
        return ctx

    def test_say_uses_log_prefix_on_success(self):
        from v2.plugins import reel_log
        from v2.steps.reels.like import LikeReel

        lines = []
        orig = reel_log.say
        reel_log.say = lambda msg, prefix=None: lines.append((msg, prefix))
        try:
            res = LikeReel().run(self._ctx(), {
                "log_prefix": "reels_warmup",
                "chance": {"mode": "p", "value": 1},
            })
            self.assertEqual(res.status, "ok")
            self.assertTrue(any(
                p == "reels_warmup" and "Polubiono" in m for m, p in lines))
        finally:
            reel_log.say = orig

    def test_no_say_without_log_prefix(self):
        """Niche/content_search mówią Polubiono sami — bez prefix cisza z kroku."""
        from v2.plugins import reel_log
        from v2.steps.reels.like import LikeReel

        lines = []
        orig = reel_log.say
        reel_log.say = lambda msg, prefix=None: lines.append((msg, prefix))
        try:
            LikeReel().run(self._ctx(), {"chance": {"mode": "p", "value": 1}})
            self.assertEqual(lines, [])
        finally:
            reel_log.say = orig


if __name__ == "__main__":
    unittest.main()
