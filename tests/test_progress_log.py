"""Testy progress_log + linie X/Y w *_log."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.data.session_stats import SessionStats
from v2.plugins import progress_log
from v2.plugins import reel_log, content_search_log, profile_log, scraper_log


class FakeCtx:
    def __init__(self, ss=None):
        self.session_stats = ss
        self.logs = []

    def log(self, kind, **payload):
        self.logs.append((kind, payload))


class ProgressLogTests(unittest.TestCase):
    def test_xy_and_with_progress(self):
        self.assertEqual(progress_log.xy(2, 3), "2/3")
        self.assertEqual(progress_log.xy(2, None), "2")
        self.assertEqual(progress_log.xy(None, 3), "")
        self.assertEqual(
            progress_log.with_progress("Rolka @x", 2, 3), "Rolka @x 2/3")
        self.assertEqual(progress_log.with_progress("Szukam", None, 2), "Szukam")

    def test_announce_progress_writes_json(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            ss = SessionStats("acc", Path(tmp.name))
            ss.begin(session_id="s")
            ss.set_task(plugin="reels_niche", task_id="t",
                        task_i=1, task_n=1, budget={"max_items": 3})
            ctx = FakeCtx(ss)
            said = []
            progress_log.announce_progress(
                ctx, said.append,
                msg="Rolka @a 1/3", phase="reel", current=1, total=3,
                label="Rolka", detail="@a")
            self.assertEqual(said, ["Rolka @a 1/3"])
            data = ss.as_dict()
            self.assertEqual(data["progress"]["current"], 1)
            self.assertEqual(data["progress"]["phase"], "reel")
            # sync counters
            progress_log.sync_plugin_stats(
                ctx, {"watched": 1, "liked": 0}, items_done=1, max_items=3)
            data = ss.as_dict()
            self.assertEqual(data["counters"]["watched"], 1)
            self.assertEqual(data["budget"]["items_done"], 1)
        finally:
            tmp.cleanup()

    def test_announce_tolerates_none_ctx(self):
        # Brak session_stats nie może wywalić pluginu
        out = progress_log.announce_progress(
            None, None, msg="x", phase="reel", current=1, total=2)
        self.assertEqual(out, "x")


class LogLineXyTests(unittest.TestCase):
    def test_reel_lines(self):
        self.assertEqual(reel_log.reel_progress_line("nature", 2, 3),
                         "Rolka @nature 2/3")
        self.assertEqual(reel_log.next_reel_line(2, 3), "Nastepna rolka… 2/3")
        self.assertEqual(
            reel_log.hot_profile_line("alice", 1, 3, where="likers"),
            "Hot @alice 1/3 z listy lajkujacych")

    def test_content_search_lines(self):
        self.assertEqual(
            content_search_log.searching_line("#gym", 1, 2),
            "Szukam #gym… 1/2")
        self.assertEqual(
            content_search_log.open_tile_line(2, kind="reel", total=8),
            "Kafelek 2/8 (rolka)")
        self.assertEqual(
            content_search_log.hot_profile_line("bob", 1, 3, where="likers"),
            "Hot @bob 1/3 z listy lajkujacych")
        # Bez i/n — stary skrót Profil @nick
        self.assertEqual(content_search_log.hot_profile_line("bob"), "Profil @bob")

    def test_profile_and_scraper_lines(self):
        self.assertEqual(
            profile_log.visit_start_line("x", 1, 5), "Wizyta @x 1/5")
        self.assertEqual(scraper_log.target_line("t", 1, 3), "Target @t 1/3")
        self.assertEqual(scraper_log.grid_item_line("Post", 1, 3), "Post 1/3")


if __name__ == "__main__":
    unittest.main()
