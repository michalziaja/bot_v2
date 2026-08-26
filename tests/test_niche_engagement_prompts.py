"""Testy briefu niszy w promptach engagement + osobnych wykluczeń + loose fallback."""
from __future__ import annotations
import unittest
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.ai.niche_inputs import effective_exclusions, niche_prompt_fields
from v2.ai.vision import _format, _load_prompt
from v2.plugins.catalog import get_plugin
from v2.steps.profile.visit import _pick_like_candidates


NICHE = {
    "description": "Agencja usług drona — mecze i nieruchomości",
    "audience": "Trenerzy piłkarscy, deweloperzy",
    "keywords": ["loty dronem", "nagrywanie meczów"],
    "hashtags": ["#dronephotography", "#droneshots"],
    "exclusions": ["Politics", "Celebrity news"],
}


class EffectiveExclusionsTests(unittest.TestCase):
    def test_plugin_list_wins_when_nonempty(self):
        got = effective_exclusions(
            NICHE, {"exclusions": ["dzieci", "ślub"]})
        self.assertEqual(got, ["dzieci", "ślub"])

    def test_empty_plugin_falls_back_to_niche(self):
        self.assertEqual(
            effective_exclusions(NICHE, {"exclusions": []}),
            ["Politics", "Celebrity news"])
        self.assertEqual(
            effective_exclusions(NICHE, {}),
            ["Politics", "Celebrity news"])
        self.assertEqual(
            effective_exclusions(NICHE, None),
            ["Politics", "Celebrity news"])

    def test_reels_and_profile_lists_are_independent(self):
        reels = effective_exclusions(NICHE, {"exclusions": ["gossip"]})
        grid = effective_exclusions(NICHE, {"exclusions": ["dzieci", "rodzina"]})
        self.assertEqual(reels, ["gossip"])
        self.assertEqual(grid, ["dzieci", "rodzina"])


class NichePromptFieldsTests(unittest.TestCase):
    def test_maps_all_brief_fields(self):
        f = niche_prompt_fields(NICHE, exclusions=["dzieci"])
        self.assertEqual(f["opis"], NICHE["description"])
        self.assertEqual(f["audiencja"], NICHE["audience"])
        self.assertEqual(f["slowa_kluczowe"], NICHE["keywords"])
        self.assertEqual(f["hashtagi"], NICHE["hashtags"])
        self.assertEqual(f["wykluczenia"], ["dzieci"])


class PromptFormatTests(unittest.TestCase):
    def test_reel_niche_user_contains_full_brief(self):
        prompt = _load_prompt("reel_niche")
        excl = effective_exclusions(NICHE, {"exclusions": ["Entertainment"]})
        text = _format(prompt["user"], {
            **niche_prompt_fields(NICHE, exclusions=excl),
            "autor": "foo",
            "podpis": "bar #dronephotography",
            "komentarze": "(brak)",
            "strictness_hint": "",
        })
        self.assertIn(NICHE["description"], text)
        self.assertIn(NICHE["audience"], text)
        self.assertIn("loty dronem", text)
        self.assertIn("#dronephotography", text)
        self.assertIn("Entertainment", text)
        self.assertNotIn("Politics", text)  # plugin wygrywa nad niche

    def test_profile_grid_user_contains_full_brief(self):
        prompt = _load_prompt("profile_grid")
        excl = ["dzieci", "rodzina", "para", "ślub"]
        text = _format(prompt["user"], {
            **niche_prompt_fields(NICHE, exclusions=excl),
            "username": "ktoz",
            "bio": "bio",
            "liczba_kafelkow": 6,
            "strictness_hint": "",
        })
        self.assertIn(NICHE["audience"], text)
        self.assertIn("nagrywanie meczów", text)
        self.assertIn("dzieci", text)
        self.assertIn("ślub", text)

    def test_yaml_placeholders_present(self):
        for name in ("reel_niche", "profile_grid"):
            data = yaml.safe_load(
                (ROOT / "ai" / "prompts" / f"{name}.yml").read_text(
                    encoding="utf-8"))
            user = data["user"]
            for ph in ("{opis}", "{audiencja}", "{slowa_kluczowe}",
                       "{hashtagi}", "{wykluczenia}", "{strictness_hint}"):
                self.assertIn(ph, user, f"{name}: brak {ph}")


class CatalogExclusionsFieldTests(unittest.TestCase):
    def test_reels_niche_ai_has_exclusions_default(self):
        d = get_plugin("reels_niche")["defaults"]["ai"]
        self.assertIn("exclusions", d)
        self.assertEqual(d["exclusions"], [])

    def test_profile_interact_grid_ai_has_exclusions_default(self):
        d = get_plugin("profile_interact")["defaults"]["grid_ai"]
        self.assertIn("exclusions", d)
        self.assertEqual(d["exclusions"], [])

    def test_exclusion_field_labels_differ_by_kind(self):
        """Rolki vs profil — różne etykiety w panelu."""
        def _labels(pid, prefix_key):
            out = []
            for sec in get_plugin(pid)["sections"]:
                for f in sec.get("fields") or []:
                    if f["key"][-2:] == [prefix_key, "exclusions"] or (
                            f["key"][-1] == "exclusions"
                            and prefix_key in f["key"]):
                        out.append(f["label"])
            return out

        reel_lbl = _labels("reels_niche", "ai")
        grid_lbl = _labels("profile_interact", "grid_ai")
        self.assertTrue(any("rolek" in x.lower() for x in reel_lbl))
        self.assertTrue(any("post" in x.lower() for x in grid_lbl))


class LooseFallbackPickTests(unittest.TestCase):
    @staticmethod
    def _tiles(specs):
        """specs: list of (category, should_like)."""
        return {str(i + 1): {"category": c, "should_like": sl}
                for i, (c, sl) in enumerate(specs)}

    def test_loose_fallback_picks_non_excluded_when_no_should_like(self):
        tiles = self._tiles([
            ("neutral", False),
            ("excluded", False),
            ("neutral", False),
        ])
        got = _pick_like_candidates(
            tiles, {"mode": "niche_match", "strictness": "loose"})
        self.assertEqual(got, ["1", "3"])

    def test_balanced_no_fallback_without_should_like(self):
        tiles = self._tiles([
            ("neutral", False),
            ("excluded", False),
            ("neutral", False),
        ])
        got = _pick_like_candidates(
            tiles, {"mode": "niche_match", "strictness": "balanced"})
        self.assertEqual(got, [])

    def test_excluded_never_in_candidates(self):
        tiles = self._tiles([
            ("excluded", True),
            ("niche", True),
            ("excluded", False),
        ])
        got = _pick_like_candidates(
            tiles, {"mode": "niche_match", "strictness": "loose"})
        self.assertEqual(got, ["2"])

    def test_best_sort_puts_niche_first(self):
        tiles = self._tiles([
            ("neutral", True),
            ("niche", True),
            ("neutral", True),
        ])
        got = _pick_like_candidates(
            tiles, {"mode": "niche_match", "strictness": "balanced",
                    "pick_strategy": "best"})
        self.assertEqual(got[0], "2")


if __name__ == "__main__":
    unittest.main()
