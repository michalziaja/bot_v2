"""Creator post/relacja — hierarchia z dumpów live technature699 (bez telefonu).

Źródła: calibration/recon_publish/ 2026-09-05, IG 444.0.0.46.85.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.steps.publish._ui import identify_creator, is_recents_folder  # noqa: E402
from v2.steps.publish.drive_creator import drive_creator  # noqa: E402
from v2.steps.publish.fallback_album import (  # noqa: E402
    _pick_named_album,
    fallback_pick_canonical,
    is_first_post_account,
    open_creator_gallery,
)
from v2.steps.publish.send_intent import (  # noqa: E402
    COMP_FEED,
    COMP_STORY,
    handler_component,
    refuse_path,
    send_to_clone,
)


def n(rid="", text="", desc="", clickable=False, bounds="[0,0][10,10]"):
    """Węzeł jak z flatten_hierarchy — pola z dumpa uiauto."""
    return {
        "resource-id": rid,
        "text": text,
        "content-desc": desc,
        "clickable": clickable,
        "bounds": bounds,
        "class": "",
        "package": "com.instagram.android",
    }


# --- wycinki z dumpów live (nie całe drzewo) ---
RESOLVER = [
    n("android:id/title", "Wykonaj czynność przez..."),
    n("android:id/resolver_list"),
    n("android:id/text2", "Feed", bounds="[105,2155][179,2203]"),
    n("android:id/text2", "Stories", bounds="[879,2155][989,2203]"),
    n("android:id/button_once", "Tylko raz", "Użyj wybranej aplikacji tylko raz",
      True, "[63,2224][538,2319]"),
]

GALLERY_POST = [
    n("com.instagram.android:id/new_post_title", "Nowy post"),
    n("com.instagram.android:id/next_button_textview", "Dalej", "Dalej", True,
      "[908,83][1080,230]"),
    n("com.instagram.android:id/gallery_folder_menu_tv", "Ostatnie",
      clickable=True, bounds="[0,1345][347,1411]"),
    n("com.instagram.android:id/cam_dest_feed", "OPUBLIKUJ", "OPUBLIKUJ", True,
      "[381,2214][700,2307]"),
    n("com.instagram.android:id/cam_dest_story", "RELACJA", "RELACJA", True),
]

GALLERY_STORY = [
    n("com.instagram.android:id/gallery_title_text", "Dodaj do relacji"),
    n("com.instagram.android:id/gallery_folder_menu_tv", "Ostatnie", clickable=True),
    n("com.instagram.android:id/cam_dest_story", "RELACJA", "RELACJA", True),
    n("com.instagram.android:id/gallery_grid_item_thumbnail",
      desc="Usunięto zaznaczenie Miniatura zdjęcia utworzono 5 września 2026 18:14",
      clickable=True),
]

EDITOR = [
    n("com.instagram.android:id/media_thumbnail_tray_button", "", "Dalej", True,
      "[864,2201][1054,2317]"),
    n("com.instagram.android:id/media_thumbnail_tray_button_text", "Dalej"),
    n("com.instagram.android:id/button_back", "", "Wstecz", True),
]

CAPTION = [
    n("com.instagram.android:id/action_bar_textview_title", "Nowy post"),
    n("com.instagram.android:id/caption_input_text_view", "Dodaj opis…",
      "Napisz opis", True, "[42,945][1038,1071]"),
    n("com.instagram.android:id/share_footer_button", "", "Udostępnij", True,
      "[42,2224][1038,2340]"),
]

STORY_SHARE = [
    n("com.instagram.android:id/cancel_button", "", "Anuluj", True),
    n("com.instagram.android:id/add_caption_textview", "Dodaj napisy…",
      clickable=True),
    n("com.instagram.android:id/your_story_share_shortcut_button", "",
      "Twoja relacja", True, "[32,2201][468,2316]"),
    n("", "Bliscy znajomi", "Bliscy znajomi", True, "[483,2201][918,2316]"),
]

HOME = [
    n("com.instagram.android:id/feed_tab", "", "Strona główna", True),
    n("com.instagram.android:id/notification"),
    n("com.instagram.android:id/main_feed_action_bar"),
    n("com.instagram.android:id/title_text", "Dla Ciebie"),
]

SHARE_LOADING = [
    n("com.instagram.android:id/loading_progress_bar"),
]

FIRST_POST_CTA = [
    n("com.instagram.android:id/igds_empty_state_title", "Utwórz pierwszy post"),
    n("com.instagram.android:id/igds_empty_state_primary_action_button",
      "", "Utwórz", True, "[407,1354][674,1486]"),
    n("com.instagram.android:id/profile_header_create_button", "", "Utwórz nowe"),
    n("com.instagram.android:id/action_bar_title", "karomazur4"),
    n("com.instagram.android:id/profile_tab_icon_view"),
]

SHARE_EDU = [
    n("com.instagram.android:id/caption_input_text_view", "Dodaj opis…",
      "Napisz opis", True),
    n("com.instagram.android:id/share_footer_button", "", "Udostępnij", True,
      "[48,2136][1032,2268]"),
    n("", "Udostępnianie postów"),
    n("com.instagram.android:id/igds_button", "", "", True,
      "[96,1827][984,1971]"),
    n("ig_text", "OK"),
]

CREATE_SHEET = [
    n("", "Post", "Post", True, "[48,1600][400,1720]"),
    n("", "Rolka", "Rolka", True, "[48,1740][400,1860]"),
    n("", "Relacja", "Relacja", True, "[48,1880][400,2000]"),
]

ALBUM_SHEET = [
    n("com.instagram.android:id/title_text_view", "Wybierz album"),
    n("com.instagram.android:id/gallery_folder_menu_tv", "Ostatnie", clickable=True),
    n("ig_text", "Ostatnie"),
    n("ig_text", "bot_v2", clickable=True, bounds="[42,800][200,860]"),
    n("ig_text", "Zobacz wszystkie", clickable=True),
]


class IdentifyTests(unittest.TestCase):
    def test_resolver_feed_stories(self):
        self.assertEqual(identify_creator(RESOLVER), "resolver")

    def test_gallery_post_nowy_post(self):
        self.assertEqual(identify_creator(GALLERY_POST), "gallery_post")
        self.assertTrue(is_recents_folder(GALLERY_POST))

    def test_gallery_story_dodaj_do_relacji(self):
        self.assertEqual(identify_creator(GALLERY_STORY), "gallery_story")

    def test_editor_dalej(self):
        self.assertEqual(identify_creator(EDITOR), "editor")

    def test_caption_udostepnij(self):
        self.assertEqual(identify_creator(CAPTION), "caption")

    def test_story_twoja_relacja(self):
        self.assertEqual(identify_creator(STORY_SHARE), "story_share")

    def test_home(self):
        self.assertEqual(identify_creator(HOME), "home")

    def test_challenge(self):
        ch = [n("com.instagram.android:id/challenge_two_factor")]
        self.assertEqual(identify_creator(ch), "challenge")

    def test_share_loading_spinner(self):
        self.assertEqual(identify_creator(SHARE_LOADING), "share_loading")

    def test_first_post_cta(self):
        self.assertEqual(identify_creator(FIRST_POST_CTA), "first_post_cta")

    def test_share_edu_przed_caption(self):
        self.assertEqual(identify_creator(SHARE_EDU), "share_edu")

    def test_create_sheet_plus(self):
        self.assertEqual(identify_creator(CREATE_SHEET), "create_sheet")


class FakeDev:
    def __init__(self, frames):
        self.frames = list(frames)
        self.i = 0
        self.taps = []
        self.shells = []

    def hierarchy(self, force=False):
        if not self.frames:
            return []
        idx = min(self.i, len(self.frames) - 1)
        # po tapie test może ++ ręcznie; tu zostajemy na bieżącej
        return self.frames[idx]

    def tap(self, x, y):
        self.taps.append((x, y))
        if self.i < len(self.frames) - 1:
            self.i += 1

    def swipe(self, x1, y1, x2, y2, duration_ms=350):
        self.shells.append(f"swipe {x1},{y1}->{x2},{y2}")
        if self.i < len(self.frames) - 1:
            self.i += 1

    def press_back(self):
        self.shells.append("back")
        if self.i < len(self.frames) - 1:
            self.i += 1

    def shell(self, cmd):
        self.shells.append(cmd)
        return ""

    def wait_for_stable_ui(self, **kw):
        return True


class FakeCtx:
    def __init__(self, frames, pkg="com.instagram.android", scratch=None):
        self.device = FakeDev(frames)
        self.ig_package = pkg
        self.account = SimpleNamespace(account_id="technature699")
        self.logs = []
        self.ht = None
        self.scratch = scratch if scratch is not None else {}
        self.navigator = SimpleNamespace(ensure=lambda *_a, **_k: True)

    def log(self, kind, **payload):
        self.logs.append((kind, payload))


class DriveCreatorTests(unittest.TestCase):
    def test_post_editor_caption_share_home(self):
        # dwa tapy na caption (pole + Udostępnij) — ta sama klatka
        ctx = FakeCtx([EDITOR, CAPTION, CAPTION, HOME])
        ok, screen = drive_creator(ctx, {"kind": "post", "caption": "hej tech"})
        self.assertTrue(ok)
        self.assertEqual(screen, "home")
        self.assertTrue(any("hej%stech" in s or "hej tech" in s or "input text"
                            in s for s in ctx.device.shells))
        self.assertGreaterEqual(len(ctx.device.taps), 2)

    def test_story_twoja_relacja_nie_bliscy(self):
        ctx = FakeCtx([STORY_SHARE, HOME])
        ok, screen = drive_creator(ctx, {"kind": "story", "caption": ""})
        self.assertTrue(ok)
        self.assertEqual(screen, "home")
        # tap w Twoja relacja (x~250), nie w Bliscy (~700)
        xs = [t[0] for t in ctx.device.taps]
        self.assertTrue(xs)
        self.assertLess(xs[0], 400)

    def test_challenge_abort(self):
        ctx = FakeCtx([[n("com.instagram.android:id/challenge_two_factor")]])
        ok, screen = drive_creator(ctx, {"kind": "post"})
        self.assertFalse(ok)
        self.assertEqual(screen, "challenge")

    def test_domyslnie_tapuje_udostepnij(self):
        """Domyślnie jedziemy do końca — dry-run nie jest włączony."""
        ctx = FakeCtx([EDITOR, CAPTION, HOME])
        ok, screen = drive_creator(ctx, {"kind": "post", "caption": ""})
        self.assertTrue(ok)
        self.assertEqual(screen, "home")
        self.assertGreaterEqual(len(ctx.device.taps), 2)

    def test_stop_before_share_nie_tapuje_udostepnij(self):
        ctx = FakeCtx([EDITOR])
        ok, screen = drive_creator(
            ctx, {"kind": "post", "caption": "hej"},
            stop_before_share=True,
        )
        self.assertTrue(ok)
        self.assertEqual(screen, "editor")
        self.assertEqual(ctx.device.taps, [])

    def test_stop_before_share_story_nie_tapuje_twoja_relacja(self):
        ctx = FakeCtx([STORY_SHARE])
        ok, screen = drive_creator(
            ctx, {"kind": "story", "caption": ""},
            stop_before_share=True,
        )
        self.assertTrue(ok)
        self.assertEqual(screen, "story_share")
        self.assertEqual(ctx.device.taps, [])

    def test_gallery_recents_nie_tap_dalej(self):
        ctx = FakeCtx([GALLERY_POST])
        ok, screen = drive_creator(ctx, {"kind": "post"})
        self.assertFalse(ok)
        self.assertEqual(screen, "gallery_post")

    def test_share_edu_ok_potem_udostepnij(self):
        ctx = FakeCtx([SHARE_EDU, CAPTION, HOME])
        ok, screen = drive_creator(ctx, {"kind": "post", "caption": ""})
        self.assertTrue(ok)
        self.assertEqual(screen, "home")
        self.assertGreaterEqual(len(ctx.device.taps), 2)

    def test_first_post_cta_tap_utworz(self):
        ctx = FakeCtx([FIRST_POST_CTA, GALLERY_POST])
        ok, screen = drive_creator(ctx, {"kind": "post"})
        self.assertFalse(ok)
        self.assertEqual(screen, "gallery_post")
        self.assertTrue(ctx.device.taps)

    def test_share_loading_oddaje_po_3(self):
        ctx = FakeCtx([SHARE_LOADING])
        ok, screen = drive_creator(ctx, {"kind": "post"})
        self.assertFalse(ok)
        self.assertEqual(screen, "share_loading")

    def test_create_sheet_tap_post(self):
        ctx = FakeCtx([CREATE_SHEET, GALLERY_POST])
        ok, screen = drive_creator(ctx, {"kind": "post"})
        self.assertFalse(ok)
        self.assertEqual(screen, "gallery_post")
        self.assertTrue(ctx.device.taps)


class FallbackAlbumTests(unittest.TestCase):
    def test_recents_bez_albumu_fail(self):
        ctx = FakeCtx([GALLERY_POST, GALLERY_POST])
        self.assertFalse(fallback_pick_canonical(ctx, {
            "kind": "post",
            "account": "technature699",
            "files": [{"canonical_name": "technature699__p1__x.jpg"}],
        }))

    def test_album_bot_v2_jedna_miniatura(self):
        after = [
            n("com.instagram.android:id/gallery_folder_menu_tv", "bot_v2"),
            n("com.instagram.android:id/gallery_grid_item_thumbnail",
              desc="x", clickable=True, bounds="[10,10][20,20]"),
        ]
        ctx = FakeCtx([GALLERY_POST, ALBUM_SHEET, after])
        self.assertTrue(fallback_pick_canonical(ctx, {
            "kind": "post",
            "account": "technature699",
            "files": [{"canonical_name": "technature699__p1__x.jpg"}],
        }))

    def test_needle_post_nie_lapie_nowy_post(self):
        mixed = [
            n("com.instagram.android:id/new_post_title", "Nowy post"),
            n("ig_text", "post", clickable=False, bounds="[568,1164][656,1214]"),
        ]
        picked = _pick_named_album(mixed, ["post", "klon1"])
        self.assertIsNotNone(picked)
        self.assertEqual(picked.get("text"), "post")

    def test_stats_1_to_nie_pierwszy_post(self):
        ctx = FakeCtx([HOME], scratch={"stats": {"posts": 1}})
        self.assertFalse(is_first_post_account(ctx))

    def test_stats_0_to_pierwszy_post(self):
        ctx = FakeCtx([HOME], scratch={"stats": {"posts": 0}})
        self.assertTrue(is_first_post_account(ctx))

    def test_kolejka_published_to_nie_pierwszy(self):
        ctx = FakeCtx([HOME])
        store = SimpleNamespace(read_all=lambda: [{"status": "published"}])
        self.assertFalse(is_first_post_account(ctx, store))

    def test_zwykly_plus_nie_szuka_cta(self):
        """Konto z postami: plus na home, nie empty-state."""
        after = [
            n("com.instagram.android:id/gallery_folder_menu_tv", "klon1"),
            n("com.instagram.android:id/gallery_grid_item_thumbnail",
              desc="x", clickable=True, bounds="[10,10][20,20]"),
        ]
        home_plus = HOME + [
            n("com.instagram.android:id/action_bar_buttons_container_left",
              "", "", False, "[0,83][127,230]"),
        ]
        ctx = FakeCtx([home_plus, GALLERY_POST, ALBUM_SHEET, after],
                      scratch={"stats": {"posts": 1}})
        ctx.account = SimpleNamespace(account_id="klon1")
        self.assertTrue(open_creator_gallery(
            ctx, {"kind": "post", "account": "klon1",
                  "files": [{"canonical_name": "klon1__p1__x.jpg"}]},
            first_post=False))
        self.assertTrue(any(k == "publish_media.gallery_path"
                            and p.get("first_post") is False
                            for k, p in ctx.logs))

    def test_pierwszy_post_bez_cta_fail(self):
        ctx = FakeCtx([HOME], scratch={"stats": {"posts": 0}})
        self.assertFalse(open_creator_gallery(
            ctx, {"kind": "post", "files": [{"canonical_name": "a.jpg"}]},
            first_post=True))
        self.assertTrue(any(k == "publish_media.first_post_no_cta"
                            for k, _ in ctx.logs))

    def test_album_wiele_miniatur_bierze_pierwsza(self):
        after = [
            n("com.instagram.android:id/gallery_folder_menu_tv", "post"),
            n("com.instagram.android:id/gallery_grid_item_thumbnail",
              desc="nowy", clickable=True, bounds="[10,10][20,20]"),
            n("com.instagram.android:id/gallery_grid_item_thumbnail",
              desc="stary", clickable=True, bounds="[30,10][40,20]"),
        ]
        ctx = FakeCtx([GALLERY_POST, ALBUM_SHEET, after])
        self.assertTrue(fallback_pick_canonical(ctx, {
            "kind": "post",
            "account": "klon1",
            "files": [{"canonical_name": "klon1__p2__y.jpg"}],
        }))
        self.assertEqual(ctx.device.taps[-1], (15, 15))


class SendIntentTests(unittest.TestCase):
    def test_handler_post_vs_story(self):
        self.assertEqual(handler_component("post"), COMP_FEED)
        self.assertEqual(handler_component("story"), COMP_STORY)

    def test_refuse_dcim(self):
        self.assertTrue(refuse_path("/sdcard/DCIM/Camera/a.jpg"))
        self.assertFalse(refuse_path(
            "/sdcard/Pictures/bot_v2/technature699/post/x.jpg"))

    def test_story_component_w_cmd(self):
        ctx = FakeCtx([HOME], pkg="com.instagram.android")
        item = {
            "kind": "story",
            "account": "technature699",
            "phone_path": "/sdcard/Pictures/bot_v2/technature699/story/a.jpg",
            "content_uri": "content://media/external/images/media/214",
            "files": [{"canonical_name": "technature699__p1__a.jpg",
                       "mime": "image/jpeg"}],
        }
        self.assertTrue(send_to_clone(ctx, item))
        cmd = ctx.device.shells[-1]
        self.assertIn(COMP_STORY, cmd)
        self.assertIn("com.instagram.android/", cmd)
        self.assertIn("content://media/external/images/media/214", cmd)
        self.assertNotIn("DCIM", cmd)
        self.assertNotIn("-p ", cmd)

    def test_spinner_to_fail_intenta(self):
        ctx = FakeCtx([SHARE_LOADING], pkg="com.instagram.androif")
        item = {
            "kind": "post",
            "account": "klon1",
            "phone_path": "/sdcard/Pictures/bot_v2/klon1/post/a.jpg",
            "content_uri": "content://media/external/images/media/1000000030",
            "files": [{"canonical_name": "klon1__p1__a.jpg",
                       "mime": "image/jpeg"}],
        }
        self.assertFalse(send_to_clone(ctx, item))
        self.assertTrue(any(k == "publish_media.intent_spinner" for k, _ in ctx.logs))


if __name__ == "__main__":
    unittest.main()
