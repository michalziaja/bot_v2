"""Wspólne bramki dla kroków reels.* — sprawdzanie że nie jesteśmy na reklamie.

Jeśli aktualna rolka to reklama, żaden krok interakcyjny (like/harvest/analyze/
comment/read_caption) nie powinien się wykonać ani zaliczyć.
"""
from __future__ import annotations
from v2.runner.step import StepResult


def skip_if_ad(ctx, skip_until: str = "reels.swipe_next") -> StepResult | None:
    """Zwróć StepResult.skipped gdy w scratch reel jest is_ad=True; None gdy OK.
    Payload zawiera `skip_until` — FlowRunner przewinie flow do tego kroku,
    zamiast wywoływać po kolei wszystkie kroki które i tak każdy skip-nie."""
    reel = ctx.scratch.get("reel") or {}
    if reel.get("is_ad"):
        ctx.log("reels.skipped_ad", step="_guard", author=reel.get("author"),
                marker=reel.get("marker"))
        return StepResult.skipped("ad_detected",
                                  author=reel.get("author"),
                                  skip_until=skip_until)
    return None
