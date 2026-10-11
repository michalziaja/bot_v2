"""Testy szacunku potencjalnych akcji (lajki / follow / komentarze)."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.scheduler.action_estimator import (
    count_used_today,
    estimate_plugin_actions,
    estimate_actions_from_rows,
    utilization_vs_limits,
)


class ChanceAndBudgetTests(unittest.TestCase):
    def test_reels_warmup_fixed_chance(self):
        s = {
            "budget": {"max_items": {"min": 10, "max": 20}},
            "like": {"enabled": True, "chance": {"mode": "p", "value": 0.5}},
        }
        est = estimate_plugin_actions("reels_warmup", s)
        self.assertEqual(est["actions"]["likes"]["min"], 5)   # 10 * 0.5
        self.assertEqual(est["actions"]["likes"]["max"], 10)  # 20 * 0.5
        self.assertFalse(est["uncertain"])

    def test_ai_gated_likes_min_zero(self):
        s = {
            "budget": {"max_items": {"min": 15, "max": 30}},
            "ai": {"enabled": True},
            "like_reel": {"enabled": True,
                          "chance": {"mode": "formula", "value": "score/100"}},
        }
        est = estimate_plugin_actions("reels_niche", s)
        self.assertEqual(est["actions"]["likes"]["min"], 0)
        self.assertEqual(est["actions"]["likes"]["max"], 30)
        self.assertTrue(est["uncertain"])

    def test_feed_browser_zero(self):
        s = {"budget": {"max_items": {"min": 10, "max": 25}}}
        est = estimate_plugin_actions("feed_browser", s)
        self.assertEqual(est["actions"]["likes"], {"min": 0, "max": 0})
        self.assertEqual(est["actions"]["follows"], {"min": 0, "max": 0})

    def test_profile_interact_follow(self):
        s = {
            "budget": {"max_items": {"min": 5, "max": 10}},
            "like_tiles": {"enabled": True, "count": {"min": 1, "max": 2}},
            "follow": {"allow": True, "chance": {"mode": "p", "value": 0.8}},
        }
        est = estimate_plugin_actions("profile_interact", s)
        self.assertEqual(est["actions"]["likes"]["min"], 5)
        self.assertEqual(est["actions"]["likes"]["max"], 20)
        self.assertEqual(est["actions"]["follows"]["min"], 4)   # floor(5*0.8)
        self.assertEqual(est["actions"]["follows"]["max"], 8)   # ceil(10*0.8)

    def test_target_scraper_zero(self):
        est = estimate_plugin_actions(
            "target_scraper", {"budget": {"max_items": {"min": 2, "max": 3}}})
        self.assertEqual(est["actions"]["likes"]["max"], 0)

    def test_sum_rows_skips_disabled(self):
        rows = [
            {"plugin": "reels_warmup", "label": "w", "enabled": True,
             "plugin_enabled_for_account": True,
             "resolved": {"budget": {"max_items": {"min": 10, "max": 10}},
                          "like": {"enabled": True,
                                   "chance": {"mode": "p", "value": 1.0}}}},
            {"plugin": "reels_warmup", "label": "off", "enabled": False,
             "plugin_enabled_for_account": True,
             "resolved": {"budget": {"max_items": {"min": 10, "max": 10}},
                          "like": {"enabled": True,
                                   "chance": {"mode": "p", "value": 1.0}}}},
        ]
        est = estimate_actions_from_rows(rows)
        self.assertEqual(est["actions"]["likes"], {"min": 10, "max": 10})
        self.assertEqual(est["task_count"], 1)

    def test_utilization(self):
        actions = {"likes": {"min": 20, "max": 60},
                   "follows": {"min": 5, "max": 10},
                   "comments": {"min": 0, "max": 0}}
        u = utilization_vs_limits(actions, {
            "daily_likes": 50, "daily_follows": 30, "daily_comments": 15,
        })
        self.assertTrue(u["likes"]["over"])
        self.assertEqual(u["likes"]["pct_max"], 120.0)
        self.assertFalse(u["follows"]["over"])
        self.assertEqual(u["likes"]["used_today"], 0)
        self.assertEqual(u["likes"]["remaining"], 50)

    def test_utilization_used_today_i_remaining(self):
        """Max 40 przy limicie 50 i 20 już dziś → over=False, over_remaining=True."""
        actions = {"likes": {"min": 20, "max": 40},
                   "follows": {"min": 0, "max": 5},
                   "comments": {"min": 0, "max": 0}}
        u = utilization_vs_limits(actions, {
            "daily_likes": 50, "daily_follows": 30, "daily_comments": 15,
        }, used_today={"likes": 20, "follows": 0, "comments": 0})
        self.assertEqual(u["likes"]["used_today"], 20)
        self.assertEqual(u["likes"]["remaining"], 30)
        self.assertFalse(u["likes"]["over"])
        self.assertTrue(u["likes"]["over_remaining"])
        self.assertFalse(u["follows"]["over_remaining"])


class UsedTodayTests(unittest.TestCase):
    def test_count_used_today_z_events(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        acc = root / "accounts" / "acc1"
        acc.mkdir(parents=True)
        now = datetime(2026, 9, 1, 18, 0, 0)
        today = now.strftime("%Y-%m-%dT%H:%M:%S")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        rows = [
            {"ts": today, "kind": "step",
             "payload": {"step": "reels.like", "status": "ok"}},
            {"ts": today, "kind": "step",
             "payload": {"step": "profile.follow_execute", "status": "ok"}},
            {"ts": yesterday, "kind": "step",
             "payload": {"step": "reels.like", "status": "ok"}},
            {"ts": today, "kind": "step",
             "payload": {"step": "reels.like", "status": "fail"}},
        ]
        (acc / "events.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        used = count_used_today("acc1", root, now=now)
        self.assertEqual(used["likes"], 1)
        self.assertEqual(used["follows"], 1)
        self.assertEqual(used["comments"], 0)

    def test_count_used_today_brak_pliku(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        used = count_used_today("nie_ma", Path(tmp.name))
        self.assertEqual(used, {"likes": 0, "follows": 0, "comments": 0})


if __name__ == "__main__":
    unittest.main()
