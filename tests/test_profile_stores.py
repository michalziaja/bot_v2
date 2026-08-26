"""Testy TargetCandidatesStore + VisitedProfilesStore."""
from __future__ import annotations
import shutil, tempfile, time, unittest, json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.data.stores import TargetCandidatesStore, VisitedProfilesStore


class TargetCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = TargetCandidatesStore("acc", self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _seed(self, entries):
        with self.store.file_path.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_pick_basic(self):
        self._seed([
            {"username": "a", "added_at": "2026-01-01T00:00:00"},
            {"username": "b", "added_at": "2026-01-01T00:00:00"},
            {"username": "c", "added_at": "2026-01-01T00:00:00"},
        ])
        picks = self.store.pick(limit=2)
        self.assertEqual(len(picks), 2)

    def test_pick_excludes(self):
        self._seed([{"username": u, "added_at": "2026-01-01T00:00:00"}
                    for u in ("a", "b", "c")])
        picks = self.store.pick(limit=5, exclude_usernames={"a", "c"})
        self.assertEqual([p["username"] for p in picks], ["b"])

    def test_pick_min_age(self):
        # świeże (dopiero co dodane) — powinny być pomijane
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        self._seed([{"username": "fresh", "added_at": now_iso},
                    {"username": "old",   "added_at": "2020-01-01T00:00:00"}])
        picks = self.store.pick(limit=5, not_added_within_s=3600)
        self.assertEqual([p["username"] for p in picks], ["old"])

    def test_add_and_preview(self):
        self.assertTrue(self.store.add("alice", source="reel_likers",
                                       from_target="gym"))
        self.assertFalse(self.store.add("alice"))  # dedup
        prev = self.store.preview(limit=10)
        self.assertEqual(prev["total"], 1)
        self.assertEqual(prev["items"][0]["username"], "alice")


class VisitedProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = VisitedProfilesStore("acc", self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_add_and_read(self):
        self.store.add("alice", source="search", action="follow", scoring=0.7)
        rows = self.store.read_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username"], "alice")
        self.assertEqual(rows[0]["action"], "follow")

    def test_visited_within_returns_recent(self):
        self.store.add("alice", action="follow")
        self.store.add("bob",   action="skip")
        s = self.store.visited_within(days=30)
        self.assertEqual(s, {"alice", "bob"})


if __name__ == "__main__":
    unittest.main()
