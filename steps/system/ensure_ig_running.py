"""system.ensure_ig_running — jeśli IG nie w foreground, uruchom właściwy klon."""
from __future__ import annotations
import time
from v2.core.device import DeviceError
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class EnsureIgRunning:
    name = "system.ensure_ig_running"
    requires_screen = None

    def run(self, ctx, params):
        target = ctx.ig_package
        pkg = ctx.device.current_pkg()
        # Komentarz: nawet gdy target już w focus — zminimalizowany inny IG też ginie
        if pkg == target:
            err = ctx.device.stop_other_instagrams(target)
            if err:
                return StepResult.failed(err, was_running=True, pkg=pkg)
            return StepResult.ok(was_running=True, pkg=pkg)
        try:
            # Komentarz: launch_app force-stopuje stock + inne klony przed monkey/am start
            ctx.device.launch_app(target)
        except DeviceError as exc:
            return StepResult.failed(str(exc), was_running=False, pkg=pkg)
        ctx.humanizer.pause("after_launch")
        # Komentarz: shade/GMS Passkey zasłaniają focus — zdejmij zanim ocenisz pkg
        if hasattr(ctx.device, "dismiss_keyguard"):
            ctx.device.dismiss_keyguard()
        pkg_after = None
        for _ in range(8):
            pkg_after = ctx.device.current_pkg()
            if pkg_after == target:
                break
            if pkg_after == "com.google.android.gms":
                ctx.device.shell("input keyevent 4")
                if hasattr(ctx.device, "dismiss_keyguard"):
                    ctx.device.dismiss_keyguard()
            time.sleep(0.6)
        if pkg_after != target:
            hint = ("dumpsys window pusty — ADB nie widzi foreground"
                    if pkg_after is None else f"oczekiwano {target}")
            return StepResult.failed(f"po launch pkg={pkg_after}, {hint}",
                                     was_running=False, pkg_after=pkg_after)
        return StepResult.ok(was_running=False, pkg=target)
