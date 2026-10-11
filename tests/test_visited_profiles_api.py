"""GET /api/visited-profiles/{acc} — newest first, pola wizyty, bez ADB."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.data.stores import (  # noqa: E402
    VisitedProfilesStore,
    visit_has_interaction,
    visit_outcome,
)


def _write_account(cfg: Path, alias: str = "jobsaver") -> None:
    (cfg / "accounts").mkdir(parents=True, exist_ok=True)
    (cfg / "accounts" / f"{alias}.json").write_text(json.dumps({
        "meta": {"account_id": alias, "ig_username": "u"},
        "device": {"device_alias": "phone_A",
                   "app_clone_package": "com.instagram.android"},
    }), encoding="utf-8")


def _seed_visited(data: Path, alias: str, rows: list[dict]) -> Path:
    d = data / "accounts" / alias
    d.mkdir(parents=True, exist_ok=True)
    path = d / "visited_profiles.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


class VisitOutcomeMappingTests(unittest.TestCase):
    """Mapowanie outcome z faktu zapisu visit.py (like/follow vs bez)."""

    def test_like_plus_follow(self):
        row = {"followed": True, "tiles_liked": 2, "action": "follow"}
        self.assertTrue(visit_has_interaction(row))
        self.assertEqual(visit_outcome(row), "like + follow")

    def test_like_only_to_interakcja(self):
        row = {"followed": False, "tiles_liked": 1, "action": "like_only"}
        self.assertTrue(visit_has_interaction(row))
        self.assertEqual(visit_outcome(row), "like")

    def test_follow_bez_lajkow(self):
        row = {"followed": True, "tiles_liked": 0, "action": "follow"}
        self.assertTrue(visit_has_interaction(row))
        self.assertEqual(visit_outcome(row), "follow")

    def test_sama_wizyta_nie_jest_interakcja(self):
        row = {"followed": False, "tiles_liked": 0, "action": "skip",
               "reason": "gate:followers"}
        self.assertFalse(visit_has_interaction(row))
        self.assertEqual(visit_outcome(row), "tylko wizyta - gate")

    def test_private_i_nisza(self):
        self.assertEqual(
            visit_outcome({"action": "skip", "reason": "sanity:became_private",
                           "tiles_liked": 0}),
            "private")
        self.assertEqual(
            visit_outcome({"action": "skip", "reason": "match:audience_fit",
                           "tiles_liked": 0}),
            "nisza nie")

    def test_lajk_po_odrzuceniu_niszy_zostaje_like(self):
        row = {"action": "skip", "reason": "match:audience_fit",
               "tiles_liked": 1, "followed": False}
        self.assertTrue(visit_has_interaction(row))
        self.assertEqual(visit_outcome(row), "like")


class LejekMarkupTests(unittest.TestCase):
    """Widok /app#lejek: 3 kolumny, pending licznik, sen klasyka."""

    def test_html_ma_kolumny_i_hint(self):
        html = (ROOT / "panel" / "static" / "app.html").read_text(encoding="utf-8")
        self.assertIn('id="view-lejek"', html)
        self.assertIn('id="lj-col-raw"', html)
        self.assertIn('id="lj-col-cand"', html)
        self.assertIn('id="lj-col-visit"', html)
        self.assertNotIn("Wkrótce — kolumny lejka", html)
        self.assertIn("pending filtra", html)
        self.assertIn("id=\"lj-sleep-between\"", html)
        self.assertIn("id=\"lj-sleep-request\"", html)
        self.assertIn("Interact jedzie z raw", html)
        js = (ROOT / "panel" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/visited-profiles/", js)
        self.assertIn("sleep_between_s", js)
        self.assertIn("sleep_request_s", js)


class VisitedStorePreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.store = VisitedProfilesStore("acc", self.data)
        self.addCleanup(self.tmp.cleanup)

    def test_preview_newest_first_i_limit(self):
        self.store.add("old", action="skip", reason="gate:x", tiles_liked=0)
        self.store.add("new", action="like_only", tiles_liked=2, followed=False)
        prev = self.store.preview(limit=1)
        self.assertEqual(prev["total"], 2)
        self.assertEqual(prev["shown"], 1)
        self.assertEqual(prev["items"][0]["username"], "new")
        self.assertEqual(prev["items"][0]["action"], "like_only")
        self.assertTrue(prev["items"][0]["interacted"])


class VisitedProfilesApiTests(unittest.TestCase):
    """TestClient — sort, limit, 404, pola action/followed. Bez ADB."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cfg = root / "config"
        self.data = root / "data"
        _write_account(self.cfg, "jobsaver")
        today = datetime.now().strftime("%Y-%m-%d")
        # Plik append-only: najstarszy pierwszy, endpoint odwraca.
        self.rows = [
            {"ts": "2026-08-01T10:00:00", "username": "old_gate",
             "action": "skip", "reason": "gate:followers",
             "tiles_liked": 0, "followed": False,
             "header": {"followers": 12, "is_private": False}},
            {"ts": f"{today}T11:00:00", "username": "today_like",
             "action": "like_only", "reason": None,
             "tiles_liked": 3, "followed": False,
             "header": {"followers": 2100}},
            {"ts": f"{today}T12:30:00", "username": "today_both",
             "action": "follow", "reason": None,
             "tiles_liked": 1, "followed": True,
             "header": {"followers": 800}},
        ]
        _seed_visited(self.data, "jobsaver", self.rows)
        self.cfg_patch = patch("v2.panel.server.CONFIG_ROOT", self.cfg)
        self.data_patch = patch("v2.panel.server.DATA_ROOT", self.data)
        self.cfg_patch.start()
        self.data_patch.start()
        self.addCleanup(self.cfg_patch.stop)
        self.addCleanup(self.data_patch.stop)
        self.addCleanup(self._tmp.cleanup)
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        self.client = TestClient(app)

    def test_newest_first_i_pola(self):
        r = self.client.get("/api/visited-profiles/jobsaver")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["total"], 3)
        names = [it["username"] for it in body["items"]]
        self.assertEqual(names, ["today_both", "today_like", "old_gate"])
        first = body["items"][0]
        self.assertEqual(first["action"], "follow")
        self.assertTrue(first["followed"])
        self.assertEqual(first["tiles_liked"], 1)
        self.assertIn("header", first)
        self.assertTrue(first["interacted"])
        self.assertEqual(first["outcome"], "like + follow")
        gate = body["items"][2]
        self.assertEqual(gate["reason"], "gate:followers")
        self.assertFalse(gate["interacted"])
        self.assertEqual(gate["outcome"], "tylko wizyta - gate")
        self.assertEqual(body["today"]["viewed"], 2)
        self.assertEqual(body["today"]["interacted"], 2)

    def test_limit(self):
        r = self.client.get("/api/visited-profiles/jobsaver?limit=1")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["shown"], 1)
        self.assertEqual(body["items"][0]["username"], "today_both")
        self.assertEqual(body["total"], 3)

    def test_brak_konta_404(self):
        r = self.client.get("/api/visited-profiles/nieistnieje")
        self.assertEqual(r.status_code, 404)

    def test_puste_konto_istniejace(self):
        _write_account(self.cfg, "kasia")
        r = self.client.get("/api/visited-profiles/kasia")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["items"], [])
        self.assertEqual(body["today"], {"viewed": 0, "interacted": 0})


if __name__ == "__main__":
    unittest.main()
