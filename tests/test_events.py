"""Testy EventLoggera — append, read, izolacja sesji."""
from __future__ import annotations
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.events import EventLogger, new_session_id


class EventLoggerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sid = new_session_id()
        self.log = EventLogger("acc_test", self.sid, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_creates_directory_and_file(self):
        rec = self.log.log("hello", x=1)
        self.assertTrue(self.log.file_path.exists())
        self.assertEqual(rec["kind"], "hello")
        self.assertEqual(rec["payload"], {"x": 1})
        self.assertEqual(rec["account_id"], "acc_test")
        self.assertEqual(rec["session_id"], self.sid)

    def test_append_preserves_history(self):
        self.log.log("a", n=1)
        self.log.log("b", n=2)
        self.log.log("c", n=3)
        recs = self.log.read_all()
        self.assertEqual(len(recs), 3)
        self.assertEqual([r["kind"] for r in recs], ["a", "b", "c"])

    def test_session_isolation(self):
        # zapisz w sesji A
        self.log.log("step", from_="A")
        # nowa sesja tego samego konta
        sid_b = new_session_id()
        log_b = EventLogger("acc_test", sid_b, self.tmp)
        log_b.log("step", from_="B")
        # read_all daje oba, session_events tylko jednej
        self.assertEqual(len(self.log.read_all()), 2)
        self.assertEqual(len(self.log.session_events()), 1)
        self.assertEqual(len(log_b.session_events()), 1)
        self.assertEqual(self.log.session_events()[0]["payload"]["from_"], "A")
        self.assertEqual(log_b.session_events()[0]["payload"]["from_"], "B")

    def test_new_session_id_unique(self):
        ids = {new_session_id() for _ in range(50)}
        self.assertEqual(len(ids), 50)


if __name__ == "__main__":
    unittest.main()
