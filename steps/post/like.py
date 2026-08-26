"""post.like — polub otwarty post. Limit sprawdzany tuż przed akcją.

Tap w przycisk serca, nie double-tap w zdjęcie: double-tap na karuzeli
przewija slajd, a na filmie bywa pauzą.

Stdout: przy udanym lajku krótko „Polubiono” (prefix z operator_say).
Szansa chybiona / how=no_chance w payloadzie — cisza na panelu (QUIET_KINDS);
szczegóły zostają w events.jsonl.
"""
from __future__ import annotations

from v2.core import limits
from v2.core.chance import roll
from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.post import _post
from v2.steps.reels._sheet import operator_say
from v2.steps.registry import register


@register
class PostLike:
    name = "post.like"
    requires_screen = None

    def run(self, ctx, params):
        chance = params.get("chance") or {}
        values = {"score": (ctx.scratch.get("content_ai") or {}).get("niche_score") or 0}
        r = roll(chance, values)
        # Chybiona szansa — bez linii na stdout (szum); JSONL i tak ma step.skipped
        if not r["pass"]:
            return StepResult.skipped("chance_missed", **r)

        lim = limits.check(ctx, "like")
        if not lim.allowed:
            ctx.log("post.like.limit", **lim.__dict__)
            return StepResult.skipped(lim.reason)

        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        center, already = _post.find_like_button(flat)
        if not center:
            ctx.log("post.like.no_button")
            return StepResult.skipped("no_like_button")
        if already:
            return StepResult.skipped("already_liked")

        ctx.ht.tap(*center, pause_kind="tap")
        limits.note_action(ctx, "like")
        # Raw dump (pass/p/roll/how) → JSONL; panel dostaje tylko „Polubiono”
        ctx.log("post.like.done", **r)
        operator_say(ctx, "Polubiono")
        return StepResult.ok(**r)
