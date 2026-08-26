"""stories.next_user — przejście do relacji kolejnej osoby swipe'm w lewo.

Natywny gest IG: zostajemy w podglądzie. Zamykanie i otwieranie od nowa
dawałoby ten sam efekt widoczny dla użytkownika, ale zupełnie inny ślad —
i kosztowałoby dwa dodatkowe przejścia UI na każdą osobę.
"""
from __future__ import annotations

import random

from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.stories import _viewer


@register
class NextUser:
    name = "stories.next_user"
    requires_screen = None

    def run(self, ctx, params):
        rng = random.Random()
        if not _viewer.in_viewer(ctx):
            return StepResult.skipped("not_in_story_viewer")

        _viewer.swipe_next_user(ctx, rng)
        ctx.humanizer.pause("between_steps")

        if not _viewer.in_viewer(ctx):
            # Ostatnia osoba w tacce — swipe wychodzi z podglądu. To normalny
            # koniec, nie błąd; wołający ma po prostu skończyć pętlę.
            ctx.log("stories.next_user.viewer_closed")
            return StepResult.skipped("viewer_closed")
        return StepResult.ok()
