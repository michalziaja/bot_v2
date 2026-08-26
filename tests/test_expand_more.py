"""Testy expand „więcej" + merge caption — bez telefonu."""
from __future__ import annotations

import unittest

from v2.plugins.impl.niche_discover import merge_caption_parts
from v2.steps._expand_more import _find_more_target, MORE_EXACT


class FindMoreTargetTests(unittest.TestCase):
    def test_prefers_exact_wiecej_button(self):
        flat = [
            {"class": "TextView", "text": "Opis ucięty.… więcej",
             "content-desc": "", "clickable": False,
             "bounds": "[0,0][100,50]"},
            {"class": "Button", "text": "", "content-desc": "więcej",
             "clickable": True, "bounds": "[80,0][100,20]"},
            {"class": "Button", "text": "Obejrzyj więcej",
             "content-desc": "Obejrzyj więcej", "clickable": True,
             "bounds": "[0,100][200,140]"},
        ]
        t = _find_more_target(flat)
        self.assertIsNotNone(t)
        self.assertEqual((t.get("content-desc") or "").casefold(), "więcej")

    def test_skips_obejrzyj_wiecej(self):
        flat = [
            {"class": "Button", "text": "Obejrzyj więcej rolek",
             "content-desc": "Dotknij, aby obejrzeć więcej rolek",
             "clickable": True, "bounds": "[0,0][200,40]"},
        ]
        self.assertIsNone(_find_more_target(flat))

    def test_falls_back_to_clickable_truncated_text(self):
        flat = [
            {"class": "TextView", "text": "Długie bio.… więcej",
             "content-desc": "", "clickable": True,
             "bounds": "[0,0][400,80]"},
        ]
        t = _find_more_target(flat)
        self.assertIsNotNone(t)
        self.assertIn("więcej", (t.get("text") or "").casefold())

    def test_falls_back_to_nonclickable_compose_bio(self):
        # MetaComposeView: TextView z …więcej bez clickable — i tak tapamy
        flat = [
            {"class": "TextView",
             "text": "Buduję AI do CV.… więcej",
             "content-desc": "", "clickable": False,
             "bounds": "[40,520][1000,700]",
             "resource-id": ""},
            {"class": "Button", "text": "Obejrzyj więcej rolek",
             "content-desc": "Obejrzyj więcej rolek", "clickable": True,
             "bounds": "[0,1800][400,1900]"},
        ]
        t = _find_more_target(flat)
        self.assertIsNotNone(t)
        self.assertIn("Buduję AI", t.get("text") or "")

    def test_prefers_profile_header_bio_rid(self):
        flat = [
            {"class": "TextView", "text": "Feed caption.… więcej",
             "content-desc": "", "clickable": True,
             "bounds": "[0,1600][400,1700]", "resource-id": ""},
            {"class": "TextView", "text": "Bio profilu.… więcej",
             "content-desc": "", "clickable": False,
             "bounds": "[40,500][900,680]",
             "resource-id": "com.instagram.android:id/profile_header_bio_text"},
        ]
        t = _find_more_target(flat)
        self.assertIsNotNone(t)
        self.assertIn("profile_header_bio", (t.get("resource-id") or ""))

    def test_exact_labels_cover_pl_en(self):
        self.assertIn("więcej", MORE_EXACT)
        self.assertIn("more", MORE_EXACT)
        self.assertIn("see more", MORE_EXACT)

    def test_skips_clips_ufi_more_button(self):
        # menu UFI po prawej ma content-desc „Więcej" — to NIE expand opisu
        flat = [
            {"resource-id": "com.instagram.android:id/clips_ufi_more_button_component",
             "class": "Button", "text": "", "content-desc": "Więcej",
             "clickable": True, "bounds": "[932,1898][1048,2014]"},
            {"resource-id": "com.instagram.android:id/caption_text_view",
             "class": "TextView", "text": "Opis ucięty.… więcej",
             "content-desc": "", "clickable": True,
             "bounds": "[42,1990][900,2119]"},
        ]
        t = _find_more_target(flat)
        self.assertIsNotNone(t)
        self.assertIn("caption", (t.get("resource-id") or ""))
        self.assertNotIn("ufi", (t.get("resource-id") or ""))

    def test_ufi_more_alone_returns_none(self):
        flat = [
            {"resource-id": "com.instagram.android:id/clips_ufi_more_button_component",
             "text": "", "content-desc": "Więcej", "clickable": True,
             "bounds": "[900,1800][1040,1940]"},
        ]
        self.assertIsNone(_find_more_target(flat))


class MergeCaptionPartsTests(unittest.TestCase):
    def test_overlap_suffix_prefix(self):
        # wspólny fragment na styku dumpów
        a = "To jest początek opisu i środek"
        b = "środek oraz zakończenie tekstu"
        m = merge_caption_parts(a, b)
        self.assertIn("początek", m)
        self.assertIn("zakończenie", m)
        self.assertEqual(m.count("środek"), 1)

    def test_contained(self):
        self.assertEqual(merge_caption_parts("abc def", "abc"), "abc def")
        self.assertEqual(merge_caption_parts("abc", "abc def ghi"), "abc def ghi")

    def test_empty(self):
        self.assertEqual(merge_caption_parts("", "hello"), "hello")
        self.assertEqual(merge_caption_parts("hello", ""), "hello")


if __name__ == "__main__":
    unittest.main()
