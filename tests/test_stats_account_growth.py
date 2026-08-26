"""Testy odczytu followers/following ze stats_snapshot (events → panel)."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from v2.data.stats import (
    account_growth,
    extract_stats_snapshot_points,
    load_account_stats,
    summarize_account_stats,
)


def _ev(ts, kind, payload, session_id="s1"):
    return {
        "ts": ts,
        "session_id": session_id,
        "account_id": "acc",
        "kind": kind,
        "payload": payload,
    }


class StatsSnapshotExtractTests(unittest.TestCase):
    def test_dedup_kind_and_step(self):
        """kind=stats_snapshot + step ok z tym samym payloadem → 1 punkt."""
        stats = {"posts": 6, "followers": 714, "following": 115}
        events = [
            _ev("2026-08-11T22:22:48", "stats_snapshot", stats),
            _ev("2026-08-11T22:22:48", "step", {
                "step": "system.stats_snapshot", "status": "ok", "payload": stats,
            }),
        ]
        points, skipped = extract_stats_snapshot_points(events)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["followers"], 714)
        self.assertEqual(points[0]["following"], 115)

    def test_session_start_and_end_delta(self):
        """Dwa odczyty w sesji → start/end + delta followers."""
        events = [
            _ev("2026-08-11T10:00:00", "stats_snapshot",
                {"posts": 1, "followers": 100, "following": 50}),
            _ev("2026-08-11T11:00:00", "stats_snapshot",
                {"posts": 1, "followers": 103, "following": 52}),
        ]
        points, _ = extract_stats_snapshot_points(events, session_id="s1")
        summary = summarize_account_stats(points)
        self.assertEqual(summary["start"]["followers"], 100)
        self.assertEqual(summary["end"]["followers"], 103)
        self.assertEqual(summary["delta"]["followers"], 3)
        self.assertEqual(summary["delta"]["following"], 2)
        self.assertIsNone(summary["enabled_hint"])

    def test_single_point_no_delta(self):
        """Tylko start sesji — bez end/delta (on_session_end off)."""
        points = [{"ts": "t", "posts": 1, "followers": 10, "following": 5}]
        summary = summarize_account_stats(points)
        self.assertIsNotNone(summary["start"])
        self.assertIsNone(summary["end"])
        self.assertIsNone(summary["delta"])

    def test_skipped_end_counted(self):
        events = [
            _ev("2026-08-11T10:00:00", "step", {
                "step": "system.stats_snapshot", "status": "ok",
                "payload": {"posts": 1, "followers": 10, "following": 5},
            }),
            _ev("2026-08-11T11:00:00", "step", {
                "step": "system.stats_snapshot", "status": "skipped",
                "payload": {"reason": "disabled_for_session_end"},
            }),
        ]
        points, skipped = extract_stats_snapshot_points(events)
        self.assertEqual(len(points), 1)
        self.assertEqual(skipped, 1)

    def test_account_growth_compat(self):
        """account_growth nadal zwraca points + enabled_hint."""
        events = [
            _ev("2026-08-11T10:00:00", "stats_snapshot",
                {"posts": 2, "followers": 20, "following": 8}),
        ]
        g = account_growth(events)
        self.assertEqual(len(g["points"]), 1)
        self.assertIsNone(g["enabled_hint"])

    def test_load_account_stats_from_file(self):
        """load_account_stats czyta events.jsonl z dysku."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acc_dir = root / "accounts" / "acc1"
            acc_dir.mkdir(parents=True)
            rows = [
                _ev("2026-08-11T22:22:48", "stats_snapshot",
                    {"posts": 6, "followers": 714, "following": 115},
                    session_id="sess-a"),
                _ev("2026-08-11T22:22:48", "step", {
                    "step": "system.stats_snapshot", "status": "ok",
                    "payload": {"posts": 6, "followers": 714, "following": 115},
                }, session_id="sess-a"),
            ]
            (acc_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n",
                encoding="utf-8",
            )
            out = load_account_stats("acc1", root, session_id="sess-a",
                                     since_days=None)
            self.assertEqual(out["start"]["followers"], 714)
            self.assertEqual(out["start"]["following"], 115)
            self.assertEqual(out["points_count"], 1)
            self.assertIsNone(out["end"])

            # Inna sesja → pusty hint
            empty = load_account_stats("acc1", root, session_id="other",
                                       since_days=None)
            self.assertIsNone(empty["start"])
            self.assertIsNotNone(empty["enabled_hint"])

            # Okno czasowe obejmujące odczyt
            win = load_account_stats(
                "acc1", root, since_days=None,
                window_start=datetime(2026, 8, 11, 22, 0, 0),
                window_end=datetime(2026, 8, 11, 23, 0, 0),
            )
            self.assertEqual(win["start"]["followers"], 714)


if __name__ == "__main__":
    unittest.main()
