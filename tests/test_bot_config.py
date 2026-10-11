"""GET/POST /api/bot-config — merge globali z override konta."""
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

from v2.config.schema import CONFIG_ROOT, load_account


def _write_account(root: Path, alias: str, **sections):
    data = {
        "meta": {"account_id": alias, "ig_username": alias},
        "device": {"device_alias": "phone_A"},
    }
    data.update(sections)
    (root / "accounts").mkdir(parents=True, exist_ok=True)
    (root / "accounts" / f"{alias}.json").write_text(
        json.dumps(data), encoding="utf-8")


class BotConfigApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "accounts").mkdir()
        (root / "devices").mkdir()
        shutil.copy(CONFIG_ROOT / "devices" / "phone_A.json",
                    root / "devices" / "phone_A.json")
        (root / "bot.json").write_text(json.dumps({
            "limits": {"daily_likes": 100, "daily_follows": 30},
            "humanizer": {"profile_name": "default", "speed": 1.0},
        }), encoding="utf-8")
        _write_account(root, "inh", limits={})
        _write_account(root, "ovr", limits={"daily_likes": 50})
        self.root = root
        self.cfg_patch = patch("v2.panel.server.CONFIG_ROOT", root)
        self.cfg_patch.start()
        self.addCleanup(self.cfg_patch.stop)
        self.addCleanup(self._tmp.cleanup)
        from fastapi.testclient import TestClient
        from v2.panel.server import app
        self.client = TestClient(app)

    def test_get_zwraca_effective(self):
        r = self.client.get("/api/bot-config")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["limits"]["daily_likes"], 100)
        self.assertIn("humanizer", body)
        self.assertIn("toggles", body)

    def test_post_80_dziedziczy_override_zostaje(self):
        """Kryterium kroku 11: global 80 → konto bez klucza 80, konto z 50 = 50."""
        r = self.client.post("/api/bot-config", json={"limits": {"daily_likes": 80}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["limits"]["daily_likes"], 80)
        self.assertEqual(
            load_account("inh", config_root=self.root).limits["daily_likes"], 80)
        self.assertEqual(
            load_account("ovr", config_root=self.root).limits["daily_likes"], 50)

    def test_post_odrzuca_smieci(self):
        bad = self.client.post("/api/bot-config", json={"limits": {"daily_likes": -3}})
        self.assertEqual(bad.status_code, 400)
        empty = self.client.post("/api/bot-config", json={"foo": 1})
        self.assertEqual(empty.status_code, 400)
        # plik nie ruszył się po odrzuceniu
        still = self.client.get("/api/bot-config").json()
        self.assertEqual(still["limits"]["daily_likes"], 100)


if __name__ == "__main__":
    unittest.main()
