"""profile.open_followers — wejście w listę obserwujących (lub obserwowanych).

Liczniki w nagłówku są klikalne i to one prowadzą do listy. Celujemy w element
z `content-desc` typu `"235obserwatorzy"` albo w sam licznik — oba warianty
występują w tej rodzinie wersji IG.
"""
from __future__ import annotations

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register

FOLLOWERS_IDS = ("com.instagram.android:id/profile_header_followers_stacked_familiar",
                 "com.instagram.android:id/profile_header_familiar_followers_value",
                 "com.instagram.android:id/profile_header_familiar_followers_label")
FOLLOWING_IDS = ("com.instagram.android:id/profile_header_following_stacked_familiar",
                 "com.instagram.android:id/profile_header_familiar_following_value",
                 "com.instagram.android:id/profile_header_familiar_following_label")
FOLLOWERS_WORDS = ("obserwatorzy", "obserwujący", "followers")
FOLLOWING_WORDS = ("obserwowani", "obserwowanie", "following")


@register
class OpenFollowers:
    name = "profile.open_followers"
    requires_screen = "profile_other"

    def run(self, ctx, params):
        which = (params.get("source") or "followers").lower()
        ids = FOLLOWERS_IDS if which == "followers" else FOLLOWING_IDS
        words = FOLLOWERS_WORDS if which == "followers" else FOLLOWING_WORDS

        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        target = _find(flat, ids, words)
        if not target:
            ctx.log("profile.open_followers.not_found", source=which)
            return StepResult.skipped("no_entry", source=which)

        ctx.scratch["baseline_screen"] = "profile_other"
        ctx.ht.tap(*target, pause_kind="tap")
        ctx.humanizer.pause("between_steps")

        if not ctx.navigator.ensure("follow_list", max_steps=3):
            top = ctx.navigator.identify().top
            ctx.log("profile.open_followers.no_list", landed=top)
            return StepResult.failed(f"po tapie w '{which}' nie ma follow_list "
                                     f"(jesteśmy: {top})")
        return StepResult.ok(source=which)


def _find(flat, ids, words):
    """Najpierw po ID (pewne), potem po tekście licznika (gdy ID się zmieniło)."""
    for n in flat:
        if (n.get("resource-id") or "") in ids:
            c = _center(n.get("bounds"))
            if c:
                return c
    for n in flat:
        blob = ((n.get("text") or "") + " " + (n.get("content-desc") or "")).lower()
        if any(w in blob for w in words):
            c = _center(n.get("bounds"))
            if c:
                return c
    return None


def _center(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None
