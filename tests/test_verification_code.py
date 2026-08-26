"""Testy kanału panel→bot dla kodu weryfikacyjnego (core/verification_code.py)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core import verification_code as vc


class VerificationCodeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_request_by_default(self):
        self.assertIsNone(vc.get_request(self.data_root, "acc1"))

    def test_request_then_get(self):
        vc.request_code(self.data_root, "acc1", "Wprowadź kod wysłany na m***@gmail.com")
        req = vc.get_request(self.data_root, "acc1")
        self.assertIn("m***@gmail.com", req["prompt"])

    def test_clear_request_removes_file(self):
        vc.request_code(self.data_root, "acc1", "prompt")
        vc.clear_request(self.data_root, "acc1")
        self.assertIsNone(vc.get_request(self.data_root, "acc1"))

    def test_take_code_returns_none_before_submit(self):
        self.assertIsNone(vc.take_code(self.data_root, "acc1"))

    def test_submit_then_take_code_roundtrip(self):
        vc.submit_code(self.data_root, "acc1", "681145")
        self.assertEqual(vc.take_code(self.data_root, "acc1"), "681145")

    def test_take_code_consumes_it_once(self):
        vc.submit_code(self.data_root, "acc1", "111111")
        vc.take_code(self.data_root, "acc1")
        self.assertIsNone(vc.take_code(self.data_root, "acc1"))

    def test_accounts_are_independent(self):
        vc.submit_code(self.data_root, "acc_a", "111111")
        self.assertIsNone(vc.take_code(self.data_root, "acc_b"))
        self.assertEqual(vc.take_code(self.data_root, "acc_a"), "111111")

    def test_wait_for_code_returns_immediately_if_already_present(self):
        vc.submit_code(self.data_root, "acc1", "222222")
        code = vc.wait_for_code(self.data_root, "acc1", timeout_s=5, poll_s=0.01)
        self.assertEqual(code, "222222")

    def test_wait_for_code_times_out(self):
        code = vc.wait_for_code(self.data_root, "acc1", timeout_s=0.05, poll_s=0.01)
        self.assertIsNone(code)

    def test_wait_for_code_picks_up_late_submission(self):
        import threading
        def submit_later():
            import time; time.sleep(0.05)
            vc.submit_code(self.data_root, "acc1", "333333")
        threading.Thread(target=submit_later).start()
        code = vc.wait_for_code(self.data_root, "acc1", timeout_s=2, poll_s=0.01)
        self.assertEqual(code, "333333")


if __name__ == "__main__":
    unittest.main()
