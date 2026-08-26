"""system.ensure_screen — doprowadź do zadeklarowanego ekranu przez Navigator."""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class EnsureScreen:
    name = "system.ensure_screen"
    requires_screen = None  # sam się doprowadza

    def run(self, ctx, params):
        target = params.get("target")
        if not target:
            return StepResult.failed("params.target wymagany (np. 'home_feed')")
        max_steps = int(params.get("max_steps", 6))
        ok = ctx.navigator.ensure(target, max_steps=max_steps)
        if not ok:
            return StepResult.failed(f"nie doprowadzono do {target}", target=target)
        return StepResult.ok(target=target)
