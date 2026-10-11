"""Smoke: ludzkie logi profile_interact (prefix + kolejnosc typowej sciezki)."""
from __future__ import annotations

import builtins
import unittest

from v2.plugins import profile_log


class ProfileLogSmokeTests(unittest.TestCase):
    def test_say_prefixes_and_typical_order(self):
        lines = []
        real_print = print

        def capture(*args, **kwargs):
            lines.append(" ".join(str(a) for a in args))

        builtins.print = capture
        try:
            profile_log.say(profile_log.visit_start_line("alice"))
            profile_log.say(profile_log.sanity_reject_line("became_private", "bob"))
            profile_log.say(profile_log.gate_fail_line("ratio<0.5,bio_excl:shop", "carol"))
            profile_log.say(profile_log.story_watching_line())
            profile_log.say(profile_log.grid_capture_line())
            profile_log.say(profile_log.grid_ai_wait_line())
            profile_log.say_grid_ai_result({
                "audience_fit": "match",
                "audience_reason": "trener, siatka lifestyle",
                "tiles": {
                    "1": {"category": "niche", "reason": "CNC w warsztacie"},
                    "2": {"category": "excluded", "reason": "Reklama sklepu"},
                    "3": {"category": "neutral", "reason": "Selfie"},
                    "5": {"category": "niche", "reason": "Narzedzia na stole"},
                },
            })
            profile_log.say(profile_log.match_ok_line(n_niche=2, n_excluded=1))
            profile_log.say(profile_log.tiles_like_line([1, 3], 1))
            profile_log.say(profile_log.follow_ok_line(scoring=0.72))
            profile_log.say(profile_log.next_profile_line("dave"))
        finally:
            builtins.print = real_print

        joined = "\n".join(lines)
        self.assertIn("profile_interact", joined)
        self.assertIn("Wizyta @alice", joined)
        self.assertIn("ODRZUT: prywatny", joined)
        self.assertIn("bramka", joined)
        self.assertIn("Relacja — ogladam", joined)
        self.assertIn("Siatka — robie zrzut", joined)
        self.assertIn("czekam na AI", joined)
        self.assertIn("Grupa docelowa — match: trener, siatka lifestyle", joined)
        self.assertIn("safe 2", joined)
        self.assertIn("excluded 1", joined)
        # Kafelki safe (legacy niche→safe) z reason
        self.assertIn("pasuje: 1, 5", joined)
        self.assertIn("Kafelek 1 — safe: CNC w warsztacie", joined)
        self.assertIn("Kafelek 5 — safe: Narzedzia na stole", joined)
        self.assertIn("Kafelek 2 — excluded: Reklama sklepu", joined)
        self.assertIn("OK (siatka)", joined)
        self.assertIn("Lajki kafelkow", joined)
        self.assertIn("Follow", joined)
        self.assertIn("Kolejny profil", joined)
        # Kolejnosc: wizyta przed siatka przed follow przed kolejnym
        self.assertLess(joined.find("Wizyta"), joined.find("Siatka — robie"))
        self.assertLess(joined.find("czekam na AI"), joined.find("Follow"))
        self.assertLess(joined.find("Follow"), joined.find("Kolejny profil"))

    def test_left_profile_lines(self):
        """Raw top=/where= → krótka fraza PL (szczegóły w JSONL)."""
        self.assertEqual(
            profile_log.left_profile_line("after_like_tiles", top="profile_self"),
            "Profil — utracony ekran (po lajkach kafelków)")
        self.assertEqual(
            profile_log.left_profile_line("after_story"),
            "Profil — utracony ekran (po relacji)")
        self.assertIn("nie na profilu",
                      profile_log.left_profile_line(None, top="home_feed"))
        self.assertIn("home_feed",
                      profile_log.left_profile_line("weird", top="home_feed"))

    def test_follow_fail_explains_why(self):
        """action=follow ale followed=false — nie goły „Skip follow — score”."""
        line = profile_log.follow_skip_line(scoring=0.79, reason="brak przycisku")
        self.assertIn("brak przycisku", line)
        self.assertIn("0.79", line)
        self.assertTrue(line.startswith("Follow — nie"))

    def test_grid_ai_no_niche_shows_brak(self):
        lines = []
        real_print = print

        def capture(*args, **kwargs):
            lines.append(" ".join(str(a) for a in args))

        builtins.print = capture
        try:
            profile_log.say_grid_ai_result({
                "tiles": {
                    "1": {"category": "excluded", "reason": "Ads"},
                    "2": {"category": "neutral", "reason": "Cat"},
                },
            })
        finally:
            builtins.print = real_print
        joined = "\n".join(lines)
        self.assertIn("pasuje: (brak)", joined)
        self.assertIn("Kafelek 1 — excluded: Ads", joined)

    def test_grid_ai_accepts_alt_reason_keys_and_casing(self):
        """AI czasem: reasons[] / powod / Category casing — i tak pokaż linię."""
        lines = []
        real_print = print

        def capture(*args, **kwargs):
            lines.append(" ".join(str(a) for a in args))

        builtins.print = capture
        try:
            profile_log.say_grid_ai_result({
                "tiles": {
                    "2": {"category": "Niche", "reasons": ["Frezarka w akcji"]},
                    "7": {"category": "NICHE", "powod": "Warsztat CNC"},
                },
            })
            # Drugie wywołanie — idempotent (bez duplikatu)
            profile_log.say_grid_ai_result({
                "tiles": {
                    "2": {"category": "Niche", "reasons": ["Frezarka w akcji"]},
                },
                "_grid_ai_said": True,
            })
        finally:
            builtins.print = real_print
        joined = "\n".join(lines)
        self.assertIn("pasuje: 2, 7", joined)
        self.assertIn("Kafelek 2 — safe: Frezarka w akcji", joined)
        self.assertIn("Kafelek 7 — safe: Warsztat CNC", joined)
        # Jedno podsumowanie (drugie wywołanie z flagą nic nie dodało)
        self.assertEqual(joined.count("Siatka AI —"), 1)

    def test_grid_ai_list_tiles_normalized(self):
        lines = []
        real_print = print

        def capture(*args, **kwargs):
            lines.append(" ".join(str(a) for a in args))

        builtins.print = capture
        try:
            profile_log.say_grid_ai_result({
                "tiles": [
                    {"num": 3, "category": "niche", "reason": "Tokarka"},
                    {"id": 5, "category": "excluded", "reason": "Sklep"},
                ],
            })
        finally:
            builtins.print = real_print
        joined = "\n".join(lines)
        self.assertIn("pasuje: 3", joined)
        self.assertIn("Kafelek 3 — safe: Tokarka", joined)
        self.assertIn("Kafelek 5 — excluded: Sklep", joined)

    def test_match_fail_audience_fit_is_human(self):
        line = profile_log.match_fail_line("audience_fit=off")
        self.assertIn("grupa docelowa", line)
        self.assertIn("audience_fit=off", line)

    def test_tiles_like_despite_skip_line(self):
        self.assertIn("bez follow", profile_log.tiles_like_despite_skip_line())

    def test_grid_ai_workspace_id_error_is_human(self):
        lines = []
        real_print = print

        def capture(*args, **kwargs):
            lines.append(" ".join(str(a) for a in args))

        builtins.print = capture
        try:
            profile_log.say_grid_ai_result({"_error": "anthropic_workspace_id"})
        finally:
            builtins.print = real_print
        self.assertIn("workspace ID", "\n".join(lines))

    def test_norm_tile_category_legacy_aliases(self):
        self.assertEqual(profile_log.norm_tile_category("niche"), "safe")
        self.assertEqual(profile_log.norm_tile_category("Neutral"), "skipped")
        self.assertEqual(profile_log.norm_tile_category("safe"), "safe")


if __name__ == "__main__":
    unittest.main()
