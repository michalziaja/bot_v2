"""system.unlock — jeśli lockscreen, swipe up. PIN nieobsługiwany (TODO)."""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class Unlock:
    name = "system.unlock"
    requires_screen = None

    def run(self, ctx, params):
        if not ctx.account.step_enabled("unlock"):
            return StepResult.skipped("disabled_in_config")
        if ctx.device.screen_is_unlocked():
            return StepResult.ok(was_locked=False, action="none")
        # swipe up po pełnej wysokości
        w, h = ctx.account.device["_hardware"]["resolution"]
        ctx.ht.swipe(w // 2, int(h * 0.85), w // 2, int(h * 0.17),
                     duration_ms=250, pause_kind="after_unlock")
        if ctx.device.screen_is_unlocked():
            return StepResult.ok(was_locked=True, action="swipe_up")
        # Komentarz: OnePlus 8T — lock to NotificationShade; swipe nie zdejmuje
        if hasattr(ctx.device, "dismiss_keyguard"):
            ctx.device.dismiss_keyguard()
            ctx.humanizer.pause("after_unlock")
        if not ctx.device.screen_is_unlocked():
            return StepResult.failed("swipe nie odblokował — być może PIN (nieobsługiwany)",
                                     needs="pin_or_pattern")
        return StepResult.ok(was_locked=True, action="dismiss_keyguard")
