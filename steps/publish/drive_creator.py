"""publish.drive_creator — reszta ekranów po ACTION_SEND.

Mapa z live technature699 (2026-09-05), IG 444, język PL:

POST (ShareHandlerActivity → MediaCaptureActivity):
  editor     media_thumbnail_tray_button „Dalej”
  caption    caption_input_text_view „Dodaj opis…” + share_footer_button „Udostępnij”
  home/profile = sukces (BACK z caption lądował na profilu)

STORY (StoryShareHandlerActivity):
  story_share  your_story_share_shortcut_button „Twoja relacja”
               NIE tapamy „Bliscy znajomi”

Gdy intent wyląduje w resolverze (Feed/Messages/Reels/Stories):
  tap kafelka Feed|Stories + „Tylko raz”.

Gdy galeria Recents — nie wybieramy miniatury; wołamy fallback album
albo przełączamy dest (OPUBLIKUJ / RELACJA) i szukamy folderu.
"""
from __future__ import annotations

from v2.runner.step import StepResult
from v2.steps.publish._ui import (
    CLOSE_FRIENDS_TEXTS,
    DISMISS_TEXTS,
    ID_CAPTION,
    ID_DEST_FEED,
    ID_DEST_STORY,
    ID_EMPTY_CTA,
    ID_GALLERY_PREVIEW,
    ID_IGDS_BUTTON,
    ID_NEXT_TV,
    ID_RESOLVER_ONCE,
    ID_SHARE_FOOTER,
    ID_STORY_CAPTION,
    ID_STORY_YOUR,
    ID_TRAY_DALEJ,
    find_id,
    find_text,
    flat_of,
    identify_creator,
    is_recents_folder,
    node_text,
    tap_node,
    type_caption,
)
from v2.steps.registry import register

MAX_SCREENS = 10


def _kind(item: dict) -> str:
    return item.get("kind") or "post"


def _tap_resolver(ctx, flat: list[dict], kind: str) -> bool:
    """Kafelek Feed vs Stories — text2 z dumpa 06_resolver. Potem Tylko raz."""
    want = ("stories", "relacj") if kind == "story" else ("feed", "opublikuj")
    # Klikamy kontener z etykietą, nie „Zawsze” (nie przypinamy handlera).
    n = find_text(flat, *want)
    if n and tap_node(ctx, n):
        once = find_id(flat_of(ctx), ID_RESOLVER_ONCE) or find_text(
            flat_of(ctx), "tylko raz", "just once")
        if once:
            tap_node(ctx, once)
        return True
    return False


def _dismiss_soft(ctx, flat: list[dict]) -> bool:
    """Nie teraz / OK / Odrzuć — nie Bliscy znajomi."""
    if find_text(flat, *CLOSE_FRIENDS_TEXTS) and find_id(flat, ID_STORY_YOUR):
        return False
    n = find_text(flat, *DISMISS_TEXTS, clickable_only=True)
    if n and not any(c in node_text(n) for c in CLOSE_FRIENDS_TEXTS):
        return tap_node(ctx, n)
    return False


def _tap_share_edu(ctx, flat: list[dict]) -> bool:
    """Pierwszy post: arkusz „Udostępnianie postów” — tylko niebieski OK."""
    btn = find_id(flat, ID_IGDS_BUTTON)
    if btn:
        return tap_node(ctx, btn)
    ok = find_text(flat, "ok")
    return tap_node(ctx, ok) if ok else False


def _tap_first_post_cta(ctx, flat: list[dict]) -> bool:
    """Przycisk „Utwórz” pod empty-state — bywa pod navem, wtedy swipe."""
    btn = find_id(flat, ID_EMPTY_CTA) or find_text(
        flat, "utwórz", "create", clickable_only=True)
    if btn and tap_node(ctx, btn):
        return True
    # Tytuł widać, CTA schowane — odsłoń jak na KB2003.
    if find_text(flat, "pierwszy post", "first post"):
        try:
            ctx.device.swipe(540, 1900, 540, 1100, 400)
        except Exception:
            return False
        if hasattr(ctx.device, "wait_for_stable_ui"):
            try:
                ctx.device.wait_for_stable_ui(max_wait_s=1.5)
            except Exception:
                pass
        nxt = find_id(flat_of(ctx), ID_EMPTY_CTA) or find_text(
            flat_of(ctx), "utwórz", "create", clickable_only=True)
        return tap_node(ctx, nxt)
    return False


def _tap_create_sheet_post(ctx, flat: list[dict]) -> bool:
    """Wiersz Post / Opublikuj — nie „pierwszy post” i nie Rolka/Relacja."""
    dest = find_id(flat, ID_DEST_FEED)
    if dest:
        return tap_node(ctx, dest)
    for n in flat:
        blob = node_text(n).strip()
        if "pierwszy" in blob:
            continue
        if blob in ("post", "opublikuj") or blob.startswith("post "):
            return tap_node(ctx, n)
    return False


def drive_creator(ctx, item: dict, *, stop_before_share: bool = False) -> tuple[bool, str]:
    """Przejdź ekrany creatora. (ok, screen_końcowy).

    `stop_before_share` — dry-run: nie tapamy Udostępnij / Twoja relacja.
    """
    kind = _kind(item)
    caption = (item.get("caption") or "").strip()
    shared = False
    last = "unknown"
    loading_hits = 0
    for i in range(MAX_SCREENS):
        if hasattr(ctx.device, "wait_for_stable_ui"):
            try:
                ctx.device.wait_for_stable_ui(max_wait_s=2.0)
            except Exception:
                pass
        flat = flat_of(ctx)
        last = identify_creator(flat)
        ctx.log("publish.drive_screen", i=i, screen=last, item_kind=kind)
        if last == "challenge":
            return False, "challenge"
        if last in ("home", "profile") and shared:
            return True, last
        if last == "share_edu":
            if not _tap_share_edu(ctx, flat):
                return False, "share_edu"
            continue
        if last == "share_loading":
            # Klon OnePlus: spinner nie wstaje — 3 klatki i oddajemy fallback.
            loading_hits += 1
            if loading_hits >= 3:
                return False, "share_loading"
            continue
        if last == "first_post_cta":
            if not _tap_first_post_cta(ctx, flat):
                return False, "first_post_cta"
            continue
        if last == "create_sheet":
            if kind != "post":
                return False, "create_sheet"
            if not _tap_create_sheet_post(ctx, flat):
                return False, "create_sheet"
            continue
        if last == "resolver":
            if not _tap_resolver(ctx, flat, kind):
                return False, "resolver"
            continue
        if last == "editor":
            # Dry-run: editor = intent wstał — nie idziemy w caption/Udostępnij.
            if stop_before_share:
                return True, last
            n = find_id(flat, ID_TRAY_DALEJ) or find_text(flat, "dalej", "next")
            if not tap_node(ctx, n):
                return False, "editor"
            continue
        if last == "caption":
            field = find_id(flat, ID_CAPTION)
            if caption and field:
                tap_node(ctx, field)
                type_caption(ctx, caption)
                flat = flat_of(ctx)
            if stop_before_share:
                return True, last
            btn = find_id(flat, ID_SHARE_FOOTER) or find_text(
                flat, "udostępnij", "share")
            if not tap_node(ctx, btn):
                return False, "caption"
            shared = True
            continue
        if last == "story_share":
            if caption:
                cap = find_id(flat, ID_STORY_CAPTION)
                if cap:
                    tap_node(ctx, cap)
                    type_caption(ctx, caption)
                    flat = flat_of(ctx)
            if stop_before_share:
                return True, last
            btn = find_id(flat, ID_STORY_YOUR) or find_text(
                flat, "twoja relacja", "your story")
            # Świadomie omijamy Bliscy znajomi.
            if btn and any(c in node_text(btn) for c in CLOSE_FRIENDS_TEXTS):
                return False, "story_close_friends"
            if not tap_node(ctx, btn):
                return False, "story_share"
            shared = True
            continue
        if last == "story_camera":
            # Intent powinien ominąć aparat — tu tylko wejście do galerii,
            # pick pliku robi fallback (nie Recents).
            gal = find_id(flat, ID_GALLERY_PREVIEW) or find_text(flat, "galeria", "gallery")
            if gal:
                tap_node(ctx, gal)
            dest = find_id(flat, ID_DEST_STORY)
            if dest:
                tap_node(ctx, dest)
            return False, "story_camera"
        if last in ("gallery_post", "gallery_story", "gallery_reel", "gallery_dest"):
            if kind == "post":
                d = find_id(flat, ID_DEST_FEED) or find_text(
                    flat, "opublikuj", "post")
                if d:
                    tap_node(ctx, d)
                    flat = flat_of(ctx)
                nxt = find_id(flat, ID_NEXT_TV)
                # Dalej bez wyboru kanonicznego = Recents — nie tapamy.
                if nxt and not is_recents_folder(flat):
                    tap_node(ctx, nxt)
                    continue
            else:
                d = find_id(flat, ID_DEST_STORY) or find_text(
                    flat, "relacja", "story")
                if d:
                    tap_node(ctx, d)
            return False, last
        if last in ("discard_dialog", "not_now"):
            _dismiss_soft(ctx, flat)
            if shared:
                return True, last
            continue
        if last in ("home", "profile") and not shared:
            return False, last
        # Nieznany popup — spróbuj miękki dismiss, inaczej stop.
        if _dismiss_soft(ctx, flat):
            continue
        return False, last
    return shared, last


@register
class PublishDriveCreator:
    name = "publish.drive_creator"
    requires_screen = None

    def run(self, ctx, params):
        item = params.get("item") or {}
        ok, screen = drive_creator(
            ctx, item,
            stop_before_share=bool(params.get("stop_before_share")),
        )
        if not ok:
            return StepResult.failed(f"creator stop na {screen}", screen=screen)
        return StepResult.ok(screen=screen, kind=_kind(item))
