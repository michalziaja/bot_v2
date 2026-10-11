"""Wspólne helpery creatora IG — z dumpów live technature699 (2026-09-05).

Hierarchia: resource-id + tekst PL/EN. Miniatury galerii NIE mają nazwy pliku
(tylko data) — zakaz tapania Recents / „najnowsze”.
"""
from __future__ import annotations

from v2.core.screen_map import flatten_hierarchy
from v2.steps.system._ui_helpers import center, escape_input_text, normalize_ws

# --- ID z dumpów recon_publish/ (IG 444.0.0.46.85, pakiet kanoniczny) ---
ID_NEW_POST_TITLE = "com.instagram.android:id/new_post_title"
ID_NEXT_TV = "com.instagram.android:id/next_button_textview"
ID_DEST_FEED = "com.instagram.android:id/cam_dest_feed"
ID_DEST_STORY = "com.instagram.android:id/cam_dest_story"
ID_DEST_CLIPS = "com.instagram.android:id/cam_dest_clips"
ID_GALLERY_TITLE = "com.instagram.android:id/gallery_title_text"
ID_FOLDER_TV = "com.instagram.android:id/gallery_folder_menu_tv"
ID_ALBUM_TITLE = "com.instagram.android:id/title_text_view"
ID_TRAY_DALEJ = "com.instagram.android:id/media_thumbnail_tray_button"
ID_CAPTION = "com.instagram.android:id/caption_input_text_view"
ID_SHARE_FOOTER = "com.instagram.android:id/share_footer_button"
ID_STORY_YOUR = "com.instagram.android:id/your_story_share_shortcut_button"
ID_STORY_CAPTION = "com.instagram.android:id/add_caption_textview"
ID_GALLERY_PREVIEW = "com.instagram.android:id/gallery_preview_button"
ID_CAMERA_SHUTTER = "com.instagram.android:id/camera_shutter_button"
ID_RESOLVER_LIST = "android:id/resolver_list"
ID_RESOLVER_ONCE = "android:id/button_once"
ID_CREATE_PLUS = "com.instagram.android:id/action_bar_buttons_container_left"
ID_PROFILE_CREATE = "com.instagram.android:id/profile_header_create_button"
ID_FEED_TAB = "com.instagram.android:id/feed_tab"
ID_CHALLENGE = "com.instagram.android:id/challenge_two_factor"
# OnePlus / pierwszy post (IG 446, live klon1 2026-09-10):
# ShareHandler wisi na czarnym spinnerze; CTA profilu + arkusz edukacji.
ID_SHARE_LOADING = "com.instagram.android:id/loading_progress_bar"
ID_EMPTY_TITLE = "com.instagram.android:id/igds_empty_state_title"
ID_EMPTY_CTA = "com.instagram.android:id/igds_empty_state_primary_action_button"
ID_IGDS_BUTTON = "com.instagram.android:id/igds_button"

# Album / Recents — nigdy nie tapamy „Ostatnie” jako źródła pliku.
RECENTS_LABELS = ("ostatnie", "recents", "recent")
# Albumy, które mogą trzymać nasz push (folder konta).
ALBUM_HINTS = ("bot_v2", "bot v2")

# Dialogi po Udostępnij / BACK — nie Bliscy znajomi jako cel publikacji.
DISMISS_TEXTS = (
    "nie teraz", "not now", "ok", "anuluj", "cancel",
    "odrzuć", "discard", "pomiń", "skip",
)
CLOSE_FRIENDS_TEXTS = ("bliscy znajomi", "close friends")
CHALLENGE_TEXTS = ("weryfikacj", "two-factor", "2fa", "kod potwierdz")

# Diakrytyki → ASCII (ten sam NPE co search.type_query przy input text).
_PL_FOLD = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N",
    "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z",
})


def fold_caption(s: str) -> str:
    """Opis pod `adb input text` — bez ogonków, max 200 znaków."""
    raw = (s or "").translate(_PL_FOLD)
    return raw[:200]


def flat_of(ctx) -> list[dict]:
    """Aktualny dump — force, żeby po tapie nie czytać cache."""
    h = ctx.device.hierarchy(force=True)
    return flatten_hierarchy(h)


def node_text(n: dict) -> str:
    return normalize_ws((n.get("text") or "") + " " + (n.get("content-desc") or ""))


def find_id(flat: list[dict], rid: str) -> dict | None:
    for n in flat:
        if (n.get("resource-id") or "") == rid:
            return n
    return None


def find_text(flat: list[dict], *needles: str, clickable_only: bool = False) -> dict | None:
    """Pierwszy węzeł, którego text/desc zawiera któryś needle (lowercase)."""
    wants = [normalize_ws(x) for x in needles if x]
    for n in flat:
        if clickable_only and not n.get("clickable"):
            continue
        blob = node_text(n)
        if any(w in blob for w in wants):
            return n
    return None


def tap_node(ctx, n: dict) -> bool:
    """Tap środka bounds. HumanTouch gdy jest, inaczej device.tap."""
    if not n:
        return False
    b = n.get("bounds")
    if not b:
        return False
    try:
        x, y = center(b)
    except Exception:
        return False
    ht = getattr(ctx, "ht", None)
    if ht is not None and hasattr(ht, "tap"):
        ht.tap(x, y, pause_kind="tap")
    else:
        ctx.device.tap(x, y)
    return True


def type_caption(ctx, text: str) -> None:
    """Wpisz opis — `input text` z escape i fold PL."""
    folded = fold_caption(text)
    if not folded.strip():
        return
    escaped = escape_input_text(folded)
    try:
        ctx.device.shell(f"input text {escaped}")
    except Exception:
        pass


def identify_creator(flat: list[dict]) -> str:
    """Nazwa ekranu creatora — z ID/tekstów live, nie z pamięci.

    Kolejność: challenge → edu pierwszego posta → resolver → caption/share
    → editor → spinner SHARE → gallery → CTA pustego profilu → home/profile.
    """
    ids = {n.get("resource-id") or "" for n in flat}
    if ID_CHALLENGE in ids or find_text(flat, *CHALLENGE_TEXTS):
        return "challenge"
    # Arkusz „Udostępnianie postów” leży NA caption — musi być wcześniej.
    if find_text(flat, "udostępnianie postów", "sharing posts"):
        return "share_edu"
    if ID_RESOLVER_LIST in ids or find_text(flat, "wykonaj czynność przez",
                                           "complete action using"):
        return "resolver"
    if ID_SHARE_FOOTER in ids and ID_CAPTION in ids:
        return "caption"
    if ID_STORY_YOUR in ids:
        return "story_share"
    if ID_TRAY_DALEJ in ids:
        return "editor"
    # Arkusz + (Post / Rolka / Relacja) — nie mylić z feedem ani pierwszym postem.
    if ID_FEED_TAB not in ids and ID_NEW_POST_TITLE not in ids:
        blobs = [node_text(n) for n in flat]
        has_reel = any("rolka" in b or "reel" in b for b in blobs)
        has_story = any("relacj" in b or "story" in b for b in blobs)
        has_post_row = any(
            b.strip() in ("post", "opublikuj") or b.startswith("post ")
            for b in blobs)
        if has_reel and has_story and has_post_row:
            return "create_sheet"
    # Sam spinner, bez Dalej/galerii — ShareHandler klona na A14 wisi tu.
    creator_ready = {
        ID_TRAY_DALEJ, ID_CAPTION, ID_SHARE_FOOTER, ID_NEW_POST_TITLE,
        ID_DEST_FEED, ID_CAMERA_SHUTTER,
    }
    if ID_SHARE_LOADING in ids and not (ids & creator_ready):
        return "share_loading"
    title = ""
    gt = find_id(flat, ID_GALLERY_TITLE)
    npt = find_id(flat, ID_NEW_POST_TITLE)
    if npt:
        title = node_text(npt)
    elif gt:
        title = node_text(gt)
    if "nowy post" in title or (ID_NEW_POST_TITLE in ids and ID_NEXT_TV in ids):
        return "gallery_post"
    if "dodaj do relacji" in title or "nowa relacja" in title:
        return "gallery_story"
    if "nowa rolka" in title or "new reel" in title:
        return "gallery_reel"
    if ID_CAMERA_SHUTTER in ids and ID_DEST_STORY in ids:
        return "story_camera"
    if ID_DEST_FEED in ids or ID_DEST_STORY in ids:
        return "gallery_dest"
    if find_text(flat, *DISMISS_TEXTS) and find_text(
            flat, "wersj", "draft", "odrzucić", "discard"):
        return "discard_dialog"
    if find_text(flat, "nie teraz", "not now"):
        return "not_now"
    empty = find_id(flat, ID_EMPTY_TITLE)
    if empty and "pierwszy post" in node_text(empty):
        return "first_post_cta"
    if find_text(flat, "utwórz pierwszy post", "create your first post"):
        return "first_post_cta"
    if ID_FEED_TAB in ids and (
            "com.instagram.android:id/notification" in ids
            or "com.instagram.android:id/main_feed_action_bar" in ids):
        return "home"
    if ID_PROFILE_CREATE in ids or (
            "com.instagram.android:id/action_bar_title" in ids
            and "com.instagram.android:id/profile_tab_icon_view" in ids):
        return "profile"
    return "unknown"


def is_recents_folder(flat: list[dict]) -> bool:
    n = find_id(flat, ID_FOLDER_TV)
    if not n:
        return False
    return any(r in node_text(n) for r in RECENTS_LABELS)
