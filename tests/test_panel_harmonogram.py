"""PATCH scheduler_enabled + lock bloku — bez ADB, bez drugiego plannera."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))


def _write_device(root: Path, alias: str, *, scheduler_enabled: bool = True) -> None:
    devices = root / "devices"
    devices.mkdir(parents=True, exist_ok=True)
    (devices / f"{alias}.json").write_text(json.dumps({
        "alias": alias,
        "adb_id": "SERIAL_" + alias,
        "scheduler_enabled": scheduler_enabled,
        "clones": [],
    }, indent=2) + "\n", encoding="utf-8")


class DeviceSchedulerPatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _write_device(self.root, "phone_A", scheduler_enabled=True)
        self.cfg_patch = patch("v2.panel.server.CONFIG_ROOT", self.root)
        self.cfg_patch.start()
        self.addCleanup(self.cfg_patch.stop)
        self.addCleanup(self._tmp.cleanup)
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        self.client = TestClient(app)

    def test_patch_scheduler_enabled_nie_kasuje_reszty(self):
        r = self.client.patch("/api/devices/phone_A", json={"scheduler_enabled": False})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["scheduler_enabled"])
        doc = json.loads((self.root / "devices" / "phone_A.json").read_text(encoding="utf-8"))
        self.assertFalse(doc["scheduler_enabled"])
        self.assertEqual(doc["adb_id"], "SERIAL_phone_A")

    def test_patch_nieznane_pole_to_400(self):
        r = self.client.patch("/api/devices/phone_A", json={"model": "x"})
        self.assertEqual(r.status_code, 400)

    def test_list_devices_oddaje_scheduler_enabled(self):
        _write_device(self.root, "galaxy", scheduler_enabled=False)
        with patch("v2.panel.server.list_adb_devices", return_value=[]):
            rows = self.client.get("/api/devices").json()
        by_alias = {d["alias"]: d["scheduler_enabled"] for d in rows}
        self.assertTrue(by_alias["phone_A"])
        self.assertFalse(by_alias["galaxy"])


if __name__ == "__main__":
    unittest.main()
