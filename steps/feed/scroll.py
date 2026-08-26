"""feed.scroll — jeden post w dół strony głównej, z postojem zależnym od treści.

Różnica wobec `system.browse_feed` (który zostaje dla smoke-testów): tam pauza
jest stałym zakresem, tu liczy ją `core/dwell.py` z długości opisu widocznego
posta.

**Opis posta nie ma resource-id** — sprawdzone na żywym dumpie (pierwszy przebieg
E5 pokazał `dwell_mode=fallback` na wszystkich sześciu postach). Rozpoznajemy go
po KLASIE widgetu `IgTextLayoutView`, w której Instagram renderuje opis:

    class=IgTextLayoutView  bounds=[32,700][1080,764]
    text='primevideopl Bohaterowie Eternii przybyli.… więcej'

Tekst zaczyna się nickiem autora i kończy skrótem „… więcej", bo w feedzie opis
jest przycięty. Liczymy postój z tego, co WIDAĆ — użytkownik, który nie rozwinął
opisu, przeczytał dokładnie tyle.
"""
from __future__ import annotations

import random

from v2.core import dwell as dwell_model
from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register

CAPTION_CLASS = "igtextlayoutview"
CAPTION_ID_HINTS = ("row_feed_comment_textview_layout",
                    "caption_text_view", "row_feed_caption")
# „… więcej" / „… more" — znacznik przycięcia, nie treść
MORE_SUFFIX = ("… więcej", "…więcej", "... więcej", "… more", "…more", "... more")
AD_HINTS = ("sponsorowane", "sponsored")
END_HINTS = ("nadrobione", "all caught up", "widziano wszystkie")


@register
class FeedScroll:
    name = "feed.scroll"
    requires_screen = "home_feed"

    def run(self, ctx, params):
        rng = random.Random()
        mode = params.get("dwell_mode", "content_aware")
        fallback = params.get("dwell_fallback_s") or {"min": 2.0, "max": 7.0}
        fb_range = (float(fallback.get("min", 2.0)), float(fallback.get("max", 7.0)))

        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if _feed_end(flat):
            ctx.log("feed.scroll.end_of_feed")
            return StepResult.skipped("end_of_feed")

        is_ad = _is_ad(flat)
        caption = None if mode != "content_aware" else _caption(flat)
        d = (dwell_model.compute(caption, rng, fallback=fb_range)
             if mode == "content_aware"
             else {"seconds": rng.uniform(*fb_range), "prose": None,
                   "mode": "fixed", "parts": {}})

        if is_ad:
            # Reklamy w feedzie przewijamy szybko — nie ma czego czytać,
            # a długi postój na reklamie to sygnał zainteresowania nią.
            d = {"seconds": round(rng.uniform(0.8, 2.0), 2), "prose": None,
                 "mode": "ad", "parts": {}}

        ctx.humanizer.sleep(d["seconds"])

        w, h = ctx.account.device["_hardware"]["resolution"]
        # Dystans swipe'a: ułamek wysokości ekranu (katalog: scroll.distance).
        # Domyślnie ~0.40 jak stary hardcode 0.75→0.35; min≈pełny post, max=ekstra.
        dist = params.get("distance") or {"min": 0.40, "max": 0.40}
        lo = float(dist.get("min", 0.40))
        hi = float(dist.get("max", lo))
        span = rng.uniform(min(lo, hi), max(lo, hi))
        y_from = 0.75
        y_to = max(0.12, y_from - span)
        ctx.ht.swipe(w // 2, int(h * y_from), w // 2, int(h * y_to),
                     duration_ms=int(rng.uniform(300, 420)), pause_kind="tap")

        ctx.log("feed.scroll", dwell_s=d["seconds"], dwell_mode=d["mode"],
                prose=d["prose"], is_ad=is_ad, distance=round(span, 3))
        return StepResult.ok(dwell_s=d["seconds"], dwell_mode=d["mode"],
                             prose=d["prose"], is_ad=is_ad, distance=span)


# ============================================================
def _caption(flat: list[dict]) -> str | None:
    """Najdłuższy tekst z elementów wyglądających na opis posta.

    `None` (a nie pusty string) znaczy „nie odczytaliśmy" — dopiero to włącza
    zakres awaryjny. Post naprawdę bez opisu dostałby sam rzut oka.
    """
    best = None
    for e in flat:
        rid = (e.get("resource-id") or "").lower()
        cls = (e.get("class") or "").lower()
        if not (cls.endswith(CAPTION_CLASS)
                or any(h in rid for h in CAPTION_ID_HINTS)):
            continue
        text = clean_caption(e.get("text"))
        if text and (best is None or len(text) > len(best)):
            best = text
    return best


def clean_caption(text: str | None) -> str:
    """Zdejmij z opisu to, czego użytkownik nie czyta jako treści: nick autora
    na początku (jest już nad postem jako osobny przycisk) i znacznik „… więcej"."""
    t = (text or "").strip()
    for suf in MORE_SUFFIX:
        if t.endswith(suf):
            t = t[: -len(suf)].rstrip()
            break
    head, sep, rest = t.partition(" ")
    # nick autora: bez spacji, dozwolone znaki nicku IG — odcinamy tylko wtedy,
    # gdy za nim faktycznie coś jeszcze jest
    if sep and head and all(c.isalnum() or c in "._" for c in head):
        t = rest.strip()
    return t


def _is_ad(flat: list[dict]) -> bool:
    for e in flat:
        blob = ((e.get("text") or "") + " " + (e.get("content-desc") or "")).lower()
        if any(h in blob for h in AD_HINTS):
            return True
    return False


def _feed_end(flat: list[dict]) -> bool:
    for e in flat:
        blob = ((e.get("text") or "") + " " + (e.get("content-desc") or "")).lower()
        if any(h in blob for h in END_HINTS):
            return True
    return False
