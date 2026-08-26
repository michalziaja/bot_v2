"""Lekki mock shell dla airplane-toggle — bez prawdziwego ADB."""
from __future__ import annotations

import unittest

from v2.core.airplane import set_airplane, toggle_airplane


class FakeShell:
    """Symuluje settings + cmd connectivity (ścieżka preferowana)."""

    def __init__(self):
        self.airplane = "0"
        self.wifi = "1"
        self.calls: list[str] = []

    def __call__(self, serial: str, command: str) -> str:
        self.calls.append(command)
        if command.startswith("settings get global airplane_mode_on"):
            return self.airplane
        if command.startswith("settings get global wifi_on"):
            return self.wifi
        if command.startswith("cmd connectivity airplane-mode enable"):
            self.airplane = "1"
            self.wifi = "0"
            return ""
        if command.startswith("cmd connectivity airplane-mode disable"):
            self.airplane = "0"
            self.wifi = "1"
            return ""
        if command.startswith("settings put global airplane_mode_on"):
            self.airplane = command.rsplit(" ", 1)[-1]
            return ""
        return ""


class FakeShellFallback(FakeShell):
    """cmd nie istnieje → fallback settings + broadcast."""

    def __call__(self, serial: str, command: str) -> str:
        if command.startswith("cmd connectivity"):
            self.calls.append(command)
            return "Unknown command"
        return super().__call__(serial, command)


class TestAirplaneToggle(unittest.TestCase):
    def test_toggle_on_pause_off(self):
        shell = FakeShell()
        sleeps: list[float] = []
        logs: list[str] = []

        out = toggle_airplane(
            "SERIAL",
            pause_s=2.5,
            shell=shell,
            sleep=sleeps.append,
            log=logs.append,
        )

        self.assertEqual(out["airplane_on"], "1")
        self.assertEqual(out["airplane_after"], "0")
        self.assertEqual(out["wifi_after"], "1")
        self.assertEqual(sleeps[0], 2.5)
        self.assertTrue(any("WL" in line for line in logs))
        self.assertTrue(any("czekam" in line for line in logs))
        self.assertTrue(any("WYL" in line for line in logs))

    def test_fallback_when_cmd_unknown(self):
        shell = FakeShellFallback()
        set_airplane("S", True, shell=shell)
        self.assertEqual(shell.airplane, "1")
        self.assertTrue(any("am broadcast" in c for c in shell.calls))
        self.assertTrue(any("settings put global airplane_mode_on 1" in c
                            for c in shell.calls))


if __name__ == "__main__":
    unittest.main()
