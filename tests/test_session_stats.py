"""Testy SessionStats — flush atomic, schemat, snapshot, dedup eventów."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from v2.data.session_stats import SessionStats


class SessionStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ss = SessionStats("acc1", self.root)
        self.events: list[dict] = []
        self.ss._on_progress = lambda prog: self.events.append(dict(prog))

    def tearDown(self):
        self.tmp.cleanup()

    def test_begin_flush_schema(self):
        """begin() zapisuje live JSON z kluczami meta."""
        self.ss.begin(session_id="sess-1", queue_name="default")
        p = self.ss.live_path
        self.assertTrue(p.exists())
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data["session_id"], "sess-1")
        self.assertEqual(data["account_id"], "acc1")
        self.assertEqual(data["queue"]["name"], "default")
        self.assertIn("started_at", data)
        self.assertIn("updated_at", data)
        self.assertEqual(data["counters"], {})
        self.assertEqual(data["by_plugin"], {})
        self.assertIn("ai", data)
        self.assertEqual(data["ai"]["tokens_in"], 0)
        self.assertEqual(data["ai"]["cost_usd"], 0.0)

    def test_set_task_and_counters_by_plugin(self):
        """set_counters sumuje lokalne stats zadania do sesji + by_plugin."""
        self.ss.begin(session_id="s", queue_name=None)
        self.ss.set_task(plugin="reels_niche", task_id="t1",
                         task_i=1, task_n=2,
                         budget={"items_done": 0, "max_items": 3})
        self.ss.set_counters({"watched": 2, "liked": 1})
        data = self.ss.as_dict()
        self.assertEqual(data["plugin"], "reels_niche")
        self.assertEqual(data["queue"]["task_i"], 1)
        self.assertEqual(data["queue"]["task_n"], 2)
        self.assertEqual(data["counters"]["watched"], 2)
        self.assertEqual(data["by_plugin"]["reels_niche"]["liked"], 1)

        # Drugie zadanie innego pluginu — poprzednie liczniki zostają
        self.ss.set_task(plugin="profile_interact", task_id="t2",
                         task_i=2, task_n=2,
                         budget={"items_done": 0, "max_items": 5})
        self.ss.set_counters({"visited": 3, "followed": 1})
        data = self.ss.as_dict()
        self.assertEqual(data["counters"]["watched"], 2)
        self.assertEqual(data["counters"]["visited"], 3)
        self.assertEqual(data["by_plugin"]["reels_niche"]["watched"], 2)
        self.assertEqual(data["by_plugin"]["profile_interact"]["visited"], 3)

    def test_bump_and_sync_budget(self):
        self.ss.begin(session_id="s")
        self.ss.set_task(plugin="reels_niche", task_id="t",
                         task_i=1, task_n=1, budget={})
        self.ss.bump("gate_skipped", 2)
        self.ss.sync_budget(1, 3)
        data = self.ss.as_dict()
        self.assertEqual(data["counters"]["gate_skipped"], 2)
        self.assertEqual(data["budget"]["items_done"], 1)
        self.assertEqual(data["budget"]["max_items"], 3)

    def test_set_progress_emits_once_per_change(self):
        """Event tylko gdy zmienia się faza/current — nie przy powtórzeniu."""
        self.ss.begin(session_id="s")
        self.ss.set_progress(phase="reel", current=1, total=3, label="Rolka")
        self.ss.set_progress(phase="reel", current=1, total=3, label="Rolka")
        self.ss.set_progress(phase="reel", current=2, total=3, label="Rolka")
        self.assertEqual(len(self.events), 2)
        self.assertEqual(self.events[0]["current"], 1)
        self.assertEqual(self.events[1]["current"], 2)
        prog = self.ss.as_dict()["progress"]
        self.assertEqual(prog["phase"], "reel")
        self.assertEqual(prog["current"], 2)

    def test_snapshot_end(self):
        self.ss.begin(session_id="snap-1", queue_name="q")
        self.ss.set_task(plugin="reels_niche", task_id="t",
                         task_i=1, task_n=1, budget={})
        self.ss.set_counters({"watched": 1})
        path = self.ss.snapshot_end(summary={"ok": True, "items_done": 1})
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["session_id"], "snap-1")
        self.assertTrue(data["summary"]["ok"])
        self.assertIn("ended_at", data)
        # Live też ma summary
        live = json.loads(self.ss.live_path.read_text(encoding="utf-8"))
        self.assertEqual(live["summary"]["items_done"], 1)

    def test_atomic_flush_no_tmp_left(self):
        self.ss.begin(session_id="s")
        self.ss.bump("liked")
        live = self.ss.live_path
        self.assertTrue(live.exists())
        self.assertFalse(live.with_suffix(".json.tmp").exists())

    def test_record_task_timing_and_rates(self):
        """Czas zadań → total_s + sec_per_like w rates."""
        self.ss.begin(session_id="t1", queue_name="default")
        self.ss.set_task(plugin="reels_niche", task_id="a",
                         task_i=1, task_n=1, budget={})
        self.ss.set_counters({"liked": 2, "watched": 5})
        self.ss.record_task_timing(
            duration_s=120, items_done=5,
            stats={"liked": 2, "watched": 5}, status="ok")
        data = self.ss.as_dict()
        self.assertEqual(data["timing"]["total_s"], 120.0)
        self.assertEqual(len(data["timing"]["tasks"]), 1)
        self.assertEqual(data["timing"]["by_plugin"]["reels_niche"]["liked"], 2)
        rates = data["timing"]["rates"]
        self.assertEqual(rates["sec_per_like"], 60.0)  # 120/2
        self.assertEqual(rates["sec_per_item"], 24.0)  # 120/5
        path = self.ss.snapshot_end(summary={"ok": True})
        snap = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("wall_s", snap["timing"])
        self.assertEqual(snap["timing"]["rates"]["likes"], 2)

    def test_add_ai_usage_per_model_and_cost(self):
        """Tokeny IN/OUT per model + koszt Haiku 4.5 (1/5 USD za 1M)."""
        self.ss.begin(session_id="ai-1")
        self.ss.add_ai_usage({
            "_usage": {
                "model": "claude-haiku-4-5-20251001",
                "mode": "profile_grid",
                "tokens_in": 1_000_000,
                "tokens_out": 200_000,
            },
        }, mode="profile_grid")
        self.ss.add_ai_usage({
            "_usage": {
                "model": "claude-sonnet-4-20250514",
                "mode": "comment_gen",
                "tokens_in": 100_000,
                "tokens_out": 10_000,
            },
        }, mode="comment_gen")
        ai = self.ss.as_dict()["ai"]
        self.assertEqual(ai["tokens_in"], 1_100_000)
        self.assertEqual(ai["tokens_out"], 210_000)
        self.assertEqual(ai["calls"], 2)
        haiku = ai["by_model"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku["tokens_in"], 1_000_000)
        self.assertEqual(haiku["tokens_out"], 200_000)
        self.assertEqual(haiku["calls"], 1)
        # 1M in × $1 + 0.2M out × $5 = $2.00
        self.assertAlmostEqual(haiku["cost_usd"], 2.0, places=5)
        sonnet = ai["by_model"]["claude-sonnet-4-20250514"]
        # 0.1M in × $3 + 0.01M out × $15 = $0.45
        self.assertAlmostEqual(sonnet["cost_usd"], 0.45, places=5)
        self.assertAlmostEqual(ai["cost_usd"], 2.45, places=5)
        self.assertFalse(ai["cost_unknown"])
        path = self.ss.snapshot_end(summary={"ok": True})
        snap = json.loads(path.read_text(encoding="utf-8"))
        self.assertAlmostEqual(snap["ai"]["cost_usd"], 2.45, places=5)


class PaceEtaTests(unittest.TestCase):
    def test_estimate_actions_eta_sums_kinds(self):
        from v2.data.pace import estimate_actions_eta
        eta = estimate_actions_eta(
            {"likes": {"min": 10, "max": 20}, "follows": {"min": 1, "max": 2}},
            {"sec_per_like": 60, "sec_per_follow": 90, "source": "sessions",
             "samples": 3})
        self.assertEqual(eta["total_s"]["min"], 10 * 60 + 1 * 90)
        self.assertEqual(eta["total_s"]["max"], 20 * 60 + 2 * 90)
        self.assertEqual(eta["pace"]["sec_per_like"], 60)

    def test_account_pace_defaults_without_sessions(self):
        from v2.data.pace import account_pace, DEFAULT_SEC_PER_LIKE
        with tempfile.TemporaryDirectory() as tmp:
            p = account_pace("nobody", tmp, last_n=5)
        self.assertEqual(p["source"], "defaults")
        self.assertEqual(p["sec_per_like"], DEFAULT_SEC_PER_LIKE)
        self.assertEqual(p["samples"], 0)


if __name__ == "__main__":
    unittest.main()
