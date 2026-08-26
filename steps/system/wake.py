"""system.wake — jeśli ekran wygaszony, wybudź."""
from __future__ import annotations
import time
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class Wake:
    name = "system.wake"
    requires_screen = None

    def run(self, ctx, params):
        if not ctx.account.step_enabled("wake"):
            return StepResult.skipped("disabled_in_config")
        was_on = ctx.device.screen_is_on()
        if was_on:
            return StepResult.ok(was_on=True, action="none")
        ctx.device.wake_up_key()
        ctx.humanizer.pause("after_wake")
        now_on = ctx.device.screen_is_on()
        if not now_on:
            return StepResult.failed("nie udało się wybudzić ekranu", was_on=False)
        return StepResult.ok(was_on=False, action="wakeUp")
