"""Testy parsera nagłówka profilu + gate/sanity eval."""
from __future__ import annotations
import unittest
from unittest.mock import MagicMock
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps.profile._header import (read_profile_header, eval_gate, eval_sanity,
                                      _to_int, story_ring_tap_xy)


def _hier(nodes):
    def _mk(p):
        return {"properties": {
            "resource-id":  p.get("rid", ""), "text": p.get("text", ""),
            "content-desc": p.get("desc", ""), "class": "android.view.View",
            "bounds": "[0,0][100,100]", "clickable": "false", "package": "com.instagram.android",
        }, "children": []}
    return {"properties": {}, "children": [_mk(n) for n in nodes]}


class CountParseTests(unittest.TestCase):
    """Kompaktowe formaty PL/EN — wcześniej `_to_int` zwracał None / złe wartości
    i gate wpadał w `followers_unknown`."""

    def test_plain_and_spaced(self):
        self.assertEqual(_to_int("42"), 42)
        self.assertEqual(_to_int("1 234"), 1234)
        self.assertEqual(_to_int("12 345"), 12345)
        # cienka spacja (narrow no-break)
        self.assertEqual(_to_int("12\u202f345"), 12345)
        self.assertEqual(_to_int("1\u00a0234"), 1234)

    def test_en_compact_k_m(self):
        self.assertEqual(_to_int("1.2K"), 1200)
        self.assertEqual(_to_int("1.2k"), 1200)
        self.assertEqual(_to_int("12K"), 12_000)
        self.assertEqual(_to_int("1.2M"), 1_200_000)
        self.assertEqual(_to_int("3.4m"), 3_400_000)

    def test_pl_compact_tys_mln(self):
        self.assertEqual(_to_int("1,2 tys."), 1200)
        self.assertEqual(_to_int("1,2 tys"), 1200)
        self.assertEqual(_to_int("12,5 tys."), 12_500)
        self.assertEqual(_to_int("258 tys."), 258_000)
        self.assertEqual(_to_int("1,2 mln"), 1_200_000)
        self.assertEqual(_to_int("1,2 mln."), 1_200_000)

    def test_us_thousands_no_suffix(self):
        self.assertEqual(_to_int("1,234"), 1234)
        self.assertEqual(_to_int("1.234"), 1234)

    def test_glued_content_desc(self):
        # stacked familiar: content-desc bez spacji między liczbą a etykietą
        self.assertEqual(_to_int("235obserwatorzy"), 235)
        self.assertEqual(_to_int("1,2 tys.obserwujący"), 1200)

    def test_garbage_is_none(self):
        self.assertIsNone(_to_int(""))
        self.assertIsNone(_to_int("obserwujący"))
        self.assertIsNone(_to_int("N/A"))


class HeaderParseTests(unittest.TestCase):
    def _dev(self, h):
        d = MagicMock(); d.hierarchy.return_value = h; return d

    def test_counts_parsing(self):
        h = _hier([
            {"rid": "com.instagram.android:id/profile_header_familiar_post_count_value",  "text": "42"},
            {"rid": "com.instagram.android:id/profile_header_familiar_followers_value",   "text": "1 234"},
            {"rid": "com.instagram.android:id/profile_header_familiar_following_value",   "text": "300"},
        ])
        r = read_profile_header(self._dev(h))
        self.assertEqual(r["posts"], 42)
        self.assertEqual(r["followers"], 1234)
        self.assertEqual(r["following"], 300)

    def test_followers_pl_tys_via_value_node(self):
        # symptom live: posts/following OK, followers=None przy „X,Y tys."
        h = _hier([
            {"rid": "com.instagram.android:id/profile_header_familiar_post_count_value", "text": "546"},
            {"rid": "com.instagram.android:id/profile_header_familiar_followers_value",
             "text": "1,2 tys."},
            {"rid": "com.instagram.android:id/profile_header_familiar_following_value", "text": "997"},
        ])
        r = read_profile_header(self._dev(h))
        self.assertEqual(r["followers"], 1200)
        self.assertEqual(r["following"], 997)

    def test_followers_from_stacked_content_desc(self):
        h = _hier([{
            "rid": "com.instagram.android:id/profile_header_followers_stacked_familiar",
            "desc": "12,5 tys.obserwujący",
        }])
        r = read_profile_header(self._dev(h))
        self.assertEqual(r["followers"], 12_500)

    def test_follow_button_state(self):
        h = _hier([{"rid": "com.instagram.android:id/profile_header_follow_button",
                    "text": "Obserwujesz"}])
        self.assertTrue(read_profile_header(self._dev(h))["already_following"])
        h2 = _hier([{"rid": "com.instagram.android:id/profile_header_follow_button",
                     "text": "Obserwuj"}])
        self.assertFalse(read_profile_header(self._dev(h2))["already_following"])

    def test_private_detection(self):
        h = _hier([{"text": "Ten profil jest prywatny"}])
        self.assertTrue(read_profile_header(self._dev(h))["is_private"])

    def test_private_detection_konto_prywatne(self):
        """Regresja @ghescrime: nowszy IG — „To konto jest prywatne" (nie -ny).

        Samo „prywatny" NIE jest podciągiem „prywatne" → wcześniej is_private=false
        i leciał drogi zrzut siatki + Vision na empty-state + Proponowane.
        """
        h = _hier([
            {"rid": "com.instagram.android:id/action_bar_title", "text": "ghescrime"},
            {"rid": "com.instagram.android:id/profile_header_familiar_post_count_value",
             "text": "81"},
            {"text": "To konto jest prywatne"},
            {"text": "Obserwuj ten profil, aby oglądać dodawane przez niego zdjęcia i filmy."},
        ])
        r = read_profile_header(self._dev(h))
        self.assertTrue(r["is_private"])
        self.assertEqual(r["username"], "ghescrime")
        self.assertEqual(r["posts"], 81)

    def test_private_detection_en_this_account(self):
        h = _hier([{"text": "This account is private"}])
        self.assertTrue(read_profile_header(self._dev(h))["is_private"])

    def test_private_detection_empty_state_resource_id(self):
        """Kontener IG bez tekstu locale — sam resource-id wystarczy."""
        h = _hier([{
            "rid": "com.instagram.android:id/private_profile_empty_state",
            "desc": "",
        }])
        self.assertTrue(read_profile_header(self._dev(h))["is_private"])


class ComposeBioTests(unittest.TestCase):
    def test_extract_compose_bio_from_metacompose(self):
        from v2.steps.profile._header import extract_compose_bio, read_profile_header

        def leaf(text, bounds="[40,520][1000,700]"):
            return {"properties": {
                "class": "android.widget.TextView", "text": text,
                "resource-id": "", "bounds": bounds, "package": "com.instagram.android",
            }, "children": []}

        tree = {
            "properties": {"class": "android.widget.FrameLayout",
                           "bounds": "[0,0][1080,2340]"},
            "children": [{
                "properties": {
                    "class": "com.facebook.compose.view.MetaComposeView",
                    "resource-id": "", "bounds": "[0,400][1080,800]",
                },
                "children": [{
                    "properties": {"class": "android.view.View", "resource-id": ""},
                    "children": [{
                        "properties": {"class": "android.view.View", "resource-id": ""},
                        "children": [
                            leaf("Obserwuj"),  # chrome — odrzuć
                            leaf("Buduję AI do CV i automatyzacji rekrutacji. "
                                 "Link w bio 👇 #job.search"),
                        ],
                    }],
                }],
            }],
        }
        bio = extract_compose_bio(tree, exclude_names={"demo"})
        self.assertIn("Buduję AI", bio or "")

        d = MagicMock()
        d.hierarchy.return_value = tree
        h = read_profile_header(d)
        self.assertEqual(h["bio_source"], "compose_textview")
        self.assertIn("Buduję AI", h["bio"] or "")

    def test_classic_bio_id_wins(self):
        from v2.steps.profile._header import read_profile_header
        h = _hier([{"rid": "com.instagram.android:id/profile_header_bio_text",
                    "text": "Klasyczne bio"}])
        d = MagicMock(); d.hierarchy.return_value = h
        # bez mocka flatten — drzewo z _hier
        r = read_profile_header(d)
        self.assertEqual(r["bio"], "Klasyczne bio")
        self.assertEqual(r["bio_source"], "resource_id")


class GateEvalTests(unittest.TestCase):
    def test_no_gate_passes(self):
        self.assertTrue(eval_gate({"posts": 0}, {})["pass"])

    def test_min_followers_fail(self):
        r = eval_gate({"followers": 10}, {"min_followers": 50})
        self.assertFalse(r["pass"])
        self.assertIn("followers<50", r["why"])

    def test_followers_unknown_when_none(self):
        r = eval_gate({"followers": None}, {"min_followers": 50})
        self.assertFalse(r["pass"])
        self.assertIn("followers_unknown", r["why"])

    def test_max_followers_fail(self):
        r = eval_gate({"followers": 100000}, {"max_followers": 50000})
        self.assertFalse(r["pass"])
        self.assertIn("followers>50000", r["why"])

    def test_ratio(self):
        r_ok = eval_gate({"followers": 200, "following": 100}, {"min_ratio": 0.5})
        self.assertTrue(r_ok["pass"])
        r_bad = eval_gate({"followers": 20, "following": 200}, {"min_ratio": 0.5})
        self.assertFalse(r_bad["pass"])

    def test_bio_exclusions(self):
        r = eval_gate({"bio": "DM me for deals 💰"}, {"bio_exclusions": ["dm me"]})
        self.assertFalse(r["pass"])

    def test_private_gate_off_when_allowed(self):
        r = eval_gate({"is_private": True}, {"is_private_ok": True})
        self.assertTrue(r["pass"])


class RealDumpTests(unittest.TestCase):
    """Parser sprawdzony na dumpie z żywego przebiegu E4.

    Pierwsza wersja czytała nick z `action_bar_large_title_auto_size` i zwracała
    `None` — profile zapisywały się jako „unknown", więc dedup odwiedzonych
    przestawał działać i bot wracałby na te same konta.
    """

    @classmethod
    def setUpClass(cls):
        import json
        p = ROOT / "calibration" / "samples" / "42_profile_other.flat.json"
        cls.FLAT = json.loads(p.read_text(encoding="utf-8"))

    def _dev(self):
        d = MagicMock()
        d.hierarchy.return_value = self.FLAT
        return d

    def setUp(self):
        # dump jest już spłaszczony — parser dostaje go bez przechodzenia drzewa
        import v2.steps.profile._header as mod
        self.mod = mod
        self._orig = mod.flatten_hierarchy
        mod.flatten_hierarchy = lambda h: h

    def tearDown(self):
        self.mod.flatten_hierarchy = self._orig

    def test_username_is_read(self):
        self.assertEqual(read_profile_header(self._dev())["username"], "j._kaplan._")

    def test_full_name_is_read(self):
        self.assertEqual(read_profile_header(self._dev())["full_name"], "Jan Kapłan")

    def test_counts_are_read(self):
        h = read_profile_header(self._dev())
        self.assertEqual((h["posts"], h["followers"], h["following"]), (3, 235, 247))

    def test_public_profile_is_not_marked_private(self):
        h = read_profile_header(self._dev())
        self.assertFalse(h["is_private"])
        self.assertFalse(h["already_following"])   # przycisk pokazuje „Obserwuj"

    def test_story_ring_tap_uses_avatar_frame_center(self):
        """Stałe % ekranu trafiały pod ring — bierzemy środek z dumpa."""
        xy = story_ring_tap_xy(self.FLAT)
        self.assertIsNotNone(xy)
        # ramka awatara [61,249][255,443] → środek (158, 346)
        self.assertEqual(xy, (158, 346))
        # stary fallback y=0.20*2340≈468 był PONIŻEJ awatara
        self.assertLess(xy[1], 450)


class SanityEvalTests(unittest.TestCase):
    def test_default_aborts_private(self):
        r = eval_sanity({"is_private": True, "already_following": False}, {})
        self.assertFalse(r["pass"])
        self.assertIn("became_private", r["why"])

    def test_default_aborts_already_following(self):
        r = eval_sanity({"is_private": False, "already_following": True}, {})
        self.assertFalse(r["pass"])
        self.assertIn("already_following", r["why"])

    def test_can_disable(self):
        r = eval_sanity({"is_private": True, "already_following": True},
                        {"abort_if_private": False, "abort_if_already_following": False})
        self.assertTrue(r["pass"])


if __name__ == "__main__":
    unittest.main()
