"""Testy wczesnej bramki w reels.watch + helperów live-logów reels_niche."""
from __future__ import annotations
import unittest
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps.reels.watch import _eval_early_gate
from v2.plugins import reel_log
from v2.plugins.catalog import get_plugin


class EarlyGateTests(unittest.TestCase):
    def test_no_gate_passes(self):
        self.assertTrue(_eval_early_gate({"likes": 5, "comments": 1}, {})["pass"])

    def test_min_likes_pass(self):
        self.assertTrue(_eval_early_gate({"likes": 200}, {"min_likes": 100})["pass"])

    def test_min_likes_fail(self):
        r = _eval_early_gate({"likes": 30}, {"min_likes": 100})
        self.assertFalse(r["pass"])
        self.assertIn("likes<100", r["why"])

    def test_max_likes_fail_live_bug(self):
        """Live: ~1.55M lajków przy max_likes=50k → skip (gate_failed)."""
        r = _eval_early_gate(
            {"likes": 1_551_979, "comments": 3219},
            {"min_likes": 100, "max_likes": 50_000, "min_comments": 35},
        )
        self.assertFalse(r["pass"])
        self.assertIn("likes>50000", r["why"])
        # min_likes / min_comments nadal OK — tylko górny limit
        self.assertNotIn("likes<", r["why"])
        self.assertNotIn("comments<", r["why"])

    def test_max_likes_pass_at_boundary(self):
        """likes == max_likes przechodzi; dopiero > odrzuca."""
        gate = {"max_likes": 50_000}
        self.assertTrue(_eval_early_gate({"likes": 50_000}, gate)["pass"])
        self.assertFalse(_eval_early_gate({"likes": 50_001}, gate)["pass"])

    def test_min_likes_unaffected_by_max(self):
        """max_likes nie psuje min_likes — za mało lajków nadal reject."""
        r = _eval_early_gate(
            {"likes": 30},
            {"min_likes": 100, "max_likes": 50_000},
        )
        self.assertFalse(r["pass"])
        self.assertIn("likes<100", r["why"])
        self.assertNotIn("likes>", r["why"])

    def test_min_comments_fail(self):
        r = _eval_early_gate({"comments": 5}, {"min_comments": 35})
        self.assertFalse(r["pass"])
        self.assertIn("comments<35", r["why"])

    def test_combined_all_pass(self):
        r = _eval_early_gate({"likes": 500, "comments": 40},
                             {"min_likes": 100, "min_comments": 35})
        self.assertTrue(r["pass"])

    def test_unknown_default_skips(self):
        r = _eval_early_gate({"likes": None}, {"min_likes": 100})
        self.assertFalse(r["pass"])
        self.assertIn("likes_unknown", r["why"])

    def test_unknown_on_pass_config(self):
        r = _eval_early_gate({"likes": None},
                             {"min_likes": 100, "on_unknown": "pass"})
        self.assertTrue(r["pass"])

    def test_multi_reason_reported(self):
        r = _eval_early_gate({"likes": 50, "comments": 10},
                             {"min_likes": 100, "min_comments": 35})
        self.assertIn("likes<100", r["why"])
        self.assertIn("comments<35", r["why"])


class LiveLogHelpersTests(unittest.TestCase):
    """Komunikaty startowe — terminal na równi z gestami / AI."""

    def test_caption_reading_before_result(self):
        self.assertIn("opis", reel_log.caption_reading_line().lower())

    def test_ai_start_line(self):
        self.assertIn("AI", reel_log.ai_start_line())

    def test_gate_ok_and_watch_lines(self):
        w = reel_log.watch_line(author="x", frames=2, interval_s=0.5,
                                likes=100, comments=10)
        self.assertIn("@x", w)
        self.assertIn("Klatki", w)
        g = reel_log.gate_ok_line(author="x", likes=100, comments=10)
        self.assertIn("Filtr OK", g)

    def test_say_prefixes_and_order_smoke(self):
        """Kolejność typowa dla gate-OK: klatki → filtr → opis → AI."""
        lines = []
        real_print = print

        def capture(*args, **kwargs):
            lines.append(" ".join(str(a) for a in args))

        import builtins
        builtins.print = capture
        try:
            reel_log.say(reel_log.watch_line(author="a", frames=2, interval_s=0.5))
            reel_log.say(reel_log.gate_ok_line(author="a", likes=200, comments=20))
            reel_log.say(reel_log.caption_reading_line())
            reel_log.say(reel_log.caption_line("#drone test"))
            reel_log.say(reel_log.ai_start_line())
            reel_log.say_ai_result({
                "niche_score": 70, "verdict": "match", "reasons": ["#drone"],
            })
            reel_log.say(reel_log.next_reel_line())
        finally:
            builtins.print = real_print

        joined = "\n".join(lines)
        self.assertIn("reels_niche", joined)
        # Kolejność: Klatki przed Filtr OK przed Czytam opis przed Analiza AI
        i_klatki = joined.find("Klatki")
        i_filtr = joined.find("Filtr OK")
        i_opis = joined.find("Czytam opis")
        i_ai = joined.find("Analiza AI")
        i_next = joined.find("Nastepna rolka")
        self.assertLess(i_klatki, i_filtr)
        self.assertLess(i_filtr, i_opis)
        self.assertLess(i_opis, i_ai)
        self.assertLess(i_ai, i_next)
        # Jedna ścieżka wyniku AI (score), bez osobnego ai_ok_line w tym smoke
        self.assertIn("score: 70", joined)
        self.assertNotIn("AI: pasuje", joined)

    def test_say_accepts_content_search_prefix(self):
        """content_search nie może logować jako reels_niche."""
        lines = []
        import builtins
        real = builtins.print
        builtins.print = lambda *a, **k: lines.append(" ".join(str(x) for x in a))
        try:
            reel_log.say("Rolka @x (bez klatek)", prefix="content_search")
        finally:
            builtins.print = real
        self.assertTrue(any("content_search" in ln for ln in lines))
        self.assertFalse(any("reels_niche" in ln for ln in lines))

    def test_watch_line_frames_zero_is_bez_klatek(self):
        line = reel_log.watch_line(author="x", frames=0, caption_only=True)
        self.assertIn("bez klatek", line.lower())
        self.assertNotIn("Klatki", line)


class ReelNicheDefaultsAndPromptTests(unittest.TestCase):
    def test_catalog_read_top_comments_default_false(self):
        d = get_plugin("reels_niche")["defaults"]["ai"]
        self.assertFalse(d.get("read_top_comments"))
        self.assertEqual(d.get("frames"), 2)
        self.assertEqual(d.get("frame_interval_s"), 0.5)
        # Niezależne wejścia AI — domyślnie oba włączone (stare zachowanie)
        self.assertTrue(d.get("use_frames", True))
        self.assertTrue(d.get("read_caption", True))

    def test_prompt_slim_no_beauty_bias(self):
        data = yaml.safe_load(
            (ROOT / "ai" / "prompts" / "reel_niche.yml").read_text(
                encoding="utf-8"))
        self.assertLessEqual(int(data["max_tokens"]), 200)
        blob = (data.get("system") or "") + (data.get("user") or "")
        for banned in ("beauty", "makeup", "outfit", "lookbook"):
            self.assertNotIn(banned, blob.lower())
        self.assertIn("{slowa_kluczowe}", data["user"])
        self.assertIn("{hashtagi}", data["user"])


if __name__ == "__main__":
    unittest.main()
