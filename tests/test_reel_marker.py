"""Testy odcisku rolki i weryfikacji przewinięcia.

Tło: marker był wcześniej SAMYM AUTOREM. Dwie kolejne rolki tego samego
twórcy dawały identyczny marker, więc krok `reels.swipe_next` uznawał udane
przewinięcie za nieudane i przewijał drugi raz, a plugin potrafił powtórzyć
akcje na tej samej rolce (otwierał komentarze dwa razy pod rząd).
"""
from __future__ import annotations

import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps.reels._reel_meta import build_marker
from v2.runner.step import StepResult


class BuildMarkerTests(unittest.TestCase):
    def test_same_author_different_content_gives_different_marker(self):
        a = build_marker({"author": "kowalski", "caption": "trening nóg",
                          "likes": 120, "comments": 8})
        b = build_marker({"author": "kowalski", "caption": "dieta na masę",
                          "likes": 340, "comments": 21})
        self.assertNotEqual(a, b)

    def test_identical_reel_gives_identical_marker(self):
        meta = {"author": "kowalski", "caption": "trening nóg",
                "likes": 120, "comments": 8}
        self.assertEqual(build_marker(meta), build_marker(dict(meta)))

    def test_marker_from_author_alone_when_nothing_else_read(self):
        m = build_marker({"author": "kowalski"})
        self.assertIn("kowalski", m)

    def test_marker_without_author_still_built_from_counts(self):
        m = build_marker({"likes": 120, "comments": 8})
        self.assertIsNotNone(m)
        self.assertIn("120", m)

    def test_caption_and_comments_use_distinct_prefixes(self):
        """Wspólny prefiks sprawiłby, że różne odczyty dają ten sam marker."""
        a = build_marker({"author": "x", "caption": "8"})
        b = build_marker({"author": "x", "comments": 8})
        self.assertNotEqual(a, b)

    def test_empty_meta_gives_none(self):
        """None znaczy 'nie wiem' — wołający NIE MOŻE uznać, że rolka się
        zmieniła."""
        self.assertIsNone(build_marker({}))
        self.assertIsNone(build_marker({"author": None, "likes": None}))

    def test_long_caption_is_truncated(self):
        m = build_marker({"author": "x", "caption": "y" * 500})
        self.assertLess(len(m), 120)


# ============================================================
class FakeHt:
    def __init__(self): self.swipes = []
    def swipe(self, *a, **kw): self.swipes.append((a, kw))


class FakeDevice:
    def wait_for_stable_ui(self, max_wait_s=0): pass


class FakeCtx:
    def __init__(self):
        self.ht = FakeHt()
        self.device = FakeDevice()
        self.scratch = {}
        self.logs = []
        self.account = type("A", (), {"device": {"_hardware": {"resolution": [1080, 2340]}}})()
        # swipe_next woła pauzę po settle — bez humanizera test by wywalił AttributeError
        self.humanizer = type("H", (), {"pause": lambda *a, **k: None})()
        # recover przed swipe woła ensure — atrapa jak w test_plugins_runtime
        self.navigator = type("N", (), {
            "ensure": staticmethod(lambda *a, **k: True),
            "calls": [],
        })()

    def log(self, kind, **payload): self.logs.append((kind, payload))


class SwipeNextTests(unittest.TestCase):
    def setUp(self):
        from v2.steps.reels import swipe_next as mod
        self.mod = mod
        self._orig = mod.read_current_reel

    def tearDown(self):
        self.mod.read_current_reel = self._orig

    def _run(self, readings, scratch_marker=None):
        it = iter(readings)
        self.mod.read_current_reel = lambda dev: next(it)
        ctx = FakeCtx()
        if scratch_marker:
            ctx.scratch["reel"] = {"marker": scratch_marker}
        res = self.mod.SwipeNext().run(ctx, {})
        return ctx, res

    def test_changed_marker_is_ok_on_first_try(self):
        ctx, res = self._run([{"marker": "b", "author": "b"}], scratch_marker="a")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["tries"], 1)
        self.assertTrue(res.payload.get("confirmed"))
        self.assertEqual(len(ctx.ht.swipes), 1)

    def test_same_marker_retries_then_succeeds(self):
        ctx, res = self._run([{"marker": "a"}, {"marker": "b"}], scratch_marker="a")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["tries"], 2)
        self.assertEqual(len(ctx.ht.swipes), 2)

    def test_same_marker_all_tries_fails(self):
        """Kluczowe: `failed`, a nie `ok` — wołający musi wiedzieć, że kolejne
        akcje trafiłyby w tę samą rolkę."""
        ctx, res = self._run([{"marker": "a"}] * 3, scratch_marker="a")
        self.assertEqual(res.status, "failed")
        self.assertEqual(len(ctx.ht.swipes), 3)

    def test_unreadable_marker_fails_and_is_flagged(self):
        # Każda próba: dump + settle-retry dump przy marker_unreadable → 2× na attempt
        ctx, res = self._run([{"marker": None}] * 6, scratch_marker="a")
        self.assertEqual(res.status, "failed")
        self.assertTrue(res.payload["marker_unreadable"])

    def test_unreadable_recovers_after_stable_ui_reread(self):
        """Po swipe marker None → wait_for_stable_ui + drugi dump ratuje przejście."""
        ctx, res = self._run(
            [{"marker": None}, {"marker": "b", "author": "b"}],
            scratch_marker="a")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["tries"], 1)
        self.assertEqual(len(ctx.ht.swipes), 1)

    def test_retry_uses_longer_swipe(self):
        """Kolejna próba ma pokonać większy dystans — krótki gest bywa
        pochłaniany przez animację."""
        ctx, res = self._run([{"marker": "a"}, {"marker": "b"}], scratch_marker="a")
        (a1, _), (a2, _) = ctx.ht.swipes
        dist1, dist2 = a1[1] - a1[3], a2[1] - a2[3]
        self.assertGreater(dist2, dist1)

    def test_missing_before_marker_fails_without_swipe(self):
        """Bez przedniego markera (nawet po recover) nie wolno „ślepo" uznać
        przejścia. Recover robi dodatkowy dump — stąd dwa None."""
        ctx, res = self._run([{"marker": None}, {"marker": None}],
                             scratch_marker=None)
        self.assertEqual(res.status, "failed")
        self.assertEqual(len(ctx.ht.swipes), 0)

    def test_missing_before_recovers_then_swipes(self):
        """Brak markera w scratch → recover (ensure+dump) dostaje before, potem OK."""
        # 1) pierwszy dump (scratch pusty), 2) recover dump = before,
        # 3) po swipe = after
        ctx, res = self._run(
            [{"marker": None}, {"marker": "a"}, {"marker": "b", "author": "b"}],
            scratch_marker=None)
        self.assertEqual(res.status, "ok")
        self.assertEqual(len(ctx.ht.swipes), 1)

    def test_before_from_device_then_change_ok(self):
        # scratch pusty → pierwszy odczyt = before, po swipe drugi = after
        ctx, res = self._run([{"marker": "a"}, {"marker": "b"}],
                             scratch_marker=None)
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.payload.get("confirmed"))
        self.assertEqual(len(ctx.ht.swipes), 1)

    def test_new_meta_lands_in_scratch(self):
        """Kolejny krok nie musi dumpować drzewa ponownie."""
        ctx, res = self._run([{"marker": "b", "author": "b"}], scratch_marker="a")
        self.assertEqual(ctx.scratch["reel"]["author"], "b")

    def test_log_prefix_passed_to_say(self):
        from v2.plugins import reel_log
        lines = []
        orig = reel_log.say
        reel_log.say = lambda msg, prefix=None: lines.append((msg, prefix))
        try:
            self._run([{"marker": "b"}], scratch_marker="a")
            # SwipeNext z params — tu bez prefix (domyślny); osobno z params
            it = iter([{"marker": "b", "author": "b"}])
            self.mod.read_current_reel = lambda dev: next(it)
            ctx = FakeCtx()
            ctx.scratch["reel"] = {"marker": "a", "author": "a"}
            self.mod.SwipeNext().run(ctx, {"log_prefix": "reels_warmup"})
            self.assertTrue(any(p == "reels_warmup" for _, p in lines))
        finally:
            reel_log.say = orig


if __name__ == "__main__":
    unittest.main()
