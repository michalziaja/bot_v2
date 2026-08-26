"""stories.close — zamknij podgląd relacji i potwierdź wyjście.

Swipe w dół czasem otwiera panel reakcji/emotek zamiast zamykać — dlatego po
geście dobijamy BACK-iem do skutku, z dumpem po każdej próbie. Bez tego
kolejny krok pracuje „w relacji", której nie widać w logu.
"""
from __future__ import annotations

import random

from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.stories import _viewer


@register
class CloseStory:
    name = "stories.close"
    requires_screen = None

    def run(self, ctx, params):
        rng = random.Random()
        max_backs = int(params.get("max_backs", 3))
        target = params.get("target")          # opcjonalny ekran do potwierdzenia

        if not _viewer.in_viewer(ctx):
            return _finish(ctx, target, backs=0, already_closed=True)

        _viewer.swipe_close(ctx, rng)
        ctx.humanizer.pause("between_steps")

        backs = 0
        while backs < max_backs and _viewer.in_viewer(ctx):
            ctx.device.press_back()
            ctx.humanizer.pause("tap")
            backs += 1

        if _viewer.in_viewer(ctx):
            ctx.log("stories.close.stuck", backs=backs)
            return StepResult.failed(f"nie wyszliśmy z relacji po {backs} próbach")
        return _finish(ctx, target, backs=backs, already_closed=False)


def _finish(ctx, target, *, backs: int, already_closed: bool):
    if target and not ctx.navigator.ensure(target, max_steps=4):
        return StepResult.failed(f"nie wróciliśmy na {target}")
    return StepResult.ok(backs=backs, already_closed=already_closed)
