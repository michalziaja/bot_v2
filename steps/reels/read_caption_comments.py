"""reels.read_caption_comments — odczyt opisu rolki (i opcjonalnie top komentarzy).

Opis: TEN SAM flow co niche discover (`_read_reel_caption_guarded`) —
tylko TAP w caption / „…więcej", BEZ swipe po feedzie (swipe psuje rolkę).

Komentarze: osobna ścieżka — dopiero gdy `open_comments=True` (wtedy wolno
expand_sheet / swipe wewnątrz lista). Na engagement domyślnie wyłączone.

Zapis: ctx.scratch['reel_caption'], ['reel_top_comments'],
['reel_caption_slipped'].
"""
from __future__ import annotations
from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels import _sheet
from v2.steps.reels._guards import skip_if_ad


CAPTION_IDS = ("com.instagram.android:id/clips_caption_component",
               "com.instagram.android:id/caption_text_view")
# Sam comment_composer jest na UFI — nie znaczy, że panel opisu jest otwarty.
PANEL_MARKERS = ("bottom_sheet_container", "layout_comment_thread",
                 "row_comment_container", "comment_thread")
COMMENT_TEXT_ID = "com.instagram.android:id/row_comment_textview_comment"
COMMENT_USER_ID = "com.instagram.android:id/row_comment_textview_username"


@register
class ReadCaptionComments:
    name = "reels.read_caption_comments"
    requires_screen = "reels_feed"

    def run(self, ctx, params):
        skip = skip_if_ad(ctx)
        if skip:
            return skip
        result = capture_reel_caption(
            ctx,
            top_k=int(params.get("top_k", 5)),
            open_comments=bool(params.get("open_comments", False)),
        )
        if result.get("error"):
            return StepResult.failed(
                result["error"],
                caption_chars=len(result.get("caption") or ""),
                reel_slipped=bool(result.get("reel_slipped")))
        return StepResult.ok(
            caption_chars=len(result.get("caption") or ""),
            caption_found=result.get("caption") is not None,
            top_comments=len(result.get("top_comments") or []),
            panel_opened=bool(result.get("panel_opened")),
            reel_slipped=bool(result.get("reel_slipped")),
        )


def capture_reel_caption(ctx, *, top_k: int = 5,
                         open_comments: bool = False,
                         caption_attempts: int = 2) -> dict:
    """Odczyt opisu bez rejestracji kroku — też z `reels.watch` (równolegle
    do composite). `caption_attempts` zostawione dla kompatybilności API
    (ignorowane: discovery robi własny retry w guarded).

    Zwraca dict: caption, top_comments, panel_opened, reel_slipped, error?
    """
    del caption_attempts  # discovery ma własną logikę retry
    expected_author = (ctx.scratch.get("reel") or {}).get("author")
    reel_slipped = False
    panel_opened = False

    # Ten sam odczyt co niche discover: TAP + expand „więcej", zero swipe.
    # Lazy import — unikamy cyklu steps ↔ plugins przy ładowaniu modułu.
    from v2.plugins.impl.niche_discover import _read_reel_caption_guarded
    from v2.plugins.reel_log import (
        say, caption_line, caption_reading_line, comments_reading_line,
    )
    from v2.steps.reels._reel_meta import read_current_reel

    # Log PRZED gestem — terminal na równi z telefonem
    say(caption_reading_line())
    text = ""
    try:
        text = (_read_reel_caption_guarded(ctx) or "").strip()
    except Exception as exc:
        ctx.log("reels.caption.guarded_failed", error=str(exc)[:120])
        text = ""

    caption = text or None
    say(caption_line(caption))

    # Guarded zwraca "" przy zmianie rolki — sprawdzamy autora po fakcie.
    # Robimy jeden dump dla meta; później weryfikacja własnego ekranu też go
    # reuse'uje.
    try:
        flat_meta = flatten_hierarchy(ctx.device.hierarchy())
        now = read_current_reel(ctx.device, flat=flat_meta) or {}
        author_now = now.get("author")
        if expected_author and author_now and author_now != expected_author:
            reel_slipped = True
            ctx.log("reels.caption.reel_changed", expected=expected_author,
                    got=author_now)
            say(f"Uwaga: przy opisie zjechalismy na @{author_now} "
                f"(bylo @{expected_author})")
    except Exception as exc:
        ctx.log("reels.caption.author_check_failed", error=str(exc)[:80])

    ctx.scratch["reel_caption"] = caption
    ctx.scratch["reel_caption_slipped"] = reel_slipped
    ctx.log("reels.caption", found=caption is not None,
            chars=len(caption or ""), preview=(caption or "")[:80],
            reel_slipped=reel_slipped, via="niche_discover_guarded")

    # Komentarze — dopiero na życzenie; tu wolno expand_sheet (swipe w sheet).
    top: list[dict] = []
    if open_comments and not reel_slipped:
        say(comments_reading_line())
        top = _read_top_comments(ctx, top_k)
        ctx.scratch["reel_top_comments"] = top
        ctx.log("reels.top_comments", n=len(top))
        panel_opened = True  # sheet komentarzy był otwierany
    else:
        ctx.scratch.setdefault("reel_top_comments", [])

    landed = ctx.navigator.identify().top
    if landed != "reels_feed":
        ctx.log("reels.read_caption.recover", landed=landed)
        if not ctx.navigator.ensure("reels_feed", max_steps=4):
            return {
                "caption": caption, "top_comments": top,
                "panel_opened": panel_opened, "reel_slipped": reel_slipped,
                "error": f"nie wrocilismy na reels_feed (jestesmy: {landed})",
            }

    return {
        "caption": caption, "top_comments": top,
        "panel_opened": panel_opened, "reel_slipped": reel_slipped,
    }


def _panel_open(flat: list[dict]) -> bool:
    """Czy panel opisu/komentarzy jest faktycznie otwarty (testy + diagnostyka)."""
    for n in flat:
        rid = (n.get("resource-id") or "").lower()
        if any(m in rid for m in PANEL_MARKERS):
            return True
    return False


def _read_top_comments(ctx, top_k: int) -> list[dict]:
    """Otwórz sheet komentarzy → zbierz top-K wierszy → back.

    Tu gesty po liście są OK — nie ruszamy feedu rolek.
    """
    if not ctx.screen_map.anchors.get("reels.comments_icon"):
        ctx.log("reels.top_comments.no_anchor")
        return []
    c = ctx.screen_map.anchor_center("reels.comments_icon")
    if not c:
        return []
    ctx.ht.tap(*c, pause_kind="tap")
    ctx.humanizer.pause("tap")
    _sheet.expand_sheet(ctx)

    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    users, texts = [], []
    for n in flat:
        rid = n.get("resource-id")
        if rid == COMMENT_USER_ID and n.get("text"):
            users.append(n["text"].strip())
        elif rid == COMMENT_TEXT_ID and n.get("text"):
            texts.append(n["text"].strip())
    top = [{"user": u, "text": t} for u, t in list(zip(users, texts))[:top_k]]
    ctx.device.press_back()
    return top
