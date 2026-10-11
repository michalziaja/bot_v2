"""Testy PhraseUsageStore — last_checked fraz content_search."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.data.stores import PhraseUsageStore, canonicalize_phrase


class PhraseUsageStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = PhraseUsageStore("acc_test", self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_canonicalize_hashtag_gets_hash(self):
        self.assertEqual(canonicalize_phrase("hashtags", "kariera"), "#kariera")
        self.assertEqual(canonicalize_phrase("tag_variants", "#kariera"), "#kariera")
        self.assertEqual(canonicalize_phrase("keywords", "#praca"), "praca")
        self.assertEqual(canonicalize_phrase("locations", "Warszawa"), "Warszawa")

    def test_read_missing_file_has_empty_buckets(self):
        data = self.store.read()
        self.assertEqual(set(data), {"hashtags", "keywords", "locations", "tag_variants"})
        self.assertEqual(data["hashtags"], {})

    def test_mark_checked_upserts_count_and_iso(self):
        rec = self.store.mark_checked("hashtags", "kariera")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["count"], 1)
        self.assertRegex(rec["last_checked"], r"^\d{4}-\d{2}-\d{2}T")
        rec2 = self.store.mark_checked("hashtags", "#Kariera")
        self.assertEqual(rec2["count"], 2)
        # ten sam klucz (pierwszy zapisany), lookup casefold
        data = self.store.read()
        self.assertEqual(list(data["hashtags"]), ["#kariera"])
        self.assertEqual(data["hashtags"]["#kariera"]["count"], 2)

    def test_lookup_casefold(self):
        self.store.mark_checked("keywords", "Portale Pracy")
        rec = self.store.lookup("keywords", "portale pracy")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["count"], 1)

    def test_empty_phrase_is_noop(self):
        self.assertIsNone(self.store.mark_checked("hashtags", "  "))
        self.assertEqual(self.store.read()["hashtags"], {})


class RecordPhraseCheckedTests(unittest.TestCase):
    """Runtime helper zapisuje usage gdy ctx ma events + account."""

    def test_records_via_events_path(self):
        from v2.plugins.impl.content_search import _record_phrase_checked

        tmp = Path(tempfile.mkdtemp())
        try:
            acc_dir = tmp / "accounts" / "demo"
            acc_dir.mkdir(parents=True)
            events = type("E", (), {"file_path": acc_dir / "events.jsonl"})()
            ctx = type("C", (), {})()
            ctx.account = type("A", (), {"account_id": "demo"})()
            ctx.events = events
            ctx.log = lambda *a, **k: None
            _record_phrase_checked(ctx, "hashtags", "#kariera")
            rec = PhraseUsageStore("demo", tmp).lookup("hashtags", "#kariera")
            self.assertIsNotNone(rec)
            self.assertEqual(rec["count"], 1)
        finally:
            shutil.rmtree(tmp)

    def test_noop_without_events(self):
        from v2.plugins.impl.content_search import _record_phrase_checked
        ctx = type("C", (), {})()
        ctx.account = None
        ctx.events = None
        _record_phrase_checked(ctx, "hashtags", "#x")  # nie rzuca
