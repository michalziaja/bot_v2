"""Testy logiki decyzyjnej profile.visit — _eval_match / _score / _decide / like tiles."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.runner.step import StepResult
from v2.steps.profile.visit import (
    _eval_match, _score_profile, _decide_action, _like_grid_tiles,
    _soft_return_to_profile, _analyze_grid, _watch_story, ProfileVisit,
)
from v2.data.stores import VisitedProfilesStore


def _tiles(cats):
    return {"tiles": {str(i + 1): {"category": c, "should_like": c == "niche"}
                       for i, c in enumerate(cats)}}


class MatchEvalTests(unittest.TestCase):
    def test_no_tiles_fails(self):
        r = _eval_match({"tiles": {}}, {"min_niche_tiles": 1})
        self.assertFalse(r["pass"])

    def test_enough_niche_passes(self):
        r = _eval_match(_tiles(["niche", "niche", "neutral"]), {"min_niche_tiles": 2})
        self.assertTrue(r["pass"])

    def test_single_excluded_tile_does_not_kill_profile(self):
        """Wykluczony kafelek wypada tylko z puli do polubienia — profil zostaje.

        Wcześniej działał przełącznik `excluded_kills`, przez który JEDEN
        kafelek dyskwalifikował cały profil. Za ostro: prawie każdy profil
        ma coś spoza niszy.
        """
        r = _eval_match(_tiles(["niche", "niche", "excluded"]),
                        {"min_niche_tiles": 2, "max_excluded_ratio": 0.5})
        self.assertTrue(r["pass"])
        self.assertEqual(r["n_excluded"], 1)

    def test_mostly_excluded_profile_fails(self):
        """Ale profil w większości wykluczony nie ma sensu — 3/4 > 0.5."""
        r = _eval_match(_tiles(["niche", "excluded", "excluded", "excluded"]),
                        {"min_niche_tiles": 1, "max_excluded_ratio": 0.5})
        self.assertFalse(r["pass"])
        self.assertIn("excluded_ratio", r["why"])

    def test_max_excluded_ratio_one_never_rejects(self):
        r = _eval_match(_tiles(["excluded", "excluded"]),
                        {"mode": "exclude_only", "min_valid_tiles": 0,
                         "max_excluded_ratio": 1.0})
        self.assertTrue(r["pass"])

    def test_exclude_only_passes_without_any_niche_tile(self):
        """Konto ogólne: nie wymagamy trafienia w niszę, wystarczy dość
        kafelków, które przeszły przez wykluczenia."""
        r = _eval_match(_tiles(["neutral", "neutral", "neutral", "excluded"]),
                        {"mode": "exclude_only", "min_valid_tiles": 3,
                         "max_excluded_ratio": 0.5})
        self.assertTrue(r["pass"])
        self.assertEqual(r["n_valid"], 3)

    def test_exclude_only_fails_when_too_few_valid_tiles(self):
        r = _eval_match(_tiles(["neutral", "excluded"]),
                        {"mode": "exclude_only", "min_valid_tiles": 3,
                         "max_excluded_ratio": 0.9})
        self.assertFalse(r["pass"])
        self.assertIn("valid_tiles", r["why"])

    def test_niche_match_fails_where_exclude_only_would_pass(self):
        """Ten sam profil: tryb decyduje o wyniku."""
        tiles = _tiles(["neutral", "neutral", "neutral"])
        strict = _eval_match(tiles, {"mode": "niche_match", "min_niche_tiles": 2})
        loose  = _eval_match(tiles, {"mode": "exclude_only", "min_valid_tiles": 2})
        self.assertFalse(strict["pass"])
        self.assertTrue(loose["pass"])

    def test_ratio(self):
        # 2/4 = 0.5, próg 0.4 — ok
        r = _eval_match(_tiles(["niche", "niche", "neutral", "neutral"]),
                        {"min_niche_ratio": 0.4})
        self.assertTrue(r["pass"])
        r2 = _eval_match(_tiles(["niche", "neutral", "neutral", "neutral"]),
                         {"min_niche_ratio": 0.5})
        self.assertFalse(r2["pass"])


class ScoreTests(unittest.TestCase):
    @staticmethod
    def _score(*a, **kw):
        return _score_profile(*a, **kw)[0]

    def test_high_niche_boosts(self):
        s_high = self._score({"followers": 1000, "following": 200},
                             _tiles(["niche", "niche", "niche", "niche"]),
                             story_watched=True, tiles_liked=2)
        s_low = self._score({"followers": 1000, "following": 200},
                            _tiles(["neutral", "neutral", "neutral", "neutral"]),
                            story_watched=False, tiles_liked=0)
        self.assertGreater(s_high, s_low)

    def test_score_bounded(self):
        s = self._score({"followers": 1000000, "following": 10},
                        _tiles(["niche"] * 6),
                        story_watched=True, tiles_liked=10)
        self.assertLessEqual(s, 1.0)
        self.assertGreaterEqual(s, 0.0)

    def test_perfect_profile_reaches_one(self):
        """Bez normalizacji maksimum wynosiło 0.85 — próg „1.0" był
        nieosiągalny nawet dla idealnego profilu."""
        s = self._score({"followers": 10000, "following": 100},
                        _tiles(["niche"] * 6), story_watched=True, tiles_liked=3)
        self.assertAlmostEqual(s, 1.0, places=6)

    def test_short_procedure_can_still_reach_threshold(self):
        """Regresja z żywego przebiegu E4: w skróconej procedurze nie ma siatki,
        relacji ani kafelków, więc przed normalizacją wynik nie mógł przekroczyć
        ~0.2 i follow „na gorąco" nie padał NIGDY."""
        s = self._score({"followers": 5000, "following": 100}, {},
                        story_watched=False, tiles_liked=0,
                        story_stage=False, tiles_stage=False)
        self.assertGreaterEqual(s, 0.5)

    def test_missing_stages_do_not_count_as_zero(self):
        """Etap, którego nie wykonano, nie może obniżać wyniku — inaczej krótsza
        procedura karze profil za coś, czego nie sprawdzaliśmy."""
        with_stages = self._score({"followers": 5000, "following": 100}, {},
                                  story_watched=False, tiles_liked=0)
        without = self._score({"followers": 5000, "following": 100}, {},
                              story_watched=False, tiles_liked=0,
                              story_stage=False, tiles_stage=False)
        self.assertGreater(without, with_stages)

    def test_score_parts_are_reported(self):
        _, parts = _score_profile({"followers": 100, "following": 100}, {},
                                  story_watched=False, tiles_liked=0,
                                  story_stage=False, tiles_stage=False)
        self.assertEqual(set(parts), {"follower_ratio"})


class DecideTests(unittest.TestCase):
    def test_below_threshold_skips(self):
        action, why = _decide_action(0.3, {"min_scoring": 0.5})
        self.assertEqual(action, "skip")
        self.assertEqual(why, "ponizej progu")

    def test_above_threshold_follows_when_no_chance(self):
        action, why = _decide_action(0.8, {"min_scoring": 0.5})
        self.assertEqual(action, "follow")
        self.assertIsNone(why)

    def test_chance_never_hits(self):
        action, why = _decide_action(0.9, {"min_scoring": 0.5, "chance": {"p": 0}})
        self.assertEqual(action, "skip")
        self.assertEqual(why, "szansa")

    def test_chance_always_hits(self):
        action, why = _decide_action(0.9, {"min_scoring": 0.5, "chance": {"p": 1.0}})
        self.assertEqual(action, "follow")
        self.assertIsNone(why)


class RecoverProfileGridTests(unittest.TestCase):
    """Live 13:56: ensure(profile_other) wspinał się search_results→search_top."""

    def test_abort_on_search_without_ensure(self):
        from v2.steps.profile.visit import _recover_profile_grid

        ensures = []
        backs = []

        class Nav:
            def identify(self):
                return type("S", (), {"top": "search_results", "overlay": None})()

            def ensure(self, target, max_steps=6):
                ensures.append(target)
                return False

        class Dev:
            def press_back(self):
                backs.append(1)

            def hierarchy(self, force=False):
                return []

        class Hum:
            def pause(self, *a, **k):
                return 0.0

            def sleep(self, *a, **k):
                return 0.0

            _rng = __import__("random").Random(0)

        ctx = type("Ctx", (), {})()
        ctx.navigator = Nav()
        ctx.device = Dev()
        ctx.humanizer = Hum()
        ctx.logs = []
        ctx.log = lambda kind, **p: ctx.logs.append((kind, p))

        with mock.patch("v2.core.screen_map.flatten_hierarchy", return_value=[]), \
             mock.patch("v2.steps.post._post.media_viewer_open", return_value=False):
            _recover_profile_grid(ctx)

        self.assertEqual(ensures, [])
        self.assertEqual(backs, [])
        kinds = [k for k, _ in ctx.logs]
        self.assertIn("profile.visit.like_recover_abort", kinds)

    def test_backs_while_viewer_open(self):
        from v2.steps.profile.visit import _recover_profile_grid

        queue = ["reels_feed", "profile_other"]
        backs = []
        ensures = []

        class Nav:
            def identify(self):
                top = queue.pop(0) if queue else "profile_other"
                return type("S", (), {"top": top, "overlay": None})()

            def ensure(self, target, max_steps=6):
                ensures.append(target)
                return False

        class Dev:
            def press_back(self):
                backs.append(1)

            def hierarchy(self, force=False):
                return []

        class Hum:
            def pause(self, *a, **k):
                return 0.0

            def sleep(self, *a, **k):
                return 0.0

            _rng = __import__("random").Random(0)

        ctx = type("Ctx", (), {})()
        ctx.navigator = Nav()
        ctx.device = Dev()
        ctx.humanizer = Hum()
        ctx.logs = []
        ctx.log = lambda kind, **p: ctx.logs.append((kind, p))

        # Pierwszy identify: viewer; po BACK: profile
        viewer_flags = [True, False]

        def media_open(flat):
            return viewer_flags.pop(0) if viewer_flags else False

        with mock.patch("v2.core.screen_map.flatten_hierarchy", return_value=[]), \
             mock.patch("v2.steps.post._post.media_viewer_open", side_effect=media_open):
            _recover_profile_grid(ctx)

        self.assertEqual(len(backs), 1)
        self.assertEqual(ensures, [])


class FollowSkipLogTests(unittest.TestCase):
    """Live 13:56: action=follow + followed=false → jasny powod, nie sam score."""

    def test_follow_miss_shows_reason(self):
        from v2.plugins.profile_log import follow_skip_line
        line = follow_skip_line(scoring=0.79, reason="brak przycisku")
        self.assertIn("brak przycisku", line)
        self.assertIn("0.79", line)
        self.assertNotEqual(line, "Skip follow — score 0.79")


class LikeGridTilesTests(unittest.TestCase):
    """Realne lajki: firm_tap → post.like → recover — bez telefonu."""

    def _ctx(self):
        taps = []
        backs = []

        class Ht:
            def firm_tap(self, x, y, hold_ms=140, pause_kind=None):
                taps.append((x, y, hold_ms))

            def tap(self, x, y, pause_kind=None):
                taps.append((x, y, None))

        class Nav:
            def identify(self):
                return type("S", (), {"top": "profile_other", "overlay": None})()

            def ensure(self, target, max_steps=6):
                return True

        class Dev:
            def hierarchy(self, force=False):
                return []

            def wait_for_stable_ui(self, **kw):
                return True

            def press_back(self):
                backs.append(1)

        class Hum:
            def pause(self, *a, **k):
                return 0.0

            def sleep(self, *a, **k):
                return 0.0

            _rng = __import__("random").Random(0)

        ctx = type("Ctx", (), {})()
        ctx.ht = Ht()
        ctx.navigator = Nav()
        ctx.device = Dev()
        ctx.humanizer = Hum()
        ctx.logs = []
        ctx.log = lambda kind, **p: ctx.logs.append((kind, p))
        ctx.scratch = {
            # 2 kafelki — numery AI 1 i 2
            "profile_grid_tiles": [(0, 0, 100, 100), (100, 0, 200, 100)],
        }
        ctx._taps = taps
        ctx._backs = backs
        return ctx

    def test_likes_open_and_calls_post_like(self):
        ctx = self._ctx()
        ai = _tiles(["niche", "niche"])
        like_calls = []

        def fake_call(registry, ctx, name, params, source=""):
            like_calls.append(name)
            return StepResult.ok()

        with mock.patch("v2.core.limits.check",
                        return_value=type("L", (), {
                            "allowed": True, "scope": "h", "reason": ""})()), \
             mock.patch("v2.plugins.stepcall.call", side_effect=fake_call), \
             mock.patch("v2.core.screen_map.flatten_hierarchy", return_value=[]), \
             mock.patch("v2.steps.post._post.media_viewer_open", return_value=True), \
             mock.patch("time.sleep", return_value=None):
            n = _like_grid_tiles(
                ctx, ai,
                {"enabled": True, "count": {"min": 1, "max": 1}},
                {"pick_strategy": "best", "mode": "niche_match"})
        self.assertEqual(n, 1)
        self.assertEqual(like_calls, ["post.like"])
        self.assertTrue(ctx._taps)
        # Już na profile_other → recover nie musi robić BACK (to OK)
        kinds = [k for k, _ in ctx.logs]
        self.assertIn("profile.visit.like_tile_ok", kinds)
        self.assertNotIn("profile.visit.like_tile_skipped_todo", kinds)

    def test_no_bounds_skips_without_like(self):
        ctx = self._ctx()
        ctx.scratch["profile_grid_tiles"] = []
        like_calls = []
        with mock.patch("v2.core.limits.check",
                        return_value=type("L", (), {
                            "allowed": True, "scope": "h", "reason": ""})()), \
             mock.patch("v2.plugins.stepcall.call",
                        side_effect=lambda *a, **k: like_calls.append(1)
                        or StepResult.ok()):
            n = _like_grid_tiles(
                ctx, _tiles(["niche"]),
                {"count": {"min": 1, "max": 1}}, {})
        self.assertEqual(n, 0)
        self.assertEqual(like_calls, [])


class SoftReturnAfterStoryTests(unittest.TestCase):
    """Regresja live 13:36: ensure(profile_other) wspinał się likers→search."""

    def _ctx(self, tops):
        """tops = kolejne wartości identify().top."""
        queue = list(tops)
        backs = []
        ensures = []

        class Nav:
            def identify(self):
                top = queue.pop(0) if queue else tops[-1]
                return type("S", (), {"top": top, "overlay": None})()

            def ensure(self, target, max_steps=6):
                ensures.append((target, max_steps))
                return False

        class Dev:
            def press_back(self):
                backs.append(1)

            def hierarchy(self, force=False):
                return []

            def wait_for_stable_ui(self, **kw):
                return True

        class Hum:
            def pause(self, *a, **k):
                return 0.0

            def sleep(self, *a, **k):
                return 0.0

            _rng = __import__("random").Random(0)

        class Ht:
            def tap(self, *a, **k):
                return None

        ctx = type("Ctx", (), {})()
        ctx.navigator = Nav()
        ctx.device = Dev()
        ctx.humanizer = Hum()
        ctx.ht = Ht()
        ctx.logs = []
        ctx.log = lambda kind, **p: ctx.logs.append((kind, p))
        ctx.scratch = {}
        ctx.account = type("A", (), {
            "device": {"_hardware": {"resolution": [1080, 2340]}},
            "niche": {},
            "raw": {},
        })()
        ctx._backs = backs
        ctx._ensures = ensures
        return ctx

    def test_soft_return_backs_once_from_story_to_profile(self):
        # story_viewer → BACK → profile_other
        ctx = self._ctx(["story_viewer", "profile_other"])
        self.assertTrue(_soft_return_to_profile(ctx, max_backs=2))
        self.assertEqual(len(ctx._backs), 1)
        self.assertEqual(ctx._ensures, [])

    def test_soft_return_second_back_if_still_story(self):
        ctx = self._ctx(["story_viewer", "story_viewer", "profile_other"])
        self.assertTrue(_soft_return_to_profile(ctx, max_backs=2))
        self.assertEqual(len(ctx._backs), 2)
        self.assertEqual(ctx._ensures, [])

    def test_soft_return_aborts_on_likers_without_ensure(self):
        """Live bug: po 1× BACK ze story top=reel_likers_list — NIE ensure."""
        ctx = self._ctx(["reel_likers_list"])
        self.assertFalse(_soft_return_to_profile(ctx, max_backs=2))
        self.assertEqual(ctx._backs, [])
        self.assertEqual(ctx._ensures, [])
        kinds = [k for k, _ in ctx.logs]
        self.assertIn("profile.visit.story_soft_return_abort", kinds)

    def test_analyze_grid_no_ensure_when_off_profile(self):
        ctx = self._ctx(["search_top"])
        with mock.patch("v2.plugins.store.niche_is_accepted", return_value=True):
            out = _analyze_grid(ctx, {"min_niche_tiles": 1})
        self.assertEqual(out.get("_error"), "not_on_profile")
        self.assertEqual(ctx._ensures, [])
        kinds = [k for k, _ in ctx.logs]
        self.assertIn("profile.visit.grid_not_on_profile", kinds)

    def test_watch_story_miss_on_profile_skips_ensure(self):
        """Tap nie otworzył viewer — zostajemy na profilu, zero ensure."""
        ctx = self._ctx(["profile_other", "profile_other"])
        with mock.patch("v2.core.screen_map.flatten_hierarchy", return_value=[]), \
             mock.patch("v2.steps.profile._header.story_ring_tap_xy",
                        return_value=(100, 200)):
            ok = _watch_story(ctx)
        self.assertFalse(ok)
        self.assertEqual(ctx._ensures, [])
        self.assertEqual(ctx._backs, [])


class SaveVisitedOnRejectTests(unittest.TestCase):
    def test_sanity_private_writes_visited(self):
        """Prywatny profil musi trafić do visited — inaczej wraca z raw."""
        tmp = Path(tempfile.mkdtemp())
        try:
            events = type("E", (), {})()
            # file_path = tmp/accounts/acc/events.jsonl → parent×3 = tmp (= data_root)
            p = tmp / "accounts" / "acc" / "events.jsonl"
            p.parent.mkdir(parents=True)
            p.write_text("", encoding="utf-8")
            events.file_path = p

            account = type("A", (), {"account_id": "acc", "device": {
                "_hardware": {"resolution": [1080, 2340]}}})()

            ctx = type("Ctx", (), {})()
            ctx.account = account
            ctx.events = events
            ctx.scratch = {}
            ctx.logs = []
            ctx.log = lambda kind, **pl: ctx.logs.append((kind, pl))
            ctx.device = type("D", (), {})()
            ctx.session_id = "s"

            with mock.patch("v2.steps.profile.visit.read_profile_header",
                            return_value={"username": "priv", "is_private": True,
                                          "already_following": False}):
                res = ProfileVisit().run(ctx, {
                    "sanity": {"abort_if_private": True},
                    "gate": {}, "story": {}, "match": {},
                    "like_tiles": {}, "follow": {},
                })
            self.assertEqual(res.status, "skipped")
            store = VisitedProfilesStore("acc", tmp)
            rows = store.read_all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["username"], "priv")
            self.assertIn("sanity:", rows[0]["reason"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pre_grid_private_aborts_before_ai(self):
        """Regresja @ghescrime: prywatny wykryty dopiero przed siatką → bez Vision.

        Pierwszy header mógł przejść (stary wording / timing), a empty-state
        „To konto jest prywatne" widać dopiero przy ponownym dumpie przed grid.
        """
        tmp = Path(tempfile.mkdtemp())
        try:
            events = type("E", (), {})()
            p = tmp / "accounts" / "acc" / "events.jsonl"
            p.parent.mkdir(parents=True)
            p.write_text("", encoding="utf-8")
            events.file_path = p

            ctx = type("Ctx", (), {})()
            ctx.account = type("A", (), {
                "account_id": "acc",
                "device": {"_hardware": {"resolution": [1080, 2340]}},
            })()
            ctx.events = events
            ctx.scratch = {}
            ctx.logs = []
            ctx.log = lambda kind, **pl: ctx.logs.append((kind, pl))
            ctx.session_id = "s"
            ctx.device = type("D", (), {})()
            ctx.navigator = type("N", (), {
                "identify": lambda self: type(
                    "S", (), {"top": "profile_other", "overlay": None})(),
            })()

            # 1) pierwszy dump: publiczny (bug stringa / timing)
            # 2) pre_grid: prywatny — „To konto jest prywatne"
            headers = [
                {"username": "ghescrime", "is_private": False,
                 "already_following": False, "has_story_ring": False,
                 "posts": 81, "followers": 88, "following": 91},
                {"username": "ghescrime", "is_private": True,
                 "already_following": False, "has_story_ring": False,
                 "posts": 81, "followers": 88, "following": 91},
            ]
            analyze_calls = []

            with mock.patch("v2.steps.profile.visit.read_profile_header",
                            side_effect=headers), \
                 mock.patch("v2.steps.profile.visit._analyze_grid",
                            side_effect=lambda *a, **k: analyze_calls.append(1)
                            or {"tiles": {}}), \
                 mock.patch("v2.plugins.profile_log.say"), \
                 mock.patch("v2.plugins.profile_log.sanity_reject_line",
                            return_value="ODRZUT: prywatny @ghescrime"), \
                 mock.patch("v2.plugins.profile_log.story_none_line",
                            return_value=""):
                res = ProfileVisit().run(ctx, {
                    "sanity": {"abort_if_private": True},
                    "gate": {},
                    "story": {"enabled": True},
                    "match": {"min_niche_tiles": 1},
                    "like_tiles": {},
                    "follow": {},
                })
            self.assertEqual(res.status, "skipped")
            self.assertEqual(res.payload.get("reason"), "became_private")
            self.assertEqual(analyze_calls, [])  # Vision / zrzut NIE wystartował
            kinds = [k for k, _ in ctx.logs]
            self.assertIn("profile.visit.sanity_abort", kinds)
            store = VisitedProfilesStore("acc", tmp)
            rows = store.read_all()
            self.assertEqual(len(rows), 1)
            self.assertIn("became_private", rows[0]["reason"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_left_profile_after_story_aborts_clean(self):
        """Po story poza profilem → skip left_profile, nie match/no_ai_tiles."""
        tmp = Path(tempfile.mkdtemp())
        try:
            events = type("E", (), {})()
            p = tmp / "accounts" / "acc" / "events.jsonl"
            p.parent.mkdir(parents=True)
            p.write_text("", encoding="utf-8")
            events.file_path = p

            ctx = type("Ctx", (), {})()
            ctx.account = type("A", (), {
                "account_id": "acc",
                "device": {"_hardware": {"resolution": [1080, 2340]}},
            })()
            ctx.events = events
            ctx.scratch = {}
            ctx.logs = []
            ctx.log = lambda kind, **pl: ctx.logs.append((kind, pl))
            ctx.session_id = "s"
            # Argument `ctx.device` jest ewaluowany przed mockiem read_profile_header
            ctx.device = type("D", (), {})()
            ctx.navigator = type("N", (), {
                "identify": lambda self: type(
                    "S", (), {"top": "reel_likers_list", "overlay": None})(),
            })()

            with mock.patch("v2.steps.profile.visit.read_profile_header",
                            return_value={
                                "username": "u1", "is_private": False,
                                "already_following": False,
                                "has_story_ring": True,
                                "followers": 10, "following": 10, "posts": 5,
                            }), \
                 mock.patch("v2.steps.profile.visit._watch_story",
                            return_value=True), \
                 mock.patch("v2.plugins.profile_log.say"), \
                 mock.patch("v2.plugins.profile_log.story_watching_line",
                            return_value=""):
                res = ProfileVisit().run(ctx, {
                    "sanity": {},
                    "gate": {},
                    "story": {"enabled": True},
                    "match": {"min_niche_tiles": 1},
                    "like_tiles": {},
                    "follow": {},
                })
            self.assertEqual(res.status, "skipped")
            self.assertEqual(res.payload.get("reason"), "left_profile")
            store = VisitedProfilesStore("acc", tmp)
            rows = store.read_all()
            self.assertEqual(len(rows), 1)
            self.assertIn("left_profile", rows[0]["reason"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
