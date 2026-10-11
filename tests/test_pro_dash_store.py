"""Store Insights: upsert dni, rolling 7/14/30, listy typów, ścieżka miniatury."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.data.pro_dash_parse import (  # noqa: E402
    load_chart_series,
    load_item_chart_series,
    seed_scrape_from_recon,
)
from v2.data.pro_dash_store import (  # noqa: E402
    ProDashStore,
    maybe_derive_last_day,
    rolling_from_daily,
    upsert_daily_points,
)


class UpsertDaysTests(unittest.TestCase):
    def test_overlap_aktualizuje_nowe_daty_dolaczaja(self):
        daily = upsert_daily_points({}, [
            {"date": "2026-09-01", "views": 10, "follows": 1, "interactions": 2},
            {"date": "2026-09-02", "views": 5},
        ])
        daily = upsert_daily_points(daily, [
            {"date": "2026-09-02", "views": 8, "follows": -1},  # overlap
            {"date": "2026-09-03", "views": 3},  # nowy dzień
        ])
        self.assertEqual(set(daily), {"2026-09-01", "2026-09-02", "2026-09-03"})
        self.assertEqual(daily["2026-09-01"]["views"], 10)
        self.assertEqual(daily["2026-09-02"]["views"], 8)
        self.assertEqual(daily["2026-09-02"]["follows"], -1)
        # brakującej metryki nie kasujemy przy częściowym upsert
        self.assertIsNone(daily["2026-09-02"].get("interactions"))
        self.assertEqual(daily["2026-09-03"]["views"], 3)


class RollingTests(unittest.TestCase):
    def test_rolling_7_14_30_z_serii_dziennej(self):
        # 6 sie = 1 … 5 wrz = 31 (31 punktów kalendarzowych)
        start = date(2026, 8, 6)
        points = []
        for i in range(31):
            day = date.fromordinal(start.toordinal() + i)
            points.append({"date": day.isoformat(), "views": i + 1,
                           "follows": 0, "interactions": 0})
        daily = upsert_daily_points({}, points)
        as_of = date(2026, 9, 5)
        r7 = rolling_from_daily(daily, 7, as_of=as_of)
        r14 = rolling_from_daily(daily, 14, as_of=as_of)
        r30 = rolling_from_daily(daily, 30, as_of=as_of)
        # 30 sie–5 wrz = views 25..31
        self.assertEqual(r7["views"], sum(range(25, 32)))
        self.assertTrue(r7["complete"])
        self.assertEqual(r14["points_filled"], 14)
        self.assertEqual(r30["points_filled"], 30)
        # okno 30: 7 sie–5 wrz = views 2..31 (6 sie wypada)
        self.assertEqual(r30["views"], sum(range(2, 32)))

    def test_dziura_nie_jest_zerem(self):
        daily = upsert_daily_points({}, [
            {"date": "2026-09-05", "views": 10},
        ])
        r = rolling_from_daily(daily, 7, as_of=date(2026, 9, 5))
        self.assertEqual(r["views"], 10)
        self.assertEqual(r["points_filled"], 1)
        self.assertFalse(r["complete"])
        self.assertIsNone(r["points"][0]["views"])


class ContentAndThumbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ProDashStore("jobsaver", self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_listy_typed_post_reel_story(self):
        self.store.merge_scrape({
            "as_of": "2026-09-05T20:00:00",
            "source": "seed",
            "content": [
                {"kind": "post", "caption": "#rekrutacja", "views": 379, "age": "4 dni"},
                {"kind": "story", "caption": None, "views": 147, "age": "4 dni"},
            ],
        })
        doc = self.store.read()
        self.assertEqual(len(doc["content"]["post"]), 1)
        self.assertEqual(len(doc["content"]["story"]), 1)
        self.assertEqual(doc["content"]["reel"], [])
        self.assertEqual(doc["content"]["post"][0]["views"], 379)
        # drugi scrape — ten sam caption, wyższe views → historia, nie nowy wiersz
        self.store.merge_scrape({
            "as_of": "2026-09-06T20:00:00",
            "source": "live",
            "content": [
                {"kind": "post", "caption": "#rekrutacja", "views": 400, "age": "5 dni"},
            ],
        })
        post = self.store.read()["content"]["post"][0]
        self.assertEqual(post["views"], 400)
        self.assertEqual(len(post["history"]), 2)

    def test_live_bez_views_nie_kasuje_snapshotu(self):
        self.store.merge_scrape({
            "as_of": "2026-09-05T20:00:00",
            "source": "seed",
            "windows": {"30": {"views": 2001, "follows": -10, "interactions": 13}},
        })
        self.store.merge_scrape({
            "as_of": "2026-09-05T21:00:00",
            "source": "live",
            "windows": {"30": {"views": None, "follows": -10, "interactions": 13}},
        })
        win = self.store.read()["windows"]["30"]
        self.assertEqual(win["views"], 2001)
        self.assertEqual(win["follows"], -10)

    def test_sciezka_miniatury(self):
        blob = b"\xff\xd8\xff" + b"\x00" * 32  # nagłówek JPEG + padding
        rel = self.store.save_thumb("post_rekrutacja_379", blob)
        self.assertEqual(rel, "thumbs/post_rekrutacja_379.jpg")
        path = self.store.thumb_path("post_rekrutacja_379")
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes()[:3], b"\xff\xd8\xff")
        self.assertTrue(str(path).endswith("pro_dash/thumbs/post_rekrutacja_379.jpg"))


class ChartSeriesTests(unittest.TestCase):
    """Seria z CHART_SERIES — N dni w kolejności, nie 3 ticki osi."""

    def test_seed_ma_30_dni_w_kolejnosci(self):
        scrape = seed_scrape_from_recon()
        daily = scrape["daily"]
        dates = [p["date"] for p in daily]
        self.assertEqual(len(dates), 30)
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(dates[0], "2026-08-06")
        self.assertEqual(dates[-1], "2026-09-04")
        self.assertGreater(len(dates), 3)
        views = [int(p["views"]) for p in daily]
        self.assertEqual(sum(views), 2001)
        # płasko, potem dwa piki (nie sinusoida)
        self.assertTrue(all(v == 0 for v in views[:20]))
        by = {p["date"]: int(p["views"]) for p in daily}
        self.assertGreater(by["2026-08-29"], 200)
        self.assertGreater(by["2026-09-02"], by["2026-08-29"])
        self.assertEqual(by["2026-09-02"], max(views))

    def test_store_rolling_n_punktow_nie_trzy_ticki(self):
        series = load_chart_series()
        tmp = tempfile.TemporaryDirectory()
        try:
            store = ProDashStore("jobsaver", Path(tmp.name))
            store.merge_scrape({
                "as_of": "2026-09-05T20:48:00",
                "source": "seed",
                "daily": series["daily"],
                "windows": {"30": {
                    "as_of_date": "2026-09-04",
                    "views": 2001,
                    "follows": -10,
                    "interactions": 13,
                }},
            })
            view = store.public_view(as_of=date(2026, 9, 4))
            pts = view["rolling"]["30"]["points"]
            self.assertEqual(len(pts), 30)
            self.assertEqual([p["date"] for p in pts], sorted(p["date"] for p in pts))
            self.assertTrue(view["rolling"]["30"]["complete"])
            self.assertEqual(view["rolling"]["30"]["views"], 2001)
            self.assertEqual(view["rolling"]["7"]["points_filled"], 7)
            self.assertEqual(len(view["rolling"]["7"]["points"]), 7)
            self.assertEqual(len(view["rolling"]["14"]["points"]), 14)
        finally:
            tmp.cleanup()


class ContentTypeSeriesTests(unittest.TestCase):
    """Seria per typ: daty posortowane; puste rolki nie walą store/UI."""

    def test_seed_type_series_date_ordered(self):
        scrape = seed_scrape_from_recon()
        cd = scrape["content_daily"]
        # post/story: ślad albo jeden as_of — daty rosnąco
        for kind in ("post", "story"):
            dates = [p["date"] for p in cd[kind]]
            self.assertTrue(dates, f"{kind} musi mieć punkt as_of albo ślad")
            self.assertEqual(dates, sorted(dates))
        # rolki: pusta seria, nie zero na 30 dniach
        self.assertEqual(cd["reel"], [])

    def test_store_type_series_ordered_reel_empty_nie_wywala(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            store = ProDashStore("jobsaver", Path(tmp.name))
            store.merge_scrape({
                "as_of": "2026-09-05T20:00:00",
                "source": "seed",
                "windows": {"30": {
                    "as_of_date": "2026-09-04",
                    "views": 2001,
                }},
                "content_daily": {
                    "post": [
                        {"date": "2026-09-03", "views": 10, "source": "as_of_total"},
                        {"date": "2026-09-01", "views": 5, "source": "as_of_total"},
                        {"date": "2026-09-02", "views": 8, "source": "as_of_total"},
                    ],
                    "story": [
                        {"date": "2026-09-04", "views": 856, "source": "as_of_total"},
                    ],
                    "reel": [],
                },
                "content": [
                    {"kind": "post", "caption": "#x", "views": 10, "age": "1 dzień"},
                ],
            })
            doc = store.read()
            post_dates = list(doc["content_daily"]["post"])
            self.assertEqual(post_dates, sorted(post_dates))
            self.assertEqual(post_dates, ["2026-09-01", "2026-09-02", "2026-09-03"])
            self.assertEqual(doc["content_daily"]["reel"], {})
            self.assertEqual(doc["content"]["reel"], [])
            view = store.public_view(as_of=date(2026, 9, 4))
            r = view["content_rolling"]["reel"]["30"]
            self.assertEqual(r["points_filled"], 0)
            self.assertIsNone(r["views"])
            self.assertEqual(len(r["points"]), 30)
            # puste rolki nie sypią wykresu
            self.assertEqual(view["content_series_meta"]["reel"]["points_n"], 0)
        finally:
            tmp.cleanup()

    def test_seed_item_daily_typowy_post_nie_trzy_ticki(self):
        """Drill-in #rekrutacja 379 — ślad linii, nie 3 etykiety osi."""
        series = load_item_chart_series()
        self.assertTrue(series)
        rec = series[0]
        self.assertEqual(rec["views"], 379)
        dates = [p["date"] for p in rec["daily"]]
        self.assertEqual(dates, sorted(dates))
        self.assertGreaterEqual(len(dates), 5)
        self.assertGreater(len(dates), 3)
        self.assertEqual(dates[0], "2026-09-01")
        self.assertEqual(dates[-1], "2026-09-05")
        scrape = seed_scrape_from_recon()
        post = next(r for r in scrape["content"] if r.get("views") == 379)
        self.assertEqual(len(post.get("daily") or []), 5)
        self.assertEqual(post["daily"][-1]["views"], 379)
        self.assertEqual(post["daily"][0]["views"], 0)
        # kształt: skok, potem plateau — nie sinusoida
        self.assertGreater(post["daily"][1]["views"], 300)
        self.assertEqual(post["daily_mode"], "cumulative")
        # 7/14/30 — datowana seria, typowy osobno od Ten post
        for w in ("7", "14", "30"):
            win = rec["series_by_window"][w]
            dates_w = [p["date"] for p in win]
            self.assertEqual(dates_w, sorted(dates_w))
            self.assertGreaterEqual(len(dates_w), 5)
            self.assertTrue(all(p.get("date") for p in win))
            typ = rec["typical_by_window"][w]
            self.assertGreaterEqual(len(typ), 7)
            self.assertNotEqual(
                [p["views"] for p in win],
                [p["views"] for p in typ],
            )
            self.assertEqual(typ[0]["date"], "2026-09-01")
            self.assertEqual(typ[-1]["date"], "2026-09-08")
        self.assertEqual(len(post.get("typical_daily") or []), 8)
        self.assertEqual(post["typical_daily"][-1]["views"], 55)

    def test_item_daily_persist_i_suma_do_typu(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            store = ProDashStore("jobsaver", Path(tmp.name))
            store.merge_scrape({
                "as_of": "2026-09-05T20:00:00",
                "source": "live",
                "content": [{
                    "kind": "post",
                    "caption": "#rekrutacja",
                    "views": 379,
                    "age": "4 dni",
                    "daily": [
                        {"date": "2026-09-02", "views": 200, "source": "live_tooltips"},
                        {"date": "2026-09-03", "views": 179, "source": "live_tooltips"},
                    ],
                }],
            })
            post = store.read()["content"]["post"][0]
            self.assertEqual(list(post["daily"]), ["2026-09-02", "2026-09-03"])
            # suma item.daily → content_daily.post
            cd = store.read()["content_daily"]["post"]
            self.assertEqual(cd["2026-09-02"]["views"], 200)
            self.assertEqual(cd["2026-09-03"]["views"], 179)
            self.assertEqual(cd["2026-09-02"]["source"], "item_sum")
        finally:
            tmp.cleanup()

    def test_skumulowane_ten_post_do_przyrostow(self):
        """Suma typu z wykresu skumulowanego = 379, nie 0+367+376+379."""
        tmp = tempfile.TemporaryDirectory()
        try:
            store = ProDashStore("jobsaver", Path(tmp.name))
            store.merge_scrape({
                "as_of": "2026-09-05T21:53:00",
                "source": "seed",
                "content": [{
                    "kind": "post",
                    "caption": "#rekrutacja",
                    "views": 379,
                    "age": "4 dni",
                    "daily_mode": "cumulative",
                    "daily": [
                        {"date": "2026-09-01", "views": 0, "source": "image_trace"},
                        {"date": "2026-09-02", "views": 367, "source": "image_trace"},
                        {"date": "2026-09-03", "views": 376, "source": "image_trace"},
                        {"date": "2026-09-04", "views": 379, "source": "image_trace"},
                        {"date": "2026-09-05", "views": 379, "source": "image_trace"},
                    ],
                }],
            })
            post = store.read()["content"]["post"][0]
            self.assertEqual(post["daily"]["2026-09-05"]["views"], 379)
            self.assertEqual(post["daily_mode"], "cumulative")
            cd = store.read()["content_daily"]["post"]
            self.assertEqual(cd["2026-09-02"]["views"], 367)
            self.assertEqual(cd["2026-09-03"]["views"], 9)
            self.assertEqual(sum(r["views"] for r in cd.values()), 379)
        finally:
            tmp.cleanup()

    def test_okna_7_14_30_i_typical_osobno_w_store(self):
        """Każde okno ma datowaną serię; typical nie miesza się z Ten post."""
        tmp = tempfile.TemporaryDirectory()
        try:
            store = ProDashStore("jobsaver", Path(tmp.name))
            store.merge_scrape(seed_scrape_from_recon())
            post = next(r for r in store.read()["content"]["post"]
                        if r.get("views") == 379)
            for w in ("7", "14", "30"):
                this = post["series_by_window"][w]
                typ = post["typical_by_window"][w]
                self.assertGreaterEqual(len(this), 5)
                self.assertGreaterEqual(len(typ), 7)
                self.assertEqual(this["2026-09-05"]["views"], 379)
                self.assertEqual(typ["2026-09-08"]["views"], 55)
                self.assertNotEqual(
                    [this[k]["views"] for k in sorted(this)],
                    [typ[k]["views"] for k in sorted(typ)],
                )
            view = store.public_view()
            pub = next(r for r in view["content"]["post"] if r.get("views") == 379)
            self.assertIn("7", pub["series_by_window"])
            self.assertIn("7", pub["typical_by_window"])
        finally:
            tmp.cleanup()


class Windows7i14KpiTests(unittest.TestCase):
    """Karta Insights: 7 i 14 mają follows / interakcje / widzowie."""

    def test_seed_okna_7_14_maja_trzy_metryki(self):
        scrape = seed_scrape_from_recon()
        for key in ("7", "14"):
            snap = scrape["windows"][key]
            self.assertIsNotNone(snap.get("follows"), f"{key} follows")
            self.assertIsNotNone(snap.get("interactions"), f"{key} interactions")
            self.assertIsNotNone(snap.get("viewers"), f"{key} viewers")

    def test_kazdy_post_i_relacja_ma_serie_albo_pusta(self):
        scrape = seed_scrape_from_recon()
        posts = [r for r in scrape["content"] if r.get("kind") == "post"]
        stories = [r for r in scrape["content"] if r.get("kind") == "story"]
        self.assertGreaterEqual(len(posts), 5)
        self.assertGreaterEqual(len(stories), 5)
        for row in posts:
            by = row.get("series_by_window") or {}
            empty = all(len(by.get(w) or []) < 2 for w in ("7", "14", "30"))
            if empty:
                self.assertIn(row.get("chart_kind"), ("donut", "none"))
            else:
                self.assertTrue(by.get("7") or by.get("30"), row.get("id"))
                for w, pts in by.items():
                    self.assertGreaterEqual(len(pts), 2, f"{row.get('id')} {w}")
        for row in stories:
            by = row.get("series_by_window") or {}
            empty = all(len(by.get(w) or []) < 2 for w in ("7", "14", "30"))
            self.assertTrue(empty, row.get("id"))
            self.assertEqual(row.get("chart_kind") or "donut", "donut")


class DeriveLastDayTests(unittest.TestCase):
    def test_spojne_okna_7_daja_punkt(self):
        doc = {
            "windows": {
                "7": {"as_of_date": "2026-09-04", "views": 100},
            },
            "daily": {
                "2026-08-29": {"date": "2026-08-29", "views": 10},
            },
        }
        got = maybe_derive_last_day(
            doc, {"as_of_date": "2026-09-05", "views": 105})
        # 105 - 100 + 10 = 15
        self.assertEqual(got["views"], 15)
        self.assertEqual(got["date"], "2026-09-05")
        self.assertEqual(got["source"], "derived")

    def test_niespojne_liczby_nie_zgaduja(self):
        doc = {
            "windows": {"7": {"as_of_date": "2026-09-04", "views": 100}},
            "daily": {"2026-08-29": {"views": 10}},
        }
        # 50 - 100 + 10 = -40 → odrzucamy
        self.assertIsNone(maybe_derive_last_day(
            doc, {"as_of_date": "2026-09-05", "views": 50}))


if __name__ == "__main__":
    unittest.main()
