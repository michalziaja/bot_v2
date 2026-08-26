"""Testy limits — count z events.jsonl, decyzja daily/hourly."""
from __future__ import annotations
import json, shutil, tempfile, time, unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core import limits


def _fake_ctx(events: list[dict], daily_follows=10, hourly_follows=3):
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "events.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    account = SimpleNamespace(limits={
        "daily_follows": daily_follows,
        "daily_likes": 100,
        "per_hour_caps": {"follows": hourly_follows, "likes": 20},
    })
    events_obj = SimpleNamespace(file_path=p)
    return SimpleNamespace(events=events_obj, account=account, scratch={}), tmp


def _follow_event(ts_str: str):
    return {"ts": ts_str, "session_id": "x", "account_id": "a", "kind": "step",
            "payload": {"step": "profile.follow_execute", "status": "ok"}}


class LimitsTests(unittest.TestCase):
    def _now_ts(self, minutes_ago=0, same_hour=False):
        """Znacznik czasu sprzed N minut.

        `same_hour=True` przytrzymuje wynik w BIEŻĄCEJ godzinie. Bez tego test
        limitu godzinowego przewracał się przez pierwsze dwie minuty każdej
        godziny: „minutę temu" lądowało w godzinie poprzedniej i zdarzenie
        nie liczyło się do bieżącego okna.
        """
        t = datetime.now()
        h = t.hour
        m = t.minute - minutes_ago
        if m < 0:
            if same_hour:
                m = 0
            else:
                m += 60; h -= 1
        return t.replace(hour=max(h, 0), minute=max(m, 0),
                         second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

    def test_no_events_allowed(self):
        ctx, tmp = _fake_ctx([])
        try:
            r = limits.check(ctx, "follow")
            self.assertTrue(r.allowed)
            self.assertEqual(r.current, 0)
        finally:
            shutil.rmtree(tmp)

    def test_daily_exceeded(self):
        events = [_follow_event(self._now_ts(minutes_ago=i * 5)) for i in range(10)]
        ctx, tmp = _fake_ctx(events, daily_follows=10)
        try:
            r = limits.check(ctx, "follow")
            self.assertFalse(r.allowed)
            self.assertEqual(r.scope, "daily")
        finally:
            shutil.rmtree(tmp)

    def test_hourly_exceeded(self):
        # 3 followsy w tej samej godzinie
        events = [_follow_event(self._now_ts(minutes_ago=i, same_hour=True))
                  for i in range(3)]
        ctx, tmp = _fake_ctx(events, daily_follows=50, hourly_follows=3)
        try:
            r = limits.check(ctx, "follow")
            self.assertFalse(r.allowed)
            self.assertEqual(r.scope, "hourly")
        finally:
            shutil.rmtree(tmp)

    def test_follow_inside_profile_visit_counts(self):
        """`profile.visit` obserwuje sam, bez osobnego kroku. Gdyby nie był
        liczony, bot wydawałby część dziennego budżetu poza ewidencją."""
        ev = {"ts": self._now_ts(), "kind": "step",
              "payload": {"step": "profile.visit", "status": "ok",
                          "payload": {"followed": True}}}
        ctx, tmp = _fake_ctx([ev] * 10, daily_follows=10)
        try:
            self.assertFalse(limits.check(ctx, "follow").allowed)
        finally:
            shutil.rmtree(tmp)

    def test_visit_without_follow_does_not_count(self):
        ev = {"ts": self._now_ts(), "kind": "step",
              "payload": {"step": "profile.visit", "status": "ok",
                          "payload": {"followed": False}}}
        ctx, tmp = _fake_ctx([ev] * 10, daily_follows=10)
        try:
            self.assertTrue(limits.check(ctx, "follow").allowed)
        finally:
            shutil.rmtree(tmp)

    def test_comment_like_counts_as_like(self):
        ev = {"ts": self._now_ts(), "kind": "step",
              "payload": {"step": "reels.like_comment", "status": "ok",
                          "payload": {"user": "x"}}}
        ctx, tmp = _fake_ctx([ev] * 100)
        try:
            self.assertFalse(limits.check(ctx, "like").allowed)   # daily_likes=100
        finally:
            shutil.rmtree(tmp)

    def test_note_action_invalidates_cache(self):
        ctx, tmp = _fake_ctx([])
        try:
            limits.check(ctx, "follow")
            self.assertIn("_limits_cache", ctx.scratch)
            limits.note_action(ctx, "follow")
            self.assertNotIn("_limits_cache", ctx.scratch)
        finally:
            shutil.rmtree(tmp)

    def test_failed_events_not_counted(self):
        ok    = _follow_event(self._now_ts())
        bad   = _follow_event(self._now_ts())
        bad["payload"]["status"] = "failed"
        ctx, tmp = _fake_ctx([ok, bad])
        try:
            r = limits.check(ctx, "follow")
            self.assertEqual(r.current, 1)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
