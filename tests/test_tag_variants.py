"""Smoke: merge/dedup niche.tag_variants + defaults catalog content_search."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from v2.plugins import store
from v2.plugins.catalog import get_plugin, plugin_ids


class MergeTagVariantsTests(unittest.TestCase):
    def test_merge_append_dedup_casefold(self):
        niche = {"tag_variants": {"#Fitness": ["#gym", "#Workout"]}}
        out = store.merge_tag_variants(
            niche, "#fitness", ["#GYM", "#fitgirl", "nohash"],
        )
        # zachowaj pierwszy klucz (#Fitness), dedup #gym, dopisz nowe z #
        self.assertIn("#Fitness", out)
        self.assertNotIn("#fitness", out)
        self.assertEqual(out["#Fitness"], ["#gym", "#Workout", "#fitgirl", "#nohash"])

    def test_exclude_seed_default(self):
        out = store.merge_tag_variants(
            {}, "#tech", ["#tech", "#techgear"], include_seed=False,
        )
        self.assertEqual(out["#tech"], ["#techgear"])

    def test_keyword_expression_key(self):
        out = store.merge_tag_variants(
            {}, "fitness training", ["#workout", "#gym"],
        )
        self.assertEqual(list(out.keys()), ["fitness training"])
        self.assertEqual(out["fitness training"], ["#workout", "#gym"])

    def test_max_variants_no_trim_keeps_old(self):
        niche = {"tag_variants": {"#a": ["#1", "#2", "#3"]}}
        out = store.merge_tag_variants(
            niche, "#a", ["#4"], max_variants=2, trim=False,
        )
        # już > limitu — nie kasuj, nie doklejaj
        self.assertEqual(out["#a"], ["#1", "#2", "#3"])

    def test_max_variants_trim(self):
        niche = {"tag_variants": {"#a": ["#1", "#2", "#3"]}}
        out = store.merge_tag_variants(
            niche, "#a", ["#4"], max_variants=2, trim=True,
        )
        self.assertEqual(out["#a"], ["#1", "#2"])

    def test_reconcile_preserves_tag_variants(self):
        prev = {
            "keywords_manual": [],
            "keywords_discovered": [],
            "hashtags_manual": [],
            "hashtags_discovered": [],
            "tag_variants": {"#x": ["#y"]},
        }
        new = {"keywords": [], "hashtags": [], "description": "d"}
        out = store.reconcile_niche_phrases(prev, new)
        self.assertEqual(out["tag_variants"], {"#x": ["#y"]})

    def test_write_niche_tag_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accounts = root / "accounts"
            accounts.mkdir()
            path = accounts / "demo.json"
            path.write_text(json.dumps({
                "account_id": "demo",
                "niche": {"tag_variants": {}},
            }, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(store, "CONFIG_ROOT", root):
                saved = store.write_niche_tag_variants(
                    "demo", "#foo", ["bar", "#baz"],
                )
            self.assertEqual(saved["tag_variants"]["#foo"], ["#bar", "#baz"])
            cfg = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(cfg["niche"]["tag_variants"]["#foo"], ["#bar", "#baz"])


class CatalogTagVariantsFlagsTests(unittest.TestCase):
    def test_content_search_defaults(self):
        pl = get_plugin("content_search")
        d = pl["defaults"]
        self.assertFalse(d["discover_tag_variants"]["enabled"])
        self.assertEqual(d["discover_tag_variants"]["mode"], "collect")
        self.assertEqual(d["discover_tag_variants"]["max_variants"], 4)
        self.assertFalse(d["tag_variants_budget"]["enabled"])
        # 0 = tylko zbierz listę; ≥1 włącza harvest w normalize()
        self.assertEqual(d["tag_variants_budget"]["max_phrases"], 0)
        self.assertEqual(d["tag_variants_budget"]["tiles_per_phrase"], 1)
        self.assertEqual(d["tag_variants_budget"]["scrolls"], 4)
        self.assertEqual(d["tag_variants_budget"]["source"], "live_collect")
        # plugin jest w rejestrze
        self.assertIn("content_search", plugin_ids())

    def test_unified_tags_section_in_schema(self):
        """Jedna sekcja Znaczniki — bez osobnego „Budżet znaczników”."""
        pl = get_plugin("content_search")
        titles = [sec.get("title") for sec in (pl.get("sections") or [])]
        self.assertIn("Znaczniki", titles)
        self.assertNotIn("Budżet znaczników (gałąź B)", titles)
        self.assertNotIn("Znaczniki (warianty IG)", titles)


class NormalizeTagVariantsSettingsTests(unittest.TestCase):
    """Adapter UI: ile otworzyć / legacy mode=grid → budget B."""

    def test_max_phrases_enables_budget(self):
        from v2.plugins.impl.content_search import normalize_tag_variants_settings

        out = normalize_tag_variants_settings({
            "discover_tag_variants": {"enabled": True, "mode": "collect"},
            "tag_variants_budget": {
                "max_phrases": 2, "tiles_per_phrase": 1, "scrolls": 4,
                "source": "live_collect",
            },
        })
        self.assertTrue(out["tag_variants_budget"]["enabled"])
        self.assertEqual(out["tag_variants_budget"]["max_phrases"], 2)
        self.assertEqual(out["discover_tag_variants"]["mode"], "collect")

    def test_zero_open_disables_budget(self):
        from v2.plugins.impl.content_search import normalize_tag_variants_settings

        out = normalize_tag_variants_settings({
            "discover_tag_variants": {"enabled": True},
            "tag_variants_budget": {"enabled": True, "max_phrases": 0},
        })
        self.assertFalse(out["tag_variants_budget"]["enabled"])

    def test_legacy_mode_grid_becomes_budget_one(self):
        from v2.plugins.impl.content_search import normalize_tag_variants_settings

        out = normalize_tag_variants_settings({
            "discover_tag_variants": {"enabled": True, "mode": "grid"},
            "per_phrase": {
                "items": {"min": 1, "max": 1},
                "scrolls_in_viewer": {"min": 6, "max": 6},
            },
        })
        tvb = out["tag_variants_budget"]
        self.assertTrue(tvb["enabled"])
        self.assertEqual(tvb["max_phrases"], 1)
        self.assertEqual(tvb["tiles_per_phrase"], 1)
        self.assertEqual(tvb["scrolls"], 6)
        self.assertEqual(tvb["source"], "live_collect")
        self.assertEqual(out["discover_tag_variants"]["mode"], "collect")

    def test_scrolls_force_single_tile(self):
        from v2.plugins.impl.content_search import normalize_tag_variants_settings

        out = normalize_tag_variants_settings({
            "discover_tag_variants": {"enabled": True},
            "tag_variants_budget": {
                "max_phrases": 2, "tiles_per_phrase": 3, "scrolls": 4,
            },
        })
        self.assertEqual(out["tag_variants_budget"]["tiles_per_phrase"], 1)

    def test_discover_off_disables_budget(self):
        from v2.plugins.impl.content_search import normalize_tag_variants_settings

        out = normalize_tag_variants_settings({
            "discover_tag_variants": {"enabled": False},
            "tag_variants_budget": {"max_phrases": 3},
        })
        self.assertFalse(out["tag_variants_budget"]["enabled"])


class ApplyRunScopeTests(unittest.TestCase):
    """Izolacja gałęzi A/B wg run_scope z karty kolejki."""

    def test_phrases_disables_branch_b(self):
        from v2.plugins.impl.content_search import (
            _apply_run_scope, normalize_tag_variants_settings,
        )
        s = normalize_tag_variants_settings({
            "run_scope": "phrases",
            "discover_tag_variants": {"enabled": True},
            "tag_variants_budget": {"max_phrases": 3, "source": "from_map"},
        })
        out = _apply_run_scope(s)
        self.assertEqual(out["run_scope"], "phrases")
        self.assertFalse(out["tag_variants_budget"]["enabled"])

    def test_tag_variants_forces_from_map_and_fallback_count(self):
        from v2.plugins.impl.content_search import _apply_run_scope
        out = _apply_run_scope({
            "run_scope": "tag_variants",
            "phrases": {"per_session": {"min": 2, "max": 4}},
            "tag_variants_budget": {
                "max_phrases": 0, "source": "live_collect", "enabled": False,
            },
            "discover_tag_variants": {"enabled": False},
        })
        tvb = out["tag_variants_budget"]
        self.assertTrue(tvb["enabled"])
        self.assertEqual(tvb["source"], "from_map")
        self.assertEqual(tvb["max_phrases"], 4)

    def test_both_is_passthrough(self):
        from v2.plugins.impl.content_search import _apply_run_scope
        tvb = {"enabled": True, "max_phrases": 2, "source": "live_collect"}
        out = _apply_run_scope({"run_scope": "both", "tag_variants_budget": tvb})
        self.assertTrue(out["tag_variants_budget"]["enabled"])
        self.assertEqual(out["tag_variants_budget"]["source"], "live_collect")

    def test_mix_disables_branch_b_and_defaults_mix_from(self):
        from v2.plugins.impl.content_search import _apply_run_scope
        out = _apply_run_scope({
            "run_scope": "mix",
            "tag_variants_budget": {
                "enabled": True, "source": "live_collect", "max_phrases": 3,
            },
        })
        tvb = out["tag_variants_budget"]
        self.assertEqual(out["run_scope"], "mix")
        self.assertFalse(tvb["enabled"])
        self.assertEqual(tvb["source"], "from_map")
        self.assertEqual(tvb["map_seed_kind"], "both")
        self.assertEqual(
            out["mix_from"], ["hashtag", "keyword", "tag_variants"])

    def test_mix_keeps_explicit_mix_from(self):
        from v2.plugins.impl.content_search import _apply_run_scope
        out = _apply_run_scope({
            "run_scope": "mix",
            "mix_from": ["keyword", "tag_variants", "bogus"],
            "tag_variants_budget": {"map_seed_kind": "keyword"},
        })
        self.assertEqual(out["mix_from"], ["keyword", "tag_variants"])
        self.assertEqual(out["tag_variants_budget"]["map_seed_kind"], "keyword")


class MixPoolAndSeedKindTests(unittest.TestCase):
    """Filtr kluczy mapy + jedna pula mix (bez urządzenia)."""

    def _ctx(self, mapping):
        ctx = mock.MagicMock()
        ctx.account.niche = {"tag_variants": mapping}
        ctx.scratch = {}
        return ctx

    def test_variants_from_map_filters_by_seed_kind(self):
        from v2.plugins.impl import content_search as mod
        ctx = self._ctx({
            "#kariera": ["#praca", "#job"],
            "portale pracy": ["#linkedin", "#cv"],
        })
        self.assertEqual(
            mod._variants_from_niche_map(ctx, "hashtag"),
            ["#praca", "#job"],
        )
        self.assertEqual(
            mod._variants_from_niche_map(ctx, "keyword"),
            ["#linkedin", "#cv"],
        )
        self.assertEqual(
            mod._variants_from_niche_map(ctx, "both"),
            ["#praca", "#job", "#linkedin", "#cv"],
        )

    def test_pick_mix_hashtag_plus_tags_skips_keyword_keys(self):
        import random
        from v2.plugins.impl import content_search as mod

        ctx = self._ctx({
            "#fitness": ["#gym", "#workout"],
            "portale pracy": ["#praca"],
        })
        s = {
            "run_scope": "mix",
            "mix_from": ["hashtag", "tag_variants"],
            "phrases": {
                "hashtags": ["#fitness"],
                "keywords": ["nie bierz tego"],
                "per_session": {"min": 20, "max": 20},
                "order": "sequential",
            },
            "tag_variants_budget": {"map_seed_kind": "hashtag"},
        }
        out = mod._pick_mix_items(ctx, s, random.Random(0))
        queries = [x["query"] for x in out]
        kinds = {x["kind"] for x in out}
        self.assertIn("#fitness", queries)
        self.assertIn("#gym", queries)
        self.assertNotIn("#praca", queries)
        self.assertIn("hashtags", kinds)
        self.assertIn("tag_variants", kinds)
        # keyword z listy fraz nie wchodzi do tego mixu
        self.assertNotIn("nie bierz tego", queries)

    def test_pick_mix_dedupes_same_query_on_list_and_map(self):
        import random
        from v2.plugins.impl import content_search as mod

        ctx = self._ctx({"#a": ["#gym"]})
        s = {
            "mix_from": ["hashtag", "tag_variants"],
            "phrases": {
                "hashtags": ["#gym"],
                "per_session": 5,
                "order": "sequential",
            },
            "tag_variants_budget": {"map_seed_kind": "both"},
        }
        out = mod._pick_mix_items(ctx, s, random.Random(0))
        gym = [x for x in out if str(x["query"]).casefold() == "#gym"]
        self.assertEqual(len(gym), 1)


class ContentSearchTagVariantsHelpersTests(unittest.TestCase):
    """Unit: pick seedów gałęzi B + dedup used (bez urządzenia)."""

    def test_pick_from_map_respects_max_and_used(self):
        import random
        from v2.plugins.impl import content_search as mod

        ctx = mock.MagicMock()
        ctx.account.niche = {
            "tag_variants": {
                "#fitness": ["#gym", "#fitgirl", "#workout"],
            },
        }
        ctx.scratch = {"tag_variants_used": ["#gym"], "session_tag_variants": []}
        s = {
            "tag_variants_budget": {
                "enabled": True,
                "max_phrases": 2,
                "source": "from_map",
            },
            "phrases": {"order": "sequential"},
        }
        out = mod._pick_tag_variant_seeds(ctx, s, random.Random(0))
        self.assertEqual(len(out), 2)
        self.assertNotIn("#gym", [x.casefold() for x in out])
        self.assertTrue(all(x.startswith("#") for x in out))

    def test_pick_live_collect_from_session_scratch(self):
        import random
        from v2.plugins.impl import content_search as mod

        ctx = mock.MagicMock()
        ctx.account.niche = {"tag_variants": {}}
        ctx.scratch = {
            "session_tag_variants": ["#a", "#b", "#a"],
            "tag_variants_used": [],
        }
        s = {
            "tag_variants_budget": {
                "enabled": True,
                "max_phrases": 5,
                "source": "live_collect",
            },
            "phrases": {"order": "sequential"},
        }
        out = mod._pick_tag_variant_seeds(ctx, s, random.Random(0))
        self.assertEqual(out, ["#a", "#b"])


if __name__ == "__main__":
    unittest.main()
