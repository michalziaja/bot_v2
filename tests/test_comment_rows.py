"""Regresja: nicki na sheetcie komentarzy bez resource-id (live A50)."""
from __future__ import annotations
import unittest
from v2.steps.reels import _sheet


class CommentOrphanNickTests(unittest.TestCase):
    def test_collects_textview_without_resource_id(self):
        flat = [
            {"resource-id": "", "class": "android.widget.TextView",
             "text": "rafalgalewski", "content-desc": "rafalgalewski",
             "clickable": False, "bounds": "[100,354][400,400]"},
            {"resource-id": "", "class": "android.view.View",
             "text": "Odpowiedź", "content-desc": "Odpowiedź",
             "clickable": True, "bounds": "[100,461][200,500]"},
            {"resource-id": "", "class": "android.widget.TextView",
             "text": "chrzastowskam", "content-desc": "chrzastowskam",
             "clickable": False, "bounds": "[100,567][400,610]"},
            # Autor posta nad wątkiem — NIE w harvest commenters
            {"resource-id": "com.instagram.android:id/row_feed_photo_profile_name",
             "class": "android.widget.Button", "text": "basenispa.pl",
             "content-desc": "basenispa.pl", "clickable": True,
             "bounds": "[50,230][400,280]"},
        ]
        rows = _sheet.collect_rows(flat, _sheet.COMMENT_USERNAME_IDS)
        names = [r["username"] for r in rows]
        self.assertIn("rafalgalewski", names)
        self.assertIn("chrzastowskam", names)
        # Autor posta wyłączony z COMMENT_USERNAME_IDS (nie trafia do raw)
        self.assertNotIn("basenispa.pl", names)
        self.assertNotIn("Odpowiedź", names)
        self.assertNotIn("odpowiedź", [n.casefold() for n in names])

    def test_author_id_not_in_comment_harvest_ids(self):
        """row_feed_photo_profile_name = from_target — poza harvestem."""
        self.assertNotIn(
            "com.instagram.android:id/row_feed_photo_profile_name",
            _sheet.COMMENT_USERNAME_IDS,
        )

    def test_liker_ids_alone_do_not_take_orphans(self):
        """Na liście lajków nie zbieraj przypadkowych TextView bez id."""
        flat = [
            {"resource-id": "", "class": "android.widget.TextView",
             "text": "orphan_nick", "content-desc": "orphan_nick",
             "clickable": False, "bounds": "[100,354][400,400]"},
        ]
        rows = _sheet.collect_rows(flat, _sheet.LIKER_USERNAME_IDS)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
