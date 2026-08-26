"""Testy AI Vision — bez API key = stub mode."""
from __future__ import annotations
import os, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.ai.vision import (
    AiVision, _format, _resolve_model, _parse_json_response,
    _extract_usage, accumulate_usage, empty_usage_totals,
)


class FormatTests(unittest.TestCase):
    def test_substitutes_placeholders(self):
        self.assertEqual(_format("hi {name}, {age}", {"name": "X", "age": 5}), "hi X, 5")

    def test_list_becomes_csv(self):
        self.assertEqual(_format("{items}", {"items": ["a", "b"]}), "a, b")

    def test_missing_key_becomes_empty(self):
        self.assertEqual(_format("{a}-{missing}", {"a": "x"}), "x-")


class ResolveModelTests(unittest.TestCase):
    def test_short_names(self):
        self.assertTrue(_resolve_model("haiku",  None).startswith("claude-haiku"))
        self.assertTrue(_resolve_model("sonnet", None).startswith("claude-sonnet"))

    def test_full_id_passthrough(self):
        m = _resolve_model("claude-custom-x", None)
        self.assertEqual(m, "claude-custom-x")


class ParseJsonTests(unittest.TestCase):
    def test_extracts_json_from_prose(self):
        r = _parse_json_response("Blah blah {\"niche_score\": 80, \"verdict\": \"match\"} extra", "reel_niche")
        self.assertEqual(r["niche_score"], 80)

    def test_returns_error_on_no_json(self):
        r = _parse_json_response("no json here", "reel_niche")
        self.assertIn("error", r)

    def test_repairs_truncated_merge_json(self):
        # jak w sesji 20260808-072852: max_tokens ucięło reasons w połowie stringa
        truncated = (
            '```json\n{\n  "audience": "Kobiety 18-35",\n'
            '  "description": "Nisza beauty i fashion",\n'
            '  "keywords": ["makeup", "styling"],\n'
            '  "hashtags": ["#makeup", "#fashion"],\n'
            '  "reasons": [\n'
            '    "ok punkt",\n'
            '    "Audience: wspólny dla trzech źródeł — kobiety 18–35, poszukujące inspiracji,'
        )
        r = _parse_json_response(truncated, "niche_discover_merge")
        self.assertNotIn("error", r)
        self.assertTrue(r.get("_json_repaired"))
        self.assertEqual(r["description"], "Nisza beauty i fashion")
        self.assertEqual(r["keywords"], ["makeup", "styling"])
        self.assertEqual(len(r["hashtags"]), 2)


class UsageExtractTests(unittest.TestCase):
    """Anthropic usage.input_tokens / output_tokens + fallback."""

    def test_anthropic_usage_attrs(self):
        resp = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=120, output_tokens=40),
        )
        u = _extract_usage(resp, model_id="claude-haiku-x", mode="niche_discover_posts")
        self.assertEqual(u["tokens_in"], 120)
        self.assertEqual(u["tokens_out"], 40)
        self.assertEqual(u["model"], "claude-haiku-x")
        self.assertFalse(u["missing"])

    def test_openai_style_dict_usage(self):
        resp = SimpleNamespace(usage={
            "prompt_tokens": 10, "completion_tokens": 5,
        })
        u = _extract_usage(resp, model_id="m", mode="x")
        self.assertEqual(u["tokens_in"], 10)
        self.assertEqual(u["tokens_out"], 5)

    def test_missing_usage_graceful(self):
        resp = SimpleNamespace(content=[])
        u = _extract_usage(resp, model_id="m", mode="x")
        self.assertEqual(u["tokens_in"], 0)
        self.assertEqual(u["tokens_out"], 0)
        self.assertTrue(u["missing"])


class AccumulateUsageTests(unittest.TestCase):
    def test_sums_across_calls_and_modes(self):
        totals = empty_usage_totals()
        accumulate_usage(totals, {
            "_usage": {"model": "haiku", "mode": "niche_discover_posts",
                       "tokens_in": 100, "tokens_out": 20},
        }, mode="niche_discover_posts")
        accumulate_usage(totals, {
            "_usage": {"model": "sonnet", "mode": "niche_discover_account",
                       "tokens_in": 50, "tokens_out": 30},
        }, mode="niche_discover_account")
        self.assertEqual(totals["tokens_in"], 150)
        self.assertEqual(totals["tokens_out"], 50)
        self.assertEqual(totals["calls"], 2)
        self.assertIn("haiku", totals["models"])
        self.assertIn("sonnet", totals["models"])
        self.assertEqual(totals["by_mode"]["niche_discover_posts"]["tokens_in"], 100)
        self.assertEqual(totals["by_mode"]["niche_discover_account"]["tokens_out"], 30)

    def test_missing_usage_does_not_crash(self):
        totals = empty_usage_totals()
        accumulate_usage(totals, {"themes": []}, mode="niche_discover_reels")
        self.assertEqual(totals["tokens_in"], 0)
        self.assertTrue(totals["usage_missing"])
        self.assertEqual(totals["calls"], 1)


class StubModeTests(unittest.TestCase):
    def setUp(self):
        # wymuś stub bez względu na środowisko
        self.old_key = os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        if self.old_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = self.old_key

    def test_reel_niche_stub_off_when_author_matches_exclusion(self):
        v = AiVision(account={"ai": {"stub": True}})
        r = v.run("reel_niche", inputs={
            "opis":        "technologia outdoor",
            "wykluczenia": ["polityka"],
            "autor":       "polityka_daily",
            "frames":      [],
        })
        self.assertEqual(r["verdict"], "off")

    def test_reel_niche_stub_match_when_niche_nonempty(self):
        v = AiVision(account={"ai": {"stub": True}})
        r = v.run("reel_niche", inputs={
            "opis":        "technologia outdoor",
            "wykluczenia": ["polityka"],
            "autor":       "some.user",
            "frames":      [],
        })
        self.assertIn(r["verdict"], ("match", "partial"))
        self.assertIsInstance(r["niche_score"], int)

    def test_stub_fallback_when_no_api_key(self):
        # bez klucza (env + secrets) → stub; mockujemy get_api_key
        with mock.patch("v2.ai.vision._api_key", return_value=None):
            v = AiVision(account={"ai": {}})
            r = v.run("reel_niche", inputs={
                "opis": "x", "wykluczenia": [], "autor": "a",
            })
        self.assertTrue(r.get("_stub"))
        self.assertIn("_usage", r)

    def test_stub_attaches_zero_usage(self):
        v = AiVision(account={"ai": {"stub": True}})
        r = v.run("niche_discover_posts", inputs={
            "bio": "x", "seed": "y", "captions_numbered": "1. hello",
            "frames": [],
        })
        u = r.get("_usage") or {}
        self.assertEqual(u.get("tokens_in"), 0)
        self.assertEqual(u.get("tokens_out"), 0)
        self.assertTrue(str(u.get("model", "")).startswith("stub"))


if __name__ == "__main__":
    unittest.main()
