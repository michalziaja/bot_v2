"""reels_warmup — oglądanie rolek bez analizy i bez zbierania.

Do rozgrzewania świeżych klonów: obejrzyj N rolek, czasem polub, przewiń.
Najprostszy z całej siódemki — pętla na gotowych krokach, zero własnej
logiki UI.

Różnica wobec `reels_niche` jest celowa i nie sprowadza się do wyłączenia
opcji: tutaj **nie ma bramki liczników ani AI**, bo rozgrzewka ma wyglądać jak
zwykłe przeglądanie, a nie jak selektywne polowanie. Reklamy pomijamy — one
jako jedyne mają sens odsiać, bo lajk reklamy to sygnał zupełnie innego rodzaju.
"""
from __future__ import annotations

import random

from v2.plugins import stepcall
from v2.plugins.base import Budget, PluginResult
from v2.plugins.registry import register
from v2.steps import registry as steps

REELS = "reels_feed"
LOG_PREFIX = "reels_warmup"


@register
class ReelsWarmup:
    id = "reels_warmup"
    entry_screen = "home_feed"
    exit_screen = "home_feed"

    def run(self, ctx, s: dict, budget: Budget) -> PluginResult:
        rng = random.Random()
        stats = {"watched": 0, "liked": 0, "ads_skipped": 0, "repeat_skipped": 0,
                 "meta_skipped": 0}
        like_cfg = s.get("like") or {}
        skip_ads = bool(s.get("skip_ads", True))

        res = _step(ctx, "reels.open", {})
        if res.status == "failed":
            return PluginResult.failed(f"reels.open: {res.error}", budget, **stats)

        stopped_by = "items"
        last_marker = None
        swipe_failures = 0
        while True:
            reason = budget.stop_reason()
            if reason:
                stopped_by = reason
                break

            ctx.scratch.pop("reel", None)
            # Bez bramki: `watch` ma tylko obejrzeć i odczytać metadane.
            # log_prefix — panel nie ma mylić linii z reels_niche.
            watch = _step(ctx, "reels.watch", {
                "frames": 0, "gate": {},
                "watch_reel": s.get("watch_reel"),
                "log_prefix": LOG_PREFIX,
            })
            reel = ctx.scratch.get("reel") or {}
            marker = reel.get("marker")
            author = reel.get("author")

            if marker and marker == last_marker:
                # ta sama rolka co poprzednio — swipe nie przewinął
                ctx.log("reels_warmup.same_reel", author=author)
                stats["repeat_skipped"] += 1
            elif reel.get("is_ad") and skip_ads:
                stats["ads_skipped"] += 1
            elif watch.status == "ok":
                # Soft-skip tylko gdy watch ZAPISAŁ reel, ale bez markera/autora.
                # Atrapy testowe często nie wypełniają scratch — wtedy nie soft-skip.
                # W produkcji watch zawsze wstawia dict; puste pola = hierarchia w locie.
                if "reel" in ctx.scratch and (not marker or author is None):
                    stats["meta_skipped"] += 1
                    ctx.log("reels_warmup.meta_unreadable",
                            author=author, has_marker=bool(marker))
                    ctx.navigator.ensure(REELS, max_steps=3)
                else:
                    stats["watched"] += 1
                    if like_cfg.get("enabled"):
                        # log_prefix — „Polubiono” też jako reels_warmup, nie niche
                        lr = _step(ctx, "reels.like", {
                            "match": {},
                            "chance": like_cfg.get("chance") or {},
                            "log_prefix": LOG_PREFIX,
                        })
                        if lr.status == "ok":
                            stats["liked"] += 1
                        elif "daily" in str((lr.payload or {}).get("reason") or ""):
                            stopped_by = "limit_daily"
                            break

            if marker:
                last_marker = marker
            budget.tick()

            if budget.stop_reason() is None:
                nxt = _step(ctx, "reels.swipe_next", {
                    "log_prefix": LOG_PREFIX,
                })
                if nxt.status == "failed":
                    swipe_failures += 1
                    if swipe_failures >= 2:
                        stopped_by = "error"
                        break
                    # Po failu swipe — ensure + upewnij się, że meta czytelna
                    # przed kolejnym watch (inaczej znowu meta_skipped / like=0).
                    ctx.navigator.ensure(REELS, max_steps=3)
                    _ensure_readable_meta(ctx)
                else:
                    swipe_failures = 0

        ctx.log("reels_warmup.done", stopped_by=stopped_by, **stats)
        return PluginResult.done(budget, stopped_by, **stats)


def _ensure_readable_meta(ctx) -> None:
    """Po recover feed spróbuj dociągnąć marker zanim kolejny watch/like."""
    try:
        from v2.steps.reels._reel_meta import read_current_reel
        from v2.steps.system._ui_helpers import settle_for_animation
        settle_for_animation(ctx, max_wait_s=2.0, fallback_wait_s=1.5)
        ctx.device.wait_for_stable_ui(max_wait_s=2.0)
        meta = read_current_reel(ctx.device)
        if meta.get("marker"):
            ctx.scratch["reel"] = meta
    except Exception:
        pass


def _step(ctx, name: str, params: dict):
    return stepcall.call(steps, ctx, name, params, source="reels_warmup")
