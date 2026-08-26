"""search.open — tap search_tab, confirm ekran search."""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class SearchOpen:
    name = "search.open"
    requires_screen = None   # bo może być w reels/profile/home — search_tab dostępny wszędzie

    def run(self, ctx, params):
        c = ctx.screen_map.anchor_center("nav.search_tab")
        if not c:
            return StepResult.failed("brak kotwicy nav.search_tab")
        ctx.ht.tap(*c, pause_kind="tap")
        # Po tapie może być search_top albo od razu search_input_active — akceptujemy oba
        s = ctx.navigator.identify()
        if s.top in ("search_top", "search_input_active"):
            return StepResult.ok(landed=s.top)
        # spróbuj ensure search_top
        if not ctx.navigator.ensure("search_top", max_steps=3):
            return StepResult.failed(f"nie doszliśmy do search_top (top={s.top})")
        return StepResult.ok(landed="search_top")
