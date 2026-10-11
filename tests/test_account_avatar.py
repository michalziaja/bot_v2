"""Zdjęcie profilowe na PC + skip kroku gdy brak pliku."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.data.account_avatar import avatar_path, save_avatar
from v2.steps.profile.set_avatar import SetAvatar
from v2.steps.system.close_ig import CloseIg
from v2.steps.system.dismiss_notifications import DismissNotifications
from v2.steps.system.sweep_tabs import SweepTabs, _tap_tab


class AccountAvatarStoreTests(unittest.TestCase):
    def test_save_and_find(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            dest = save_avatar(root, "acc1", b"\xff\xd8fake", "foto.JPG")
            self.assertEqual(dest.name, "avatar.jpg")
            self.assertEqual(avatar_path(root, "acc1"), dest)
            self.assertIsNone(avatar_path(root, "brak"))

    def test_replaces_previous_ext(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            save_avatar(root, "acc1", b"aaa", "a.png")
            dest = save_avatar(root, "acc1", b"bbb", "b.webp")
            self.assertEqual(dest.name, "avatar.webp")
            self.assertIsNone((root / "accounts" / "acc1" / "avatar.png").is_file() or None)
            self.assertFalse((root / "accounts" / "acc1" / "avatar.png").exists())


class CloseIgForceTests(unittest.TestCase):
    def test_force_zamyka_gdy_toggle_off(self):
        stopped = []

        class Acc:
            def step_enabled(self, name):
                return False

        class Dev:
            def force_stop_app(self, pkg):
                stopped.append(pkg)
            def current_pkg(self):
                return "android"
            def press_home(self):
                pass

        ctx = type("C", (), {})()
        ctx.account = Acc()
        ctx.device = Dev()
        ctx.ig_package = "com.instagram.androif"
        ctx.humanizer = type("Hu", (), {"pause": lambda *a, **k: None})()
        skipped = CloseIg().run(ctx, {})
        self.assertEqual(skipped.status, "skipped")
        self.assertEqual(stopped, [])
        forced = CloseIg().run(ctx, {"force": True})
        self.assertEqual(forced.status, "ok")
        self.assertEqual(stopped, ["com.instagram.androif"])


class SetAvatarSkipTests(unittest.TestCase):
    def test_skips_without_file(self):
        ctx = type("C", (), {})()
        ctx.account = type("A", (), {"account_id": "ghost"})()
        ctx.data_root = Path("/tmp/no-such-bot-avatar-root")
        res = SetAvatar().run(ctx, {})
        self.assertEqual(res.status, "skipped")


class DismissNotificationsTests(unittest.TestCase):
    def test_collapses_and_ok(self):
        shells = []

        class Dev:
            serial = "S"
            def shell(self, cmd):
                shells.append(cmd)
                return ""
            def hierarchy(self, force=False):
                return []

        ctx = type("C", (), {})()
        ctx.device = Dev()
        ctx.navigator = type("N", (), {
            "identify": lambda self: type("S", (), {"top": "home_feed"})(),
        })()
        ctx.ht = type("H", (), {"tap": lambda *a, **k: None})()
        ctx.humanizer = type("Hu", (), {"pause": lambda *a, **k: None})()
        ctx.log = lambda *a, **k: None
        with patch("v2.steps.system.dismiss_notifications._confirm_post_login_popups",
                   return_value=1):
            res = DismissNotifications().run(ctx, {})
        self.assertEqual(res.status, "ok")
        self.assertTrue(any("statusbar collapse" in s for s in shells))
        self.assertEqual(res.payload.get("popups"), 1)


class SweepTabsTests(unittest.TestCase):
    def test_taps_present_tabs_only(self):
        taps = []

        class Dev:
            def hierarchy(self, force=False):
                return [
                    {"resource-id": "com.instagram.android:id/feed_tab",
                     "bounds": "[0,2200][200,2340]", "class": "View",
                     "text": "", "content-desc": ""},
                    {"resource-id": "com.instagram.android:id/profile_tab",
                     "bounds": "[800,2200][1080,2340]", "class": "View",
                     "text": "", "content-desc": ""},
                ]

        ctx = type("C", (), {})()
        ctx.device = Dev()
        ctx.ht = type("H", (), {"tap": lambda self, *a, **k: taps.append(a)})()
        ctx.navigator = type("N", (), {
            "identify": lambda self: type("S", (), {"top": "home_feed"})(),
        })()
        ctx.humanizer = type("Hu", (), {"pause": lambda *a, **k: None})()
        ctx.log = lambda *a, **k: None
        ctx.account = type("A", (), {"account_id": "x"})()
        with patch("v2.steps.system.sweep_tabs._confirm_post_login_popups",
                   return_value=0), \
             patch("v2.steps.system.sweep_tabs._code_screen_visible",
                   return_value=False), \
             patch("v2.steps.system.sweep_tabs.time.sleep"):
            res = SweepTabs().run(ctx, {})
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.payload["visited"], ["feed_tab", "profile_tab"])
        self.assertEqual(len(taps), 2)

    def test_tap_tab_false_when_missing(self):
        ctx = type("C", (), {})()
        ctx.device = type("D", (), {"hierarchy": lambda self, force=False: []})()
        with patch("v2.steps.system.sweep_tabs.flatten_hierarchy", return_value=[]):
            self.assertFalse(_tap_tab(ctx, "com.instagram.android:id/feed_tab"))
