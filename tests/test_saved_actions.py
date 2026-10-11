"""Zapisane akcje (presety kroku) — store + API panelu.

To nie jest nowy typ runu: preset = plugin + overrides + nazwa/komentarz
w `config/accounts/<alias>.actions/<slug>.json`. Wstawienie do kolejki
kopiuje pola (osobny task.id).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.plugins import store  # noqa: E402


def _write_account(root: Path, alias: str = "acc1") -> None:
    (root / "accounts").mkdir(parents=True, exist_ok=True)
    (root / "devices").mkdir(parents=True, exist_ok=True)
    (root / "accounts" / f"{alias}.json").write_text(json.dumps({
        "meta": {"account_id": alias, "ig_username": "u"},
        "device": {"device_alias": "dev1", "app_clone_package": "com.instagram.android"},
        "humanizer": {"profile_name": "default"},
    }), encoding="utf-8")
    # write_queue zapala plugins.<id>.enabled → write_settings → load_account
    (root / "devices" / "dev1.json").write_text(json.dumps({
        "adb_id": "x", "resolution": [1080, 2340],
        "uiautodev": {"base_url": "http://127.0.0.1:20242"},
    }), encoding="utf-8")


class SavedActionStoreTests(unittest.TestCase):
    """CRUD plików `.actions/` na tymczasowym drzewie configu."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        _write_account(root)
        self._orig_root = store.CONFIG_ROOT
        store.CONFIG_ROOT = root

    def tearDown(self):
        store.CONFIG_ROOT = self._orig_root
        self._tmp.cleanup()

    def test_slugify_polish_name(self):
        self.assertEqual(
            store.slugify_action_id("Hashtagi + harvest", "content_search"),
            "hashtagi-harvest")
        self.assertEqual(
            store.slugify_action_id("Łąka żniwna", "feed_browser"),
            "laka-zniwna")

    def test_list_empty_when_no_dir(self):
        self.assertEqual(store.list_actions("acc1"), [])

    def test_create_list_delete_roundtrip(self):
        created = store.create_action("acc1", {
            "name": "Hashtagi + harvest",
            "comment": "tylko # z harvestem",
            "plugin": "content_search",
            "overrides": {"source": "hashtag", "run_scope": "phrases"},
        })
        self.assertTrue(created["ok"])
        self.assertEqual(created["id"], "hashtagi-harvest")
        self.assertEqual(created["plugin"], "content_search")
        path = Path(created["path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.parent.name, "acc1.actions")

        listed = store.list_actions("acc1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], "hashtagi-harvest")
        self.assertEqual(listed[0]["comment"], "tylko # z harvestem")
        self.assertEqual(listed[0]["overrides"]["source"], "hashtag")

        got = store.read_action("acc1", "hashtagi-harvest")
        self.assertEqual(got["name"], "Hashtagi + harvest")

        store.delete_action("acc1", "hashtagi-harvest")
        self.assertEqual(store.list_actions("acc1"), [])
        with self.assertRaises(FileNotFoundError):
            store.read_action("acc1", "hashtagi-harvest")

    def test_create_rejects_unknown_plugin_and_bad_overrides(self):
        with self.assertRaises(ValueError):
            store.create_action("acc1", {
                "name": "x", "plugin": "nie_ma", "overrides": {}})
        with self.assertRaises(ValueError):
            store.create_action("acc1", {
                "name": "x", "plugin": "feed_browser", "overrides": "nie"})
        with self.assertRaises(ValueError):
            store.create_action("acc1", {
                "plugin": "feed_browser", "overrides": {}})

    def test_duplicate_explicit_id_raises(self):
        body = {
            "id": "moje-hashtagi",
            "name": "A",
            "plugin": "content_search",
            "overrides": {"source": "hashtag"},
        }
        store.create_action("acc1", body)
        with self.assertRaises(FileExistsError):
            store.create_action("acc1", dict(body, name="B"))

    def test_insert_into_run_copies_overrides(self):
        """Wstawienie do runu kopiuje overrides — mutacja taska nie rusza presetu."""
        created = store.create_action("acc1", {
            "name": "Hashtagi",
            "plugin": "content_search",
            "overrides": {
                "source": "hashtag",
                "run_scope": "phrases",
                "likers": {"harvest": {"enabled": True}},
            },
            "budget": {"max_items": {"min": 5, "max": 10}},
        })
        saved = store.read_action("acc1", created["id"])
        task = store.action_to_task(saved)
        self.assertNotEqual(task["id"], created["id"])
        self.assertEqual(task["plugin"], "content_search")
        self.assertEqual(task["from_action"], created["id"])
        self.assertEqual(task["overrides"]["source"], "hashtag")
        # mutacja kopii w pamięci
        task["overrides"]["source"] = "keyword"
        task["overrides"]["likers"]["harvest"]["enabled"] = False
        again = store.read_action("acc1", created["id"])
        self.assertEqual(again["overrides"]["source"], "hashtag")
        self.assertTrue(again["overrides"]["likers"]["harvest"]["enabled"])

        store.write_queue("acc1", {"queue": [task]})
        q = store.read_queue("acc1")
        self.assertEqual(q["queue"][0]["overrides"]["source"], "keyword")
        self.assertEqual(q["queue"][0]["from_action"], created["id"])
        q["queue"][0]["overrides"]["source"] = "location"
        store.write_queue("acc1", q)
        still = store.read_action("acc1", created["id"])
        self.assertEqual(still["overrides"]["source"], "hashtag")

    def test_two_tasks_from_same_preset_stay_independent(self):
        created = store.create_action("acc1", {
            "name": "Szukaj",
            "plugin": "content_search",
            "overrides": {"source": "hashtag"},
        })
        saved = store.read_action("acc1", created["id"])
        t1 = store.action_to_task(saved)
        t2 = store.action_to_task(saved)
        t1["overrides"]["source"] = "keyword"
        t2["overrides"]["source"] = "location"
        store.write_queue("acc1", {"queue": [t1, t2]})
        q = store.read_queue("acc1")
        self.assertEqual(q["queue"][0]["overrides"]["source"], "keyword")
        self.assertEqual(q["queue"][1]["overrides"]["source"], "location")
        self.assertEqual(
            store.read_action("acc1", created["id"])["overrides"]["source"],
            "hashtag")

    def test_update_name_comment_keeps_overrides(self):
        created = store.create_action("acc1", {
            "name": "Stara",
            "comment": "a",
            "plugin": "content_search",
            "overrides": {"source": "hashtag"},
        })
        store.update_action("acc1", created["id"], {
            "name": "Nowa", "comment": "b",
        })
        got = store.read_action("acc1", created["id"])
        self.assertEqual(got["name"], "Nowa")
        self.assertEqual(got["comment"], "b")
        self.assertEqual(got["overrides"]["source"], "hashtag")


class SavedActionApiTests(unittest.TestCase):
    """CRUD przez `/api/accounts/{acc}/actions` (TestClient)."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        _write_account(root)
        self._orig_root = store.CONFIG_ROOT
        store.CONFIG_ROOT = root
        self.cfg_patch = patch("v2.panel.server.CONFIG_ROOT", root)
        self.cfg_patch.start()
        self.addCleanup(self.cfg_patch.stop)
        self.addCleanup(self._tmp.cleanup)
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        self.client = TestClient(app)

    def tearDown(self):
        store.CONFIG_ROOT = self._orig_root

    def test_list_create_delete(self):
        acc = "acc1"
        r0 = self.client.get(f"/api/accounts/{acc}/actions")
        self.assertEqual(r0.status_code, 200)
        self.assertEqual(r0.json(), [])

        r = self.client.post(f"/api/accounts/{acc}/actions", json={
            "name": "Hashtagi + harvest",
            "comment": "tylko #",
            "plugin": "content_search",
            "overrides": {"source": "hashtag", "run_scope": "phrases"},
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        slug = body["id"]
        self.assertEqual(slug, "hashtagi-harvest")

        listed = self.client.get(f"/api/accounts/{acc}/actions").json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["comment"], "tylko #")

        one = self.client.get(f"/api/accounts/{acc}/actions/{slug}")
        self.assertEqual(one.status_code, 200)
        self.assertEqual(one.json()["overrides"]["source"], "hashtag")

        # wstawienie do runu przez API kolejki — kopia overrides
        task = store.action_to_task(one.json())
        q = self.client.post(
            f"/api/queue/{acc}?name=default",
            json={"queue": [task]},
        )
        self.assertEqual(q.status_code, 200, q.text)
        saved_q = self.client.get(f"/api/queue/{acc}?name=default").json()
        self.assertEqual(saved_q["queue"][0]["overrides"]["source"], "hashtag")
        saved_q["queue"][0]["overrides"]["source"] = "keyword"
        self.client.post(f"/api/queue/{acc}?name=default", json=saved_q)
        still = self.client.get(f"/api/accounts/{acc}/actions/{slug}").json()
        self.assertEqual(still["overrides"]["source"], "hashtag")

        gone = self.client.delete(f"/api/accounts/{acc}/actions/{slug}")
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(self.client.get(f"/api/accounts/{acc}/actions").json(), [])

    def test_unknown_plugin_400_and_missing_404(self):
        r = self.client.post("/api/accounts/acc1/actions", json={
            "name": "x", "plugin": "nie_ma", "overrides": {},
        })
        self.assertEqual(r.status_code, 400)
        miss = self.client.get("/api/accounts/acc1/actions/nie-ma")
        self.assertEqual(miss.status_code, 404)
        miss_acc = self.client.get("/api/accounts/nieistnieje/actions")
        self.assertEqual(miss_acc.status_code, 404)

    def test_duplicate_id_409(self):
        payload = {
            "id": "duplikat",
            "name": "A",
            "plugin": "feed_browser",
            "overrides": {},
        }
        self.assertEqual(
            self.client.post("/api/accounts/acc1/actions", json=payload).status_code,
            200)
        r2 = self.client.post("/api/accounts/acc1/actions", json=payload)
        self.assertEqual(r2.status_code, 409)


if __name__ == "__main__":
    unittest.main()
