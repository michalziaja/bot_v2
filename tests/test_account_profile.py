"""Bio na PC + skip kroku gdy brak tekstu."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.data.account_profile import BIO_MAX_LEN, get_bio, profile_status, set_bio
from v2.steps.profile.set_bio import SetBio, _find_bio_field


class AccountBioStoreTests(unittest.TestCase):
    def test_roundtrip_i_obciecie(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(get_bio(root, "acc1"), "")
            out = set_bio(root, "acc1", "  hej   swiecie  ")
            self.assertEqual(out, "hej swiecie")
            self.assertEqual(get_bio(root, "acc1"), "hej swiecie")
            long = "x" * (BIO_MAX_LEN + 40)
            cut = set_bio(root, "acc1", long)
            self.assertEqual(len(cut), BIO_MAX_LEN)
            set_bio(root, "acc1", "")
            self.assertEqual(get_bio(root, "acc1"), "")
            self.assertFalse((root / "accounts" / "acc1" / "profile.json").exists())

    def test_status_bez_awatara(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            set_bio(root, "acc1", "bio test")
            st = profile_status(root, "acc1")
            self.assertEqual(st["bio"], "bio test")
            self.assertFalse(st["has_avatar"])
            self.assertIsNone(st["filename"])


class SetBioSkipTests(unittest.TestCase):
    def test_skips_without_bio(self):
        ctx = type("C", (), {})()
        ctx.account = type("A", (), {"account_id": "ghost"})()
        ctx.data_root = Path("/tmp/no-such-bot-bio-root")
        res = SetBio().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload.get("reason"), "no_bio_text")

    def test_find_bio_field_po_hint(self):
        flat = [
            {"class": "android.widget.EditText", "text": "",
             "content-desc": "Biogram", "resource-id": "",
             "bounds": "[48,400][1032,520]"},
        ]
        field = _find_bio_field(flat)
        self.assertIsNotNone(field)
        self.assertIn("Biogram", field["content-desc"])
