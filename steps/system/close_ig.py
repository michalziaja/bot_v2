"""system.close_ig — force-stop klona IG."""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class CloseIg:
    name = "system.close_ig"
    requires_screen = None

    def run(self, ctx, params):
        if not ctx.account.step_enabled("close_ig"):
            return StepResult.skipped("disabled_in_config")
        ctx.device.force_stop_app(ctx.ig_package)
        ctx.humanizer.pause("between_steps")
        pkg = ctx.device.current_pkg() or ""
        if ctx.ig_package in pkg:
            # fallback: press home
            ctx.device.press_home()
        return StepResult.ok(closed=ctx.ig_package)
