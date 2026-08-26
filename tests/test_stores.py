"""Testy RawProfilesStore."""
from __future__ import annotations
import shutil, tempfile, unittest
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.data.stores import RawProfilesStore


class RawProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = RawProfilesStore("acc_test", self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_add_many_and_read(self):
        n = self.store.add_many(["a", "b", "c"], source="reel_likers", from_author="X")
        self.assertEqual(n, 3)
        rows = self.store.read_all()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["source"], "reel_likers")
        self.assertEqual(rows[0]["from_author"], "X")

    def test_dedup_within_batch(self):
        # 'a' powtórzone w tej samej porcji — liczymy raz
        n = self.store.add_many(["a", "a", "b"], source="s")
        self.assertEqual(n, 2)

    def test_append_across_batches(self):
        self.store.add_many(["a", "b"], source="s1")
        self.store.add_many(["c"],       source="s2")
        self.assertEqual(len(self.store.read_all()), 3)
        self.assertEqual(self.store.unique_usernames(), {"a", "b", "c"})

    def test_cross_session_dedup_skips_known_username(self):
        """Drugi add_rows tego samego nicka → saved=0 (docstring: nie mnożymy)."""
        n1 = self.store.add_rows(
            [{"username": "ala", "has_ring": True}],
            source="post_likers", from_target="gym", content_kind="post")
        self.assertEqual(n1, 1)
        n2 = self.store.add_rows(
            [{"username": "ala", "has_ring": False}],
            source="reel_commenters", from_target="gym", content_kind="reel")
        self.assertEqual(n2, 0)
        self.assertEqual(len(self.store.read_all()), 1)

    def test_dedup_days_zero_allows_reappend(self):
        """dedup_days=0 → bez dedupu historii (tylko w porcji)."""
        self.store.add_many(["a"], source="s1", dedup_days=0)
        n = self.store.add_many(["a"], source="s2", dedup_days=0)
        self.assertEqual(n, 1)
        self.assertEqual(len(self.store.read_all()), 2)

    def test_content_kind_metadata_persisted(self):
        self.store.add_rows(
            [{"username": "x"}], source="followers",
            from_target="t1", content_kind=None)
        row = self.store.read_all()[0]
        self.assertEqual(row["from_target"], "t1")
        self.assertIsNone(row["content_kind"])
        self.assertIn("ts", row)

    def test_preview_unique_newest_first(self):
        self.store.add_rows(
            [{"username": "old"}], source="s1", from_target="a",
            content_kind="post", dedup_days=0)
        self.store.add_rows(
            [{"username": "new"}], source="s2", from_target="b",
            content_kind="reel", dedup_days=0)
        # Duplikat old — preview unique bierze najnowszy (ten sam nick raz)
        self.store.add_rows(
            [{"username": "old"}], source="s3", from_target="c",
            content_kind="reel", dedup_days=0)
        prev = self.store.preview(limit=10, unique=True)
        self.assertEqual(prev["unique"], 2)
        self.assertEqual(prev["shown"], 2)
        # Najnowszy unikalny: old (ostatni append) albo new — kolejność newest-first
        names = [i["username"] for i in prev["items"]]
        self.assertEqual(set(names), {"old", "new"})
        self.assertEqual(names[0], "old")  # ostatni zapisany unikalnie pierwszy

    def test_pick_unique_excludes_visited(self):
        self.store.add_rows(
            [{"username": "a"}, {"username": "b"}, {"username": "a"}],
            source="reel_likers", from_target="t", content_kind="reel",
            dedup_days=0)
        picks = self.store.pick(limit=10, exclude_usernames={"a"})
        self.assertEqual([p["username"] for p in picks], ["b"])
        self.assertEqual(picks[0]["from_target"], "t")

    def test_mark_outcome_updates_last_row(self):
        """Rewrite ostatniego wiersza nicka — harvest nadal append-only."""
        self.store.add_many(["ala"], source="s1", dedup_days=0)
        self.store.add_many(["ala"], source="s2", dedup_days=0)
        ok = self.store.mark_outcome("ala", "gate_failed", note="x")
        self.assertTrue(ok)
        rows = self.store.read_all()
        self.assertEqual(len(rows), 2)
        self.assertNotIn("interacted_at", rows[0])
        self.assertEqual(rows[1]["outcome"], "gate_failed")
        self.assertIn("interacted_at", rows[1])
        self.assertEqual(rows[1]["note"], "x")
        self.assertEqual(rows[1]["source"], "s2")  # ostatni wiersz

    def test_mark_outcome_missing_username(self):
        self.store.add_many(["a"], source="s")
        self.assertFalse(self.store.mark_outcome("ghost", "skipped"))

    def test_pick_skips_interacted_within_window(self):
        self.store.add_many(["old", "fresh"], source="s", dedup_days=0)
        self.store.mark_outcome("fresh", "private")
        picks = self.store.pick(limit=10, skip_interacted_within_s=30 * 86400)
        names = [p["username"] for p in picks]
        self.assertIn("old", names)
        self.assertNotIn("fresh", names)


if __name__ == "__main__":
    unittest.main()
