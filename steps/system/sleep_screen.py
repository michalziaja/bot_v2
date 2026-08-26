"""system.sleep_screen — wygaszenie ekranu (KEYCODE_POWER)."""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class SleepScreen:
    name = "system.sleep_screen"
    requires_screen = None

    def run(self, ctx, params):
        if not ctx.account.step_enabled("sleep_screen"):
            return StepResult.skipped("disabled_in_config")
        ctx.humanizer.pause("before_sleep")
        ctx.device.press_power()
        if ctx.device.screen_is_on():
            # jeszcze raz
            ctx.device.press_power()
        return StepResult.ok(screen_on=ctx.device.screen_is_on())
