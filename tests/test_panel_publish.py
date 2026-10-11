"""API Treści: GET lista, POST bez konta → 400, push z mockiem ADB."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))


def _write_device(config: Path, alias: str, serial: str) -> None:
    devices = config / "devices"
    devices.mkdir(parents=True, exist_ok=True)
    (devices / f"{alias}.json").write_text(json.dumps({
        "alias": alias,
        "adb_id": serial,
        "model": "SM_FAKE",
        "android_version": "11",
        "resolution": [1080, 2340],
        "dpi": 420,
        "uiautodev": {"base_url": "http://127.0.0.1:20242"},
        "scrcpy": {"port": 27183, "max_size": 900, "max_fps": 15},
    }), encoding="utf-8")


def _write_account(config: Path, alias: str, device_alias: str) -> None:
    acc = config / "accounts"
    acc.mkdir(parents=True, exist_ok=True)
    (acc / f"{alias}.json").write_text(json.dumps({
        "device": {"device_alias": device_alias,
                   "app_clone_package": "com.instagram.androip"},
    }), encoding="utf-8")


class PublishApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config"
        self.data = self.root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        _write_device(self.config, "phone_A", "SERIAL_A")
        _write_account(self.config, "jobsaver", "phone_A")
        _write_account(self.config, "acc_b", "phone_A")
        self.cfg_patch = patch("v2.panel.server.CONFIG_ROOT", self.config)
        self.data_patch = patch("v2.panel.server.DATA_ROOT", self.data)
        self.adb_patch = patch(
            "v2.panel.server.list_adb_devices",
            return_value=[{"serial": "SERIAL_A", "state": "device"}],
        )
        self.cfg_patch.start()
        self.data_patch.start()
        self.adb_patch.start()
        self.addCleanup(self.cfg_patch.stop)
        self.addCleanup(self.data_patch.stop)
        self.addCleanup(self.adb_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        self.client = TestClient(app)

    def test_get_pusta_lista(self):
        r = self.client.get("/api/publish")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["items"], [])

    def test_post_bez_konta_400(self):
        r = self.client.post("/api/publish", json={"kind": "post"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("account", (r.json().get("detail") or "").lower())

    def test_post_multipart_bez_konta_400(self):
        r = self.client.post(
            "/api/publish",
            data={"kind": "post"},
            files={"file": ("x.jpg", b"abc", "image/jpeg")},
        )
        self.assertEqual(r.status_code, 400)

    def test_post_z_kontem_i_lista(self):
        r = self.client.post(
            "/api/publish",
            data={"account": "jobsaver", "kind": "post", "caption": "hej"},
            files={"file": ("sunset.jpg", b"\xff\xd8xx", "image/jpeg")},
        )
        self.assertEqual(r.status_code, 200, r.text)
        rec = r.json()
        self.assertEqual(rec["account"], "jobsaver")
        self.assertEqual(rec["kind"], "post")
        self.assertEqual(rec["status"], "ready")
        self.assertIsNone(rec.get("phone_path"))
        cname = rec["files"][0]["canonical_name"]
        self.assertTrue(cname.startswith("jobsaver__"))
        self.assertTrue(cname.endswith(".jpg"))
        self.assertIn(rec["id"], cname)
        listed = self.client.get("/api/publish", params={"account": "jobsaver"})
        self.assertEqual(len(listed.json()["items"]), 1)
        mixed = self.client.get("/api/publish", params={"device": "phone_A"})
        self.assertEqual(len(mixed.json()["items"]), 1)

    def test_push_mock_adb_dozwolony_w_sesji(self):
        created = self.client.post(
            "/api/publish",
            data={"account": "jobsaver", "kind": "story"},
            files={"file": ("rel.jpg", b"img", "image/jpeg")},
        ).json()
        iid = created["id"]
        calls = []

        def fake_adb(args, timeout=30.0):
            calls.append(list(args))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        # Sesja na tym serialu NIE blokuje pusha (to tylko pliki).
        with patch("v2.panel.server.SESSION") as sess, \
             patch("v2.data.publish_store._run_adb", side_effect=fake_adb):
            sess.is_running_on.return_value = True
            r = self.client.post(f"/api/publish/jobsaver/{iid}/push")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "on_device")
        self.assertIn("/sdcard/Pictures/bot_v2/jobsaver/story/", r.json()["phone_path"])
        self.assertTrue(any("push" in c for c in [" ".join(x) for x in calls]))
        self.assertTrue(any("MEDIA_SCANNER" in " ".join(x) for x in calls))

    def test_delete_i_patch(self):
        rec = self.client.post(
            "/api/publish",
            data={"account": "jobsaver", "kind": "post"},
            files={"file": ("a.jpg", b"z", "image/jpeg")},
        ).json()
        iid = rec["id"]
        p = self.client.patch(
            f"/api/publish/jobsaver/{iid}",
            json={"caption": "nowy"},
        )
        self.assertEqual(p.status_code, 200)
        self.assertEqual(p.json()["caption"], "nowy")
        d = self.client.delete(f"/api/publish/jobsaver/{iid}")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(self.client.get("/api/publish").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
