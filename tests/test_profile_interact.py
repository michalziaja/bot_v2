"""Testy pluginu `profile_interact` i wejścia na profil z sheetu rolki (E4).

Bez telefonu. Sprawdzamy rzeczy, które realnie psują przebieg na żywo:
wykrywanie aktywnej relacji w wierszu listy, wybór kandydatów, zatrzymanie
pętli na limicie dziennym i to, czy skrócona procedura faktycznie pomija
kosztowne etapy.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.plugins.base import Budget
from v2.plugins.impl.profile_interact import (
    ProfileInteract, settings_for, visit_params, _map_raw_outcome, _mark_raw_outcome,
)
from v2.runner.step import StepResult
from v2.steps.reels import _sheet
from v2.steps.reels.open_profile_from_sheet import OpenLikerProfile
from v2.tests.test_plugins_runtime import FakeCtx, FakeNavigator, fake_registry


def node(rid="", cls="android.widget.TextView", text=None, x=100, y=100, w=200, h=40):
    return {"resource-id": rid, "class": cls, "text": text,
            "bounds": f"[{x},{y}][{x + w},{y + h}]"}


def user_row(name, y, *, ring=False, ring_x=80):
    """Wiersz listy: nick + (opcjonalnie) nakładka ringu przy awatarze."""
    out = [node("com.instagram.android:id/row_user_primary_name", text=name,
                x=300, y=y)]
    if ring:
        out.append(node("com.instagram.android:id/row_user_imageview",
                        cls="android.view.View", x=ring_x, y=y))
    return out


# ============================================================
class SheetRowTests(unittest.TestCase):
    def test_ring_detected_next_to_username(self):
        flat = user_row("ala", 500, ring=True) + user_row("ola", 700)
        rows = _sheet.collect_rows(flat)
        self.assertEqual([r["username"] for r in rows], ["ala", "ola"])
        self.assertIs(rows[0]["ring"], True)
        self.assertIs(rows[1]["ring"], False)

    def test_ring_on_the_right_is_ignored(self):
        """Po prawej stronie wiersza stoi przycisk „Obserwuj" z podobnym
        wrapperem — pomyłka kończy się obserwowaniem zamiast wejścia."""
        flat = user_row("ala", 500, ring=True, ring_x=800)
        rows = _sheet.collect_rows(flat)
        self.assertIsNone(rows[0]["ring"])

    def test_unknown_ring_when_nothing_detected(self):
        """Brak jakiejkolwiek nakładki na ekranie znaczy „nie wiemy", a nie
        „nikt nie ma relacji" — od tego zależy zachowanie require_ring."""
        rows = _sheet.collect_rows(user_row("ala", 500))
        self.assertIsNone(rows[0]["ring"])

    def test_duplicate_usernames_collapse(self):
        flat = user_row("ala", 500) + user_row("ala", 900)
        self.assertEqual(len(_sheet.collect_rows(flat)), 1)

    def test_prefer_ring_sorts_but_keeps_everyone(self):
        rows = [{"username": "bez", "ring": False, "center": (1, 1)},
                {"username": "z", "ring": True, "center": (1, 2)}]
        out = _sheet.pick_candidates(rows, prefer_ring=True)
        self.assertEqual([r["username"] for r in out], ["z", "bez"])

    def test_require_ring_drops_unknown(self):
        """Twardy wymóg ma gwarantować profil publiczny — przy nieznanym
        stanie nie wolno zgadywać."""
        rows = [{"username": "a", "ring": None, "center": (1, 1)},
                {"username": "b", "ring": True, "center": (1, 2)}]
        out = _sheet.pick_candidates(rows, require_ring=True)
        self.assertEqual([r["username"] for r in out], ["b"])

    def test_excluded_usernames_are_not_returned(self):
        rows = [{"username": "a", "ring": True, "center": (1, 1)},
                {"username": "b", "ring": True, "center": (1, 2)}]
        out = _sheet.pick_candidates(rows, exclude={"a"})
        self.assertEqual([r["username"] for r in out], ["b"])

    def test_nick_tap_xy_biases_away_from_avatar_ring(self):
        """Środek krótkiego nicku wpada w hitbox ringu — bias w prawo."""
        row = {"username": "x", "center": (320, 500),
               "bounds": (280, 480, 360, 520), "avatar": (80, 500), "ring": True}
        x, y = _sheet.nick_tap_xy(row, bias="safe")
        self.assertGreaterEqual(x, _sheet._AVATAR_MAX_X + 40)
        self.assertLessEqual(x, 360 - 6)
        self.assertEqual(y, 500)
        xr, _ = _sheet.nick_tap_xy(row, bias="right")
        self.assertGreaterEqual(xr, x)


# ============================================================
class FakeHt:
    def __init__(self):
        self.taps = []

    def tap(self, x, y, pause_kind=None):
        self.taps.append((x, y))


class FakeDevice:
    def __init__(self, flat):
        self._flat = flat

    def hierarchy(self, force=False):
        return self._flat

    def wait_for_stable_ui(self, **kw):
        return True

    def press_back(self):
        return True


class OpenProfileStepTests(unittest.TestCase):
    @staticmethod
    def _nav(top="profile_other"):
        """Nawigator widzący zadany ekran. Krok sprawdza go PO tapie i to
        decyduje, czy profil się otworzył."""
        nav = FakeNavigator()
        nav.identify = lambda: type("S", (), {"top": top, "overlay": None})()
        return nav

    def _ctx(self, flat, nav=None, top="profile_other"):
        ctx = FakeCtx(nav or self._nav(top))
        ctx.ht = FakeHt()
        ctx.device = FakeDevice(flat)
        ctx.humanizer = type("H", (), {"pause": lambda *a, **k: None})()
        return ctx

    def setUp(self):
        self._orig_flatten = _sheet.flatten_hierarchy if hasattr(_sheet, "flatten_hierarchy") else None
        import v2.steps.reels.open_profile_from_sheet as mod
        self.mod = mod
        self._orig = mod.flatten_hierarchy
        mod.flatten_hierarchy = lambda h: h

    def tearDown(self):
        self.mod.flatten_hierarchy = self._orig

    def test_taps_username_and_sets_baseline(self):
        """Tap w awatar otwiera relację, nie profil — dlatego celujemy w nick
        z biasem w prawo od ringu (nie środek — live 14:06 → story)."""
        flat = user_row("ala", 500, ring=True)
        ctx = self._ctx(flat)
        res = OpenLikerProfile().run(ctx, {})
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["username"], "ala")
        self.assertEqual(ctx.scratch["baseline_screen"], "reel_likers_list")
        # bounds [300,500][500,540] → 2/3 szerokości = 433 (nie środek 400)
        self.assertEqual(ctx.ht.taps, [(433, 520)])

    def test_second_call_picks_a_different_row(self):
        flat = user_row("ala", 500, ring=True) + user_row("ola", 700, ring=True)
        ctx = self._ctx(flat)
        first = OpenLikerProfile().run(ctx, {})
        second = OpenLikerProfile().run(ctx, {})
        self.assertNotEqual(first.payload["username"], second.payload["username"])

    def test_exhausted_list_is_skipped_not_failed(self):
        ctx = self._ctx(user_row("ala", 500, ring=True))
        OpenLikerProfile().run(ctx, {})
        res = OpenLikerProfile().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "no_candidates")

    def test_require_ring_without_detection_is_reported(self):
        """Bez tego logu „zero interakcji" wygląda jak brak relacji, a może
        znaczyć, że nie umiemy ich wykryć na tej wersji IG."""
        ctx = self._ctx(user_row("ala", 500))
        res = OpenLikerProfile().run(ctx, {"require_ring": True})
        self.assertEqual(res.status, "skipped")
        self.assertIn("reels.open_profile.ring_detection_unconfirmed", ctx.kinds())

    def test_lost_list_after_tap_fails(self):
        """Tap wyprowadził nas i z profilu, i z listy — dopiero to jest błąd.

        Po restore (reopen likers) dalej reels_feed → failed (nie content_search).
        """
        import unittest.mock as mock
        ctx = self._ctx(user_row("ala", 500, ring=True), top="reels_feed")
        with mock.patch.object(_sheet, "open_likers_sheet", return_value=False), \
             mock.patch.object(_sheet, "return_to_reels", return_value=True), \
             mock.patch.object(self.mod.time, "sleep", return_value=None):
            res = OpenLikerProfile().run(ctx, {})
        self.assertEqual(res.status, "failed")

    def test_reels_feed_flash_then_profile_is_ok(self):
        """Animacja sheet→profil miga rolką — pierwszy hit reels_feed ≠ fail."""
        import unittest.mock as mock
        flat = user_row("ala", 500, ring=True)
        tops = [
            "reels_feed",       # mignięcie pod spodem
            "reels_feed",
            "profile_other",    # profil dogonił zanim 3× confirm
        ]
        idx = {"i": 0}

        def _id():
            i = idx["i"]
            top = tops[i] if i < len(tops) else "profile_other"
            idx["i"] = i + 1
            return type("S", (), {"top": top, "overlay": None})()

        nav = FakeNavigator()
        nav.identify = _id
        ctx = self._ctx(flat, nav=nav)
        with mock.patch.object(self.mod.time, "sleep", return_value=None):
            res = OpenLikerProfile().run(ctx, {})
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["username"], "ala")

    def test_failed_tap_that_stays_on_list_is_only_skipped(self):
        """Nieudany tap w jeden wiersz nie może kosztować całej listy.

        Wcześniej krok wołał tu `ensure(profile_other)`, a drabinka traktuje
        listę lajkujących jako ekran do wyjścia (L4c) — BACK wyprowadzał nas
        z sheetu i reszta kandydatów z tej rolki przepadała. Wyszło na żywym
        przebiegu E4.
        """
        ctx = self._ctx(user_row("ala", 500, ring=True), top="reel_likers_list")
        res = OpenLikerProfile().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "open_failed")
        self.assertEqual(res.payload.get("why"), "still_on_list")
        self.assertIn("reels.open_profile.no_profile", ctx.kinds())

    def test_no_profile_log_includes_landed_why(self):
        ctx = self._ctx(user_row("ala", 500, ring=True), top="reel_likers_list")
        OpenLikerProfile().run(ctx, {})
        hit = None
        for kind, payload in ctx.logs:
            if kind == "reels.open_profile.no_profile":
                hit = payload
                break
        self.assertIsNotNone(hit)
        self.assertEqual(hit.get("landed"), "reel_likers_list")
        self.assertEqual(hit.get("why"), "still_on_list")

    def test_story_then_retry_opens_profile(self):
        """prefer_ring: pierwszy tap w ring → story; BACK + bias right → profil."""
        flat = user_row("ala", 500, ring=True)
        # _wait_open_result czyta identify w pętli — sekwencja musi pokryć poll
        tops = [
            "story_viewer",                 # po 1. tapie
            "reel_likers_list",             # po BACK
            "reel_likers_list",             # wait_stable / refind
            "profile_other",               # po 2. tapie (right)
        ]
        idx = {"i": 0}

        def _id():
            i = idx["i"]
            top = tops[i] if i < len(tops) else "profile_other"
            idx["i"] = i + 1
            return type("S", (), {"top": top, "overlay": None})()

        nav = FakeNavigator()
        nav.identify = _id
        ctx = self._ctx(flat, nav=nav)
        res = OpenLikerProfile().run(ctx, {})
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["username"], "ala")
        self.assertGreaterEqual(len(ctx.ht.taps), 2)
        self.assertIn("reels.open_profile.got_story", ctx.kinds())

    def test_waits_for_the_list_to_settle_before_reading_it(self):
        """Zbieranie kończy się swipe'em — lista jedzie jeszcze rozpędem
        i współrzędne z dumpu robionego od razu są nieaktualne."""
        calls = []
        ctx = self._ctx(user_row("ala", 500, ring=True))
        ctx.device.wait_for_stable_ui = lambda **kw: calls.append(kw)
        OpenLikerProfile().run(ctx, {})
        self.assertGreaterEqual(len(calls), 1)


# ============================================================
class VisitParamsTests(unittest.TestCase):
    FULL = {
        "sanity": {"abort_if_private": True},
        "gate": {"enabled": True, "min_posts": 6},
        "story": {"enabled": True, "watch": {"segments": {"min": 1, "max": 3}}},
        "grid_ai": {"enabled": True, "mode": "niche_match", "min_niche_tiles": 2},
        "like_tiles": {"enabled": True, "count": {"min": 1, "max": 2}},
        "follow": {"allow": True, "min_scoring": 0.5},
    }

    def test_full_procedure_passes_everything(self):
        p = visit_params(self.FULL)
        self.assertEqual(p["gate"], {"min_posts": 6})       # bez klucza `enabled`
        self.assertTrue(p["story"]["enabled"])
        self.assertEqual(p["match"]["min_niche_tiles"], 2)
        self.assertTrue(p["like_tiles"]["enabled"])

    def test_short_procedure_skips_costly_stages(self):
        """Skrót to ~20 s na profil zamiast 60–90 — bez tego trzy profile
        na rolkę zajmują cztery minuty zamiast jednej."""
        p = visit_params(self.FULL, short=True)
        self.assertEqual(p["match"], {})
        self.assertEqual(p["story"], {})
        self.assertEqual(p["like_tiles"], {})
        self.assertEqual(p["gate"], {"min_posts": 6})       # bramka zostaje
        self.assertEqual(p["follow"], self.FULL["follow"])

    def test_disabled_gate_is_not_passed(self):
        p = visit_params({**self.FULL, "gate": {"enabled": False, "min_posts": 6}})
        self.assertEqual(p["gate"], {})

    def test_tiles_without_grid_ai_still_passed(self):
        """Tylko % — lajki bez Vision; like_tiles idzie do visit niezależnie od AI."""
        p = visit_params({**self.FULL, "grid_ai": {"enabled": False}})
        self.assertEqual(p["match"], {})
        self.assertTrue(p["like_tiles"]["enabled"])

    def test_tiles_include_chance_default_shape(self):
        s = {**self.FULL, "like_tiles": {
            "enabled": True, "count": {"min": 1, "max": 2},
            "chance": {"mode": "p", "value": 0.5},
        }}
        p = visit_params(s)
        self.assertEqual(p["like_tiles"]["chance"]["value"], 0.5)


# ============================================================
class ProfileInteractPluginTests(unittest.TestCase):
    def setUp(self):
        from v2.plugins.impl import profile_interact as mod
        self.mod = mod
        self.recorder = []
        self._orig_get = mod.steps.get

    def tearDown(self):
        self.mod.steps.get = self._orig_get

    BASE = {
        "enabled": True,
        "source": {"kind": "target_candidates", "dedup_days": 30},
        "sanity": {}, "gate": {"enabled": False}, "story": {"enabled": False},
        "grid_ai": {"enabled": False}, "like_tiles": {"enabled": False},
        "follow": {"allow": True},
        "pause_between_profiles_s": {"min": 0, "max": 0},
    }

    def _run(self, spec, settings=None, budget=None, queue=None, ctx=None):
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = ctx or FakeCtx()
        ctx.scratch["profile_queue"] = queue if queue is not None else [
            {"username": "a"}, {"username": "b"}, {"username": "c"}]
        res = ProfileInteract().run(ctx, settings or self.BASE,
                                    budget or Budget(max_items=2))
        return ctx, res, [n for n, _ in self.recorder]

    def _spec(self, visits, picked=3):
        return {"profile.pick_from_store": [StepResult.ok(picked=picked)],
                "search.open": [], "search.type_query": [],
                "search.pick_result": [StepResult.ok(username="x")],
                "profile.visit": visits,
                "profile.exit": []}

    def test_visits_profiles_from_queue(self):
        ctx, res, names = self._run(self._spec(
            [StepResult.ok(username="a", action="follow", followed=True),
             StepResult.ok(username="b", action="skip")]))
        self.assertEqual(names.count("profile.visit"), 2)
        self.assertEqual(res.stats["visited"], 2)
        self.assertEqual(res.stats["followed"], 1)
        self.assertEqual(res.stopped_by, "items")

    def test_empty_store_ends_without_touching_search(self):
        ctx, res, names = self._run(self._spec([], picked=0), queue=[])
        self.assertNotIn("search.open", names)
        self.assertEqual(res.stopped_by, "empty")

    def test_returns_to_search_even_when_visit_raises(self):
        """Po padzie wizyty wracamy na search (nie orphan na profilu)."""
        ctx, res, names = self._run(self._spec([RuntimeError("profil padł")]))
        # nowa ścieżka: _return_to_search → search.open (FakeNav ≠ search)
        self.assertIn("search.open", names)
        self.assertNotIn("profile.exit", names)

    def test_second_profile_reuses_search_tab(self):
        """Drugi profil: bez ponownego search.open — zostajemy na search UX."""
        nav = FakeNavigator()
        # identify: reuse check ×2 wizyty + _return_to_search loops
        nav.identify = lambda: type(
            "S", (), {"top": "search_input_active", "overlay": None})()
        ctx = FakeCtx(nav)
        ctx.device = type("D", (), {
            "press_back": lambda self: None,
        })()
        _, res, names = self._run(
            self._spec(
                [StepResult.ok(username="a", action="skip"),
                 StepResult.ok(username="b", action="skip")]),
            ctx=ctx)
        # Już na search → zero search.open (ani przed, ani w finally)
        self.assertEqual(names.count("search.open"), 0)
        self.assertEqual(names.count("search.type_query"), 2)
        self.assertEqual(res.stats["visited"], 2)
        self.assertIn("profile_interact.search_reuse", ctx.kinds())

    def test_daily_limit_stops_the_whole_plugin(self):
        ctx, res, names = self._run(self._spec(
            [StepResult.skipped("follow_daily_exceeded (30/30)",
                                skip_until="session.end"),
             StepResult.ok(username="b")]))
        self.assertEqual(res.stopped_by, "limit_daily")
        self.assertEqual(names.count("profile.visit"), 1)

    def test_three_failed_entries_stop_the_plugin(self):
        """Jedno pudło bywa przypadkiem, trzy z rzędu znaczą zły ekran —
        dalsze próby tylko mielą budżet."""
        spec = self._spec([])
        spec["search.pick_result"] = [StepResult.failed("brak wyników")] * 3
        ctx, res, names = self._run(spec, budget=Budget(max_items=10))
        self.assertEqual(res.stopped_by, "error")
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.stats["failed"], 3)

    def test_unsupported_source_does_not_silently_fall_back(self):
        s = {**self.BASE, "source": {"kind": "reel_harvest"}}
        ctx, res, names = self._run(self._spec([]), settings=s)
        self.assertEqual(res.stopped_by, "empty")
        self.assertIn("profile_interact.unsupported_source", ctx.kinds())
        self.assertNotIn("profile.pick_from_store", names)

    def test_raw_profiles_source_calls_pick(self):
        """Bez target_candidates — raw_profiles musi iść do pick_from_store."""
        s = {**self.BASE, "source": {"kind": "raw_profiles", "dedup_days": 7}}
        ctx, res, names = self._run(self._spec(
            [StepResult.ok(username="a", action="skip")]), settings=s)
        self.assertIn("profile.pick_from_store", names)
        params = dict(self.recorder)["profile.pick_from_store"]
        self.assertEqual(params["source_kind"], "raw_profiles")
        self.assertEqual(params["dedup_days"], 7)
        self.assertFalse(params.get("shuffle"))  # najnowsze pierwsze

    def test_budget_limits_number_of_profiles(self):
        ctx, res, names = self._run(self._spec([StepResult.ok()] * 5),
                                    budget=Budget(max_items=1))
        self.assertEqual(names.count("profile.visit"), 1)
        self.assertEqual(res.items_done, 1)


# ============================================================
class RawOutcomeMapTests(unittest.TestCase):
    def test_private_and_gate(self):
        self.assertEqual(_map_raw_outcome({"action": "became_private"}), "private")
        self.assertEqual(_map_raw_outcome({"action": "gate_failed"}), "gate_failed")
        self.assertEqual(_map_raw_outcome({"action": "match_failed"}), "match_failed")

    def test_liked_and_followed_win(self):
        self.assertEqual(_map_raw_outcome({"followed": True, "tiles_liked": 2}),
                         "followed")
        self.assertEqual(_map_raw_outcome({"followed": False, "tiles_liked": 1}),
                         "liked")

    def test_hot_flag_skips_mark(self):
        """Hot z rolek nie mutuje raw_profiles."""
        ctx = FakeCtx()
        ctx.scratch["hot_from_reels"] = True
        # bez account/events — gdyby flaga nie działała, też by nic nie zrobił;
        # sprawdzamy, że flaga kończy wcześniej (brak logu raw_outcome)
        _mark_raw_outcome(ctx, "ala", {"action": "skip", "followed": False,
                                       "tiles_liked": 0, "status": "ok"})
        self.assertNotIn("profile_interact.raw_outcome", ctx.kinds())


# ============================================================
class SettingsForTests(unittest.TestCase):
    def test_falls_back_to_catalog_defaults(self):
        """Wołane z rolek, gdzie configu konta może nie dać się wczytać —
        ma zejść do domyślnych, a nie wysypać rolki w połowie."""
        ctx = FakeCtx()
        s = settings_for(ctx)
        self.assertIn("follow", s)
        self.assertIn("profile_interact.settings_fallback", ctx.kinds())

    def test_overrides_are_applied_in_order(self):
        ctx = FakeCtx()
        s = settings_for(ctx,
                         {"follow": {"min_scoring": 0.9}},
                         {"follow": {"min_scoring": 0.2}})
        self.assertEqual(s["follow"]["min_scoring"], 0.2)
        self.assertTrue(s["follow"]["allow"])          # reszta z domyślnych

    def test_override_does_not_leak_into_catalog(self):
        from v2.plugins.catalog import get_plugin
        ctx = FakeCtx()
        settings_for(ctx, {"follow": {"min_scoring": 0.01}})
        self.assertEqual(get_plugin("profile_interact")["defaults"]["follow"]["min_scoring"],
                         0.5)


if __name__ == "__main__":
    unittest.main()
