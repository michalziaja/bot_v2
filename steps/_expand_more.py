"""Tap w „więcej" / „more" — rozwija skrócone bio albo opis posta/rolki.

IG ucina długie teksty i dokleja znacznik; pełny tekst widać dopiero po
tapnięciu w przycisk (osobny Button z content-desc=\"więcej\") albo w sam
przycięty TextView. Bez tego discovery dostaje pół zdania zamiast bio/opisu.
"""
from __future__ import annotations

import time

from v2.core.screen_map import flatten_hierarchy

# dokładne etykiety przycisku rozwijania (PL/EN)
MORE_EXACT = {
    "więcej", "more", "see more", "czytaj więcej", "read more",
}
# znacznik w treści przyciętego tekstu
MORE_IN_TEXT = (
    "… więcej", "…więcej", "... więcej", "...więcej",
    "… more", "…more", "... more", "...more",
)
# NIE tapamy w te CTA — to nie jest expand opisu/bio
MORE_BLOCKLIST = (
    "obejrzyj więcej", "więcej rolek", "dotknij, aby",
    "watch more", "see more reels", "load more",
)
# Menu „…” / UFI po prawej rolki — NIE mylić z „…więcej" w tekście opisu
UFI_MORE_RID_MARKERS = (
    "clips_ufi_more", "ufi_more_button", "feed_ufi_more",
)


def expand_more_if_present(ctx, *, settle_s: float = 0.55) -> bool:
    """Jeśli na ekranie jest „więcej"/„more" — tapnij i poczekaj na expand.

    Zwraca True gdy coś tapnięto. Bezpieczne no-op gdy brak znacznika.
    """
    try:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    except Exception:
        return False

    target = _find_more_target(flat)
    if not target:
        return False
    c = _center(target.get("bounds"))
    if not c:
        return False

    ctx.ht.tap(*c, pause_kind="tap")
    time.sleep(settle_s)
    ctx.log("expand_more.tapped",
            text=(target.get("text") or "")[:40],
            desc=(target.get("content-desc") or "")[:40])
    return True


def _find_more_target(flat: list[dict]) -> dict | None:
    """Preferuj krótki przycisk „więcej"; potem tekst z …więcej (także nieklikalny).

    Bio w MetaComposeView / TextView bywa bez clickable=true — wtedy i tak
    tapamy w sam węzeł z markerem (IG rozwija po tapnięciu w tekst).
    """
    exact_clickable = []
    text_clickable = []
    text_passive = []  # nieklikalny TextView z …więcej (Compose / bio)

    for n in flat:
        rid = (n.get("resource-id") or "").lower()
        # clips_ufi_more_button ma content-desc „Więcej" — to menu UFI, nie opis
        if any(m in rid for m in UFI_MORE_RID_MARKERS):
            continue

        text = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()
        blob = f"{text} {desc}".casefold()
        if any(b in blob for b in MORE_BLOCKLIST):
            continue

        t_cf = text.casefold()
        d_cf = desc.casefold()
        if t_cf in MORE_EXACT or d_cf in MORE_EXACT:
            if n.get("clickable"):
                exact_clickable.append(n)
            continue

        has_marker = (
            any(m in text for m in MORE_IN_TEXT)
            or any(m in desc for m in MORE_IN_TEXT)
        )
        if not has_marker:
            continue
        if n.get("clickable"):
            text_clickable.append(n)
        else:
            text_passive.append(n)

    if exact_clickable:
        # najmniejszy przycisk = sam „więcej", nie cały wiersz
        return min(exact_clickable, key=_area)
    # klikalne + nieklikalne razem — bio rid wygrywa z captionem feedu
    text_all = text_clickable + text_passive
    if text_all:
        return _prefer_bio_node(text_all)
    return None


def _prefer_bio_node(nodes: list[dict]) -> dict:
    """Wybierz węzeł bio profilu, gdy jest kilka kandydatów z …więcej."""
    def _score(n: dict) -> tuple:
        rid = (n.get("resource-id") or "").lower()
        # profile_header_bio_text > zwykły tekst; clickable > passive; wyżej = lepiej
        is_bio = 0 if "profile_header_bio" in rid else 1
        clickable = 0 if n.get("clickable") else 1
        y = _top_y(n.get("bounds"))
        return (is_bio, clickable, y if y is not None else 10**9, _area(n))

    return min(nodes, key=_score)


def _top_y(bounds) -> int | None:
    try:
        a, _b = str(bounds).split("][")
        _x1, y1 = map(int, a.strip("[").split(","))
        return y1
    except Exception:
        return None


def _area(n: dict) -> int:
    c = _center(n.get("bounds"))
    if not c:
        return 10**9
    try:
        a, b = str(n.get("bounds")).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return abs(x2 - x1) * abs(y2 - y1)
    except Exception:
        return 10**9


def _center(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None
