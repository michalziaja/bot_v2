"""SessionManager per device — dwa Popen, stop A nie gasi B. Bez ADB."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.panel.server import SessionManager  # noqa: E402


def _write_fleet(root: Path) -> None:
    """Dwa telefony + dwa konta — minimalny config bez hardware."""
    accounts = root / "accounts"
    devices = root / "devices"
    accounts.mkdir(parents=True)
    devices.mkdir(parents=True)
    (devices / "phone_A.json").write_text(json.dumps({
        "alias": "phone_A", "adb_id": "SERIAL_A",
    }), encoding="utf-8")
    (devices / "galaxy.json").write_text(json.dumps({
        "alias": "galaxy", "adb_id": "SERIAL_B",
    }), encoding="utf-8")
    (accounts / "jobsaver.json").write_text(json.dumps({
        "device": {"device_alias": "phone_A"},
    }), encoding="utf-8")
    (accounts / "kasia.json").write_text(json.dumps({
        "device": {"device_alias": "galaxy"},
    }), encoding="utf-8")


def _alive_proc(pid: int) -> MagicMock:
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = pid
    proc.returncode = None
    return proc


class SessionManagerFleetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config = self.root / "config"
        self.data = self.root / "data"
        _write_fleet(self.config)
        self.mgr = SessionManager(config_root=self.config, data_root=self.data)
        self._pid = 4000
        self.addCleanup(self._tmp.cleanup)

    def _next_proc(self, *args, **kwargs):
        self._pid += 1
        return _alive_proc(self._pid)

    def _online(self, serials=("SERIAL_A", "SERIAL_B")):
        return [
            {"serial": s, "state": "device"} for s in serials
        ]

    def _start_both(self, mock_popen, mock_adb):
        mock_adb.return_value = self._online()
        mock_popen.side_effect = self._next_proc
        self.mgr.start("jobsaver", mode="queue", queue_name="default")
        self.mgr.start("kasia", mode="queue", queue_name="default")

    @patch("v2.panel.server.list_adb_devices")
    @patch("v2.panel.server.subprocess.Popen")
    def test_dwa_sloty_dwa_popen(self, mock_popen, mock_adb):
        self._start_both(mock_popen, mock_adb)
        st = self.mgr.status()
        self.assertTrue(st["running"])
        self.assertTrue(st["running_any"])
        self.assertIsInstance(st["running"], bool)
        aliases = {s["device_alias"] for s in st["sessions"]}
        self.assertEqual(aliases, {"phone_A", "galaxy"})
        self.assertEqual(len(st["sessions"]), 2)
        self.assertEqual(mock_popen.call_count, 2)
        self.assertTrue(self.mgr.is_running_on("phone_A"))
        self.assertTrue(self.mgr.is_running_on("galaxy"))

    @patch("v2.panel.server.list_adb_devices")
    @patch("v2.panel.server.subprocess.Popen")
    def test_start_tego_samego_urzadzenia_409(self, mock_popen, mock_adb):
        mock_adb.return_value = self._online()
        mock_popen.side_effect = self._next_proc
        self.mgr.start("jobsaver", mode="queue")
        with self.assertRaises(RuntimeError) as ctx:
            self.mgr.start("jobsaver", mode="queue")
        self.assertIn("phone_A", str(ctx.exception))

    @patch("v2.panel.server.list_adb_devices")
    @patch("v2.panel.server.subprocess.Popen")
    def test_stop_a_nie_gasi_b(self, mock_popen, mock_adb):
        self._start_both(mock_popen, mock_adb)
        slot_a = self.mgr.slot_for_device("phone_A")
        slot_b = self.mgr.slot_for_device("galaxy")
        self.assertIsNotNone(slot_a)
        self.assertIsNotNone(slot_b)
        self.mgr.stop(device_alias="phone_A", grace_s=0)
        # A dostał STOP / terminate; B nadal żywy i nietknięty.
        slot_b.proc.terminate.assert_not_called()
        slot_b.proc.kill.assert_not_called()
        self.assertTrue(slot_b.is_alive())
        self.assertTrue(self.mgr.is_running_on("galaxy"))
        st = self.mgr.status()
        by_alias = {s["device_alias"]: s for s in st["sessions"]}
        self.assertTrue(by_alias["galaxy"]["running"])

    @patch("v2.panel.server.list_adb_devices")
    @patch("v2.panel.server.subprocess.Popen")
    def test_stop_po_account_celuje_w_slot(self, mock_popen, mock_adb):
        self._start_both(mock_popen, mock_adb)
        self.mgr.stop(account="kasia", grace_s=0)
        self.assertTrue(self.mgr.is_running_on("phone_A"))
        galaxy = self.mgr.slot_for_device("galaxy")
        self.assertIsNotNone(galaxy)
        self.assertIsNotNone(galaxy.stopping_since)

    @patch("v2.panel.server.list_adb_devices")
    @patch("v2.panel.server.subprocess.Popen")
    def test_stop_bez_body_gasi_focus_nie_flote(self, mock_popen, mock_adb):
        self._start_both(mock_popen, mock_adb)
        # Ostatni start = kasia/galaxy — focus, nie cała flota.
        self.mgr.stop(grace_s=0)
        self.assertTrue(self.mgr.is_running_on("phone_A"))
        self.assertFalse(
            self.mgr.slot_for_device("galaxy").stopping_since is None
        )

    @patch("v2.panel.server.list_adb_devices")
    @patch("v2.panel.server.subprocess.Popen")
    def test_start_calibrate_odpala_cli(self, mock_popen, mock_adb):
        # Komentarz: panel Flota → cli calibrate --account=
        mock_adb.return_value = self._online()
        mock_popen.side_effect = self._next_proc
        self.mgr.start("jobsaver", mode="calibrate")
        self.assertTrue(self.mgr.is_running_on("phone_A"))
        cmd = mock_popen.call_args[0][0]
        self.assertIn("calibrate", cmd)
        self.assertIn("--account=jobsaver", cmd)

    @patch("v2.panel.server.list_adb_devices")
    @patch("v2.panel.server.subprocess.Popen")
    def test_brak_adb_to_runtime_error(self, mock_popen, mock_adb):
        mock_adb.return_value = []
        with self.assertRaises(RuntimeError) as ctx:
            self.mgr.start("jobsaver", mode="queue")
        self.assertIn("ADB", str(ctx.exception))
        mock_popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
