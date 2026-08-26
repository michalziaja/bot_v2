"""Testy E5: model postoju, tacka relacji, kroki stories.* i plugin feed_browser.

Parser tacki jest sprawdzany na PRAWDZIWYM dumpie z `calibration/samples/` —
to jedyny sposób, żeby wiedzieć, że opisy awatarów wyglądają tak, jak zakładamy.
"""
from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core import dwell
from v2.plugins.base import Budget
from v2.plugins.impl.feed_browser import FeedBrowser
from v2.runner.step import StepResult
from v2.steps.feed.open_tray_story import collect_tray
from v2.steps.stories import _viewer
from v2.tests.test_plugins_runtime import FakeCtx, FakeNavigator, fake_registry

SAMPLES = ROOT / "calibration" / "samples"


def load_sample(name: str) -> list[dict]:
    d = json.loads((SAMPLES / name).read_text(encoding="utf-8"))
    return d if isinstance(d, list) else (d.get("elements") or d.get("flat"))


# ============================================================
class DwellModelTests(unittest.TestCase):
    def test_hashtag_wall_is_not_reading(self):
        """Trzydzieści hashtagów to nie tekst do przeczytania — bez filtra
        dawałyby kilkanaście sekund wpatrywania się w ekran."""
        tags = " ".join(f"#tag{i}" for i in range(30))
        self.assertEqual(dwell.prose_length(tags), 0)

    def test_mentions_and_urls_are_stripped(self):
        n = dwell.prose_length("Zobacz @ala http://foo.pl/x #lato")
        self.assertEqual(n, len("Zobacz"))

    def test_longer_text_takes_longer(self):
        rng = random.Random(1)
        short = dwell.compute("a" * 30, random.Random(1))["seconds"]
        long_ = dwell.compute("a" * 600, random.Random(1))["seconds"]
        self.assertGreater(long_, short)

    def test_reading_time_is_capped(self):
        """Nikt nie czyta opisu w feedzie dwie minuty."""
        d = dwell.compute("a" * 100000, random.Random(3))
        self.assertLessEqual(d["parts"]["read"], dwell.READ_CAP_S)

    def test_empty_caption_is_just_a_glance(self):
        d = dwell.compute("", random.Random(5))
        self.assertEqual(d["parts"]["read"], 0.0)
        self.assertLessEqual(d["seconds"], dwell.GLANCE_S[1] + dwell.LINGER_S[1])

    def test_unreadable_caption_uses_fallback_range(self):
        """`None` znaczy „nie odczytaliśmy", a nie „post nie ma opisu" —
        wtedy trzymamy się starego, bezpiecznego zakresu."""
        d = dwell.compute(None, random.Random(7), fallback=(2.0, 7.0))
        self.assertEqual(d["mode"], "fallback")
        self.assertGreaterEqual(d["seconds"], 2.0)
        self.assertLessEqual(d["seconds"], 7.0)

    def test_variance_between_posts(self):
        """Identyczny czas dla identycznej treści byłby wzorcem."""
        vals = {dwell.compute("a" * 200)["seconds"] for _ in range(20)}
        self.assertGreater(len(vals), 10)


# Dwell opisu w rolkach usuniety (engagement nie „czyta na glos").
# Model postoju nadal w `CaptionDwellTests` powyzej — uzywany np. w feedzie.


# ============================================================
class TrayParserTests(unittest.TestCase):
    FLAT = None

    @classmethod
    def setUpClass(cls):
        cls.FLAT = load_sample("01_home.flat.json")

    def test_reads_real_tray_from_sample_dump(self):
        rows = collect_tray(self.FLAT, own_username="technature699")
        names = [r["username"] for r in rows]
        self.assertEqual(names, ["agagrzelakk", "_toddalinddaloja", "drylkaa"])

    def test_own_account_is_excluded(self):
        rows = collect_tray(self.FLAT, own_username="technature699")
        self.assertNotIn("technature699", [r["username"] for r in rows])

    def test_own_account_excluded_even_with_wrong_config_username(self):
        """Druga droga odsiania siebie — podpis „Twoja relacja" pod awatarem."""
        rows = collect_tray(self.FLAT, own_username="cos_innego")
        self.assertNotIn("technature699", [r["username"] for r in rows])

    def test_unseen_state_and_position_are_read(self):
        rows = collect_tray(self.FLAT, own_username="technature699")
        self.assertIs(rows[0]["seen"], False)          # „Nie wyświetlono."
        self.assertEqual(rows[0]["position"], 1)

    def test_seen_story_is_marked(self):
        flat = [{"resource-id": "com.instagram.android:id/avatar_image_view",
                 "content-desc": "Relacja użytkownika ala, 2 z 5, Wyświetlono.",
                 "bounds": "[0,230][294,513]"}]
        self.assertIs(collect_tray(flat)[0]["seen"], True)

    def test_tray_is_gone_after_scrolling(self):
        """Dump z przewiniętego feedu — tacki już nie ma. To jest powód, dla
        którego plugin ogląda relacje PRZED przewijaniem."""
        self.assertEqual(collect_tray(load_sample("41_home_scrolled.flat.json")), [])

    def test_tray_read_from_second_live_dump(self):
        rows = collect_tray(load_sample("40_home_tray.flat.json"),
                            own_username="technature699")
        self.assertEqual([r["username"] for r in rows],
                         ["_toddalinddaloja", "mgrams66", "cinta_ajjaa16"])

    def test_unknown_state_is_none(self):
        flat = [{"resource-id": "com.instagram.android:id/avatar_image_view",
                 "content-desc": "Relacja użytkownika ala",
                 "bounds": "[0,230][294,513]"}]
        self.assertIsNone(collect_tray(flat)[0]["seen"])


class FeedCaptionTests(unittest.TestCase):
    """Opis posta czytany z dumpu zebranego podczas pierwszego przebiegu E5.

    Pierwsza wersja szukała opisu po `resource-id` i nie znalazła go ani razu
    (`dwell_mode=fallback` na wszystkich sześciu postach) — bo opis nie ma
    żadnego id, tylko własną klasę widgetu.
    """

    @classmethod
    def setUpClass(cls):
        cls.FLAT = load_sample("41_home_scrolled.flat.json")

    def test_caption_found_in_real_dump(self):
        from v2.steps.feed.scroll import _caption
        cap = _caption(self.FLAT)
        self.assertIsNotNone(cap)
        self.assertIn("Bohaterowie Eternii", cap)

    def test_author_and_more_marker_are_stripped(self):
        """Nick jest osobnym przyciskiem nad postem, a „… więcej" to znacznik
        przycięcia — żadne z nich nie jest treścią do przeczytania."""
        from v2.steps.feed.scroll import clean_caption
        self.assertEqual(
            clean_caption("primevideopl Bohaterowie Eternii przybyli.… więcej"),
            "Bohaterowie Eternii przybyli.")

    def test_caption_without_author_is_left_alone(self):
        from v2.steps.feed.scroll import clean_caption
        self.assertEqual(clean_caption("Bohaterowie przybyli"), "przybyli")
        self.assertEqual(clean_caption("jedno_slowo"), "jedno_slowo")

    def test_top_of_feed_has_no_caption(self):
        """Na samej górze widać tackę i nagłówek posta, nie opis — wtedy
        `None` włącza zakres awaryjny, a nie „post bez opisu"."""
        from v2.steps.feed.scroll import _caption
        self.assertIsNone(_caption(load_sample("40_home_tray.flat.json")))


# ============================================================
class StoryViewerHelpersTests(unittest.TestCase):
    def test_sponsored_detected_in_text_or_desc(self):
        self.assertTrue(_viewer.is_sponsored([{"text": "Sponsorowane"}]))
        self.assertTrue(_viewer.is_sponsored([{"content-desc": "Sponsored"}]))
        self.assertFalse(_viewer.is_sponsored([{"text": "zwykły segment"}]))

    def test_real_sponsored_label_from_live_run(self):
        """Element z żywego przebiegu — po nim poznajemy reklamę na pewno."""
        marker = _viewer.sponsored_marker([{
            "resource-id": "com.instagram.android:id/reel_item_sponsored_label_footer_pill",
            "text": "Sponsorowane"}])
        self.assertIn("reel_item_sponsored_label_footer_pill", marker)

    def test_id_match_wins_over_keyword_match(self):
        """Słowo „sponsorowane" może paść w treści cudzej relacji — wtedy
        pierwszeństwo ma element z etykietą, a nie przypadkowy tekst."""
        marker = _viewer.sponsored_marker([
            {"text": "u nas bez sponsorowanych postów!"},
            {"resource-id": "com.instagram.android:id/reel_item_sponsored_label_footer_pill",
             "text": "Sponsorowane"}])
        self.assertIn("reel_item_sponsored_label_footer_pill", marker)

    def test_reply_keyboard_detected_by_resource_id(self):
        self.assertTrue(_viewer.has_reply_keyboard(
            [{"resource-id": "com.instagram.android:id/reply_text"}]))
        self.assertFalse(_viewer.has_reply_keyboard(
            [{"resource-id": "com.instagram.android:id/reel_viewer_root"}]))

    def test_next_tap_stays_away_from_header_and_composer(self):
        """Tap „dalej" ma trafiać w wąski pasek przy prawej krawędzi: wyżej
        jest nick autora (tap otwiera profil), niżej pole odpowiedzi."""
        rng = random.Random(0)
        for _ in range(50):
            x, y = _viewer._pick(rng, 1080, 2340,
                                 _viewer.NEXT_X_FRAC, _viewer.NEXT_Y_FRAC)
            self.assertGreater(x, 1000)
            self.assertGreater(y, 850)
            self.assertLess(y, 1800)


# ============================================================
class StoriesStepsTests(unittest.TestCase):
    def _ctx(self, tops):
        """`tops` — kolejne odpowiedzi `identify().top`, żeby dało się odegrać
        zamknięcie relacji w środku serii."""
        seq = list(tops)
        nav = FakeNavigator()
        nav.identify = lambda: type("S", (), {
            "top": seq.pop(0) if seq else "home_feed", "overlay": None})()
        ctx = FakeCtx(nav)
        ctx.ht = type("H", (), {"tap": lambda *a, **k: None,
                                "swipe": lambda *a, **k: None})()
        ctx.humanizer = type("Hu", (), {
            "pause": staticmethod(lambda *a, **k: None),
            "sleep": staticmethod(lambda s, *a, **k: None),
            "scale": staticmethod(lambda s: s)})()
        ctx.account = type("A", (), {"device": {"_hardware": {"resolution": (1080, 2340)}}})()
        ctx.device = type("D", (), {
            "hierarchy": lambda *a, **k: {"properties": {}, "children": []},
            "press_back": lambda *a, **k: None})()
        return ctx

    def test_watch_segments_skips_when_not_in_viewer(self):
        from v2.steps.stories.watch_segments import WatchSegments
        res = WatchSegments().run(self._ctx(["home_feed"]), {"segments": 2})
        self.assertEqual(res.status, "skipped")

    def test_watch_segments_stops_when_story_closes_early(self):
        """Relacja kończy się sama — dalsze tapy trafiłyby w ekran pod spodem."""
        from v2.steps.stories.watch_segments import WatchSegments
        ctx = self._ctx(["story_viewer", "story_viewer", "home_feed"])
        res = WatchSegments().run(ctx, {"segments": 4,
                                        "segment_view_s": {"min": 0, "max": 0}})
        self.assertEqual(res.payload["watched"], 1)
        self.assertIn("stories.closed_early", ctx.kinds())

    def test_next_user_reports_closed_viewer_as_skip(self):
        """Swipe na ostatniej osobie wychodzi z podglądu — to koniec tacki,
        nie błąd."""
        from v2.steps.stories.next_user import NextUser
        res = NextUser().run(self._ctx(["story_viewer", "home_feed"]), {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "viewer_closed")

    def test_close_is_noop_when_already_closed(self):
        from v2.steps.stories.close import CloseStory
        res = CloseStory().run(self._ctx(["home_feed"]), {})
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.payload["already_closed"])

    def test_close_falls_back_to_back_presses(self):
        """Swipe w dół czasem otwiera panel reakcji zamiast zamykać."""
        from v2.steps.stories.close import CloseStory
        ctx = self._ctx(["story_viewer", "story_viewer", "home_feed"])
        res = CloseStory().run(ctx, {})
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["backs"], 1)

    def test_close_reports_failure_when_stuck(self):
        from v2.steps.stories.close import CloseStory
        ctx = self._ctx(["story_viewer"] * 10)
        res = CloseStory().run(ctx, {"max_backs": 2})
        self.assertEqual(res.status, "failed")
        self.assertIn("stories.close.stuck", ctx.kinds())


# ============================================================
class FeedBrowserPluginTests(unittest.TestCase):
    def setUp(self):
        from v2.plugins.impl import feed_browser as mod
        self.mod = mod
        self.recorder = []
        self._orig = mod.steps.get

    def tearDown(self):
        self.mod.steps.get = self._orig

    BASE = {"enabled": True,
            "scroll": {"dwell_mode": "content_aware",
                       "dwell_fallback_s": {"min": 0, "max": 0},
                       "long_pause_every_n": 0},
            "tray_stories": {"enabled": False}}

    def _run(self, spec, s=None, budget=None):
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = FakeCtx()
        res = FeedBrowser().run(ctx, s or self.BASE, budget or Budget(max_items=3))
        return ctx, res, [n for n, _ in self.recorder]

    def test_scrolls_up_to_budget(self):
        spec = {"feed.scroll": [StepResult.ok(dwell_mode="content_aware")] * 9}
        ctx, res, names = self._run(spec, budget=Budget(max_items=3))
        self.assertEqual(names.count("feed.scroll"), 3)
        self.assertEqual(res.stats["posts"], 3)

    def test_never_calls_any_interaction_step(self):
        """Cały sens tego pluginu: zero śladu na koncie. Gdyby kiedykolwiek
        pojawił się tu lajk, ten test ma spaść."""
        spec = {"feed.scroll": [StepResult.ok()] * 5,
                "feed.open_tray_story": [StepResult.ok(username="a")],
                "stories.watch_segments": [StepResult.ok(watched=2)],
                "stories.next_user": [StepResult.skipped("viewer_closed")],
                "stories.close": [StepResult.ok()]}
        s = {**self.BASE, "tray_stories": {"enabled": True, "max_users": 2,
                                           "watch": {"segments": 2}}}
        ctx, res, names = self._run(spec, s)
        forbidden = [n for n in names if "like" in n or "follow" in n
                     or "comment" in n]
        self.assertEqual(forbidden, [])

    def test_story_like_chance_is_forced_to_zero(self):
        """Nawet gdy ktoś dopisze szansę na lajk do configu ręcznie."""
        spec = {"feed.scroll": [StepResult.ok()] * 5,
                "feed.open_tray_story": [StepResult.ok(username="a")],
                "stories.watch_segments": [StepResult.ok(watched=1)],
                "stories.next_user": [StepResult.skipped("x")],
                "stories.close": [StepResult.ok()]}
        s = {**self.BASE, "tray_stories": {
            "enabled": True, "max_users": 1,
            "watch": {"segments": 1, "like_chance_percent": 90}}}
        ctx, res, names = self._run(spec, s)
        params = dict(self.recorder)["stories.watch_segments"]
        self.assertEqual(params["like_chance_percent"], 0)

    def test_end_of_feed_stops_early(self):
        spec = {"feed.scroll": [StepResult.ok(),
                                StepResult.skipped("end_of_feed")]}
        ctx, res, names = self._run(spec, budget=Budget(max_items=10))
        self.assertEqual(res.stopped_by, "empty")
        self.assertEqual(res.stats["posts"], 1)

    def test_story_viewer_is_closed_even_when_a_step_raises(self):
        """Bez tego epilog sesji zamyka aplikację stojąc w relacji."""
        spec = {"feed.scroll": [StepResult.ok()] * 5,
                "feed.open_tray_story": [StepResult.ok(username="a")],
                "stories.watch_segments": [RuntimeError("relacja padła")],
                "stories.close": [StepResult.ok()]}
        s = {**self.BASE, "tray_stories": {"enabled": True, "max_users": 2,
                                           "watch": {}}}
        ctx, res, names = self._run(spec, s)
        self.assertIn("stories.close", names)

    def test_tray_is_watched_before_scrolling(self):
        """Kolejność ma znaczenie: tacka jest na górze feedu i po przewinięciu
        znika. Pierwszy przebieg na żywo zobaczył `in_tray=0` właśnie dlatego."""
        spec = {"feed.scroll": [StepResult.ok()] * 5,
                "feed.open_tray_story": [StepResult.ok(username="a")],
                "stories.watch_segments": [StepResult.ok(watched=2)],
                "stories.next_user": [StepResult.skipped("viewer_closed")],
                "stories.close": [StepResult.ok()]}
        s = {**self.BASE, "tray_stories": {"enabled": True, "max_users": 1,
                                           "watch": {}}}
        ctx, res, names = self._run(spec, s, budget=Budget(max_items=2))
        self.assertLess(names.index("feed.open_tray_story"),
                        names.index("feed.scroll"))

    def test_no_double_advance_after_sponsored_swipe(self):
        """Krok pomijający reklamę swipe'm jest już u następnej osoby —
        własne „dalej" przeskoczyłoby ją w całości."""
        spec = {"feed.scroll": [StepResult.ok()] * 5,
                "feed.open_tray_story": [StepResult.ok(username="a")],
                "stories.watch_segments": [
                    StepResult.ok(watched=1, advanced_to_next_user=True),
                    StepResult.ok(watched=2, advanced_to_next_user=False),
                    StepResult.ok(watched=2, advanced_to_next_user=False)],
                "stories.next_user": [StepResult.ok()] * 3,
                "stories.close": [StepResult.ok()]}
        s = {**self.BASE, "tray_stories": {"enabled": True, "max_users": 3,
                                           "watch": {}}}
        ctx, res, names = self._run(spec, s, budget=Budget(max_items=1))
        self.assertEqual(names.count("stories.watch_segments"), 3)
        self.assertEqual(names.count("stories.next_user"), 1)   # nie 2

    def test_tray_disabled_never_opens_stories(self):
        spec = {"feed.scroll": [StepResult.ok()] * 5,
                "feed.open_tray_story": []}
        ctx, res, names = self._run(spec, budget=Budget(max_items=2))
        self.assertNotIn("feed.open_tray_story", names)

    def test_failed_tray_open_does_not_break_the_run(self):
        spec = {"feed.scroll": [StepResult.ok()] * 5,
                "feed.open_tray_story": [StepResult.failed("tap nie otworzył")],
                "stories.close": []}
        s = {**self.BASE, "tray_stories": {"enabled": True, "max_users": 2}}
        ctx, res, names = self._run(spec, s, budget=Budget(max_items=1))
        self.assertEqual(res.status, "ok")
        self.assertNotIn("stories.watch_segments", names)

    def test_fallback_dwell_is_counted_separately(self):
        """Licznik mówi, czy trafiliśmy w ID opisu posta — bez niego nie da się
        stwierdzić, czy postój liczy się z treści, czy ze stałego zakresu."""
        spec = {"feed.scroll": [StepResult.ok(dwell_mode="fallback"),
                                StepResult.ok(dwell_mode="content_aware")]}
        ctx, res, names = self._run(spec, budget=Budget(max_items=2))
        self.assertEqual(res.stats["fallback_dwell"], 1)
        self.assertEqual(res.stats["content_aware"], 1)


if __name__ == "__main__":
    unittest.main()
