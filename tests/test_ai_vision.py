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
    _extract_usage, accumulate_usage, empty_usage_totals, merge_usage_totals,
    _record_session_ai_usage,
    _strictness_hint, _STRICTNESS_HINTS, _STRICTNESS_HINTS_GRID,
    _anthropic_client_kwargs, _is_workspace_id_error,
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
        self.assertEqual(totals["by_model"]["haiku"]["tokens_in"], 100)
        self.assertEqual(totals["by_model"]["sonnet"]["tokens_out"], 30)
        self.assertIn("cost_usd", totals)
        self.assertIn("cost_usd", totals["by_model"]["haiku"])

    def test_merge_usage_totals_sums_models(self):
        a = empty_usage_totals()
        accumulate_usage(a, {
            "_usage": {"model": "claude-haiku-4-5", "mode": "grid",
                       "tokens_in": 10, "tokens_out": 2},
        })
        b = empty_usage_totals()
        accumulate_usage(b, {
            "_usage": {"model": "claude-haiku-4-5", "mode": "grid",
                       "tokens_in": 5, "tokens_out": 1},
        })
        merge_usage_totals(a, b)
        self.assertEqual(a["tokens_in"], 15)
        self.assertEqual(a["tokens_out"], 3)
        self.assertEqual(a["calls"], 2)
        self.assertEqual(a["by_model"]["claude-haiku-4-5"]["calls"], 2)

    def test_missing_usage_does_not_crash(self):
        totals = empty_usage_totals()
        accumulate_usage(totals, {"themes": []}, mode="niche_discover_reels")
        self.assertEqual(totals["tokens_in"], 0)
        self.assertTrue(totals["usage_missing"])
        self.assertEqual(totals["calls"], 1)


class RecordSessionAiUsageTests(unittest.TestCase):
    def test_skips_stub_and_zero_tokens(self):
        called = []
        ctx = SimpleNamespace(
            session_stats=SimpleNamespace(
                add_ai_usage=lambda *a, **k: called.append(1)),
            log=lambda *a, **k: None,
        )
        _record_session_ai_usage(ctx, {
            "_usage": {"model": "stub", "tokens_in": 10, "tokens_out": 1},
        })
        _record_session_ai_usage(ctx, {
            "_usage": {"model": "claude-haiku-4-5", "tokens_in": 0, "tokens_out": 0},
        })
        self.assertEqual(called, [])

    def test_records_real_usage(self):
        called = []
        ctx = SimpleNamespace(
            session_stats=SimpleNamespace(
                add_ai_usage=lambda *a, **k: called.append((a, k))),
            log=lambda *a, **k: None,
        )
        result = {"_usage": {
            "model": "claude-haiku-4-5-20251001",
            "mode": "profile_grid",
            "tokens_in": 80, "tokens_out": 20,
        }}
        _record_session_ai_usage(ctx, result)
        self.assertEqual(len(called), 1)


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

    def test_profile_grid_stub_has_audience_fit_and_safe_tiles(self):
        """Stub bez API — nowy kontrakt, nie category=niche."""
        v = AiVision(account={"ai": {"stub": True}})
        r = v.run("profile_grid", inputs={
            "opis": "trenerzy piłkarscy",
            "username": "coach.jan",
            "bio": "trener U12",
            "wykluczenia": [],
        })
        self.assertTrue(r.get("_stub"))
        self.assertIn(r.get("audience_fit"), ("match", "partial"))
        self.assertTrue(str(r.get("audience_reason") or "").strip())
        tiles = r.get("tiles") or {}
        self.assertTrue(tiles)
        cats = {t.get("category") for t in tiles.values()}
        self.assertTrue(cats <= {"safe", "skipped", "excluded"})
        self.assertNotIn("niche", cats)

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


class GridStrictnessHintTests(unittest.TestCase):
    def test_grid_hint_separates_tiles_from_niche(self):
        """Siatka nie może dostać hintu rolek („związek z niszą”)."""
        h = _strictness_hint("balanced", mode="profile_grid")
        self.assertNotEqual(h, _STRICTNESS_HINTS["balanced"])
        self.assertIn("safe", h.lower())
        self.assertEqual(len(set(_STRICTNESS_HINTS_GRID.values())), 3)


class AnthropicWorkspaceTests(unittest.TestCase):
    def test_client_kwargs_include_workspace_header(self):
        with mock.patch("v2.ai.vision._api_key", return_value="sk-ant-x"), \
             mock.patch("v2.ai.vision._workspace_id", return_value="wrkspc_abc"):
            kw = _anthropic_client_kwargs()
        self.assertEqual(kw["api_key"], "sk-ant-x")
        self.assertEqual(kw["default_headers"]["anthropic-workspace-id"],
                         "wrkspc_abc")

    def test_client_kwargs_omit_header_when_no_workspace(self):
        with mock.patch("v2.ai.vision._api_key", return_value="sk-ant-x"), \
             mock.patch("v2.ai.vision._workspace_id", return_value=None):
            kw = _anthropic_client_kwargs()
        self.assertNotIn("default_headers", kw)

    def test_detects_identity_linked_400(self):
        err = Exception(
            "Error code: 400 - anthropic-workspace-id is required when "
            "authenticating with an identity-linked API key")
        self.assertTrue(_is_workspace_id_error(err))
        self.assertFalse(_is_workspace_id_error(Exception("rate limit")))


if __name__ == "__main__":
    unittest.main()
