"""Testy magazynu danych logowania IG (config/credentials.py)."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.config import credentials


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._orig_path = credentials.CREDENTIALS_PATH
        credentials.CREDENTIALS_PATH = Path(self._tmp.name) / "credentials.json"

    def tearDown(self):
        credentials.CREDENTIALS_PATH = self._orig_path
        self._tmp.cleanup()

    def test_missing_account_means_none(self):
        self.assertIsNone(credentials.get("nope"))
        self.assertFalse(credentials.status("nope")["configured"])

    def test_roundtrip(self):
        credentials.set("clone_ip", "jan_kowalski23", "hunter22")
        entry = credentials.get("clone_ip")
        self.assertEqual(entry["ig_username"], "jan_kowalski23")
        self.assertEqual(entry["ig_password"], "hunter22")
        st = credentials.status("clone_ip")
        self.assertTrue(st["configured"])
        self.assertNotIn("hunter22", st["username_masked"])

    def test_two_accounts_independent(self):
        credentials.set("acc_a", "user_a", "pass_a")
        credentials.set("acc_b", "user_b", "pass_b")
        self.assertEqual(credentials.get("acc_a")["ig_username"], "user_a")
        self.assertEqual(credentials.get("acc_b")["ig_username"], "user_b")

    def test_clear_by_empty_values(self):
        credentials.set("acc_a", "user_a", "pass_a")
        credentials.set("acc_a", "", "")
        self.assertIsNone(credentials.get("acc_a"))

    def test_incomplete_pair_not_stored(self):
        credentials.set("acc_a", "user_a", "")
        self.assertIsNone(credentials.get("acc_a"))

    def test_mask_username(self):
        self.assertEqual(credentials.mask_username(""), "")
        self.assertEqual(credentials.mask_username("ab"), "••")
        masked = credentials.mask_username("jan_kowalski23")
        self.assertTrue(masked.startswith("ja"))
        self.assertNotIn("kowalski", masked)

    def test_signup_roundtrip_and_preserve(self):
        # Komentarz: signup zostaje przy zwykłym zapisie loginu
        credentials.set("acc_s", "nowy_user", "haslo1", signup={
            "full_name": "Anna Nowak",
            "birthday": "1998-04-12",
            "contact": "anna@example.com",
        })
        su = credentials.get_signup("acc_s")
        self.assertEqual(su["full_name"], "Anna Nowak")
        self.assertEqual(su["contact"], "anna@example.com")
        st = credentials.status("acc_s")
        self.assertTrue(st["signup_configured"])
        self.assertNotIn("anna@example.com", st["signup"]["contact_masked"])
        credentials.set("acc_s", "nowy_user", "haslo2")
        su2 = credentials.get_signup("acc_s")
        self.assertEqual(su2["full_name"], "Anna Nowak")
        self.assertEqual(su2["ig_password"], "haslo2")

    def test_incomplete_signup_is_none(self):
        credentials.set("acc_s", "u", "p", signup={"full_name": "X"})
        self.assertIsNone(credentials.get_signup("acc_s"))
        self.assertTrue(credentials.status("acc_s")["configured"])
        self.assertFalse(credentials.status("acc_s")["signup_configured"])


if __name__ == "__main__":
    unittest.main()
