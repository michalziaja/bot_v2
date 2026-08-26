"""system.ensure_ig_running — jeśli IG nie w foreground, uruchom właściwy klon."""
from __future__ import annotations
import time
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class EnsureIgRunning:
    name = "system.ensure_ig_running"
    requires_screen = None

    def run(self, ctx, params):
        pkg = ctx.device.current_pkg()
        target = ctx.ig_package
        if pkg == target:
            return StepResult.ok(was_running=True, pkg=pkg)
        # jeśli w foreground jest INNY klon IG, najpierw stop
        if pkg and "instagram" in pkg and pkg != target:
            ctx.device.force_stop_app(pkg)
        ctx.device.launch_app(target)
        ctx.humanizer.pause("after_launch")
        # dumpsys czasem nie zdąży — kilka prób zamiast fałszywego failed
        pkg_after = None
        for _ in range(5):
            pkg_after = ctx.device.current_pkg()
            if pkg_after == target:
                break
            time.sleep(0.6)
        if pkg_after != target:
            return StepResult.failed(f"po launch pkg={pkg_after}, oczekiwano {target}",
                                     was_running=False, pkg_after=pkg_after)
        return StepResult.ok(was_running=False, pkg=target)
