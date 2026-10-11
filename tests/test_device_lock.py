"""device_lock — jeden proces na adb serial (live race dwóch sesji)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.device_lock import DeviceBusy, DeviceLock, peek_device_lock  # noqa: E402


class DeviceLockTests(unittest.TestCase):
    def test_second_acquire_raises_busy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = DeviceLock(root, "SERIAL_TEST_1")
            b = DeviceLock(root, "SERIAL_TEST_1")
            a.acquire(owner="acc_a", session_id="s1")
            with self.assertRaises(DeviceBusy):
                b.acquire(owner="acc_b", session_id="s2")
            a.release()
            # po release drugi może wejść
            b.acquire(owner="acc_b", session_id="s2")
            b.release()

    def test_peek_widzi_holdera_i_znika_po_release(self):
        """Flota: GET lock czyta flock, nie samo istnienie pliku."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lock = DeviceLock(root, "SERIAL_PEEK")
            self.assertIsNone(peek_device_lock(root, "SERIAL_PEEK"))
            lock.acquire(owner="jobsaver", session_id="s9")
            holder = peek_device_lock(root, "SERIAL_PEEK")
            self.assertIsInstance(holder, dict)
            self.assertEqual(holder.get("owner"), "jobsaver")
            self.assertEqual(holder.get("session_id"), "s9")
            lock.release()
            self.assertIsNone(peek_device_lock(root, "SERIAL_PEEK"))
            # plik locka zostaje — to nie jest zajętość
            self.assertTrue(lock.path.exists())

    def test_different_serials_independent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = DeviceLock(root, "PHONE_A")
            b = DeviceLock(root, "PHONE_B")
            a.acquire(owner="x", session_id="1")
            b.acquire(owner="y", session_id="2")  # OK — inny telefon
            a.release()
            b.release()


if __name__ == "__main__":
    unittest.main()
