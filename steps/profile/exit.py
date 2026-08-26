"""profile.exit — placeholder step + wygodny fast-forward target.

Realizuje powrót do baseline_screen (który wołacz zapisał w scratch przed
tap'em w profil). W flow'ie jest 'exit target' dla skip_until, ale można go
też wywołać wprost gdy iteracja się kończy pomyślnie.
"""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class ProfileExit:
    name = "profile.exit"
    requires_screen = None

    def run(self, ctx, params):
        baseline = ctx.scratch.get("baseline_screen")
        if not baseline:
            # Nie mamy baseline — próbujemy home_feed jako fallback
            ok = ctx.navigator.ensure("home_feed", max_steps=4)
            return StepResult.ok(baseline="home_feed_fallback", reached=ok)

        # 3× back, potem pełen ensure jeśli nie osiągnięto
        for _ in range(3):
            s = ctx.navigator.identify()
            if s.top == baseline and not s.overlay:
                # posprzątaj scratch profile-scope
                _clear_profile_scratch(ctx)
                return StepResult.ok(baseline=baseline, reached=True)
            ctx.device.press_back()
            ctx.humanizer.pause("tap")

        # Fallback: pełna drabinka
        ok = ctx.navigator.ensure(baseline, max_steps=4)
        _clear_profile_scratch(ctx)
        if not ok:
            ctx.log("profile.exit.baseline_missed", baseline=baseline)
            # content_search hot: NIE home_feed — zostawia harvest na złym
            # ekranie (live 13:57). search_results nie ma tab-kotwicy →
            # search_top (plugin i tak odtworzy frazę gdy RESULTS utracone).
            if ctx.scratch.get("content_search_active"):
                ctx.navigator.ensure("search_top", max_steps=4)
            else:
                ctx.navigator.ensure("home_feed", max_steps=4)
        return StepResult.ok(baseline=baseline, reached=ok)


def _clear_profile_scratch(ctx):
    """Wyczyść cache scope'u pojedynczego profilu (nie baseline_screen)."""
    for k in ("profile_header", "profile_ai", "profile_decision",
              "tiles_liked", "story_watched", "followed"):
        ctx.scratch.pop(k, None)
