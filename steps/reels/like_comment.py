"""reels.like_comment — polub JEDEN komentarz w otwartym panelu komentarzy.

Zakłada, że panel komentarzy jest już otwarty (woła to plugin po
`reels.harvest_commenters`). Krok jest celowo atomowy: jedno polubienie na
wywołanie, żeby limit dzienny był sprawdzany przed każdym z osobna.

Filtr `min_comment_likes` opiera się na liczniku obok serduszka. Instagram
nie zawsze go pokazuje (przy zerze bywa pusty) — wtedy krok degraduje się do
KOLEJNOŚCI: komentarze w panelu są posortowane po popularności, więc te
u góry mają najwięcej lajków. Fakt degradacji trafia do logu, żeby nie było
złudzenia, że filtr zadziałał.
"""
from __future__ import annotations

import random
import re

from v2.core import limits
from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register

USERNAME_ID = "com.instagram.android:id/row_comment_textview_username"
LIKE_BTN_IDS = (
    "com.instagram.android:id/row_comment_like_button",
    "com.instagram.android:id/comment_like_button",
)
LIKE_COUNT_IDS = (
    "com.instagram.android:id/row_comment_textview_like_count",
    "com.instagram.android:id/comment_like_count",
)
LIKED_MARKERS = ("nie lubię", "unlike", "polubione", "liked")

_ROW_MATCH_PX = 90       # jak blisko w pionie element musi być, by należeć do wiersza


@register
class LikeComment:
    name = "reels.like_comment"
    requires_screen = None      # panel komentarzy — nie jest osobnym ekranem mapy

    def run(self, ctx, params):
        min_likes = int(params.get("min_comment_likes", 0))
        extra_chance = int(params.get("extra_chance_percent", 100))
        rng = random.Random()

        lim = limits.check(ctx, "like")
        if not lim.allowed:
            ctx.log("reels.like_comment.limit", **lim.__dict__)
            return StepResult.skipped(lim.reason)

        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        rows = _collect_rows(flat)
        if not rows:
            return StepResult.skipped("no_candidates")

        counts_readable = any(r["likes"] is not None for r in rows)
        pool = [r for r in rows if not r["already_liked"]]
        if counts_readable and min_likes > 0:
            pool = [r for r in pool if (r["likes"] or 0) >= min_likes]
        elif min_likes > 0:
            # licznik nieczytelny — bierzemy z górnej połowy panelu, bo IG
            # sortuje komentarze po popularności
            ctx.log("reels.like_comment.counts_unreadable", min_likes=min_likes)
            pool = pool[:max(1, len(pool) // 2)]

        if not pool:
            return StepResult.skipped("no_candidates", checked=len(rows))

        if extra_chance < 100 and rng.randint(1, 100) > extra_chance:
            return StepResult.skipped("chance_missed", chance=extra_chance)

        target = rng.choice(pool)
        ctx.ht.tap(*target["like_center"], pause_kind="tap")
        limits.note_action(ctx, "like")
        ctx.log("reels.like_comment.done", user=target["user"],
                comment_likes=target["likes"], candidates=len(pool))
        return StepResult.ok(user=target["user"], comment_likes=target["likes"])


# ============================================================
def _collect_rows(flat: list[dict]) -> list[dict]:
    """Złóż wiersze komentarzy: username + przycisk lajka + licznik.

    Panel komentarzy nie ma kontenera na wiersz, więc wiążemy elementy po
    współrzędnej Y — tak samo jak stary bot wiązał ring z awatarem.
    """
    users, buttons, counts = [], [], []
    for n in flat:
        rid = n.get("resource-id") or ""
        c = _center(n.get("bounds"))
        if not c:
            continue
        if rid == USERNAME_ID and (n.get("text") or "").strip():
            users.append(((n.get("text") or "").strip(), c))
        elif rid in LIKE_BTN_IDS:
            desc = ((n.get("content-desc") or "") + " " + (n.get("text") or "")).lower()
            buttons.append((c, any(m in desc for m in LIKED_MARKERS)))
        elif rid in LIKE_COUNT_IDS:
            counts.append((_parse_count(n.get("text")), c))

    rows = []
    for user, ucenter in users:
        btn = _nearest(buttons, ucenter[1], key=lambda b: b[0][1])
        if not btn:
            continue
        cnt = _nearest(counts, ucenter[1], key=lambda c: c[1][1])
        rows.append({
            "user": user,
            "like_center": btn[0],
            "already_liked": btn[1],
            "likes": cnt[0] if cnt else None,
        })
    return rows


def _nearest(items, target_y, key):
    best, best_d = None, _ROW_MATCH_PX
    for it in items:
        d = abs(key(it) - target_y)
        if d < best_d:
            best, best_d = it, d
    return best


def _parse_count(text) -> int | None:
    """'12', '1,2 tys.', '3.4K' → liczba. None gdy nieczytelne."""
    if not text:
        return None
    t = str(text).strip().lower().replace(" ", " ")
    m = re.search(r"(\d+[.,]?\d*)\s*(tys\.?|k|mln|m)?", t)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    mult = {"tys": 1_000, "tys.": 1_000, "k": 1_000, "mln": 1_000_000, "m": 1_000_000}
    suffix = (m.group(2) or "").rstrip(".")
    return int(val * mult.get(suffix, 1))


def _center(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None
