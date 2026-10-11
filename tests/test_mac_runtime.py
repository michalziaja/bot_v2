"""Apply / restore MAC — mock su_shell, bez telefonu."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.cloner import mac_runtime
from v2.runner.step import StepResult
from v2.steps.system.apply_clone_identity import ApplyCloneIdentity
from v2.steps.system.restore_clone_identity import RestoreCloneIdentity


class FakeSuDevice:
    def __init__(self, mac="aa:aa:aa:aa:aa:aa"):
        self.serial = "SERIAL1"
        self.mac = mac
        self.wifi = "1"
        self.calls: list[str] = []

    def shell(self, command: str) -> str:
        self.calls.append(command)
        if "wifi_on" in command:
            return self.wifi
        if command.startswith("su -c"):
            return self.su_shell(command)
        return ""

    def su_shell(self, command: str) -> str:
        self.calls.append("SU:" + command)
        if "cat /sys/class/net/" in command and "address" in command:
            return self.mac
        if "ip link set" in command and "address" in command:
            # ... address xx:xx:...
            parts = command.split()
            if "address" in parts:
                self.mac = parts[parts.index("address") + 1]
            return ""
        return ""


class FakeAccount:
    def __init__(self, mac="02:00:00:00:00:01"):
        self.account_id = "acc1"
        self.device = {"spoofed_mac": mac}

    def step_enabled(self, name: str) -> bool:
        return True


class FakeCtx:
    def __init__(self, device, account):
        self.device = device
        self.account = account
        self.logs = []

    def log(self, kind, **payload):
        self.logs.append((kind, payload))


class MacRuntimeTests(unittest.TestCase):
    def setUp(self):
        mac_runtime._PENDING.clear()
        mac_runtime._DEVICES.clear()

    def test_apply_saves_original_and_sets_spoof(self):
        dev = FakeSuDevice("aa:bb:cc:dd:ee:ff")
        with tempfile.TemporaryDirectory() as td:
            persist = Path(td) / "last_real_mac.txt"
            info = mac_runtime.apply_wifi_mac(
                dev, "02:00:00:00:00:01", persist, sleep=lambda s: None)
            self.assertEqual(info["original"], "aa:bb:cc:dd:ee:ff")
            self.assertEqual(info["spoofed"], "02:00:00:00:00:01")
            self.assertEqual(persist.read_text().strip(), "aa:bb:cc:dd:ee:ff")
            self.assertEqual(dev.mac, "02:00:00:00:00:01")

    def test_restore_returns_original(self):
        dev = FakeSuDevice("02:00:00:00:00:01")
        with tempfile.TemporaryDirectory() as td:
            persist = Path(td) / "last_real_mac.txt"
            persist.write_text("aa:bb:cc:dd:ee:ff\n")
            info = mac_runtime.restore_wifi_mac(dev, persist, sleep=lambda s: None)
            self.assertTrue(info["restored"])
            self.assertEqual(dev.mac, "aa:bb:cc:dd:ee:ff")
            self.assertFalse(persist.exists())

    def test_restore_skip_when_no_file(self):
        dev = FakeSuDevice()
        with tempfile.TemporaryDirectory() as td:
            persist = Path(td) / "missing.txt"
            info = mac_runtime.restore_wifi_mac(dev, persist, sleep=lambda s: None)
            self.assertFalse(info["restored"])

    def test_apply_skip_gdy_brak_su(self):
        # Komentarz: OnePlus bez Magisk — pusty cat wlan0 i brak uid=0
        dev = FakeSuDevice(mac="")
        with tempfile.TemporaryDirectory() as td:
            persist = Path(td) / "last_real_mac.txt"
            with self.assertRaises(mac_runtime.MacSpoofError) as ctx:
                mac_runtime.apply_wifi_mac(
                    dev, "02:00:00:00:00:01", persist, sleep=lambda s: None)
            self.assertIn("brak root", str(ctx.exception))

    def test_apply_step_skips_empty_mac(self):
        ctx = FakeCtx(FakeSuDevice(), FakeAccount(mac=""))
        res = ApplyCloneIdentity().run(ctx, {})
        self.assertEqual(res.status, "skipped")

    def test_restore_step_ok_after_apply(self):
        dev = FakeSuDevice("11:22:33:44:55:66")
        acc = FakeAccount("02:00:00:00:00:01")
        ctx = FakeCtx(dev, acc)
        with tempfile.TemporaryDirectory() as td:
            # krok używa v2/data — podmieniamy persist przez monkeypatch ścieżki modułu
            import v2.steps.system.apply_clone_identity as apply_mod
            import v2.steps.system.restore_clone_identity as rest_mod
            apply_mod._DATA_ROOT = Path(td)
            rest_mod._DATA_ROOT = Path(td)
            r1 = ApplyCloneIdentity().run(ctx, {})
            self.assertEqual(r1.status, "ok")
            self.assertEqual(dev.mac, "02:00:00:00:00:01")
            r2 = RestoreCloneIdentity().run(ctx, {})
            self.assertEqual(r2.status, "ok")
            self.assertEqual(dev.mac, "11:22:33:44:55:66")


if __name__ == "__main__":
    unittest.main()
