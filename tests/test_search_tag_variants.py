"""Unit: search.switch_results_tab + collect_tag_variants (bez urządzenia)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from v2.steps.search.collect_tag_variants import (
    SearchCollectTagVariants,
    _as_hashtag,
    _dedup_key,
    _scrape_tag_rows,
    parse_post_count,
)
from v2.steps.search.switch_results_tab import (
    RESULTS_TAB_ALIASES,
    SearchSwitchResultsTab,
    _find_tab_button,
    _visible_tab_labels,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_TAGS = ROOT / "calibration" / "samples" / "45_search_tags_tab.flat.json"
SAMPLE_GRID = ROOT / "calibration" / "samples" / "44_search_results_grid.flat.json"


def _load_flat(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeNav:
    def __init__(self, top="search_results"):
        self._top = top

    def identify(self):
        s = MagicMock()
        s.top = self._top
        return s


class TestResultsTabAliases(unittest.TestCase):
    def test_tags_aliases_pl_en(self):
        aliases = RESULTS_TAB_ALIASES["tags"]
        self.assertIn("znaczniki", aliases)
        self.assertIn("tags", aliases)

    def test_find_tags_after_swipe_sample(self):
        """Sample 45 ma Znaczniki widoczne (po swipe w live dumpie)."""
        if not SAMPLE_TAGS.exists():
            self.skipTest("brak sample 45_search_tags_tab")
        flat = _load_flat(SAMPLE_TAGS)
        btn = _find_tab_button(flat, RESULTS_TAB_ALIASES["tags"])
        self.assertIsNotNone(btn)
        self.assertEqual((btn.get("text") or "").casefold(), "znaczniki")
        self.assertTrue(btn.get("selected"))

    def test_grid_sample_missing_tags(self):
        """Sample 44 — Tags poza ekranem, tylko Audio na prawej krawędzi."""
        if not SAMPLE_GRID.exists():
            self.skipTest("brak sample 44")
        flat = _load_flat(SAMPLE_GRID)
        labels = _visible_tab_labels(flat)
        self.assertIn("Dla Ciebie", labels)
        self.assertNotIn("Znaczniki", labels)
        self.assertIsNone(
            _find_tab_button(flat, RESULTS_TAB_ALIASES["tags"]))

    def test_switch_taps_when_visible(self):
        if not SAMPLE_TAGS.exists():
            self.skipTest("brak sample 45")
        flat = _load_flat(SAMPLE_TAGS)
        ctx = MagicMock()
        ctx.navigator = FakeNav()
        ctx.scratch = {}
        ctx.ht = MagicMock()
        ctx.device = MagicMock()
        ctx.log = MagicMock()
        with patch(
            "v2.steps.search.switch_results_tab.flatten_hierarchy",
            return_value=flat,
        ), patch("v2.steps.search.switch_results_tab.time.sleep"):
            r = SearchSwitchResultsTab().run(ctx, {"tab": "tags"})
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.payload.get("tab"), "tags")
        self.assertEqual(r.payload.get("label"), "Znaczniki")
        self.assertEqual(ctx.scratch.get("search_results_tab"), "tags")
        ctx.ht.tap.assert_called_once()

    def test_switch_swipes_then_finds(self):
        """Najpierw sample bez Tags, po swipe — z Tags."""
        if not (SAMPLE_GRID.exists() and SAMPLE_TAGS.exists()):
            self.skipTest("brak samples 44/45")
        flat_grid = _load_flat(SAMPLE_GRID)
        flat_tags = _load_flat(SAMPLE_TAGS)
        flats = [flat_grid, flat_grid, flat_tags, flat_tags]
        ctx = MagicMock()
        ctx.navigator = FakeNav()
        ctx.scratch = {}
        ctx.ht = MagicMock()
        ctx.device = MagicMock()
        ctx.log = MagicMock()
        ctx.screen_map.anchor_center.return_value = (540, 288)
        ctx.account.device = {"_hardware": {"resolution": [1080, 2340]}}

        with patch(
            "v2.steps.search.switch_results_tab.flatten_hierarchy",
            side_effect=lambda *_a, **_k: flats.pop(0) if flats else flat_tags,
        ), patch("v2.steps.search.switch_results_tab.time.sleep"):
            r = SearchSwitchResultsTab().run(ctx, {"tab": "tags", "max_swipes": 3})
        self.assertEqual(r.status, "ok")
        self.assertGreaterEqual(r.payload.get("swipes", 0), 1)
        self.assertTrue(ctx.ht.swipe.called)
        self.assertTrue(ctx.ht.tap.called)


class TestCollectTagVariants(unittest.TestCase):
    def test_normalize_hash(self):
        self.assertEqual(_as_hashtag("Foo"), "#Foo")
        self.assertEqual(_as_hashtag("#bar"), "#bar")
        self.assertEqual(_dedup_key("#Żółć"), _dedup_key("#zolc"))

    def test_parse_post_count_pl(self):
        self.assertEqual(parse_post_count("174 mln postów"), 174_000_000)
        self.assertEqual(parse_post_count("23,1 mln postów"), 23_100_000)
        self.assertEqual(parse_post_count("922 tys. postów"), 922_000)
        self.assertEqual(parse_post_count("1,9 mln postów"), 1_900_000)

    def test_parse_post_count_en(self):
        self.assertEqual(parse_post_count("174M posts"), 174_000_000)
        self.assertEqual(parse_post_count("500K posts"), 500_000)

    def test_scrape_from_live_sample(self):
        if not SAMPLE_TAGS.exists():
            self.skipTest("brak sample 45")
        flat = _load_flat(SAMPLE_TAGS)
        rows = _scrape_tag_rows(flat)
        self.assertGreaterEqual(len(rows), 5)
        tags = [t for t, _ in rows]
        self.assertTrue(all(t.startswith("#") for t in tags))
        self.assertIn("#fitnessmotivation", tags)
        # subtitle sparowany → duża liczba postów
        by_tag = {t: p for t, p in rows}
        self.assertEqual(by_tag.get("#fitnessmotivation"), 174_000_000)

    def test_collect_writes_scratch_and_sorts(self):
        if not SAMPLE_TAGS.exists():
            self.skipTest("brak sample 45")
        flat = _load_flat(SAMPLE_TAGS)
        ctx = MagicMock()
        ctx.navigator = FakeNav()
        ctx.scratch = {}
        ctx.ht = MagicMock()
        ctx.device = MagicMock()
        ctx.log = MagicMock()
        ctx.account.account_id = "demo"
        ctx.account.device = {"_hardware": {"resolution": [1080, 2340]}}

        with patch(
            "v2.steps.search.collect_tag_variants.flatten_hierarchy",
            return_value=flat,
        ), patch("v2.steps.search.collect_tag_variants.time.sleep"):
            r = SearchCollectTagVariants().run(ctx, {
                "query": "fitness",
                "max_items": 5,
                "max_scrolls": 0,
                "sort_by_post_count": True,
            })
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.payload["count"], 5)
        self.assertEqual(ctx.scratch["tag_variants_tab"], "tags")
        self.assertEqual(ctx.scratch["tag_variants_query"], "fitness")
        variants = ctx.scratch["tag_variants"]
        self.assertEqual(len(variants), 5)
        # największy count pierwszy (#fitnessmotivation = 174 mln)
        self.assertEqual(variants[0], "#fitnessmotivation")
        # bez write_to_account — tylko scratch
        self.assertFalse(r.payload.get("persisted"))

    def test_collect_persists_via_store(self):
        """write_to_account=True → write_niche_tag_variants + niche in-memory."""
        flat = [
            {"resource-id": "com.instagram.android:id/row_hashtag_textview_tag_name",
             "text": "#fitgirl", "bounds": "[40,400][400,460]"},
            {"resource-id": "com.instagram.android:id/row_hashtag_textview_tag_subtitle",
             "text": "10 mln postów", "bounds": "[40,460][400,500]"},
            {"resource-id": "com.instagram.android:id/row_hashtag_textview_tag_name",
             "text": "#gym", "bounds": "[40,520][400,580]"},
            {"resource-id": "com.instagram.android:id/row_hashtag_textview_tag_subtitle",
             "text": "5 mln postów", "bounds": "[40,580][400,620]"},
        ]
        ctx = MagicMock()
        ctx.scratch = {}
        ctx.ht = MagicMock()
        ctx.device = MagicMock()
        ctx.log = MagicMock()
        ctx.account.account_id = "demo"
        ctx.account.niche = {"tag_variants": {}}
        ctx.account.device = {"_hardware": {"resolution": [1080, 2340]}}

        saved_niche = {
            "tag_variants": {"fitness": ["#fitgirl", "#gym"]},
        }
        with patch(
            "v2.steps.search.collect_tag_variants.flatten_hierarchy",
            return_value=flat,
        ), patch("v2.steps.search.collect_tag_variants.time.sleep"), patch(
            "v2.plugins.store.write_niche_tag_variants",
            return_value=saved_niche,
        ) as write_tv:
            r = SearchCollectTagVariants().run(ctx, {
                "query": "fitness",
                "max_scrolls": 0,
                "write_to_account": True,
                "sort_by_post_count": False,
            })
        self.assertEqual(r.status, "ok")
        self.assertTrue(r.payload.get("persisted"))
        self.assertEqual(r.payload.get("persist_note"), "write_niche_tag_variants")
        write_tv.assert_called_once()
        args, kwargs = write_tv.call_args
        self.assertEqual(args[0], "demo")
        self.assertEqual(args[1], "fitness")
        self.assertIn("#fitgirl", args[2])
        # in-memory niche zsynchronizowane
        self.assertEqual(ctx.account.niche["tag_variants"]["fitness"],
                         ["#fitgirl", "#gym"])

    def test_exclude_seed_hashtag(self):
        flat = [
            {"resource-id": "com.instagram.android:id/row_hashtag_textview_tag_name",
             "text": "#fitness", "bounds": "[40,400][400,460]"},
            {"resource-id": "com.instagram.android:id/row_hashtag_textview_tag_subtitle",
             "text": "10 mln postów", "bounds": "[40,460][400,500]"},
            {"resource-id": "com.instagram.android:id/row_hashtag_textview_tag_name",
             "text": "#fitnesslife", "bounds": "[40,520][400,580]"},
            {"resource-id": "com.instagram.android:id/row_hashtag_textview_tag_subtitle",
             "text": "1 mln postów", "bounds": "[40,580][400,620]"},
        ]
        ctx = MagicMock()
        ctx.scratch = {}
        ctx.ht = MagicMock()
        ctx.device = MagicMock()
        ctx.log = MagicMock()
        ctx.account.device = {"_hardware": {"resolution": [1080, 2340]}}
        with patch(
            "v2.steps.search.collect_tag_variants.flatten_hierarchy",
            return_value=flat,
        ), patch("v2.steps.search.collect_tag_variants.time.sleep"):
            r = SearchCollectTagVariants().run(ctx, {
                "query": "#fitness",
                "max_scrolls": 0,
                "include_seed": False,
                "sort_by_post_count": False,
            })
        self.assertEqual(r.status, "ok")
        self.assertNotIn("#fitness", r.payload["hashtags"])
        self.assertIn("#fitnesslife", r.payload["hashtags"])


if __name__ == "__main__":
    unittest.main()
