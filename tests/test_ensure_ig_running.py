"""system.ensure_ig_running — force-stop innych IG nawet gdy target już w focus."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.device import DeviceError
from v2.steps.system.ensure_ig_running import EnsureIgRunning


class _Dev:
    """Minimalny telefon: current_pkg + stop/launch."""

    def __init__(self, fg: str | None):
        self.fg = fg
        self.stopped_for: list[str] = []
        self.launched: list[str] = []
        self.stop_err = ""

    def current_pkg(self):
        return self.fg

    def stop_other_instagrams(self, target, *, wait_s=2.0):
        self.stopped_for.append(target)
        return self.stop_err

    def launch_app(self, pkg, *, wait_s=2.0):
        if self.stop_err:
            raise DeviceError(self.stop_err)
        self.launched.append(pkg)
        self.fg = pkg

    def dismiss_keyguard(self):
        return None

    def shell(self, command: str) -> str:
        return ""


class EnsureIgRunningTests(unittest.TestCase):
    def _ctx(self, dev, target="com.instagram.androib"):
        ctx = type("C", (), {})()
        ctx.device = dev
        ctx.ig_package = target
        ctx.humanizer = type("Hu", (), {"pause": lambda *a, **k: None})()
        return ctx

    def test_already_running_still_stops_others(self):
        # Komentarz: zminimalizowany stock/klon ma zginąć mimo że target jest w focus
        dev = _Dev("com.instagram.androib")
        res = EnsureIgRunning().run(self._ctx(dev), {})
        self.assertEqual(res.status, "ok")
        self.assertEqual(dev.stopped_for, ["com.instagram.androib"])
        self.assertEqual(dev.launched, [])

    def test_launch_when_other_ig_in_foreground(self):
        dev = _Dev("com.instagram.android")
        res = EnsureIgRunning().run(self._ctx(dev), {})
        self.assertEqual(res.status, "ok")
        self.assertEqual(dev.launched, ["com.instagram.androib"])

    def test_blocks_when_other_ig_pid_lingers(self):
        dev = _Dev("com.instagram.android")
        dev.stop_err = "sole_ig_pid: inne IG nadal mają PID: com.instagram.android"
        res = EnsureIgRunning().run(self._ctx(dev), {})
        self.assertEqual(res.status, "failed")
        self.assertIn("sole_ig_pid", res.error)
        self.assertEqual(dev.launched, [])


if __name__ == "__main__":
    unittest.main()
