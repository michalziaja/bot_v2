"""reels.swipe_next — przewiń do następnej rolki i POTWIERDŹ, że się zmieniła.

Zawsze wymagamy `before != after` (oba niepuste). Bez przedniego markera
najpierw dociągamy odczyt / recover feed — nie akceptujemy „ślepego” przejścia.
"""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels._reel_meta import read_current_reel
from v2.steps.system._ui_helpers import settle_for_animation

ATTEMPTS = ((0.65, 0.30, 300), (0.70, 0.25, 350), (0.75, 0.20, 420))
REELS = "reels_feed"


@register
class SwipeNext:
    name = "reels.swipe_next"
    requires_screen = "reels_feed"

    def run(self, ctx, params):
        from v2.plugins.reel_log import say, swipe_ok_line, swipe_fail_line, next_reel_line

        # content_search / reels_warmup przekazują własny prefix; default = niche
        log_prefix = params.get("log_prefix")

        def _say(msg: str) -> None:
            say(msg, prefix=log_prefix) if log_prefix is not None else say(msg)

        w, h = ctx.account.device["_hardware"]["resolution"]
        max_tries = max(1, int(params.get("max_tries", len(ATTEMPTS))))

        before_meta = ctx.scratch.get("reel") or {}
        before = before_meta.get("marker")
        author_before = before_meta.get("author")
        if not before:
            before_meta = read_current_reel(ctx.device)
            before = before_meta.get("marker")
            author_before = before_meta.get("author")

        # Brak markera przed swipe — często chwilowa nakładka / zły ekran.
        # Recover feed + settle + ponowny dump zamiast natychmiastowego fail.
        if not before:
            before_meta, before, author_before = _recover_before_marker(ctx)
            if before:
                ctx.scratch["reel"] = before_meta

        if not before:
            _say(swipe_fail_line())
            return StepResult.failed(
                "brak markera przed swipe — nie da sie potwierdzic zmiany rolki")

        # Komunikat „Nastepna rolka…” zwykle z pluginu (przed krótką pauzą);
        # tu tylko gdy ktoś woła swipe bezpośrednio.
        if params.get("announce", True):
            _say(next_reel_line())
        after = None
        after_meta = {}
        for i, (y_from_r, y_to_r, dur) in enumerate(ATTEMPTS[:max_tries]):
            ctx.ht.swipe(w // 2, int(h * y_from_r), w // 2, int(h * y_to_r),
                         duration_ms=dur, pause_kind="swipe")
            # Swipe zmienia rolkę; czekamy na zakończenie animacji, a potem
            # read_current_reel robi własny dump do weryfikacji markera.
            settle_for_animation(ctx, max_wait_s=3.0, fallback_wait_s=1.5)
            ctx.humanizer.pause("tap")

            after_meta = read_current_reel(ctx.device)
            after = after_meta.get("marker")

            # marker_unreadable: settle hierarchii + drugi dump zanim uznamy fail
            if not after:
                ctx.log("reels.swipe_next.meta_settle", attempt=i + 1)
                ctx.device.wait_for_stable_ui(max_wait_s=2.5)
                after_meta = read_current_reel(ctx.device)
                after = after_meta.get("marker")

            if after and after != before:
                ctx.scratch["reel"] = after_meta
                _say(swipe_ok_line(
                    author_before=author_before,
                    author_after=after_meta.get("author"),
                ))
                return StepResult.ok(
                    before=_short(before), after=_short(after),
                    tries=i + 1, confirmed=True,
                    author=after_meta.get("author"))

            ctx.log("reels.swipe_next.retry", attempt=i + 1,
                    why="marker_unreadable" if not after else "same_reel")

        _say(swipe_fail_line())
        return StepResult.failed(
            "nie potwierdzono przejscia do kolejnej rolki",
            before=_short(before), after=_short(after),
            marker_unreadable=not bool(after), confirmed=False)


def _recover_before_marker(ctx) -> tuple[dict, str | None, str | None]:
    """ensure reels_feed + dłuższy settle + ponowny odczyt meta."""
    nav = getattr(ctx, "navigator", None)
    if nav is not None:
        try:
            nav.ensure(REELS, max_steps=3)
        except Exception:
            pass
    settle_for_animation(ctx, max_wait_s=2.5, fallback_wait_s=2.0)
    try:
        ctx.device.wait_for_stable_ui(max_wait_s=2.0)
    except Exception:
        pass
    meta = read_current_reel(ctx.device)
    return meta, meta.get("marker"), meta.get("author")


def _short(marker):
    if not marker:
        return None
    return marker if len(marker) <= 70 else marker[:67] + "..."
