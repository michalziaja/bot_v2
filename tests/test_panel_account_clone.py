"""DELETE konta zdejmuje klona; POST konta bez loginu IG; preview APPID."""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.config.schema import CONFIG_ROOT as REAL_CONFIG  # noqa: E402


def _write_device(config: Path, alias: str, **extra) -> None:
    devices = config / "devices"
    devices.mkdir(parents=True, exist_ok=True)
    doc = {
        "alias": alias,
        "adb_id": extra.get("adb_id", f"SERIAL_{alias}"),
        "model": extra.get("model", "SM_FAKE"),
        "product": extra.get("product", "fake"),
        "manufacturer": extra.get("manufacturer", "test"),
        "android_version": extra.get("android_version", "11"),
        "resolution": extra.get("resolution", [1080, 2340]),
        "dpi": extra.get("dpi", 420),
        "clones": extra.get("clones") or [
            {"slot": 1, "package": "com.instagram.android", "note": "orig"},
        ],
        "uiautodev": {"base_url": "http://127.0.0.1:20242"},
        "scrcpy": {"port": 27183, "max_size": 900, "max_fps": 15},
        "scheduler_enabled": True,
    }
    (devices / f"{alias}.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")


def _write_account(config: Path, alias: str, device_alias: str, package: str) -> None:
    acc = config / "accounts"
    acc.mkdir(parents=True, exist_ok=True)
    (acc / f"{alias}.json").write_text(json.dumps({
        "device": {"device_alias": device_alias, "app_clone_package": package},
    }), encoding="utf-8")


class AccountCloneApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config"
        self.data = self.root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        tmpl = self.config / "_template"
        tmpl.mkdir(parents=True)
        shutil.copy(REAL_CONFIG / "_template" / "account.json",
                    tmpl / "account.json")
        _write_device(
            self.config, "alpha",
            adb_id="SER_A",
            clones=[
                {"slot": 1, "package": "com.instagram.android"},
                {"slot": 2, "package": "com.instagram.androip"},
            ],
        )
        _write_account(self.config, "klon_a", "alpha", "com.instagram.androip")
        _write_account(self.config, "stock_a", "alpha", "com.instagram.android")
        self.cfg_patch = patch("v2.panel.server.CONFIG_ROOT", self.config)
        self.data_patch = patch("v2.panel.server.DATA_ROOT", self.data)
        self.cred_patch = patch("v2.config.credentials.set")
        self.cfg_patch.start()
        self.data_patch.start()
        self.cred_patch.start()
        self.addCleanup(self.cfg_patch.stop)
        self.addCleanup(self.data_patch.stop)
        self.addCleanup(self.cred_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        self.client = TestClient(app)

    def test_delete_uninstalls_clone_not_stock(self):
        with patch("v2.cloner.job.uninstall_apk", return_value="Success") as un:
            r = self.client.delete("/api/accounts/klon_a")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        clone = body["clone"]
        self.assertTrue(clone["uninstalled"])
        self.assertTrue(clone["dropped"])
        self.assertEqual(clone["package"], "com.instagram.androip")
        un.assert_called_once()
        self.assertEqual(un.call_args.args[1], "com.instagram.androip")
        self.assertFalse((self.config / "accounts" / "klon_a.json").exists())
        dev = json.loads(
            (self.config / "devices" / "alpha.json").read_text(encoding="utf-8"))
        pkgs = [c["package"] for c in dev["clones"]]
        self.assertNotIn("com.instagram.androip", pkgs)
        self.assertIn("com.instagram.android", pkgs)

        with patch("v2.cloner.job.uninstall_apk") as un2:
            r2 = self.client.delete("/api/accounts/stock_a")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["clone"]["skipped"], "stock_or_foreign")
        un2.assert_not_called()

    def test_delete_skips_uninstall_when_package_shared(self):
        _write_account(self.config, "klon_b", "alpha", "com.instagram.androip")
        with patch("v2.cloner.job.uninstall_apk") as un:
            r = self.client.delete("/api/accounts/klon_a")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["clone"]["skipped"], "shared")
        un.assert_not_called()
        dev = json.loads(
            (self.config / "devices" / "alpha.json").read_text(encoding="utf-8"))
        pkgs = [c["package"] for c in dev["clones"]]
        self.assertIn("com.instagram.androip", pkgs)

    def test_delete_keeps_account_gone_when_adb_fails(self):
        from v2.cloner.job import CloneJobError
        with patch("v2.cloner.job.uninstall_apk",
                   side_effect=CloneJobError("adb down")):
            r = self.client.delete("/api/accounts/klon_a")
        self.assertEqual(r.status_code, 200)
        self.assertFalse((self.config / "accounts" / "klon_a.json").exists())
        self.assertIn("adb down", r.json()["clone"]["error"])

    def test_create_account_ig_username_optional(self):
        r = self.client.post("/api/accounts", json={
            "alias": "nowy",
            "device_alias": "alpha",
            "app_clone_package": "com.instagram.androia",
        })
        self.assertEqual(r.status_code, 200, r.text)
        cfg = json.loads(
            (self.config / "accounts" / "nowy.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["meta"]["ig_username"], "nowy")
        self.assertEqual(cfg["device"]["app_clone_package"],
                         "com.instagram.androia")

    def test_preview_returns_free_packages(self):
        with patch("v2.cloner.job.list_installed_packages", return_value=set()):
            r = self.client.post("/api/clones/preview",
                                 json={"device_alias": "alpha"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d["identity"]["package"].startswith("com.instagram.androi"))
        self.assertNotEqual(d["identity"]["package"], "com.instagram.android")
        self.assertIn("com.instagram.androia", d["suggested_packages"])
        self.assertNotIn("com.instagram.android", d["suggested_packages"])

    def test_preview_succeeds_without_adb_or_java(self):
        """Tożsamość z JSON urządzenia — bez pm list, JDK i appclonera."""
        from v2.cloner.job import CloneJobError as _CJE
        with patch("v2.cloner.job.list_installed_packages",
                   side_effect=OSError("brak adb")):
            with patch("v2.cloner.job.require_jdk",
                       side_effect=_CJE("brak JDK")):
                with patch("v2.cloner.job.appcloner_root",
                           side_effect=_CJE("brak appcloner")):
                    r = self.client.post("/api/clones/preview",
                                         json={"device_alias": "alpha"})
        self.assertEqual(r.status_code, 200, r.text)
        ident = r.json()["identity"]
        for key in ("mac", "imei", "android_id", "fingerprint", "package",
                    "model", "gaid", "media_drm_id", "build_time", "boot_time"):
            self.assertTrue(ident.get(key), key)
        self.assertIsNotNone(ident.get("gps_lat"))
        self.assertEqual(ident.get("sim_country"), "PL")
        self.assertTrue(r.json()["suggested_packages"])

    def test_preview_unknown_device_404(self):
        r = self.client.post("/api/clones/preview",
                             json={"device_alias": "nie_ma"})
        self.assertEqual(r.status_code, 404)

    def test_avatar_upload_and_status(self):
        import base64
        raw = b"\xff\xd8jpeg-bytes"
        r0 = self.client.get("/api/accounts/klon_a/avatar")
        self.assertEqual(r0.status_code, 200)
        self.assertFalse(r0.json()["has_avatar"])
        r = self.client.post("/api/accounts/klon_a/avatar", json={
            "filename": "twarz.png",
            "file_b64": base64.b64encode(raw).decode("ascii"),
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["has_avatar"])
        self.assertEqual(r.json()["filename"], "avatar.png")
        r2 = self.client.get("/api/accounts/klon_a/avatar")
        self.assertTrue(r2.json()["has_avatar"])

    def test_profile_bio_and_apply_requires_content(self):
        r0 = self.client.get("/api/accounts/klon_a/profile")
        self.assertEqual(r0.status_code, 200)
        self.assertEqual(r0.json()["bio"], "")
        self.assertFalse(r0.json()["has_avatar"])
        empty = self.client.post("/api/accounts/klon_a/profile/apply")
        self.assertEqual(empty.status_code, 400)
        r = self.client.post("/api/accounts/klon_a/profile", json={"bio": "  hej  swiecie "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["bio"], "hej swiecie")
        r2 = self.client.get("/api/accounts/klon_a/profile")
        self.assertEqual(r2.json()["bio"], "hej swiecie")


if __name__ == "__main__":
    unittest.main()
