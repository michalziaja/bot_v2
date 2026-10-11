"""Kanał panel ↔ walker kalibracji (needs_agent + reply)."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core import calibrate_state as cs


class CalibrateStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data = Path(self._tmp.name)
        self.walk = self.data / "walk"
        self.walk.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def test_public_idle_gdy_brak_pliku(self):
        self.assertEqual(cs.public(cs.load(self.data, "phone")), {"status": "idle"})

    def test_ask_kopiuje_screen_i_czeka_na_reply(self):
        shot = self.walk / "step.jpg"
        shot.write_bytes(b"\xff\xd8\xff")  # komentarz: minimalny JPEG
        cs.ask(
            self.data, "phone", self.walk,
            step="ig", expected=["home_feed"], actual="unknown",
            question="co to?", shot_path=str(shot), account="acc1",
        )
        pub = cs.public(cs.load(self.data, "phone"))
        self.assertEqual(pub["status"], "needs_agent")
        self.assertTrue(pub["has_shot"])
        self.assertEqual(pub["step"], "ig")
        self.assertIn("co to?", pub["question"])
        copy = self.data / "devices" / "phone" / "calibrate.jpg"
        self.assertTrue(copy.exists())
        self.assertTrue((self.walk / "AGENT_ASK.md").exists())

        def reply_later():
            time.sleep(0.05)
            cs.set_reply(self.data, "phone", "ok", "to home")

        threading.Thread(target=reply_later).start()
        got = cs.wait_reply(self.data, "phone", timeout_s=2, poll_s=0.01)
        self.assertEqual(got["action"], "ok")
        self.assertEqual(got["note"], "to home")
        # Komentarz: reply zużyte — drugi wait nie weźmie starego
        stale = cs.load(self.data, "phone").get("reply")
        self.assertFalse(stale)

    def test_reply_zly_action(self):
        cs.save(self.data, "phone", {"status": "needs_agent"})
        with self.assertRaises(ValueError):
            cs.set_reply(self.data, "phone", "maybe")

    def test_wait_timeout_to_abort(self):
        cs.save(self.data, "phone", {"status": "needs_agent", "reply": None})
        got = cs.wait_reply(self.data, "phone", timeout_s=0.05, poll_s=0.01)
        self.assertEqual(got["action"], "abort")
