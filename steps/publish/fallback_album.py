"""publish.fallback_album — gdy intent wylądował w galerii, nie w edytorze.

Live: miniatury mają tylko datę („Miniatura zdjęcia utworzono …”), NIE nazwę
kanoniczną. Recents („Ostatnie”) jest zakazane.

Szukamy albumu: bot_v2 / alias konta / kind (post|story). Gdy album ma
dokładnie jedną miniaturę — to nasz plik. Wiele miniatur bez nazwy = fail
(uczciwa luka, nie zgadujemy Recents).
"""
from __future__ import annotations

from v2.runner.step import StepResult
from v2.steps.publish._ui import (
    ALBUM_HINTS,
    ID_CREATE_PLUS,
    ID_DEST_FEED,
    ID_EMPTY_CTA,
    ID_FOLDER_TV,
    ID_PROFILE_CREATE,
    RECENTS_LABELS,
    find_id,
    find_text,
    flat_of,
    identify_creator,
    is_recents_folder,
    node_text,
    tap_node,
)
from v2.steps.registry import register

THUMB_ID = "com.instagram.android:id/gallery_grid_item_thumbnail"


def _canonical(item: dict) -> str:
    files = item.get("files") or [{}]
    return (files[0] or {}).get("canonical_name") or ""


def _album_needles(item: dict, account: str) -> list[str]:
    kind = (item.get("kind") or "post").lower()
    name = _canonical(item)
    stem = name.rsplit(".", 1)[0] if name else ""
    out = list(ALBUM_HINTS)
    if account:
        out.append(account.lower())
    # kind „post” tylko jako cała etykieta kafelka (nie „Nowy post”).
    if kind:
        out.append(kind)
    if stem:
        out.append(stem.lower())
    return [x for x in out if x]


def _open_album_sheet(ctx, flat: list[dict]) -> bool:
    folder = find_id(flat, ID_FOLDER_TV)
    if folder:
        return tap_node(ctx, folder)
    return False


def _pick_named_album(flat: list[dict], needles: list[str]) -> dict | None:
    """Wiersz albumu (ig_text / dowolny text) — nie Ostatnie/Recents."""
    for n in flat:
        rid = n.get("resource-id") or ""
        # Tytuł „Nowy post” nie jest albumem.
        if rid.endswith("new_post_title"):
            continue
        blob = node_text(n).strip()
        if not blob:
            continue
        if any(r in blob for r in RECENTS_LABELS):
            continue
        for need in needles:
            if not need:
                continue
            # Krótki needle (post) tylko jako cała etykieta kafelka.
            if len(need) < 5:
                if blob == need:
                    return n
                continue
            if need in blob:
                return n
    return None


def _wait_ui(ctx, s: float = 2.0) -> None:
    if hasattr(ctx.device, "wait_for_stable_ui"):
        try:
            ctx.device.wait_for_stable_ui(max_wait_s=s)
        except Exception:
            pass


def _leave_share_spinner(ctx) -> str:
    """BACK ze spinnera SHARE — inaczej plus/CTA są pod czarnym ekranem."""
    flat = flat_of(ctx)
    screen = identify_creator(flat)
    if screen != "share_loading":
        return screen
    try:
        ctx.device.press_back()
    except Exception:
        pass
    _wait_ui(ctx, 2.0)
    return identify_creator(flat_of(ctx))


def is_first_post_account(ctx, store=None) -> bool:
    """Pusty profil = pierwszy post. Już 1+ postów = zwykły + / album.

    Źródła (kolejność): stats_snapshot, kolejka published, CTA na UI.
    Brak danych → zwykła ścieżka (nie zgadujemy empty-state).
    """
    scratch = getattr(ctx, "scratch", None) or {}
    posts = (scratch.get("stats") or {}).get("posts")
    if isinstance(posts, int):
        return posts <= 0
    if store is not None:
        try:
            if any((r.get("status") == "published") for r in store.read_all()):
                return False
        except Exception:
            pass
    flat = flat_of(ctx)
    screen = identify_creator(flat)
    if screen == "first_post_cta" or find_text(flat, "pierwszy post", "first post"):
        return True
    return False


def _tap_create_plus(ctx, flat: list[dict]) -> bool:
    """Plus na feedzie albo „Utwórz nowe” na profilu — nie empty-state."""
    plus = find_id(flat, ID_CREATE_PLUS)
    if plus and tap_node(ctx, plus):
        return True
    prof = find_id(flat, ID_PROFILE_CREATE)
    if prof and tap_node(ctx, prof):
        return True
    alt = find_text(flat, "utwórz nowe")
    return tap_node(ctx, alt) if alt else False


def _tap_sheet_or_dest_post(ctx, flat: list[dict]) -> bool:
    """Arkusz + albo dest OPUBLIKUJ — nie Rolka/Relacja."""
    dest = find_id(flat, ID_DEST_FEED) or find_text(flat, "opublikuj")
    if dest and "relacj" not in node_text(dest):
        return tap_node(ctx, dest)
    for n in flat:
        blob = node_text(n).strip()
        if "pierwszy" in blob:
            continue
        if blob in ("post", "opublikuj") or blob.startswith("post "):
            return tap_node(ctx, n)
    return False


def open_first_post_gallery(ctx, item: dict) -> bool:
    """Pusty profil: BACK → CTA „Utwórz pierwszy post” → album. Nie Recents."""
    screen = _leave_share_spinner(ctx)
    if screen in ("editor", "caption", "story_share"):
        return True
    if screen.startswith("gallery"):
        return fallback_pick_canonical(ctx, item)
    nav = getattr(ctx, "navigator", None)
    if nav is not None and hasattr(nav, "ensure"):
        try:
            nav.ensure("profile_self")
        except Exception:
            pass
    flat = flat_of(ctx)
    btn = find_id(flat, ID_EMPTY_CTA)
    if not btn and find_text(flat, "pierwszy post", "first post"):
        try:
            ctx.device.swipe(540, 1900, 540, 1100, 400)
        except Exception:
            return False
        _wait_ui(ctx, 1.5)
        flat = flat_of(ctx)
        btn = find_id(flat, ID_EMPTY_CTA) or find_text(
            flat, "utwórz", "create", clickable_only=True)
    if not btn or not tap_node(ctx, btn):
        ctx.log("publish_media.first_post_no_cta")
        return False
    _wait_ui(ctx, 2.5)
    return fallback_pick_canonical(ctx, item)


def open_normal_create_gallery(ctx, item: dict) -> bool:
    """Konto z postami: BACK → + / Utwórz nowe → Post → album. Nie empty-state."""
    screen = _leave_share_spinner(ctx)
    if screen in ("editor", "caption", "story_share"):
        return True
    if screen.startswith("gallery"):
        return fallback_pick_canonical(ctx, item)
    if screen == "first_post_cta":
        ctx.log("publish_media.normal_blocked_first_cta")
        return False
    nav = getattr(ctx, "navigator", None)
    if screen not in ("home", "profile") and nav is not None and hasattr(nav, "ensure"):
        try:
            nav.ensure("home_feed")
        except Exception:
            pass
    flat = flat_of(ctx)
    if not _tap_create_plus(ctx, flat):
        ctx.log("publish_media.normal_no_plus")
        return False
    _wait_ui(ctx, 2.5)
    flat = flat_of(ctx)
    screen = identify_creator(flat)
    if screen == "create_sheet" or find_id(flat, ID_DEST_FEED):
        if not _tap_sheet_or_dest_post(ctx, flat):
            ctx.log("publish_media.normal_no_post_row")
            return False
        _wait_ui(ctx, 2.0)
    return fallback_pick_canonical(ctx, item)


def open_creator_gallery(ctx, item: dict, *, first_post: bool) -> bool:
    """Rozdział: pierwszy post vs zwykły plus — nie mieszamy ścieżek."""
    ctx.log("publish_media.gallery_path", first_post=first_post)
    if first_post:
        return open_first_post_gallery(ctx, item)
    return open_normal_create_gallery(ctx, item)


def fallback_pick_canonical(ctx, item: dict) -> bool:
    """Szukaj albumu po nazwie kanonicznej / folderze bota. Nie Recents."""
    name = _canonical(item)
    if not name:
        ctx.log("publish_media.fallback_name", name="")
        return False
    acc = getattr(getattr(ctx, "account", None), "account_id", None) or (
        item.get("account") or "")
    needles = _album_needles(item, str(acc))
    ctx.log("publish_media.fallback_name", name=name, needles=needles)
    flat = flat_of(ctx)
    screen = identify_creator(flat)
    if screen in ("editor", "caption", "story_share"):
        return True
    if is_recents_folder(flat) or screen.startswith("gallery"):
        _open_album_sheet(ctx, flat)
        if hasattr(ctx.device, "wait_for_stable_ui"):
            try:
                ctx.device.wait_for_stable_ui(max_wait_s=2.0)
            except Exception:
                pass
        flat = flat_of(ctx)
        # „Zobacz wszystkie” gdy lista skrócona (dump 04_folder_menu).
        more = find_text(flat, "zobacz wszystkie", "see all")
        if more:
            tap_node(ctx, more)
            flat = flat_of(ctx)
        album = _pick_named_album(flat, needles)
        if not album:
            ctx.log("publish_media.fallback_no_album", name=name)
            return False
        tap_node(ctx, album)
        if hasattr(ctx.device, "wait_for_stable_ui"):
            try:
                ctx.device.wait_for_stable_ui(max_wait_s=2.0)
            except Exception:
                pass
        flat = flat_of(ctx)
        thumbs = [n for n in flat if (n.get("resource-id") or "") == THUMB_ID]
        # Jedna miniatura w albumie konta = ten plik.
        if len(thumbs) == 1:
            return tap_node(ctx, thumbs[0])
        # content-desc czasem zawiera fragment nazwy po skanie — wtedy OK.
        for t in thumbs:
            blob = node_text(t)
            if name.lower() in blob or any(n in blob for n in needles if len(n) > 4):
                return tap_node(ctx, t)
        # Album bota (nie Recents): pierwsza miniatura = najnowszy push.
        if thumbs and not is_recents_folder(flat):
            ctx.log("publish_media.fallback_newest", thumbs=len(thumbs), name=name)
            return tap_node(ctx, thumbs[0])
        ctx.log("publish_media.fallback_ambiguous", thumbs=len(thumbs), name=name)
        return False
    return False


@register
class PublishFallbackAlbum:
    name = "publish.fallback_album"
    requires_screen = None

    def run(self, ctx, params):
        item = params.get("item") or {}
        if fallback_pick_canonical(ctx, item):
            return StepResult.ok(name=_canonical(item))
        return StepResult.failed(
            "fallback: brak albumu bot_v2/alias albo za dużo miniatur "
            "(nie biorę Recents)")
