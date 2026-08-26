"""Testy E7/E8: content_search, target_scraper, reels_warmup + kroki post.*/list.*.

Nacisk na dwie rzeczy, które przy tych pluginach są najważniejsze:
zero interakcji w scraperze (wymuszone kodem) i to, że przy niepotwierdzonych
selektorach kroki mówią „nie znalazłem", zamiast tapać w losowe miejsce.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.plugins.base import Budget
from v2.plugins.impl.content_search import ContentSearch, _pick_phrases, _query_for
from v2.plugins.impl.reels_warmup import ReelsWarmup
from v2.plugins.impl.target_scraper import TargetScraper, _pick_targets
from v2.runner.step import StepResult
from v2.steps.post import _post
from v2.tests.test_plugins_runtime import FakeCtx, FakeNavigator, ScratchStep, fake_registry


def tile(desc, x, y):
    return {"resource-id": "com.instagram.android:id/image_button",
            "class": "android.widget.Button", "content-desc": desc,
            "bounds": f"[{x},{y}][{x + 300},{y + 300}]"}


def load_sample(name: str) -> list[dict]:
    import json
    p = ROOT / "calibration" / "samples" / f"{name}.flat.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ============================================================
class RealSearchGridTests(unittest.TestCase):
    """Siatka wyników z żywego przebiegu — po zatwierdzeniu frazy „gym".

    Wcześniej plugin nie zatwierdzał frazy i zostawał na liście sugestii
    (`no_tiles` przy każdej frazie), a kafelki wyników mają inne ID niż
    siatka na profilu.
    """

    def test_tiles_found_in_results_grid(self):
        tiles = _post.collect_tiles(load_sample("44_search_results_grid"))
        self.assertEqual(len(tiles), 6)

    def test_kind_is_read_from_tile_description(self):
        """„Autor rolki" vs „Autor zdjęcia" — typ widać PRZED kliknięciem."""
        tiles = _post.collect_tiles(load_sample("44_search_results_grid"))
        kinds = [t["kind"] for t in tiles]
        self.assertEqual(kinds.count("reel"), 4)
        self.assertEqual(kinds.count("post"), 2)

    def test_suggestion_list_has_no_tiles(self):
        """Sama lista sugestii to jeszcze nie wyniki — stąd `no_tiles`."""
        self.assertEqual(_post.collect_tiles(load_sample("43_search_suggestions")), [])


class GridParsingTests(unittest.TestCase):
    def test_tiles_sorted_row_by_row(self):
        flat = [tile("wiersz 2 kolumna 1", 0, 700),
                tile("wiersz 1 kolumna 2", 360, 100),
                tile("wiersz 1 kolumna 1", 0, 100)]
        got = [t["desc"] for t in _post.collect_tiles(flat)]
        self.assertEqual(got, ["wiersz 1 kolumna 1", "wiersz 1 kolumna 2",
                               "wiersz 2 kolumna 1"])

    def test_tiles_found_by_accessibility_description(self):
        """Opis „wiersz/kolumna" jest stabilniejszy niż nazwy widoków —
        działa nawet gdy zmieni się resource-id kafelka."""
        flat = [{"resource-id": "", "class": "android.widget.Button",
                 "content-desc": "Autor zdjęcia: Jan, wiersz: 1, kolumna: 1",
                 "bounds": "[0,100][300,400]"}]
        self.assertEqual(len(_post.collect_tiles(flat)), 1)

    def test_polish_word_containing_row_is_not_a_tile(self):
        """Regresja z żywego przebiegu: „w-p-r-o-w-adzania" zawiera podciąg
        „row", więc systemowe okienko „Przełączanie metody wprowadzania"
        zostało uznane za kafelek siatki i bot w nie tapnął."""
        flat = [{"resource-id": "android:id/title", "class": "android.widget.TextView",
                 "content-desc": "Przełączanie metody wprowadzania",
                 "package": "com.android.settings", "bounds": "[0,100][300,400]"}]
        self.assertEqual(_post.collect_tiles(flat), [])

    def test_foreign_package_is_never_a_tile(self):
        """Druga bariera: kafelek siatki nie należy do innego pakietu."""
        flat = [{"resource-id": "com.instagram.android:id/image_button",
                 "class": "android.widget.Button", "content-desc": "wiersz 1",
                 "package": "com.android.systemui", "bounds": "[0,100][300,400]"}]
        self.assertEqual(_post.collect_tiles(flat), [])

    def test_tile_description_needs_a_number(self):
        """Samo słowo „wiersz" bez numeru to nie opis kafelka siatki."""
        flat = [{"resource-id": "", "class": "android.widget.Button",
                 "content-desc": "wiersz tekstu", "package": "com.instagram.android",
                 "bounds": "[0,100][300,400]"}]
        self.assertEqual(_post.collect_tiles(flat), [])

    def test_reel_detected_by_clips_container(self):
        self.assertTrue(_post.is_reel(
            [{"resource-id": "com.instagram.android:id/clips_video_container"}]))
        self.assertFalse(_post.is_reel(
            [{"resource-id": "com.instagram.android:id/media_group"}]))


class OpenFromGridTests(unittest.TestCase):
    def setUp(self):
        import v2.steps.post.open_from_grid as mod
        self.mod = mod
        self._orig = mod.flatten_hierarchy
        mod.flatten_hierarchy = lambda h: h

    def tearDown(self):
        self.mod.flatten_hierarchy = self._orig

    def _ctx(self, flat, tops, flat_after=None):
        seq = list(tops)
        frames = [flat, flat_after if flat_after is not None else flat]
        box = {"i": 0}
        nav = FakeNavigator()
        nav.identify = lambda: type("S", (), {
            "top": seq.pop(0) if len(seq) > 1 else seq[0], "overlay": None})()
        ctx = FakeCtx(nav)
        # firm_tap — open_from_grid używa dłuższego tapu (nie zwykłego tap)
        ctx.ht = type("H", (), {
            "tap": staticmethod(lambda *a, **k: None),
            "firm_tap": staticmethod(lambda *a, **k: None),
        })()
        ctx.humanizer = type("Hu", (), {"pause": staticmethod(lambda *a, **k: None)})()

        def hierarchy(**k):
            f = frames[min(box["i"], len(frames) - 1)]
            box["i"] += 1
            return f

        ctx.device = type("D", (), {
            "hierarchy": staticmethod(hierarchy),
            "wait_for_stable_ui": staticmethod(lambda **k: True),
            "press_back": staticmethod(lambda: True)})()
        return ctx

    def test_missing_tiles_are_reported_not_guessed(self):
        """Selektory kafelków nie są jeszcze potwierdzone na tej wersji IG —
        krok ma to powiedzieć, a nie tapnąć w losowy punkt ekranu."""
        ctx = self._ctx([], ["search_results"])
        res = self.mod.OpenFromGrid().run(ctx, {"baseline": "search_results"})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "no_tiles")
        self.assertIn("post.open_from_grid.no_tiles", ctx.kinds())

    def test_tap_that_changed_nothing_is_reported(self):
        ctx = self._ctx([tile("wiersz 1 kolumna 1", 0, 100)],
                        ["search_results", "search_results"])
        res = self.mod.OpenFromGrid().run(ctx, {"baseline": "search_results"})
        self.assertEqual(res.payload["reason"], "nothing_opened")

    def test_unknown_screen_after_tap_is_not_success(self):
        """`landed=None` to obcy dialog albo ekran spoza mapy — na żywym
        przebiegu tak właśnie wyglądało „otwarcie posta", którym było
        systemowe okienko wyboru klawiatury."""
        ctx = self._ctx([tile("wiersz 1 kolumna 1", 0, 100)], [None])
        res = self.mod.OpenFromGrid().run(ctx, {"baseline": "search_results"})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "nothing_opened")

    def test_catalog_plural_kinds_match_tile_kinds(self):
        """Katalog mówi „reels"/„posts", kafelek zwraca „reel"/„post" —
        bez ujednolicenia filtr nie trafiłby nigdy i plugin odrzucałby
        całą siatkę."""
        flat = [{"resource-id": "com.instagram.android:id/grid_card_layout_container",
                 "class": "android.widget.LinearLayout",
                 "package": "com.instagram.android",
                 "content-desc": "Autor rolki: ALIONA, wiersz: 0, kolumna: 0",
                 "bounds": "[0,346][537,1064]"}]
        # po tapie: viewer rolki (nie siatka)
        after = [{"resource-id": "com.instagram.android:id/clips_video_container",
                  "package": "com.instagram.android",
                  "content-desc": "", "bounds": "[0,0][1080,2000]"}]
        ctx = self._ctx(flat, ["reels_feed"], flat_after=after)
        res = self.mod.OpenFromGrid().run(
            ctx, {"baseline": "search_results", "content_types": ["reels"]})
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["kind"], "reel")

    def test_false_open_on_profile_grid_is_skipped(self):
        """Tap który zostawia siatkę profilu ≠ sukces (scroll po gridzie)."""
        tiles = [
            {"resource-id": "com.instagram.android:id/image_button",
             "package": "com.instagram.android",
             "content-desc": f"Autor zdjęcia: x, wiersz: 0, kolumna: {i}",
             "bounds": f"[{i*100},400][{i*100+90},500]"}
            for i in range(6)
        ]
        ctx = self._ctx(tiles, ["home_feed"], flat_after=tiles)
        res = self.mod.OpenFromGrid().run(
            ctx, {"baseline": "profile_other", "content_types": ["posts"]})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "nothing_opened")

    def test_opened_reel_is_recognised(self):
        flat = [tile("wiersz 1 kolumna 1", 0, 100),
                {"resource-id": "com.instagram.android:id/clips_video_container"}]
        ctx = self._ctx(flat, ["reels_feed"])
        res = self.mod.OpenFromGrid().run(ctx, {"baseline": "search_results"})
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["kind"], "reel")
        self.assertEqual(ctx.scratch["baseline_screen"], "search_results")


# ============================================================
class FeedViewerLikersEntryTests(unittest.TestCase):
    """Viewer „Posty" z siatki: licznik „258 tys." obok serca, nie tekst polubi*."""

    def test_find_likers_entry_uses_count_right_of_like_button(self):
        flat = [
            {"resource-id": "com.instagram.android:id/row_feed_button_like",
             "clickable": False, "text": "", "content-desc": "Lubię to!",
             "bounds": "[32,2150][95,2271]"},
            {"resource-id": "", "clickable": True, "text": "258 tys.",
             "content-desc": "", "bounds": "[106,2150][258,2271]"},
            {"resource-id": "com.instagram.android:id/row_feed_button_comment",
             "clickable": False, "text": "", "content-desc": "Komentarz",
             "bounds": "[290,2150][353,2271]"},
            {"resource-id": "", "clickable": True, "text": "699",
             "content-desc": "", "bounds": "[364,2150][436,2271]"},
        ]
        likers = _post.find_likers_entry(flat)
        self.assertEqual(likers, ((106 + 258) // 2, (2150 + 2271) // 2))
        comments = _post.find_comments_entry(flat)
        self.assertEqual(comments, ((364 + 436) // 2, (2150 + 2271) // 2))


class OpenEngagementTests(unittest.TestCase):
    """Sukces po TREŚCI, nie po nazwie ekranu.

    Otwarty post ma pole komentarza, więc mapa widzi go jako
    `reel_comments_sheet` — dokładnie tak samo jak listę, która się z niego
    otwiera. Na żywym przebiegu co druga lista przepadała jako
    „nic się nie otworzyło", choć realnie była na ekranie.
    """

    def setUp(self):
        import v2.steps.post.open_engagement as mod
        self.mod = mod
        self._orig = mod.flatten_hierarchy
        mod.flatten_hierarchy = lambda h: h

    def tearDown(self):
        self.mod.flatten_hierarchy = self._orig

    LINK = {"resource-id": "", "class": "android.widget.TextView",
            "package": "com.instagram.android", "clickable": True,
            "text": "Polubione przez ala i innych", "bounds": "[40,900][600,960]"}
    ROW = {"resource-id": "com.instagram.android:id/row_user_primary_name",
           "class": "android.widget.TextView", "package": "com.instagram.android",
           "text": "ktos", "bounds": "[300,500][700,540]"}

    def _ctx(self, after_flat):
        frames = [[self.LINK], after_flat]
        box = {"i": 0}
        nav = FakeNavigator()
        # ta sama nazwa ekranu przed i po — jak na żywym telefonie
        nav.identify = lambda: type("S", (), {"top": "reel_comments_sheet",
                                              "overlay": None})()
        ctx = FakeCtx(nav)
        # expand_sheet po sukcesie — potrzebuje swipe + resolution
        ctx.ht = type("H", (), {
            "tap": staticmethod(lambda *a, **k: None),
            "swipe": staticmethod(lambda *a, **k: None),
        })()
        ctx.account = type("A", (), {
            "device": {"_hardware": {"resolution": (1080, 2340)}},
        })()

        def hierarchy(**k):
            f = frames[min(box["i"], len(frames) - 1)]
            box["i"] += 1
            return f
        ctx.device = type("D", (), {
            "hierarchy": staticmethod(hierarchy),
            "wait_for_stable_ui": staticmethod(lambda **k: True)})()
        return ctx

    def test_list_with_rows_is_success_despite_same_screen_name(self):
        """Wiersze + full_screen dump → ok (expand mock — bez telefonu)."""
        orig = self.mod._sheet.expand_sheet_confirmed
        self.mod._sheet.expand_sheet_confirmed = lambda ctx, max_pulls=3: {
            "confirmed": True, "full_screen": True,
            "rows_before": 1, "rows_after": 1,
            "sheet_top_before": 800, "sheet_top_after": 120,
            "sheet_top_pct": 5.1, "sheet_h": 2220, "screen_h": 2340,
            "pulls_done": 1, "y_from": 830, "y_to": 120,
        }
        try:
            ctx = self._ctx([self.ROW])
            res = self.mod.OpenLikers().run(ctx, {})
        finally:
            self.mod._sheet.expand_sheet_confirmed = orig
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["rows"], 1)
        self.assertTrue(res.payload.get("full_screen"))

    def test_expand_sheet_always_called_on_success(self):
        """Lista lajkujących — expand+full_screen przed return; log potwierdzenia."""
        calls = []
        orig = self.mod._sheet.expand_sheet_confirmed

        def fake_confirmed(ctx, max_pulls=3):
            calls.append(max_pulls)
            return {
                "confirmed": True, "full_screen": True,
                "rows_before": 1, "rows_after": 4,
                "sheet_top_before": 800, "sheet_top_after": 100,
                "sheet_top_pct": 4.3, "sheet_h": 2240, "screen_h": 2340,
                "pulls_done": 2, "y_from": 830, "y_to": 100,
                "full_top": True, "grew": True,
            }

        self.mod._sheet.expand_sheet_confirmed = fake_confirmed
        try:
            ctx = self._ctx([self.ROW])
            res = self.mod.OpenLikers().run(ctx, {})
        finally:
            self.mod._sheet.expand_sheet_confirmed = orig
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.payload.get("expanded"))
        self.assertTrue(res.payload.get("expand_confirmed"))
        self.assertTrue(res.payload.get("full_screen"))
        self.assertGreaterEqual(len(calls), 1)
        self.assertIn("post.open_likers.expanded_confirmed", ctx.kinds())
        self.assertIn("rows_after", res.payload)

    def test_not_expanded_skips_without_ok(self):
        """Half-sheet po expand → skipped not_expanded (bez zielonego OK)."""
        orig = self.mod._sheet.expand_sheet_confirmed
        self.mod._sheet.expand_sheet_confirmed = lambda ctx, max_pulls=3: {
            "confirmed": False, "full_screen": False,
            "rows_before": 1, "rows_after": 5,
            "sheet_top_after": 800, "sheet_top_pct": 34.4,
            "sheet_h": 1540, "screen_h": 2340, "pulls_done": 3,
        }
        try:
            ctx = self._ctx([self.ROW])
            res = self.mod.OpenLikers().run(ctx, {})
        finally:
            self.mod._sheet.expand_sheet_confirmed = orig
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "not_expanded")
        self.assertNotIn("post.open_likers.expanded_confirmed", ctx.kinds())

    def test_no_rows_means_nothing_opened(self):
        ctx = self._ctx([])
        # Fixture domyślnie mówi reel_comments_sheet — to fałszywy sygnał sheetu;
        # przy braku wierszy sprawdzamy przypadek „tap nic nie otworzył".
        ctx.navigator.identify = lambda: type("S", (), {
            "top": "home_feed", "overlay": None})()
        res = self.mod.OpenLikers().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "nothing_opened")
        self.assertNotIn("post.open_likers.expanded_confirmed", ctx.kinds())


class PhraseTests(unittest.TestCase):
    def test_hashtag_gets_hash(self):
        self.assertEqual(_query_for("hashtag", "koty"), "#koty")
        self.assertEqual(_query_for("hashtag", "#koty"), "#koty")

    def test_keyword_and_location_have_no_hash(self):
        self.assertEqual(_query_for("keyword", "#koty"), "koty")
        self.assertEqual(_query_for("location", "Kraków"), "Kraków")

    def test_per_session_limits_how_many_phrases(self):
        import random
        s = {"phrases": {"list": ["a", "b", "c", "d"], "order": "sequential",
                         "per_session": {"min": 2, "max": 2}}}
        self.assertEqual(len(_pick_phrases(s, random.Random(1))), 2)


class TypeQueryHashEscapeTests(unittest.TestCase):
    """adb shell: `#` bez cudzysłowów = komentarz → hashtag nie ląduje w polu."""

    def test_shell_cmd_quotes_hash(self):
        from v2.steps.search.type_query import _shell_input_text_cmd
        cmd = _shell_input_text_cmd("#koty")
        # komentarz: cały #tag w cudzysłowach, nie `input text #koty`
        self.assertEqual(cmd, 'input text "#koty"')
        self.assertNotIn("input text #", cmd)

    def test_query_landed_requires_hash_for_hashtag(self):
        from v2.steps.search.type_query import _query_landed
        # shell zjadł # → samo słowo NIE jest sukcesem
        self.assertFalse(_query_landed("koty", "#koty"))
        self.assertTrue(_query_landed("#koty", "#koty"))
        self.assertTrue(_query_landed("koty", "koty"))

    def test_fold_for_adb_polish_diacritics(self):
        """Live: input text NPE na ó/ł/ć — fold do ASCII przed adb."""
        from v2.steps.search.type_query import _fold_for_adb, _SAFE_QUERY_RE
        self.assertEqual(_fold_for_adb("kobiecość"), "kobiecosc")
        self.assertEqual(_fold_for_adb("#bólpleców"), "#bolplecow")
        self.assertEqual(_fold_for_adb("#postawaciała"), "#postawaciala")
        # po foldzie bezpieczne dla jednego input text
        self.assertTrue(_SAFE_QUERY_RE.match(_fold_for_adb("kobiecość")))
        self.assertTrue(_SAFE_QUERY_RE.match(_fold_for_adb("#bólpleców")))

    def test_query_landed_accepts_folded_field(self):
        """W polu ląduje ASCII fold, want ma ogonki — nadal sukces."""
        from v2.steps.search.type_query import _query_landed
        self.assertTrue(_query_landed("kobiecosc", "kobiecość"))
        self.assertTrue(_query_landed("#bolplecow", "#bólpleców"))
        self.assertTrue(_query_landed("#postawaciala", "#postawaciała"))
        self.assertFalse(_query_landed("Szukaj", "kobiecość"))
        self.assertFalse(_query_landed("", "kobiecość"))

    def test_type_query_chars_skips_unicode_codepoint(self):
        """Obrona: raw Unicode nie idzie do shell (NPE na urządzeniu)."""
        from v2.steps.search import type_query as mod
        shells = []

        class Dev:
            def shell(self, cmd):
                shells.append(cmd)

        ctx = type("Ctx", (), {})()
        ctx.device = Dev()
        # celowo nie-foldnięte — znak ó musi być pominięty
        mod._type_query_chars(ctx, "bó")
        self.assertEqual(shells, ['input text "b"'])
        self.assertTrue(all("ó" not in s and "\u00f3" not in s for s in shells))

    def test_bulk_query_with_spaces_uses_percent_s(self):
        """Keyword ze spacją → jeden input text z %s, nie char-by-char."""
        from v2.steps.search import type_query as mod
        shells = []

        class Dev:
            def shell(self, cmd):
                shells.append(cmd)

        ctx = type("Ctx", (), {})()
        ctx.device = Dev()
        mod._type_query_chars(ctx, "polish beauty")
        self.assertEqual(shells, ['input text "polish%sbeauty"'])
        # nutrition coaching — to samo
        shells.clear()
        mod._type_query_chars(ctx, "nutrition coaching")
        self.assertEqual(shells, ['input text "nutrition%scoaching"'])
        self.assertTrue(mod._BULK_QUERY_RE.match("polish beauty"))
        self.assertFalse(mod._SAFE_QUERY_RE.match("polish beauty"))

    def test_query_landed_keyword_with_space(self):
        from v2.steps.search.type_query import _query_landed
        self.assertTrue(_query_landed("polish beauty", "polish beauty"))
        self.assertTrue(_query_landed("nutrition coaching", "nutrition coaching"))
        self.assertFalse(_query_landed("polish", "polish beauty"))


class TypeQueryImeDismissTests(unittest.TestCase):
    """Dialog „Przełączanie metody wprowadzania" — bez zbędnego BACK na system_ui."""

    def test_ime_marker_detected(self):
        from v2.steps.search.type_query import _ime_picker_marker
        # Tytuł dialogu — wysoko na ekranie
        flat = [{"text": "Przełączanie metody wprowadzania", "content-desc": "",
                 "bounds": "[100,400][900,500]", "clickable": False}]
        self.assertEqual(_ime_picker_marker(flat, 2340),
                         "przełączanie metody wprowadzania")

    def test_ime_marker_ignores_keyboard_globe_button(self):
        """Przycisk globusa ma ten sam opis, ale siedzi nisko — to NIE dialog."""
        from v2.steps.search.type_query import _ime_picker_marker
        flat = [{"text": "", "content-desc": "Przełączanie metody wprowadzania",
                 "bounds": "[50,2100][150,2250]", "clickable": True}]
        self.assertIsNone(_ime_picker_marker(flat, 2340))

    def test_dismiss_skips_bare_system_ui(self):
        """Goły overlay=system_ui BEZ markera IME nie może robić press_back —
        to gubiło focus pola (live: pole='Szukaj')."""
        from v2.steps.search import type_query as mod

        backs = []
        taps = []

        class Dev:
            def hierarchy(self, force=False):
                return {"properties": {}, "children": []}

            def press_back(self):
                backs.append(1)

        class Nav:
            def identify(self):
                return type("S", (), {
                    "top": "search_input_active", "overlay": "system_ui",
                    "package": "com.android.systemui"})()

        class Ht:
            def tap(self, x, y, pause_kind=None):
                taps.append((x, y))

        class Hum:
            def pause(self, *a, **k):
                return 0.0

        ctx = type("Ctx", (), {})()
        ctx.device = Dev()
        ctx.navigator = Nav()
        ctx.ht = Ht()
        ctx.humanizer = Hum()
        ctx.logs = []
        ctx.log = lambda kind, **p: ctx.logs.append((kind, p))

        # flatten pusty → brak markera → zero BACK
        with mock.patch(
                "v2.steps.search.type_query.flatten_hierarchy", return_value=[]):
            did = mod._dismiss_ime_picker(ctx)
        self.assertFalse(did)
        self.assertEqual(backs, [])

    def test_dismiss_backs_only_when_picker_visible(self):
        from v2.steps.search import type_query as mod

        backs = []
        calls = {"n": 0}

        class Dev:
            def hierarchy(self, force=False):
                return {}

            def press_back(self):
                backs.append(1)

        class Nav:
            def identify(self):
                return type("S", (), {
                    "top": "search_input_active", "overlay": "keyboard",
                    "package": "com.android.systemui"})()

        class Ht:
            def tap(self, *a, **k):
                pass

        class Hum:
            def pause(self, *a, **k):
                return 0.0

        ctx = type("Ctx", (), {})()
        ctx.device = Dev()
        ctx.navigator = Nav()
        ctx.ht = Ht()
        ctx.humanizer = Hum()
        ctx.logs = []
        ctx.log = lambda kind, **p: ctx.logs.append((kind, p))

        # pierwsza iteracja: picker; druga: już nie
        flats = [
            [{"text": "Przełączanie metody wprowadzania", "content-desc": "",
              "class": "TextView", "clickable": False, "bounds": "[0,0][10,10]"}],
            [],
        ]

        def fake_flat(h):
            i = min(calls["n"], len(flats) - 1)
            calls["n"] += 1
            return flats[i]

        with mock.patch(
                "v2.steps.search.type_query.flatten_hierarchy",
                side_effect=fake_flat), \
             mock.patch("v2.steps.search.type_query.time.sleep"):
            did = mod._dismiss_ime_picker(ctx, max_attempts=3)
        self.assertTrue(did)
        self.assertEqual(len(backs), 1)  # jeden BACK, nie seria na system_ui

    def test_soft_success_username_on_list(self):
        from v2.steps.search import type_query as mod

        class Dev:
            def hierarchy(self, force=False):
                return {}

        ctx = type("Ctx", (), {})()
        ctx.device = Dev()
        flat = [{
            "resource-id": "com.instagram.android:id/row_search_user_username",
            "text": "mateuszbartex",
        }]
        with mock.patch(
                "v2.steps.search.type_query.flatten_hierarchy", return_value=flat):
            self.assertTrue(mod._results_show_username(ctx, "@mateuszbartex"))
            self.assertFalse(mod._results_show_username(ctx, "@other"))

    def test_run_fails_fast_off_search_without_recover(self):
        """Poza search + ensure fail → fail z off_search, bez 3× probe."""
        from v2.steps.search.type_query import SearchTypeQuery
        from v2.runner.step import StepResult

        shells = []
        logs = []

        class Dev:
            def hierarchy(self, force=False):
                return {}

            def shell(self, cmd):
                shells.append(cmd)

            def press_back(self):
                pass

            def wait_for_stable_ui(self, max_wait_s=0):
                pass

        class Nav:
            def identify(self):
                return type("S", (), {
                    "top": "home_feed", "overlay": None,
                    "package": "com.instagram.androjb"})()

            def ensure(self, target, max_steps=4):
                return False

        class Sm:
            def anchor_center(self, name):
                return (100, 100)

        class Ht:
            def tap(self, *a, **k):
                pass

        class Hum:
            def pause(self, *a, **k):
                return 0.0

        ctx = type("Ctx", (), {})()
        ctx.device = Dev()
        ctx.navigator = Nav()
        ctx.screen_map = Sm()
        ctx.ht = Ht()
        ctx.humanizer = Hum()
        ctx.log = lambda kind, **p: logs.append((kind, p))

        with mock.patch("v2.steps.search.type_query.flatten_hierarchy",
                        return_value=[]), \
             mock.patch("v2.steps.search.type_query.time.sleep"):
            r = SearchTypeQuery().run(ctx, {"query": "kobiecość"})
        self.assertEqual(r.status, "failed")
        self.assertTrue((r.payload or {}).get("off_search"))
        # nie spamuj input text na home_feed
        self.assertEqual(shells, [])
        self.assertTrue(any(k == "search.type_query.fold_diacritics" for k, _ in logs))
        self.assertTrue(any(k == "search.type_query.recover_search" for k, _ in logs))


class ContentSearchTests(unittest.TestCase):
    def setUp(self):
        from v2.plugins.impl import content_search as mod
        self.mod = mod
        self.recorder = []
        self._orig = mod.steps.get

    def tearDown(self):
        self.mod.steps.get = self._orig

    BASE = {"enabled": True,
            "source": "hashtag",
            "phrases": {"list": ["koty"], "order": "sequential",
                        "per_session": 1},
            "per_phrase": {"items": 1, "grid_pool_size": 4},
            "gate": {"min_likes": 0, "min_comments": 0, "on_unknown": "pass"},
            "like_content": {"enabled": False},
            "likers": {"harvest": {"enabled": False}, "hot_interact": {"enabled": False}},
            "comments": {"harvest": {"enabled": False}, "hot_interact": {"enabled": False}}}

    def _run(self, spec, s=None, budget=None):
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = FakeCtx()
        res = ContentSearch().run(ctx, s or self.BASE, budget or Budget(max_items=2))
        return ctx, res, [n for n, _ in self.recorder]

    def _spec(self, **over):
        spec = {"search.open": [],
                # `submitted` — plugin przerywa frazę, gdy siatka się nie pojawiła
                "search.type_query": [StepResult.ok(submitted=True)] * 4,
                "post.open_from_grid": [StepResult.ok(kind="post", landed="x")],
                "reels.watch": [StepResult.ok()],
                "post.like": [], "reels.like": [],
                "post.open_likers": [StepResult.ok(baseline="x")],
                "post.open_comments": [StepResult.ok(baseline="x")],
                "list.harvest_usernames": [StepResult.ok(saved=7)],
                "reels.harvest_likers": [StepResult.ok(saved=5)],
                "reels.harvest_commenters": [StepResult.ok(saved=4)]}
        spec.update(over)
        return spec

    def test_hashtag_is_typed_with_hash(self):
        self._run(self._spec())
        self.assertEqual(dict(self.recorder)["search.type_query"]["query"], "#koty")

    def test_query_is_submitted_and_field_cleared(self):
        """Bez zatwierdzenia zostajemy na sugestiach (`no_tiles`), a bez
        czyszczenia druga fraza dokleja się do pierwszej."""
        self._run(self._spec())
        params = dict(self.recorder)["search.type_query"]
        self.assertTrue(params["submit"])
        self.assertTrue(params["clear_first"])

    def test_phrase_is_abandoned_when_results_grid_never_appears(self):
        spec = self._spec(**{"search.type_query": [StepResult.ok(submitted=False)]})
        ctx, res, names = self._run(spec)
        self.assertIn("content_search.no_results_grid", ctx.kinds())
        self.assertNotIn("post.open_from_grid", names)

    def test_keyword_mode_types_plain_phrase(self):
        s = {**self.BASE, "source": "keyword"}
        self._run(self._spec(), s)
        self.assertEqual(dict(self.recorder)["search.type_query"]["query"], "koty")

    def test_no_tiles_ends_the_phrase_cleanly(self):
        spec = self._spec(**{"post.open_from_grid": [StepResult.skipped("no_tiles")]})
        ctx, res, names = self._run(spec)
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.stats["no_tiles"], 1)
        self.assertEqual(res.stats["items"], 0)

    def test_post_uses_generic_harvest_reel_uses_sheet_steps(self):
        """Post i rolka mają inne ekrany zaangażowania — plugin nie może
        wołać kroków rolkowych na otwartym poście."""
        s = {**self.BASE,
             "likers": {"harvest": {"enabled": True, "collect": 5},
                        "hot_interact": {"enabled": False}}}
        ctx, res, names = self._run(self._spec(), s)
        self.assertIn("list.harvest_usernames", names)
        self.assertNotIn("reels.harvest_likers", names)

        self.recorder.clear()
        spec = self._spec(**{
            "post.open_from_grid": [StepResult.ok(kind="reel", landed="reels_feed")]})
        ctx, res, names = self._run(spec, s)
        self.assertIn("reels.harvest_likers", names)
        self.assertNotIn("list.harvest_usernames", names)

    def test_ai_is_not_called_by_default(self):
        """Fraza sama celuje w niszę — płacenie za model przy każdej treści
        byłoby marnotrawstwem."""
        ctx, res, names = self._run(self._spec())
        self.assertNotIn("reels.analyze_frames_ai", names)

    def test_finally_clears_baseline_screen(self):
        """Wyciek baseline_screen=search_results psuje kolejne zadanie w kolejce."""
        ctx, res, names = self._run(self._spec())
        self.assertNotIn("baseline_screen", ctx.scratch)
        self.assertNotIn("content_search_active", ctx.scratch)

    def test_empty_phrase_list_ends_immediately(self):
        s = {**self.BASE, "phrases": {"list": []}}
        ctx, res, names = self._run(self._spec(), s)
        self.assertEqual(res.stopped_by, "empty")
        self.assertEqual(names, [])

    def test_discover_collect_after_main_harvest(self):
        """Flaga discover + mode=collect: najpierw siatka główna, potem Tags."""
        s = {
            **self.BASE,
            "discover_tag_variants": {
                "enabled": True,
                "mode": "collect",
                "max_variants": 5,
                "max_scrolls": 0,
                "write_to_account": False,
            },
        }
        spec = self._spec(**{
            "search.switch_results_tab": [
                StepResult.ok(tab="tags"),
            ],
            "search.collect_tag_variants": [
                StepResult.ok(hashtags=["#fitgirl", "#gym"], count=2,
                              persisted=False),
            ],
        })
        ctx, res, names = self._run(spec, s)
        self.assertIn("search.switch_results_tab", names)
        self.assertIn("search.collect_tag_variants", names)
        self.assertIn("post.open_from_grid", names)
        # kolejność: open → type → open_from_grid (główne) → tags → collect
        i_grid = names.index("post.open_from_grid")
        i_tags = names.index("search.switch_results_tab")
        i_collect = names.index("search.collect_tag_variants")
        self.assertLess(i_grid, i_tags)
        self.assertLess(i_tags, i_collect)
        tabs = [p.get("tab") for n, p in self.recorder
                if n == "search.switch_results_tab"]
        # mode=collect: bez powrotu na Dla Ciebie (harvest już był)
        self.assertEqual(tabs, ["tags"])
        self.assertEqual(res.stats.get("tag_variants_collected"), 2)

    def test_discover_grid_harvests_main_then_variant(self):
        """Legacy mode=grid → normalize: collect + gałąź B (1 znacznik)."""
        s = {
            **self.BASE,
            "phrases": {"list": ["fitness"], "order": "sequential",
                        "per_session": 1},
            "per_phrase": {"items": 1, "grid_pool_size": 4},
            "discover_tag_variants": {
                "enabled": True,
                "mode": "grid",
                "max_variants": 5,
                "max_scrolls": 0,
                "write_to_account": False,
            },
            # normalize włączy budget z max_phrases=1 + live_collect
        }
        spec = self._spec(**{
            "search.type_query": [StepResult.ok(submitted=True)] * 4,
            "search.switch_results_tab": [StepResult.ok(tab="tags")],
            "search.collect_tag_variants": [
                StepResult.ok(hashtags=["#fitgirl", "#gym"], count=2,
                              persisted=False),
            ],
            "post.open_from_grid": [
                StepResult.ok(kind="post", landed="x"),
                StepResult.ok(kind="post", landed="x"),
            ],
        })
        ctx, res, names = self._run(spec, s)
        queries = [p.get("query") for n, p in self.recorder
                   if n == "search.type_query"]
        # 1) fraza główna, 2) wariant z Tags
        self.assertTrue(any("fitness" in str(q).lower() for q in queries),
                        queries)
        self.assertTrue(any(q in ("#fitgirl", "#gym") for q in queries),
                        queries)
        i_grid1 = names.index("post.open_from_grid")
        i_tags = names.index("search.switch_results_tab")
        i_collect = names.index("search.collect_tag_variants")
        # drugi open_from_grid po collect (harvest wariantu)
        i_grid2 = names.index("post.open_from_grid", i_collect + 1)
        self.assertLess(i_grid1, i_tags)
        self.assertLess(i_tags, i_collect)
        self.assertLess(i_collect, i_grid2)
        self.assertEqual(names.count("post.open_from_grid"), 2)
        self.assertEqual(res.stats.get("tag_variants_collected"), 2)

    def test_tag_variants_budget_branch_b_from_map(self):
        """Gałąź B: harvest z mapy (discover ON, source=from_map → bez Tags tab)."""
        s = {
            **self.BASE,
            "phrases": {"list": ["koty"], "order": "sequential",
                        "per_session": 1},
            "per_phrase": {"items": 1, "grid_pool_size": 4},
            # master switch Znaczniki — from_map pomija collect w A
            "discover_tag_variants": {"enabled": True, "mode": "collect"},
            "tag_variants_budget": {
                "enabled": True,
                "max_phrases": 1,
                "tiles_per_phrase": 1,
                "scrolls": 0,
                "source": "from_map",
            },
        }
        # phrases.order sequential — pick B deterministyczny (kolejność mapy)
        s["phrases"]["order"] = "sequential"

        spec = self._spec(**{
            # 1 fraza A + 1 znacznik B → 2× type_query + 2× open
            "search.type_query": [StepResult.ok(submitted=True)] * 4,
            "post.open_from_grid": [
                StepResult.ok(kind="post", landed="x"),
                StepResult.ok(kind="post", landed="x"),
            ],
        })
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = FakeCtx()
        ctx.account = type("A", (), {
            "account_id": "demo",
            "niche": {"tag_variants": {"#koty": ["#kotki", "#kotypl"]}},
        })()
        res = ContentSearch().run(ctx, s, Budget(max_items=10))
        names = [n for n, _ in self.recorder]
        queries = [p.get("query") for n, p in self.recorder
                   if n == "search.type_query"]
        self.assertIn("#koty", queries)
        # gałąź B szuka wariantu z mapy
        self.assertTrue(any(q in ("#kotki", "#kotypl") for q in queries), queries)
        self.assertEqual(res.stats.get("tag_variants_phrases"), 1)
        self.assertEqual(names.count("post.open_from_grid"), 2)
        # gałąź B dopiero po harvestcie gałęzi A (nie przed / nie w środku)
        i_a_query = next(i for i, (n, p) in enumerate(self.recorder)
                         if n == "search.type_query"
                         and "#koty" in str(p.get("query") or ""))
        i_a_grid = next(i for i, (n, _) in enumerate(self.recorder)
                        if n == "post.open_from_grid")
        i_b_query = next(i for i, (n, p) in enumerate(self.recorder)
                         if n == "search.type_query"
                         and str(p.get("query") or "") in ("#kotki", "#kotypl"))
        self.assertLess(i_a_query, i_a_grid)
        self.assertLess(i_a_grid, i_b_query)

    def test_back_to_climbs_reels_feed_to_search_results(self):
        """Regresja: BACK z rolki # zatrzymywał się na powiązanej rolce."""
        from v2.plugins.impl import content_search as mod
        tops = ["reels_feed", "reels_feed", "reel_comments_sheet",
                "reels_feed", "search_results"]
        idx = {"i": 0}

        class Nav:
            def identify(self):
                t = tops[min(idx["i"], len(tops) - 1)]
                return type("S", (), {"top": t, "overlay": None})()

        class Dev:
            def press_back(self):
                idx["i"] += 1

        ctx = FakeCtx(Nav())
        ctx.device = Dev()
        self.assertTrue(mod._back_to(ctx, "search_results"))
        self.assertGreaterEqual(idx["i"], 1)
        self.assertEqual(ctx.navigator.identify().top, "search_results")

    def test_reel_watch_disables_frames_and_sets_log_prefix(self):
        """content_search: frames:0 musi iść z use_frames=False (inaczej
        max(1,0)=1 → „Klatki” + prefix reels_niche)."""
        spec = self._spec(**{
            "post.open_from_grid": [
                StepResult.ok(kind="reel", landed="reel_comments_sheet")],
            "reels.watch": [StepResult.skipped("gate_failed")],
        })
        self._run(spec)
        params = dict(self.recorder)["reels.watch"]
        self.assertEqual(params.get("frames"), 0)
        self.assertFalse(params.get("use_frames"))
        self.assertEqual(params.get("log_prefix"), "content_search")

    def test_ai_on_passes_use_frames_and_caption_flags(self):
        """Merge settings: AI włączone → watch dostaje use_frames/read_caption."""
        s = {**self.BASE,
             "ai": {
                 "enabled": True, "use_frames": False, "read_caption": True,
                 "frames": 3, "frame_interval_s": 0.4,
                 "mode": "exclude_only", "min_niche_score": 0,
                 "allow_verdict": ["match", "partial", "off"],
             },
             "like_content": {"enabled": True, "chance": {"mode": "p", "value": 1.0}}}
        spec = self._spec(**{
            "post.open_from_grid": [
                StepResult.ok(kind="reel", landed="reels_feed")],
            "reels.watch": [StepResult.ok()],
            "reels.analyze_frames_ai": ScratchStep(
                {"reel_ai": {"niche_score": 80, "verdict": "match"}},
                self.recorder, "reels.analyze_frames_ai"),
            "reels.like": [StepResult.ok()],
        })
        self._run(spec, s)
        watch = [p for n, p in self.recorder if n == "reels.watch"][0]
        self.assertFalse(watch.get("use_frames"))
        self.assertTrue(watch.get("read_caption"))
        self.assertEqual(watch.get("frames"), 0)
        names = [n for n, _ in self.recorder]
        self.assertIn("reels.analyze_frames_ai", names)
        self.assertIn("reels.like", names)

    def test_ai_frames_only_passes_use_frames_true(self):
        """Vision ON, opis OFF → watch: use_frames=True, read_caption=False."""
        s = {**self.BASE,
             "ai": {
                 "enabled": True, "use_frames": True, "read_caption": False,
                 "frames": 4, "frame_interval_s": 0.5,
                 "mode": "exclude_only",
             },
             "like_content": {"enabled": False}}
        spec = self._spec(**{
            "post.open_from_grid": [
                StepResult.ok(kind="reel", landed="reels_feed")],
            "reels.watch": [StepResult.ok()],
            "reels.analyze_frames_ai": ScratchStep(
                {"reel_ai": {"niche_score": 70, "verdict": "match"}},
                self.recorder, "reels.analyze_frames_ai"),
        })
        self._run(spec, s)
        watch = [p for n, p in self.recorder if n == "reels.watch"][0]
        self.assertTrue(watch.get("use_frames"))
        self.assertEqual(watch.get("frames"), 4)
        self.assertFalse(watch.get("read_caption"))

    def test_post_never_calls_analyze_even_if_ai_enabled(self):
        """AI w settings nie dotyczy postów z siatki."""
        s = {**self.BASE,
             "ai": {"enabled": True, "use_frames": True, "read_caption": True}}
        self._run(self._spec(), s)
        names = [n for n, _ in self.recorder]
        self.assertNotIn("reels.analyze_frames_ai", names)
        self.assertNotIn("reels.watch", names)

    def test_gate_skip_then_next_tile_without_new_search(self):
        """Po odrzucie bramki: BACK na wyniki i kolejny kafelek — bez search.open."""
        from v2.plugins.impl import content_search as mod

        s = {**self.BASE, "per_phrase": {"items": 2, "grid_pool_size": 4}}
        spec = self._spec(**{
            "post.open_from_grid": [
                StepResult.ok(kind="reel", landed="reel_comments_sheet"),
                StepResult.ok(kind="reel", landed="reel_comments_sheet"),
            ],
            "reels.watch": [
                StepResult.skipped("gate_failed"),
                StepResult.ok(),
            ],
        })
        # FakeNavigator domyślnie zostaje na reels_feed — tu symulujemy powrót na #
        class Nav:
            def __init__(self):
                self.top = "reel_comments_sheet"

            def identify(self):
                return type("S", (), {"top": self.top, "overlay": None})()

            def ensure(self, target, max_steps=6):
                self.top = target
                return True

        class Dev:
            def __init__(self, nav):
                self.nav = nav

            def press_back(self):
                # BACK z viewera → siatka wyników
                self.nav.top = "search_results"

            def hierarchy(self, force=False):
                return {}

            def wait_for_stable_ui(self, **kw):
                return True

        self.mod.steps.get = fake_registry(spec, self.recorder)
        nav = Nav()
        ctx = FakeCtx(nav)
        ctx.device = Dev(nav)
        ContentSearch().run(ctx, s, Budget(max_items=5))
        names = [n for n, _ in self.recorder]
        self.assertEqual(names.count("search.open"), 1)
        self.assertEqual(names.count("post.open_from_grid"), 2)
        self.assertNotIn("content_search.results_lost", ctx.kinds())

    def _nav_back_to_results(self):
        """Navigator: viewer reels_feed; BACK z treści → search_results.

        Sheet (likers/comments) → BACK wraca na reels_feed (nie od razu RESULTS),
        żeby scrolle nie ginęły po harvest/hot.
        """
        class Nav:
            def __init__(self):
                self.top = "reels_feed"

            def identify(self):
                return type("S", (), {"top": self.top, "overlay": None,
                                      "package": None})()

            def ensure(self, target, max_steps=6):
                self.top = target
                return True

        class Dev:
            def __init__(self, nav):
                self.nav = nav
                self.backs = 0
                self.back_from = []  # top przed każdym BACK

            def press_back(self):
                self.backs += 1
                self.back_from.append(self.nav.top)
                # Sheet → treść; treść → wyniki (jak na telefonie)
                if self.nav.top in ("reel_likers_list", "reel_comments_sheet",
                                    "profile_other", "profile_self",
                                    "share_sheet", "story_viewer",
                                    "profile_options_sheet"):
                    self.nav.top = "reels_feed"
                else:
                    self.nav.top = "search_results"

            def hierarchy(self, force=False):
                return {}

            def wait_for_stable_ui(self, **kw):
                return True

        nav = Nav()
        return nav, Dev(nav)

    def test_scrolls_zero_one_item_and_back(self):
        """scrolls=0 → 1× _one_item + BACK do RESULTS (bez swipe)."""
        s = {**self.BASE,
             "per_phrase": {"items": 1, "grid_pool_size": 4,
                            "scrolls_in_viewer": 0}}
        spec = self._spec(**{
            "post.open_from_grid": [
                StepResult.ok(kind="reel", landed="reels_feed")],
            "reels.watch": [StepResult.ok()],
            "reels.swipe_next": [StepResult.ok()],
        })
        nav, dev = self._nav_back_to_results()
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = FakeCtx(nav)
        ctx.device = dev
        res = ContentSearch().run(ctx, s, Budget(max_items=5))
        names = [n for n, _ in self.recorder]
        self.assertEqual(names.count("post.open_from_grid"), 1)
        self.assertEqual(names.count("reels.watch"), 1)
        self.assertEqual(names.count("reels.swipe_next"), 0)
        self.assertEqual(res.stats["items"], 1)
        self.assertGreaterEqual(dev.backs, 1)  # BACK do RESULTS przed ensure(home)
        self.assertNotIn("content_search.results_lost", ctx.kinds())

    def test_scrolls_two_three_items_two_swipes(self):
        """scrolls=2 → 3× item + 2× swipe + JEDEN BACK do RESULTS na końcu."""
        s = {**self.BASE,
             "per_phrase": {"items": 1, "grid_pool_size": 4,
                            "scrolls_in_viewer": 2}}
        spec = self._spec(**{
            "post.open_from_grid": [
                StepResult.ok(kind="reel", landed="reels_feed")],
            "reels.watch": [StepResult.ok()] * 3,
            "reels.swipe_next": [StepResult.ok()] * 2,
        })
        nav, dev = self._nav_back_to_results()
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = FakeCtx(nav)
        ctx.device = dev
        res = ContentSearch().run(ctx, s, Budget(max_items=10))
        names = [n for n, _ in self.recorder]
        self.assertEqual(names.count("post.open_from_grid"), 1)
        self.assertEqual(names.count("reels.watch"), 3)
        self.assertEqual(names.count("reels.swipe_next"), 2)
        self.assertEqual(res.stats["items"], 3)
        self.assertEqual(res.stats["reels"], 3)
        self.assertIn("content_search.viewer_scroll", ctx.kinds())
        self.assertNotIn("content_search.results_lost", ctx.kinds())
        self.assertNotIn("content_search.viewer_lost", ctx.kinds())
        # Między scrollami nie wolno BACK do RESULTS — tylko final z viewera
        mid_results = [t for t in dev.back_from if t == "search_results"]
        self.assertEqual(mid_results, [],
                         "BACK z RESULTS w trakcie pętli = za wczesny powrot")
        # Dokładnie jeden BACK z treści (reels_feed) do RESULTS
        from_viewer = [t for t in dev.back_from if t == "reels_feed"]
        self.assertEqual(len(from_viewer), 1)

    def test_false_comments_sheet_keeps_viewer_for_scrolls(self):
        """Live: open ląduje jako reel_comments_sheet (false positive) —
        nie BACK do RESULTS przed swipe; scrolls=2 → 3× watch + 2× swipe."""
        s = {**self.BASE,
             "per_phrase": {"items": 1, "grid_pool_size": 4,
                            "scrolls_in_viewer": 2}}
        spec = self._spec(**{
            "post.open_from_grid": [
                StepResult.ok(kind="reel", landed="reel_comments_sheet")],
            "reels.watch": [StepResult.ok()] * 3,
            "reels.swipe_next": [StepResult.ok()] * 2,
        })
        nav, dev = self._nav_back_to_results()
        nav.top = "reel_comments_sheet"  # jak po open_from_grid
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = FakeCtx(nav)
        ctx.device = dev
        res = ContentSearch().run(ctx, s, Budget(max_items=10))
        names = [n for n, _ in self.recorder]
        self.assertEqual(names.count("reels.watch"), 3)
        self.assertEqual(names.count("reels.swipe_next"), 2)
        self.assertEqual(res.stats["items"], 3)
        self.assertNotIn("content_search.viewer_lost", ctx.kinds())
        # Między scrollami nie wolno lądować na RESULTS (final BACK OK)
        self.assertEqual(
            [t for t in dev.back_from if t == "search_results"], [])

    def test_harvest_dismisses_to_content_not_results(self):
        """Po harvest: BACK z sheetu do reels_feed, potem swipe; RESULTS na końcu."""
        s = {**self.BASE,
             "per_phrase": {"items": 1, "grid_pool_size": 4,
                            "scrolls_in_viewer": 2},
             "likers": {"harvest": {"enabled": True, "collect": 5},
                        "hot_interact": {"enabled": False}}}
        spec = self._spec(**{
            "post.open_from_grid": [
                StepResult.ok(kind="reel", landed="reels_feed")],
            "reels.watch": [StepResult.ok()] * 3,
            "reels.swipe_next": [StepResult.ok()] * 2,
            "reels.harvest_likers": [
                StepResult.ok(saved=3, dupes_skipped=0)] * 3,
        })
        nav, dev = self._nav_back_to_results()
        inner = fake_registry(spec, self.recorder)

        def get(name):
            factory = inner(name)
            if name != "reels.harvest_likers":
                return factory
            step = factory()
            orig = step.run

            def run(ctx, params):
                # Symuluj otwarty sheet lajkujących
                ctx.navigator.top = "reel_likers_list"
                return orig(ctx, params)

            step.run = run
            return lambda: step

        self.mod.steps.get = get
        ctx = FakeCtx(nav)
        ctx.device = dev
        res = ContentSearch().run(ctx, s, Budget(max_items=10))
        names = [n for n, _ in self.recorder]
        self.assertEqual(names.count("reels.watch"), 3)
        self.assertEqual(names.count("reels.swipe_next"), 2)
        self.assertEqual(res.stats["items"], 3)
        self.assertNotIn("content_search.viewer_lost", ctx.kinds())
        # Sheet → reels_feed (między itemami); RESULTS tylko z reels_feed na końcu
        self.assertIn("reel_likers_list", dev.back_from)
        from_viewer = [t for t in dev.back_from if t == "reels_feed"]
        self.assertEqual(len(from_viewer), 1)

    def test_scroll_aborts_when_content_lost_then_back(self):
        """Po swipe poza treścią — stop pętli i BACK (bez kolejnych watch)."""
        s = {**self.BASE,
             "per_phrase": {"items": 1, "grid_pool_size": 4,
                            "scrolls_in_viewer": 2}}
        spec = self._spec(**{
            "post.open_from_grid": [
                StepResult.ok(kind="reel", landed="reels_feed")],
            "reels.watch": [StepResult.ok()] * 3,
            "reels.swipe_next": [StepResult.ok()] * 2,
        })
        nav, dev = self._nav_back_to_results()
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = FakeCtx(nav)
        ctx.device = dev

        def fake_swipe(c, kind):
            # Symuluj utratę viewera zaraz po scrollu
            c.navigator.top = "home_feed"
            return True

        with mock.patch.object(self.mod, "_swipe_in_viewer", fake_swipe):
            res = ContentSearch().run(ctx, s, Budget(max_items=10))
        names = [n for n, _ in self.recorder]
        self.assertEqual(names.count("reels.watch"), 1)
        self.assertEqual(names.count("reels.swipe_next"), 0)  # mock, nie krok
        self.assertEqual(res.stats["items"], 1)
        self.assertIn("content_search.viewer_lost", ctx.kinds())
        self.assertGreaterEqual(dev.backs, 1)


class ContentSearchStepcallRoutingTests(unittest.TestCase):
    """Live 13:03: ensure(reels_feed) zamykał # i otwierał clips_tab."""

    def test_does_not_ensure_clips_when_viewer_misidentified_as_sheet(self):
        from v2.plugins import stepcall
        from v2.runner.step import StepResult

        ensure_calls = []
        ran = []

        class Nav:
            def identify(self):
                return type("S", (), {
                    "top": "reel_comments_sheet", "overlay": None})()

            def ensure(self, target, max_steps=6, **kw):
                ensure_calls.append(target)
                return True

        class FakeStep:
            requires_screen = "reels_feed"

            def run(self, ctx, params):
                ran.append(params)
                return StepResult.ok()

        class Reg:
            def get(self, name):
                return FakeStep

        ctx = FakeCtx(Nav())
        ctx.scratch["baseline_screen"] = "search_results"
        res = stepcall.call(Reg(), ctx, "reels.watch", {"frames": 0},
                            source="content_search")
        self.assertEqual(res.status, "ok")
        self.assertEqual(ensure_calls, [])
        self.assertEqual(len(ran), 1)

    def test_still_ensures_clips_for_reels_niche(self):
        from v2.plugins import stepcall
        from v2.runner.step import StepResult

        ensure_calls = []

        class Nav:
            def identify(self):
                return type("S", (), {"top": "home_feed", "overlay": None})()

            def ensure(self, target, max_steps=6, **kw):
                ensure_calls.append(target)
                return True

        class FakeStep:
            requires_screen = "reels_feed"

            def run(self, ctx, params):
                return StepResult.ok()

        class Reg:
            def get(self, name):
                return FakeStep

        ctx = FakeCtx(Nav())
        res = stepcall.call(Reg(), ctx, "reels.watch", {}, source="reels_niche")
        self.assertEqual(res.status, "ok")
        self.assertEqual(ensure_calls, ["reels_feed"])


# ============================================================
class TargetScraperTests(unittest.TestCase):
    def setUp(self):
        from v2.plugins.impl import target_scraper as mod
        self.mod = mod
        self.recorder = []
        self.reels_tab_calls = []
        self._orig = mod.steps.get
        # expand potwierdzony — no-op (bez telefonu)
        self._orig_expand = mod._sheet.expand_sheet
        self._orig_confirmed = mod._sheet.expand_sheet_confirmed
        self._orig_looks = mod._sheet.sheet_looks_expanded
        self._orig_dump = mod._sheet.dump_sheet_geometry
        self._orig_reels_tab = mod._ensure_profile_reels_tab
        mod._sheet.expand_sheet = lambda ctx, pulls=1: {
            "y_from": 0, "y_to": 0, "pulls": pulls, "x": 0}
        mod._sheet.expand_sheet_confirmed = lambda ctx, max_pulls=3: {
            "confirmed": True, "full_screen": True,
            "rows_before": 2, "rows_after": 6,
            "sheet_top_before": 800, "sheet_top_after": 120,
            "sheet_top_pct": 5.1, "sheet_h": 2220, "screen_h": 2340,
            "pulls_done": 1, "y_from": 830, "y_to": 120, "full_top": True,
            "grew": True,
        }
        mod._sheet.sheet_looks_expanded = lambda ctx, rows_before=0: {
            "confirmed": True, "full_screen": True, "rows": 6,
            "sheet_top": 120, "sheet_top_pct": 5.1, "sheet_h": 2220,
            "screen_h": 2340, "full_top": True, "grew": True, "h": 2340,
        }
        # Gate po open_* też czyta dump — bez telefonu zawsze full_screen
        mod._sheet.dump_sheet_geometry = lambda ctx: {
            "full_screen": True, "sheet_top": 120, "sheet_top_pct": 5.1,
            "sheet_h": 2220, "screen_h": 2340, "handle_y": 120,
            "title_y": 160, "rows": 6, "top_src": "drag_handle",
        }
        # Zakładka Rolki na profilu — zawsze OK w unitach
        def fake_reels_tab(ctx):
            self.reels_tab_calls.append(True)
            return True
        mod._ensure_profile_reels_tab = fake_reels_tab

    def tearDown(self):
        self.mod.steps.get = self._orig
        self.mod._sheet.expand_sheet = self._orig_expand
        self.mod._sheet.expand_sheet_confirmed = self._orig_confirmed
        self.mod._sheet.sheet_looks_expanded = self._orig_looks
        self.mod._sheet.dump_sheet_geometry = self._orig_dump
        self.mod._ensure_profile_reels_tab = self._orig_reels_tab

    BASE = {"enabled": True,
            "targets": {"list": ["target_a"], "order": "sequential"},
            "sources": ["followers"],
            "posts": {"last_posts": 2},
            "collect": {"per_list": 50, "per_post_likers": 20,
                        "per_post_commenters": 10},
            "filters": {}, "harvest": {"source_tag": "auto", "enabled": True}}

    def _spec(self, **over):
        # rows>0 + full_screen — plugin nie harvestuje bez dumpa pełnego ekranu
        ok_eng = StepResult.ok(
            baseline="x", rows=3, expanded=True, expand_confirmed=True,
            full_screen=True, rows_before=1, rows_after=3,
            sheet_top_after=120, sheet_top_pct=5.1, sheet_h=2220,
            screen_h=2340, pulls_done=1)
        spec = {"search.open": [], "search.type_query": [],
                "search.pick_result": [StepResult.ok(username="target_a")],
                "profile.open_followers": [StepResult.ok(source="followers")],
                "post.open_from_grid": [StepResult.ok(kind="post", landed="x")] * 5,
                "post.open_likers": [ok_eng] * 5,
                "post.open_comments": [ok_eng] * 5,
                "reels.harvest_likers": [StepResult.ok(saved=5, with_ring=1,
                                                      dupes_skipped=0)] * 5,
                "reels.harvest_commenters": [StepResult.ok(saved=4,
                                                          dupes_skipped=0)] * 5,
                "list.harvest_usernames": [
                    StepResult.ok(saved=30, with_ring=4, dupes_skipped=0)] * 10}
        spec.update(over)
        return spec

    def _run(self, spec, s=None, budget=None):
        self.mod.steps.get = fake_registry(spec, self.recorder)
        # Profil OK: identify → profile_other (inaczej _go_to_profile woła ensure)
        nav = FakeNavigator()
        nav.identify = lambda: type("S", (), {"top": "profile_other",
                                              "overlay": None})()
        ctx = FakeCtx(nav)
        ctx.account = type("A", (), {
            "account_id": "t",
            "device": {"_hardware": {"resolution": (1080, 2340)}},
        })()
        ctx.ht = type("H", (), {
            "tap": staticmethod(lambda *a, **k: None),
            "swipe": staticmethod(lambda *a, **k: None),
        })()
        res = TargetScraper().run(ctx, s or self.BASE, budget or Budget(max_items=1))
        return ctx, res, [n for n, _ in self.recorder]

    def test_never_calls_any_interaction_step(self):
        """Cały sens tego pluginu: zbiera i nic więcej. Gdyby kiedykolwiek
        pojawił się tu lajk albo follow, ten test ma spaść."""
        s = {**self.BASE, "sources": ["followers", "post_likers", "post_commenters"]}
        ctx, res, names = self._run(self._spec(), s)
        # UWAGA na dopasowanie po fragmencie: `post.open_likers` OTWIERA listę
        # lajkujących i jest w porządku — zakazane są kroki WYKONUJĄCE akcję.
        forbidden_steps = {"post.like", "reels.like", "reels.like_comment",
                           "profile.follow_execute", "profile.visit",
                           "profile.like_grid_tile"}
        self.assertEqual([n for n in names if n in forbidden_steps], [])

    def test_query_has_no_at_sign(self):
        """Search bez @ — jak niche_discover; @ jest tylko w stripie listy."""
        self._run(self._spec())
        q = dict(self.recorder)["search.type_query"]["query"]
        self.assertEqual(q, "target_a")
        self.assertNotIn("@", q)

    def test_followers_source_uses_profile_list(self):
        ctx, res, names = self._run(self._spec())
        self.assertIn("profile.open_followers", names)
        self.assertNotIn("post.open_from_grid", names)
        self.assertEqual(res.stats["saved"], 30)

    def test_post_source_opens_posts_from_grid(self):
        s = {**self.BASE, "sources": ["post_likers"]}
        ctx, res, names = self._run(self._spec(), s)
        self.assertEqual(names.count("post.open_from_grid"), 2)   # last_posts=2
        self.assertIn("post.open_likers", names)
        params = dict(self.recorder)["post.open_from_grid"]
        self.assertTrue(params["ordered"])
        self.assertEqual(params["content_types"], ["posts"])

    def test_likers_and_commenters_share_one_grid_loop(self):
        """Jedna pętla siatki: N× open_from_grid, na każdym likers+comments —
        nie 2N otwarć kafelków."""
        s = {**self.BASE, "sources": ["post_likers", "post_commenters"],
             "posts": {"last_posts": 2}}
        ctx, res, names = self._run(self._spec(), s)
        self.assertEqual(names.count("post.open_from_grid"), 2)
        self.assertEqual(names.count("post.open_likers"), 2)
        self.assertEqual(names.count("post.open_comments"), 2)

    def test_reel_only_opens_reels_tab_then_grid(self):
        """Same reel_* → zakładka Rolki, potem open z content_types=reels."""
        s = {**self.BASE, "sources": ["reel_likers"], "posts": {"last_posts": 1}}
        spec = self._spec(**{
            "post.open_from_grid": [StepResult.ok(kind="reel", landed="x")]})
        ctx, res, names = self._run(spec, s)
        self.assertEqual(len(self.reels_tab_calls), 1)
        params = dict(self.recorder)["post.open_from_grid"]
        self.assertEqual(params["content_types"], ["reels"])
        self.assertIn("post.open_likers", names)
        self.assertNotIn("reels.harvest_likers", names)

    def test_reel_opened_uses_post_open_not_reels_harvest(self):
        """Rolka z zakładki profilu: post.open_* in-place; bez reels.harvest_*."""
        s = {**self.BASE, "sources": ["reel_likers", "reel_commenters"],
             "posts": {"last_posts": 1}}
        spec = self._spec(**{
            "post.open_from_grid": [StepResult.ok(kind="reel", landed="x")]})
        ctx, res, names = self._run(spec, s)
        self.assertIn("post.open_likers", names)
        self.assertIn("post.open_comments", names)
        self.assertNotIn("reels.harvest_likers", names)
        self.assertNotIn("reels.harvest_commenters", names)
        harvests = [p for n, p in self.recorder if n == "list.harvest_usernames"]
        self.assertTrue(harvests)
        self.assertEqual(harvests[0]["extra"]["content_kind"], "reel")

    def test_posts_then_reels_tab_separate_loops(self):
        """Docelowy flow: najpierw pętla postów, potem zakładka Rolki + pętla rolek.

        Search tylko na starcie targetu (1×) — nie między kafelkami / zakładkami.
        """
        s = {**self.BASE,
             "sources": ["post_likers", "post_commenters",
                         "reel_likers", "reel_commenters"],
             "posts": {"last_posts": 2}}
        # 2 posty + 2 rolki
        opens = (
            [StepResult.ok(kind="post", landed="x")] * 2
            + [StepResult.ok(kind="reel", landed="x")] * 2)
        ok_eng = StepResult.ok(
            baseline="x", rows=3, expanded=True, expand_confirmed=True,
            full_screen=True, rows_before=1, rows_after=3,
            sheet_top_after=120, sheet_top_pct=5.1, sheet_h=2220,
            screen_h=2340, pulls_done=1)
        spec = self._spec(**{
            "post.open_from_grid": opens,
            "post.open_likers": [ok_eng] * 8,
            "post.open_comments": [ok_eng] * 8,
            "list.harvest_usernames": [
                StepResult.ok(saved=5, with_ring=0, dupes_skipped=0)] * 20,
        })
        ctx, res, names = self._run(spec, s)
        self.assertEqual(names.count("search.open"), 1)
        self.assertEqual(names.count("search.type_query"), 1)
        self.assertEqual(len(self.reels_tab_calls), 1)
        self.assertEqual(names.count("post.open_from_grid"), 4)
        self.assertEqual(names.count("post.open_likers"), 4)
        self.assertEqual(names.count("post.open_comments"), 4)
        # Kolejność typów w open_from_grid: posts → reels
        grid_params = [p for n, p in self.recorder if n == "post.open_from_grid"]
        self.assertEqual([p["content_types"] for p in grid_params],
                         [["posts"], ["posts"], ["reels"], ["reels"]])
        # Zakładka Rolki po obu postach (po 2× open posts)
        # — w recorderze: search… → 2× posts open → … → 2× reels open
        first_reels_idx = next(
            i for i, (n, p) in enumerate(self.recorder)
            if n == "post.open_from_grid" and p.get("content_types") == ["reels"])
        last_posts_idx = max(
            i for i, (n, p) in enumerate(self.recorder)
            if n == "post.open_from_grid" and p.get("content_types") == ["posts"])
        self.assertLess(last_posts_idx, first_reels_idx)
        self.assertEqual(res.stats.get("posts_opened"), 2)
        self.assertEqual(res.stats.get("reels_opened"), 2)

    def test_search_only_when_target_changes(self):
        """Dwa targety → search.open ×2 (raz na każdy), nie między kafelkami."""
        s = {**self.BASE,
             "targets": {"list": ["aaa", "bbb"], "order": "sequential",
                         "per_session": 2},
             "sources": ["post_likers"],
             "posts": {"last_posts": 1}}
        ok_eng = StepResult.ok(
            baseline="x", rows=2, expanded=True, expand_confirmed=True,
            full_screen=True, rows_before=1, rows_after=2,
            sheet_top_after=120, sheet_top_pct=5.1, screen_h=2340)
        spec = self._spec(**{
            "search.pick_result": [
                StepResult.ok(username="aaa"),
                StepResult.ok(username="bbb")],
            "post.open_from_grid": [
                StepResult.ok(kind="post", landed="x")] * 4,
            "post.open_likers": [ok_eng] * 4,
            "list.harvest_usernames": [
                StepResult.ok(saved=3, with_ring=0, dupes_skipped=0)] * 4,
        })
        ctx, res, names = self._run(spec, s, budget=Budget(max_items=2))
        self.assertEqual(names.count("search.open"), 2)
        self.assertEqual(names.count("search.type_query"), 2)
        self.assertEqual(res.stats["targets"], 2)
        # Między searchami są open_from_grid — nie drugi search zaraz po pierwszym
        idxs = [i for i, n in enumerate(names) if n == "search.open"]
        self.assertEqual(len(idxs), 2)
        between = names[idxs[0] + 1:idxs[1]]
        self.assertIn("post.open_from_grid", between)

    def test_followers_after_grids(self):
        """followers po siatkach post/reel — nadal na tym samym profilu."""
        s = {**self.BASE,
             "sources": ["post_likers", "followers"],
             "posts": {"last_posts": 1}}
        ctx, res, names = self._run(self._spec(), s)
        i_grid = names.index("post.open_from_grid")
        i_fol = names.index("profile.open_followers")
        self.assertLess(i_grid, i_fol)

    def test_harvest_extra_has_from_target_and_content_kind(self):
        s = {**self.BASE, "sources": ["post_likers"], "posts": {"last_posts": 1}}
        self._run(self._spec(), s)
        params = dict(self.recorder)["list.harvest_usernames"]
        self.assertEqual(params["extra"]["from_target"], "target_a")
        self.assertEqual(params["extra"]["content_kind"], "post")

    def test_harvest_disabled_skips_save(self):
        s = {**self.BASE, "sources": ["followers"],
             "harvest": {"enabled": False, "source_tag": "auto"}}
        ctx, res, names = self._run(self._spec(), s)
        self.assertNotIn("list.harvest_usernames", names)
        self.assertEqual(res.stats["saved"], 0)

    def test_unknown_source_is_reported(self):
        s = {**self.BASE, "sources": ["telepatia"]}
        ctx, res, names = self._run(self._spec(), s)
        self.assertIn("target_scraper.unknown_source", ctx.kinds())

    def test_unreachable_target_does_not_kill_the_run(self):
        spec = self._spec(**{"search.pick_result": [StepResult.failed("brak")]})
        ctx, res, names = self._run(spec)
        self.assertEqual(res.status, "ok")
        self.assertIn("target_scraper.target_unreachable", ctx.kinds())

    def test_ring_filter_is_passed_to_harvest(self):
        s = {**self.BASE, "filters": {"only_with_story_ring": True}}
        self._run(self._spec(), s)
        params = dict(self.recorder)["list.harvest_usernames"]
        self.assertTrue(params["only_with_story_ring"])

    def _engagement_ctx(self):
        """Minimalny ctx pod bezpośrednie `_viewer_engagement` (bez search)."""
        nav = FakeNavigator()
        nav.identify = lambda: type("S", (), {
            "top": "media_viewer", "overlay": None})()
        ctx = FakeCtx(nav)
        ctx.account = type("A", (), {
            "account_id": "t",
            "device": {"_hardware": {"resolution": (1080, 2340)}},
        })()
        return ctx

    def test_nothing_opened_zero_backs(self):
        """nothing_opened / no_entry — zostajemy na viewerze, 0× BACK."""
        skip = StepResult.skipped("nothing_opened")
        self.mod.steps.get = fake_registry(
            {"post.open_likers": [skip, skip]}, self.recorder)
        ctx = self._engagement_ctx()
        before = ctx.device.backs
        stats = {"lists": 0, "saved": 0, "with_ring": 0, "dupes_skipped": 0}
        TargetScraper()._viewer_engagement(
            ctx, self.BASE, "post_likers", "likers", "target_a",
            __import__("random").Random(0), stats,
            limit=10, content_kind="post")
        self.assertEqual(ctx.device.backs - before, 0)
        self.assertNotIn("list.harvest_usernames",
                         [n for n, _ in self.recorder])

    def test_no_entry_zero_backs(self):
        skip = StepResult.skipped("no_entry")
        self.mod.steps.get = fake_registry(
            {"post.open_likers": [skip, skip]}, self.recorder)
        ctx = self._engagement_ctx()
        before = ctx.device.backs
        stats = {"lists": 0, "saved": 0, "with_ring": 0, "dupes_skipped": 0}
        TargetScraper()._viewer_engagement(
            ctx, self.BASE, "post_likers", "likers", "target_a",
            __import__("random").Random(0), stats,
            limit=10, content_kind="post")
        self.assertEqual(ctx.device.backs - before, 0)

    def test_not_expanded_backs_without_leaving_viewer(self):
        """not_expanded → BACK zamyka half-sheet; identify nadal viewer."""
        skip = StepResult.skipped("not_expanded", full_screen=False, rows=0)
        self.mod.steps.get = fake_registry(
            {"post.open_likers": [skip, skip]}, self.recorder)
        ctx = self._engagement_ctx()
        before = ctx.device.backs
        tops = []

        def identify():
            tops.append("media_viewer")
            return type("S", (), {"top": "media_viewer", "overlay": None})()

        ctx.navigator.identify = identify
        stats = {"lists": 0, "saved": 0, "with_ring": 0, "dupes_skipped": 0}
        TargetScraper()._viewer_engagement(
            ctx, self.BASE, "post_likers", "likers", "target_a",
            __import__("random").Random(0), stats,
            limit=10, content_kind="post")
        # 2 próby × BACK (sheet był otwarty) — bez zejścia z viewer
        self.assertEqual(ctx.device.backs - before, 2)
        self.assertTrue(tops)
        self.assertTrue(all(t == "media_viewer" for t in tops))

    def test_followers_path_skips_expand_sheet_confirmed(self):
        """follow_list = pełny ekran — harvest bez expand_sheet_confirmed."""
        expand_calls = []
        self.mod._sheet.expand_sheet_confirmed = (
            lambda ctx, max_pulls=3: expand_calls.append(max_pulls) or {
                "confirmed": True, "full_screen": True,
                "rows_before": 0, "rows_after": 0})
        ctx, res, names = self._run(self._spec(), {**self.BASE,
                                                   "sources": ["followers"]})
        self.assertEqual(expand_calls, [])
        self.assertIn("profile.open_followers", names)
        self.assertIn("list.harvest_usernames", names)
        self.assertNotIn("target_scraper.list_expand_check", ctx.kinds())

    def test_commenters_harvest_excludes_from_target(self):
        """Autor posta (from_target) nie trafia do raw przez exclude=."""
        s = {**self.BASE, "sources": ["post_commenters"],
             "posts": {"last_posts": 1}}
        self._run(self._spec(), s)
        harvests = [p for n, p in self.recorder
                    if n == "list.harvest_usernames"]
        self.assertTrue(harvests)
        self.assertIn("target_a", harvests[0].get("exclude") or [])


# ============================================================
class ReelsWarmupTests(unittest.TestCase):
    def setUp(self):
        from v2.plugins.impl import reels_warmup as mod
        self.mod = mod
        self.recorder = []
        self._orig = mod.steps.get

    def tearDown(self):
        self.mod.steps.get = self._orig

    BASE = {"enabled": True, "like": {"enabled": False}, "skip_ads": True}

    def _run(self, spec, s=None, budget=None):
        self.mod.steps.get = fake_registry(spec, self.recorder)
        ctx = FakeCtx()
        res = ReelsWarmup().run(ctx, s or self.BASE, budget or Budget(max_items=3))
        return ctx, res, [n for n, _ in self.recorder]

    def test_watches_without_ai_or_harvest(self):
        """Rozgrzewka ma wyglądać jak zwykłe przeglądanie, nie jak selektywne
        polowanie — żadnej analizy i żadnego zbierania."""
        spec = {"reels.open": [], "reels.watch": [StepResult.ok()] * 5,
                "reels.swipe_next": []}
        ctx, res, names = self._run(spec)
        self.assertEqual(res.stats["watched"], 3)
        self.assertNotIn("reels.analyze_frames_ai", names)
        self.assertNotIn("reels.harvest_likers", names)

    def test_no_gate_is_passed_to_watch(self):
        spec = {"reels.open": [], "reels.watch": [StepResult.ok()],
                "reels.swipe_next": []}
        self._run(spec, budget=Budget(max_items=1))
        self.assertEqual(dict(self.recorder)["reels.watch"]["gate"], {})

    def test_ads_are_skipped_without_liking(self):
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": ScratchStep({"reel": {"is_ad": True, "marker": "m1"}},
                                           self.recorder, "x"),
                "reels.like": []}
        s = {**self.BASE, "like": {"enabled": True,
                                   "chance": {"mode": "p", "value": 1}}}
        ctx, res, names = self._run(spec, s, budget=Budget(max_items=1))
        self.assertEqual(res.stats["ads_skipped"], 1)
        self.assertNotIn("reels.like", names)

    def test_like_disabled_never_calls_like(self):
        spec = {"reels.open": [], "reels.watch": [StepResult.ok()] * 3,
                "reels.swipe_next": [], "reels.like": []}
        ctx, res, names = self._run(spec)
        self.assertNotIn("reels.like", names)

    def test_empty_meta_soft_skips_like_and_watched(self):
        """Watch ok + author/marker None → nie lajkuj i nie licz watched."""
        empty = {"reel": {"author": None, "marker": None}}
        spec = {"reels.open": [], "reels.swipe_next": [],
                "reels.watch": ScratchStep(empty, self.recorder, "x"),
                "reels.like": []}
        s = {**self.BASE, "like": {"enabled": True,
                                   "chance": {"mode": "p", "value": 1}}}
        ctx, res, names = self._run(spec, s, budget=Budget(max_items=1))
        self.assertEqual(res.stats["watched"], 0)
        self.assertEqual(res.stats.get("meta_skipped", 0), 1)
        self.assertNotIn("reels.like", names)

    def test_watch_gets_warmup_log_prefix(self):
        spec = {"reels.open": [], "reels.watch": [StepResult.ok()],
                "reels.swipe_next": []}
        self._run(spec, budget=Budget(max_items=1))
        watch_params = dict(self.recorder)["reels.watch"]
        self.assertEqual(watch_params.get("log_prefix"), "reels_warmup")

    def test_swipe_and_like_get_warmup_log_prefix(self):
        """Cała pętla warmup — watch / swipe / like z tagiem reels_warmup."""
        reel = {"reel": {"author": "a", "marker": "m1"}}
        spec = {
            "reels.open": [],
            # Jeden ScratchStep — fake_registry cache'uje i nadpisuje name
            "reels.watch": ScratchStep(reel, self.recorder, "w"),
            "reels.swipe_next": [StepResult.ok()],
            "reels.like": [StepResult.ok(), StepResult.ok()],
        }
        s = {**self.BASE, "like": {"enabled": True,
                                   "chance": {"mode": "p", "value": 1}}}
        # max_items=2 → po 1. watch jest swipe (prefix), potem 2. watch
        self._run(spec, s, budget=Budget(max_items=2))
        by_name = {}
        for name, params in self.recorder:
            by_name.setdefault(name, params)
        self.assertEqual(by_name["reels.watch"].get("log_prefix"), "reels_warmup")
        self.assertEqual(by_name["reels.swipe_next"].get("log_prefix"),
                         "reels_warmup")
        self.assertEqual(by_name["reels.like"].get("log_prefix"), "reels_warmup")

    def test_two_swipe_failures_mark_failed(self):
        spec = {"reels.open": [], "reels.watch": [StepResult.ok()] * 5,
                "reels.swipe_next": [StepResult.failed("x"),
                                     StepResult.failed("x")]}
        ctx, res, names = self._run(spec, budget=Budget(max_items=5))
        self.assertEqual(res.stopped_by, "error")
        self.assertEqual(res.status, "failed")


if __name__ == "__main__":
    unittest.main()
