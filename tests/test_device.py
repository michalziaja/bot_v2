"""Testy UiAuto2Device — mockowany HTTP + shell."""
from __future__ import annotations
import json
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.device import DeviceError, UiAuto2Device, ig_ps_has_package


def _mock_http(json_body=None, raw_body=None):
    r = MagicMock()
    if raw_body is not None:
        r.read.return_value = raw_body
    else:
        r.read.return_value = json.dumps(json_body or {}).encode()
    r.__enter__ = lambda self: self
    r.__exit__  = lambda self, *a: False
    return r


class DeviceHttpTests(unittest.TestCase):
    def setUp(self):
        self.dev = UiAuto2Device("SERIAL")

    @patch("urllib.request.urlopen")
    def test_hierarchy_returns_json(self, mock_open):
        mock_open.return_value = _mock_http(json_body={"name": "root", "children": []})
        h = self.dev.hierarchy()
        self.assertEqual(h["name"], "root")

    @patch("urllib.request.urlopen")
    def test_hierarchy_cache_within_window(self, mock_open):
        mock_open.return_value = _mock_http(json_body={"name": "root"})
        self.dev.hierarchy()
        # drugi call w oknie cache — powinien NIE wołać http ponownie
        self.dev.hierarchy()
        self.assertEqual(mock_open.call_count, 1)

    @patch("urllib.request.urlopen")
    def test_hierarchy_force_bypasses_cache(self, mock_open):
        mock_open.return_value = _mock_http(json_body={"name": "root"})
        self.dev.hierarchy()
        self.dev.hierarchy(force=True)
        self.assertEqual(mock_open.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_shell_output_stripped(self, mock_open):
        mock_open.return_value = _mock_http(json_body={"output": "hello\n", "error": None})
        self.assertEqual(self.dev.shell("echo hello"), "hello")

    @patch("urllib.request.urlopen")
    def test_tap_invalidates_cache(self, mock_open):
        mock_open.return_value = _mock_http(json_body={"name":"a"})
        self.dev.hierarchy()               # cache
        mock_open.return_value = _mock_http(json_body={})
        self.dev.tap(100, 200)             # tap → invalidate
        mock_open.return_value = _mock_http(json_body={"name":"b"})
        h = self.dev.hierarchy()
        self.assertEqual(h["name"], "b")   # cache się nie użył

    @patch("urllib.request.urlopen")
    def test_http_error_raises_deviceerror(self, mock_open):
        mock_open.side_effect = Exception("boom")
        with self.assertRaises(DeviceError):
            self.dev.hierarchy()

    @patch("urllib.request.urlopen")
    def test_current_pkg_parses_focus_line(self, mock_open):
        mock_open.return_value = _mock_http(
            json_body={"output": "mCurrentFocus=Window{aaa u0 com.instagram.android/com.instagram.mainactivity.InstagramMainActivity}"}
        )
        self.assertEqual(self.dev.current_pkg(), "com.instagram.android")

    @patch("urllib.request.urlopen")
    def test_current_pkg_shade_bierze_focused_app(self, mock_open):
        # Komentarz: OnePlus — mCurrentFocus=NotificationShade bez pkg/activity
        mock_open.return_value = _mock_http(json_body={"output": (
            "  mCurrentFocus=Window{e356362 u0 NotificationShade}\n"
            "  mFocusedApp=ActivityRecord{ad7b64f u0 "
            "com.instagram.androif/com.instagram.nux.activity.SignedOutFragmentActivity}\n"
        )})
        self.assertEqual(self.dev.current_pkg(), "com.instagram.androif")

    @patch("urllib.request.urlopen")
    def test_hierarchy_normalizes_clone_resource_ids(self, mock_open):
        dev = UiAuto2Device("SERIAL", ig_package="com.instagram.androip")
        raw = {
            "properties": {"resource-id": "com.instagram.androip:id/action_bar_title",
                           "package": "com.instagram.androip"},
            "children": [
                {"properties": {"resource-id": "com.instagram.androip:id/profile_header_bio_text"},
                 "children": []},
                {"properties": {"resource-id": "android:id/content"}, "children": []},
            ],
        }
        mock_open.return_value = _mock_http(json_body=raw)
        h = dev.hierarchy()
        self.assertEqual(h["properties"]["resource-id"], "com.instagram.android:id/action_bar_title")
        self.assertEqual(h["children"][0]["properties"]["resource-id"],
                         "com.instagram.android:id/profile_header_bio_text")
        self.assertEqual(h["children"][1]["properties"]["resource-id"], "android:id/content")

    @patch("urllib.request.urlopen")
    def test_hierarchy_no_normalization_for_original_package(self, mock_open):
        dev = UiAuto2Device("SERIAL")  # default ig_package=com.instagram.android
        raw = {"properties": {"resource-id": "com.instagram.android:id/action_bar_title"},
               "children": []}
        mock_open.return_value = _mock_http(json_body=raw)
        h = dev.hierarchy()
        self.assertEqual(h["properties"]["resource-id"], "com.instagram.android:id/action_bar_title")

    @patch("urllib.request.urlopen")
    def test_screen_state_helpers(self, mock_open):
        # ON + unlocked
        mock_open.return_value = _mock_http(json_body={"output": "Display Power: state=ON"})
        self.assertTrue(self.dev.screen_is_on())
        mock_open.return_value = _mock_http(json_body={"output": "mKeyguardShowing=false"})
        self.assertTrue(self.dev.screen_is_unlocked())
        # OFF + locked
        mock_open.return_value = _mock_http(json_body={"output": "Display Power: state=OFF"})
        self.assertFalse(self.dev.screen_is_on())
        mock_open.return_value = _mock_http(json_body={"output": "mKeyguardShowing=true"})
        self.assertFalse(self.dev.screen_is_unlocked())

    @patch("urllib.request.urlopen")
    def test_su_shell_wraps_command(self, mock_open):
        mock_open.return_value = _mock_http(json_body={"output": "aa:bb:cc:dd:ee:ff\n"})
        out = self.dev.su_shell("cat /sys/class/net/wlan0/address")
        self.assertEqual(out, "aa:bb:cc:dd:ee:ff")
        # body.command zawiera su -c (Magisk) i oryginalną komendę
        posted = json.loads(mock_open.call_args[0][0].data.decode())
        self.assertIn("su -c", posted["command"])
        self.assertIn("wlan0", posted["command"])

    @patch("urllib.request.urlopen")
    def test_dumpsys_power_empty_is_not_on(self, mock_open):
        # Pusty output = ADB milczy; screen_is_on ma być False, nie „zgadujemy ON”.
        mock_open.return_value = _mock_http(json_body={"output": ""})
        self.assertEqual(self.dev.dumpsys_power(), "")
        self.assertFalse(self.dev.screen_is_on())

    def test_launch_app_force_stops_other_ig_including_stock(self):
        commands: list[str] = []

        def fake_shell(cmd: str) -> str:
            commands.append(cmd)
            if cmd.startswith("pm list packages"):
                return (
                    "package:com.instagram.android\n"
                    "package:com.instagram.androia\n"
                    "package:com.instagram.androib"
                )
            if cmd.startswith("ps"):
                return "USER PID NAME\nu0_a1 77 com.instagram.androib"
            if cmd.startswith("pidof "):
                return ""
            return ""

        with patch.object(self.dev, "shell", side_effect=fake_shell):
            self.dev.launch_app("com.instagram.androib", wait_s=0)
        self.assertIn("am force-stop com.instagram.android", commands)
        self.assertIn("am force-stop com.instagram.androia", commands)
        self.assertTrue(any(c.startswith("monkey -p com.instagram.androib") for c in commands))
        self.assertTrue(any("am start" in c and "com.instagram.androib" in c for c in commands))

    def test_launch_app_blocks_when_other_ig_pid_lingers(self):
        def fake_shell(cmd: str) -> str:
            if cmd.startswith("pm list packages"):
                return "package:com.instagram.android\npackage:com.instagram.androib"
            if cmd.startswith("ps"):
                return (
                    "USER PID NAME\n"
                    "u0_a1 1234 com.instagram.android\n"
                    "u0_a2 77 com.instagram.androib"
                )
            return ""

        with patch.object(self.dev, "shell", side_effect=fake_shell):
            with self.assertRaises(DeviceError) as ctx:
                self.dev.launch_app("com.instagram.androib", wait_s=0)
        self.assertIn("sole_ig_pid", str(ctx.exception))
        self.assertIn("com.instagram.android", str(ctx.exception))

    def test_ig_ps_has_package_nie_myli_stocka_z_klonem(self):
        # Komentarz: pidof comm=com.instagram.a — ps NAME rozróżnia android vs androib
        ps = (
            "USER PID NAME\n"
            "u0_a1 77 com.instagram.androib\n"
            "u0_a1 78 com.instagram.androib:mqtt\n"
        )
        self.assertFalse(ig_ps_has_package(ps, "com.instagram.android"))
        self.assertTrue(ig_ps_has_package(ps, "com.instagram.androib"))
        stock = "USER PID NAME\nu0_a9 11 com.instagram.android:video\n"
        self.assertTrue(ig_ps_has_package(stock, "com.instagram.android"))
        self.assertFalse(ig_ps_has_package(stock, "com.instagram.androia"))


if __name__ == "__main__":
    unittest.main()
