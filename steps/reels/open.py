"""reels.open — z home_feed tap clips_tab, confirm reels_feed."""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.system._ui_helpers import settle_for_animation


@register
class OpenReels:
    name = "reels.open"
    requires_screen = "home_feed"

    def run(self, ctx, params):
        c = ctx.screen_map.anchor_center("nav.clips_tab")
        if not c:
            return StepResult.failed("brak kotwicy nav.clips_tab w mapie UI")
        ctx.ht.tap(*c, pause_kind="tap")
        if not ctx.navigator.ensure("reels_feed"):
            return StepResult.failed("nie potwierdzono reels_feed po tapie")
        # opcjonalnie chwila na render pierwszej rolki (animacja, nie hierarchy)
        settle_for_animation(ctx, max_wait_s=2.5, fallback_wait_s=1.5)
        return StepResult.ok(target="reels_feed")
