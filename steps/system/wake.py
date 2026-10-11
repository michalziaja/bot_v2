"""system.wake — jeśli ekran wygaszony, wybudź."""
from __future__ import annotations
import time
from v2.runner.step import StepResult
from v2.steps.registry import register

# KEYCODE_WAKEUP bywa ignorowany w głębokim śnie Samsunga; POWER to plan B.
_WAKE_KEYS = (
    ("wakeUp", "wake_up_key"),
    ("power", "press_power"),
)


@register
class Wake:
    name = "system.wake"
    requires_screen = None

    def run(self, ctx, params):
        if not ctx.account.step_enabled("wake"):
            return StepResult.skipped("disabled_in_config")
        # Pusty dumpsys ≠ ekran OFF — zwykle martwy ADB / odpięty USB.
        power = ctx.device.dumpsys_power()
        if not (power or "").strip():
            return StepResult.failed(
                "dumpsys power pusty — telefon nie odpowiada (USB/ADB?)",
                was_on=False, dumpsys="empty")
        if ctx.device.screen_is_on():
            return StepResult.ok(was_on=True, action="none")
        used = []
        for action, method in _WAKE_KEYS:
            getattr(ctx.device, method)()
            used.append(action)
            ctx.humanizer.pause("after_wake")
            for _ in range(4):
                if ctx.device.screen_is_on():
                    return StepResult.ok(was_on=False, action=action)
                time.sleep(0.4)
        return StepResult.failed(
            "nie udało się wybudzić ekranu (WAKEUP+POWER)",
            was_on=False, tried=used)
