"""GET /api/devices — pola Floty (lock, scrcpy, kalibracja) + PATCH + hydrate deny."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.device_lock import DeviceLock  # noqa: E402


def _write_device(config: Path, alias: str, **extra) -> None:
    """Kompletny JSON — żeby list_devices nie wołał ADB (brak hardware_gaps)."""
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
        "uiautodev": extra.get("uiautodev") or {"base_url": "http://127.0.0.1:20242"},
        "scrcpy": extra.get("scrcpy") or {"port": 27183, "max_size": 900, "max_fps": 15},
        "scheduler_enabled": extra.get("scheduler_enabled", True),
    }
    (devices / f"{alias}.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8",
    )


def _write_account(config: Path, alias: str, device_alias: str, package: str) -> None:
    acc = config / "accounts"
    acc.mkdir(parents=True, exist_ok=True)
    (acc / f"{alias}.json").write_text(json.dumps({
        "device": {"device_alias": device_alias, "app_clone_package": package},
    }), encoding="utf-8")


class DevicesFleetApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config"
        self.data = self.root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        _write_device(
            self.config, "alpha",
            adb_id="SER_A",
            model="SM_A505FN",
            scrcpy={"port": 27183, "max_size": 900, "max_fps": 15},
            clones=[
                {"slot": 1, "package": "com.instagram.android"},
                {"slot": 2, "package": "com.instagram.androip"},
            ],
        )
        _write_device(
            self.config, "beta",
            adb_id="SER_B",
            model="NO_SUCH_CALIB",
            android_version="8.0.0",
            resolution=[1080, 1920],
            scrcpy={"port": 27184, "max_size": 900, "max_fps": 15},
            scheduler_enabled=False,
        )
        _write_account(self.config, "acc_a", "alpha", "com.instagram.android")
        _write_account(self.config, "acc_b", "beta", "com.instagram.android")
        self.cfg_patch = patch("v2.panel.server.CONFIG_ROOT", self.config)
        self.data_patch = patch("v2.panel.server.DATA_ROOT", self.data)
        self.adb_patch = patch(
            "v2.panel.server.list_adb_devices",
            return_value=[{"serial": "SER_A", "state": "device"}],
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

    def test_get_devices_pola_floty(self):
        r = self.client.get("/api/devices")
        self.assertEqual(r.status_code, 200)
        by = {d["alias"]: d for d in r.json()}
        self.assertIn("alpha", by)
        self.assertIn("beta", by)
        a = by["alpha"]
        self.assertTrue(a["online"])
        self.assertTrue(a["connected"])
        self.assertEqual(a["scrcpy_port"], 27183)
        self.assertEqual(a["scrcpy_max_fps"], 15)
        self.assertEqual(a["scrcpy_max_size"], 900)
        self.assertEqual(a["android_version"], "11")
        self.assertEqual(a["resolution"], [1080, 2340])
        self.assertEqual(a["adb_id"], "SER_A")
        self.assertTrue(a["scheduler_enabled"])
        self.assertFalse(a["locked"])
        self.assertIsNone(a["claimed_by"])
        self.assertIsNone(a["lock"])
        self.assertEqual(a["accounts"], ["acc_a"])
        self.assertEqual((a.get("calibrate") or {}).get("status"), "idle")
        self.assertTrue(a["calibration_exists"])
        self.assertEqual(a["calibration_file"], "SM_A505FN_draft.json")
        self.assertEqual(a["uiautodev_port"], 20242)
        clones = {c["package"]: c.get("claimed_by") for c in a["clones"]}
        self.assertEqual(clones["com.instagram.android"], "acc_a")
        self.assertIsNone(clones.get("com.instagram.androip"))

        b = by["beta"]
        self.assertFalse(b["online"])
        self.assertEqual(b["scrcpy_port"], 27184)
        self.assertEqual(b["scrcpy_max_fps"], 15)
        self.assertFalse(b["scheduler_enabled"])
        self.assertFalse(b["calibration_exists"])
        self.assertIsNone(b["calibration_file"])
        self.assertEqual(b["accounts"], ["acc_b"])

    def test_get_devices_lock_z_flocka(self):
        lock = DeviceLock(self.data, "SER_A")
        lock.acquire(owner="acc_a", session_id="sid-1")
        self.addCleanup(lock.release)
        r = self.client.get("/api/devices")
        a = {d["alias"]: d for d in r.json()}["alpha"]
        self.assertTrue(a["locked"])
        self.assertEqual(a["claimed_by"], "acc_a")
        self.assertEqual((a["lock"] or {}).get("session_id"), "sid-1")

    def test_patch_scheduler_enabled(self):
        r = self.client.patch(
            "/api/devices/beta",
            json={"scheduler_enabled": True},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["scheduler_enabled"])
        doc = json.loads(
            (self.config / "devices" / "beta.json").read_text(encoding="utf-8")
        )
        self.assertTrue(doc["scheduler_enabled"])

    def test_patch_scrcpy_max_fps(self):
        r = self.client.patch(
            "/api/devices/alpha",
            json={"scrcpy_max_fps": 30},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["scrcpy_max_fps"], 30)
        doc = json.loads(
            (self.config / "devices" / "alpha.json").read_text(encoding="utf-8")
        )
        self.assertEqual(doc["scrcpy"]["max_fps"], 30)
        self.assertEqual(doc["scrcpy"]["port"], 27183)
        self.assertEqual(doc["scrcpy"]["max_size"], 900)
        listed = {d["alias"]: d for d in self.client.get("/api/devices").json()}
        self.assertEqual(listed["alpha"]["scrcpy_max_fps"], 30)

    def test_patch_scrcpy_max_fps_klamra(self):
        hi = self.client.patch("/api/devices/alpha", json={"scrcpy_max_fps": 99})
        self.assertEqual(hi.status_code, 200)
        self.assertEqual(hi.json()["scrcpy_max_fps"], 60)
        lo = self.client.patch("/api/devices/alpha", json={"scrcpy_max_fps": 0})
        self.assertEqual(lo.status_code, 200)
        self.assertEqual(lo.json()["scrcpy_max_fps"], 1)
        trash = self.client.patch(
            "/api/devices/alpha", json={"scrcpy_max_fps": "abc"},
        )
        self.assertEqual(trash.status_code, 200)
        self.assertEqual(trash.json()["scrcpy_max_fps"], 15)

    def test_js_fps_nazywo_nie_flota(self):
        """Edycja FPS kanonicznie na Na żywo; Flota tylko odczyt."""
        js = (ROOT / "panel" / "static" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "panel" / "static" / "app.html").read_text(encoding="utf-8")
        self.assertNotIn("flota-fps-input", js)
        self.assertNotIn("function setFlotaScrcpyFps", js)
        self.assertIn("zmień na Na żywo", js)
        self.assertIn("scrcpy_max_fps", js)
        self.assertIn("function setLiveScrcpyFps", js)
        self.assertIn('id="live-fps-input"', html)
        self.assertIn("podgląd panelu: od razu", html)

    def test_hydrate_409_tylko_ten_serial(self):
        """Sesja na alpha nie blokuje hydrate beta."""
        with patch("v2.panel.server.SESSION.is_running_on",
                   side_effect=lambda alias: alias == "alpha"), \
             patch("v2.panel.server.hydrate_device_file") as hyd:
            hyd.return_value = {
                "changed": False, "probed": {}, "doc": {}, "error": None,
            }
            blocked = self.client.post("/api/devices/alpha/hydrate")
            self.assertEqual(blocked.status_code, 409)
            ok = self.client.post("/api/devices/beta/hydrate")
            self.assertEqual(ok.status_code, 200)
            hyd.assert_called_once()

    def test_calibrate_start_pierwsze_konto(self):
        with patch("v2.panel.server.SESSION.start") as start:
            start.return_value = {"running": True, "sessions": []}
            r = self.client.post("/api/devices/alpha/calibrate")
            self.assertEqual(r.status_code, 200)
            start.assert_called_once()
            args, kwargs = start.call_args
            self.assertEqual(args[0], "acc_a")
            self.assertEqual(kwargs.get("mode"), "calibrate")
        st = self.client.get("/api/devices/alpha/calibrate")
        self.assertEqual(st.status_code, 200)
        self.assertEqual(st.json()["status"], "running")
        self.assertEqual(st.json()["account"], "acc_a")

    def test_calibrate_400_bez_konta(self):
        _write_device(self.config, "gamma", adb_id="SER_G", model="X")
        r = self.client.post("/api/devices/gamma/calibrate")
        self.assertEqual(r.status_code, 400)
        self.assertIn("konta", r.json()["detail"])

    def test_calibrate_409_gdy_serial_zajety(self):
        with patch("v2.panel.server.SESSION.start",
                   side_effect=RuntimeError("sesja już działa na alpha")):
            r = self.client.post("/api/devices/alpha/calibrate")
        self.assertEqual(r.status_code, 409)

    def test_calibrate_reply_i_shot(self):
        from v2.core import calibrate_state
        dest = self.data / "devices" / "beta" / "calibrate.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\xff\xd8\xff")
        calibrate_state.save(self.data, "beta", {
            "status": "needs_agent",
            "step": "ig",
            "question": "co to?",
            "shot_path": str(dest),
            "account": "acc_b",
        })
        listed = {d["alias"]: d for d in self.client.get("/api/devices").json()}
        self.assertEqual(listed["beta"]["calibrate"]["status"], "needs_agent")
        self.assertTrue(listed["beta"]["calibrate"]["has_shot"])
        shot = self.client.get("/api/devices/beta/calibrate/shot")
        self.assertEqual(shot.status_code, 200)
        self.assertIn("image/jpeg", shot.headers.get("content-type", ""))
        bad = self.client.post(
            "/api/devices/beta/calibrate/reply", json={"action": "maybe"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(
            "/api/devices/beta/calibrate/reply", json={"action": "ok"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["status"], "running")

    def test_js_calib_zawsze_nie_tylko_gdy_mapa(self):
        js = (ROOT / "panel" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function startFlotaCalib", js)
        self.assertIn("/calibrate/reply", js)
        self.assertNotIn("diagnostyka ekranu zostaje w klasycznym", js)


if __name__ == "__main__":
    unittest.main()
