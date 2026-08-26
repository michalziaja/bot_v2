"""system.browse_feed — scroll home feedu N razy z pauzą między krokami.

Prosty krok: sprawdź że jesteś na home_feed, potem N swipe up + pauza.
Loguje każdy scroll jako event `browse_feed.scroll`.
"""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class BrowseFeed:
    name = "system.browse_feed"
    requires_screen = "home_feed"

    def run(self, ctx, params):
        count      = int(params.get("count", 3))
        pause_kind = params.get("pause_kind", "between_steps")
        w, h = ctx.account.device["_hardware"]["resolution"]

        for i in range(count):
            # scroll ~40% ekranu w środku
            y_from = int(h * 0.75)
            y_to   = int(h * 0.35)
            ctx.ht.swipe(w // 2, y_from, w // 2, y_to,
                         duration_ms=350, pause_kind=pause_kind)
            ctx.log("browse_feed.scroll", n=i + 1, of=count)

        return StepResult.ok(scrolls=count)
