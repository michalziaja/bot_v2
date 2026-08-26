"""Testy E6: mass_stories + list.scroll_until_new + wybór osoby z ringiem.

Sprawdzamy głównie te rzeczy, które w starym bocie kosztowały realne błędy:
wykrywanie dna listy, tap w awatar zamiast w nick, reakcja na tap, który
otworzył profil zamiast relacji, i zamknięcie podglądu w każdej ścieżce.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.plugins.base import Budget
from v2.plugins.impl.mass_stories import MassStories, _pick_targets
from v2.runner.step import StepResult
from v2.steps.lists.scroll_until_new import ScrollUntilNew
from v2.steps.reels import _sheet
from v2.steps.stories.pick_ring_from_list import PickRingFromList
from v2.tests.test_plugins_runtime import FakeCtx, FakeNavigator, fake_registry


def row(name, y, *, ring=False, avatar_x=120):
    """Wiersz listy obserwujących: nick + awatar (+ nakładka ringu)."""
    out = [
        {"resource-id": "com.instagram.android:id/follow_list_username",
         "class": "android.widget.TextView", "text": name,
         "bounds": f"[300,{y}][700,{y + 40}]"},
        {"resource-id": "com.instagram.android:id/follow_list_user_imageview",
         "class": "android.widget.FrameLayout", "text": "",
         "bounds": f"[{avatar_x - 60},{y - 20}][{avatar_x + 60},{y + 60}]"},
    ]
    if ring:
        out.append({"resource-id": "com.instagram.android:id/follow_list_user_imageview",
                    "class": "android.view.View", "text": "",
                    "bounds": f"[{avatar_x - 70},{y - 30}][{avatar_x + 70},{y + 70}]"})
    return out


class FakeHt:
    def __init__(self):
        self.taps, self.swipes = [], []

    def tap(self, x, y, pause_kind=None):
        self.taps.append((x, y))

    def swipe(self, x1, y1, x2, y2, duration_ms=None, pause_kind=None):
        self.swipes.append((y1, y2))


def make_ctx(flat, tops=("follow_list",)):
    seq = list(tops)
    nav = FakeNavigator()
    nav.identify = lambda: type("S", (), {
        "top": seq.pop(0) if len(seq) > 1 else seq[0], "overlay": None})()
    ctx = FakeCtx(nav)
    ctx.ht = FakeHt()
    ctx.humanizer = type("H", (), {
        "pause": staticmethod(lambda *a, **k: None),
        "sleep": staticmethod(lambda s, *a, **k: None)})()
    ctx.account = type("A", (), {
        "device": {"_hardware": {"resolution": (1080, 2340)}}})()
    frames = flat if isinstance(flat, list) and flat and isinstance(flat[0], list) else [flat]
    box = {"i": 0}

    def hierarchy(force=False):
        f = frames[min(box["i"], len(frames) - 1)]
        box["i"] += 1
        return f

    ctx.device = type("D", (), {
        "hierarchy": staticmethod(hierarchy),
        "wait_for_stable_ui": staticmethod(lambda **k: True),
        "press_back": staticmethod(lambda: True)})()
    return ctx


# ============================================================
class RowParsingTests(unittest.TestCase):
    def test_avatar_and_username_are_separate_targets(self):
        """Nick otwiera profil, awatar otwiera relację — pomylenie ich znaczy
        zrobienie czegoś zupełnie innego."""
        rows = _sheet.collect_rows(row("ala", 500, ring=True),
                                   ("com.instagram.android:id/follow_list_username",))
        self.assertEqual(rows[0]["center"][0], 500)      # środek nicku
        self.assertEqual(rows[0]["avatar"][0], 120)      # środek awatara
        self.assertIs(rows[0]["ring"], True)

    def test_avatar_on_the_right_is_not_taken(self):
        """Po prawej stoi „Obserwuj" — awatar szukany tylko po lewej."""
        rows = _sheet.collect_rows(row("ala", 500, avatar_x=900),
                                   ("com.instagram.android:id/follow_list_username",))
        self.assertIsNone(rows[0]["avatar"])


class _FlatDumpMixin:
    """Atrapy podają dump JUŻ spłaszczony — spłaszczanie ma własne testy."""
    MODULES: tuple = ()

    def setUp(self):
        self._orig = []
        for mod in self.MODULES:
            self._orig.append((mod, mod.flatten_hierarchy))
            mod.flatten_hierarchy = lambda h: h

    def tearDown(self):
        for mod, fn in self._orig:
            mod.flatten_hierarchy = fn


class ScrollUntilNewTests(_FlatDumpMixin, unittest.TestCase):
    IDS = ("com.instagram.android:id/follow_list_username",)

    @property
    def MODULES(self):
        import v2.steps.lists.scroll_until_new as m
        return (m,)

    def test_new_rows_stop_the_scrolling(self):
        ctx = make_ctx([row("ala", 500) + row("ola", 700)])
        res = ScrollUntilNew().run(ctx, {"username_ids": self.IDS})
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["new"], 2)

    def test_identical_list_twice_means_bottom(self):
        """Bez tego bot przewijał w pustkę aż do wyczerpania max_scrolls."""
        same = row("ala", 500)
        ctx = make_ctx([same, same, same])
        ctx.scratch["list_snapshot"] = {("ala", 10)}
        ctx.scratch["list_stalls"] = 1
        res = ScrollUntilNew().run(ctx, {"username_ids": self.IDS})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "bottom_reached")

    def test_control_half_scroll_is_shorter(self):
        """Lista nieprzewinięta w żadną stronę potrafi zawiesić się przy
        pełnym scrollu — pierwszy gest jest o połowę krótszy."""
        ctx = make_ctx([row("ala", 500)])
        ScrollUntilNew().run(ctx, {"username_ids": self.IDS,
                                   "control_half_scroll": True})
        y1, y2 = ctx.ht.swipes[0]
        self.assertAlmostEqual(y1 - y2, 2340 * 0.2, delta=2)


class PickRingTests(_FlatDumpMixin, unittest.TestCase):
    @property
    def MODULES(self):
        import v2.steps.stories.pick_ring_from_list as m
        return (m,)

    def test_taps_avatar_of_first_ringed_row(self):
        ctx = make_ctx(row("bez", 400) + row("zring", 800, ring=True),
                       tops=("story_viewer",))
        res = PickRingFromList().run(ctx, {})
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["username"], "zring")
        self.assertEqual(ctx.ht.taps[0][0], 120)          # awatar, nie nick

    def test_no_ring_means_skip(self):
        ctx = make_ctx(row("ala", 400) + row("ola", 800))
        res = PickRingFromList().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "no_ring_candidates")

    def test_tap_that_opened_a_profile_is_recovered(self):
        """Tap czasem trafia w profil (relacja właśnie wygasła) — wtedy BACK
        i próbujemy dalej, zamiast „oglądać segmenty" na profilu."""
        ctx = make_ctx(row("zring", 800, ring=True),
                       tops=("profile_other", "follow_list", "follow_list"))
        res = PickRingFromList().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "not_a_story")

    def test_recent_users_are_not_reopened(self):
        ctx = make_ctx(row("zring", 800, ring=True), tops=("story_viewer",))
        ctx.scratch["ring_recent"] = ["zring"]
        res = PickRingFromList().run(ctx, {})
        self.assertEqual(res.status, "skipped")


class MassStoriesPluginTests(unittest.TestCase):
    def setUp(self):
        from v2.plugins.impl import mass_stories as mod
        self.mod = mod
        self.recorder = []
        self._orig = mod.steps.get

    def tearDown(self):
        self.mod.steps.get = self._orig

    BASE = {"enabled": True,
            "targets": {"list": ["target_a"], "order": "sequential",
                        "source": "followers"},
            "per_target": {"max_ring_opens": 2, "max_scrolls": 3, "recent_cap": 3},
            "watch": {"segments": {"min": 1, "max": 2}}}

    def _spec(self, **over):
        spec = {"search.open": [], "search.type_query": [],
                "search.pick_result": [StepResult.ok(username="target_a")],
                "profile.open_followers": [StepResult.ok(source="followers")],
                "stories.pick_ring_from_list": [StepResult.ok(username="a"),
                                                StepResult.ok(username="b")],
                "stories.watch_segments": [StepResult.ok(watched=2),
                                           StepResult.ok(watched=1)],
                "stories.close": [StepResult.ok()] * 6,
                "list.scroll_until_new": [StepResult.ok(new=3)]}
        spec.update(over)
        return spec

    def _run(self, spec, s=None, budget=None):
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = FakeCtx()
        res = MassStories().run(ctx, s or self.BASE, budget or Budget(max_items=1))
        return ctx, res, [n for n, _ in self.recorder]

    def test_watches_stories_for_a_target(self):
        ctx, res, names = self._run(self._spec())
        self.assertEqual(res.stats["profiles"], 2)
        self.assertEqual(res.stats["segments"], 3)
        self.assertEqual(res.stats["targets"], 1)

    def test_empty_target_list_ends_immediately(self):
        s = {**self.BASE, "targets": {"list": []}}
        ctx, res, names = self._run(self._spec(), s)
        self.assertEqual(res.stopped_by, "empty")
        self.assertNotIn("search.open", names)

    def test_unreachable_target_does_not_kill_the_run(self):
        spec = self._spec(**{"search.pick_result": [StepResult.failed("brak wyniku")]})
        ctx, res, names = self._run(spec)
        self.assertEqual(res.status, "ok")
        self.assertIn("mass_stories.target_unreachable", ctx.kinds())

    def test_bottom_of_list_ends_the_target(self):
        spec = self._spec(**{
            "stories.pick_ring_from_list": [StepResult.skipped("no_ring_candidates")],
            "list.scroll_until_new": [StepResult.skipped("bottom_reached")]})
        ctx, res, names = self._run(spec)
        self.assertEqual(res.stats["lists_exhausted"], 1)
        self.assertEqual(res.stats["profiles"], 0)

    def test_first_scroll_is_the_control_half_scroll(self):
        spec = self._spec(**{
            "stories.pick_ring_from_list": [StepResult.skipped("no_ring_candidates")],
            "list.scroll_until_new": [StepResult.skipped("bottom_reached")]})
        self._run(spec)
        params = dict(self.recorder)["list.scroll_until_new"]
        self.assertTrue(params["control_half_scroll"])

    def test_story_viewer_is_closed_even_when_a_step_raises(self):
        spec = self._spec(**{"stories.watch_segments": [RuntimeError("padło")]})
        ctx, res, names = self._run(spec)
        self.assertIn("stories.close", names)

    def test_targets_are_shuffled_when_random(self):
        s = {"targets": {"list": [f"t{i}" for i in range(20)], "order": "random"}}
        import random
        picked = _pick_targets(s, random.Random(1))
        self.assertNotEqual(picked, [f"t{i}" for i in range(20)])
        self.assertEqual(sorted(picked), sorted(f"t{i}" for i in range(20)))

    def test_at_symbol_is_stripped_from_targets(self):
        picked = _pick_targets({"targets": {"list": ["@ala", " ola "],
                                            "order": "sequential"}}, None)
        self.assertEqual(picked, ["ala", "ola"])


if __name__ == "__main__":
    unittest.main()
