"""list.scroll_until_new — przewiń listę, aż pojawią się nowe wiersze albo skończy się lista.

Port dwóch rozwiązań ze starego `mass_stories.py`, obu kupionych realnymi
błędami na koncie:

1. **Kontrolny PÓŁ-scroll przed pierwszym dumpem.** Lista, która nie była
   jeszcze przewinięta w żadną stronę, potrafi zawiesić się przy pełnym
   scrollu. Krótszy gest ją „rozrusza".

2. **Snapshot listy do wykrywania dna.** Dwa identyczne zestawy
   `(nick, pozycja)` z rzędu znaczą, że lista się nie rusza — czyli albo dno,
   albo zawieszenie. Bez tego bot przewijał w pustkę aż do wyczerpania
   `max_scrolls`, tracąc minutę na każdym targecie.

Krok jest generyczny — używa go `mass_stories`, a docelowo też `target_scraper`
(listy obserwujących, lajkujących post, komentujących).
"""
from __future__ import annotations

import random

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels import _sheet

SCRATCH_SNAPSHOT = "list_snapshot"
SCRATCH_STALLS = "list_stalls"


@register
class ScrollUntilNew:
    name = "list.scroll_until_new"
    requires_screen = None

    def run(self, ctx, params):
        rng = random.Random()
        username_ids = tuple(params.get("username_ids")
                             or _sheet.LIKER_USERNAME_IDS)
        max_scrolls = int(params.get("max_scrolls", 3))
        control_half = bool(params.get("control_half_scroll", False))

        if control_half:
            _swipe(ctx, rng, fraction=0.5)
            ctx.device.wait_for_stable_ui(max_wait_s=1.2)
            ctx.log("list.control_half_scroll")

        before = ctx.scratch.get(SCRATCH_SNAPSHOT) or set()

        for attempt in range(max_scrolls):
            rows = _sheet.collect_rows(
                flatten_hierarchy(ctx.device.hierarchy(force=True)), username_ids)
            snapshot = _snapshot(rows)
            new = {u for u, _ in snapshot} - {u for u, _ in before}

            if new:
                ctx.scratch[SCRATCH_SNAPSHOT] = snapshot
                ctx.scratch[SCRATCH_STALLS] = 0
                return StepResult.ok(new=len(new), visible=len(rows),
                                     scrolls=attempt)

            if snapshot and snapshot == before:
                # Identyczny zestaw wierszy dwa razy z rzędu — lista stoi.
                stalls = int(ctx.scratch.get(SCRATCH_STALLS) or 0) + 1
                ctx.scratch[SCRATCH_STALLS] = stalls
                if stalls >= 2:
                    ctx.log("list.bottom_reached", visible=len(rows))
                    return StepResult.skipped("bottom_reached", visible=len(rows))

            before = snapshot
            ctx.scratch[SCRATCH_SNAPSHOT] = snapshot
            _swipe(ctx, rng)
            ctx.device.wait_for_stable_ui(max_wait_s=1.2)

        return StepResult.skipped("no_new_rows", scrolls=max_scrolls)


def _snapshot(rows) -> set:
    """`frozenset{(nick, y)}` — sam zestaw nicków nie wystarcza, bo lista
    może pokazywać te same osoby po przesunięciu o pół ekranu."""
    return {(r["username"], (r["center"] or (0, 0))[1] // 50) for r in rows}


def _swipe(ctx, rng, fraction: float = 1.0) -> None:
    w, h = ctx.account.device["_hardware"]["resolution"]
    span = (0.75 - 0.35) * fraction
    ctx.ht.swipe(w // 2, int(h * 0.75), w // 2, int(h * (0.75 - span)),
                 duration_ms=int(rng.uniform(320, 420)), pause_kind="tap")
