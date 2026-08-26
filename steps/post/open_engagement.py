"""post.open_likers / post.open_comments — otwórz listę zaangażowanych z posta.

Krok tylko OTWIERA — zbieranie robi `list.harvest_usernames`, a zamknięcie
należy do wołającego.

**Obowiązkowa sekwencja (likers I commenters):**
1. Otwórz listę (tap entry)
2. Expand swipe w górę (pełny sheet)
3. POTWIERDŹ rozwinięcie (więcej wierszy / sheet top < 35% H) — log
4. Dopiero potem wołający może scroll + harvest
5. Half-sheet → retry pull; NIE zwracamy ok bez confirmed
"""
from __future__ import annotations

import time

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.lists.harvest_usernames import ALL_USERNAME_IDS
from v2.steps.post import _post
from v2.steps.registry import register
from v2.steps.reels import _sheet
from v2.steps.system._ui_helpers import settle_for_animation

# Settle po tapie — sheet bywa pusty w pierwszej klatce
_OPEN_SETTLE_S = 0.5

# Sygnał „sheet otwarty" bez wymagania nicków (peek komentarzy bywa bez username ID)
_SHEET_SIGNAL_SUBSTR = (
    "bottom_sheet", "layout_listview", "layout_comment_thread",
    "row_user_primary_name", "row_comment_textview_username",
    "follow_list_username", "row_search_user_username",
)
_SHEET_SCREENS = (
    "reel_likers_list", "reel_comments_sheet", "followers_list",
    "following_list",
)


def _count_rows(flat) -> int:
    return len(_sheet.collect_rows(flat, ALL_USERNAME_IDS))


def _sheet_signal(flat, landed) -> bool:
    if landed in _SHEET_SCREENS:
        return True
    for e in flat:
        rid = (e.get("resource-id") or "").lower()
        if any(s in rid for s in _SHEET_SIGNAL_SUBSTR):
            return True
    return False


class _OpenEngagement:
    what: str = ""
    requires_screen = None

    def _find_entry(self, flat) -> tuple | None:
        raise NotImplementedError

    def run(self, ctx, params):
        baseline = ctx.navigator.identify().top
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        target = self._find_entry(flat)
        if not target:
            sample = []
            for e in flat:
                if not e.get("clickable"):
                    continue
                t = (e.get("text") or "")[:40]
                d = (e.get("content-desc") or "")[:40]
                rid = (e.get("resource-id") or "").split("/")[-1]
                if t or d or rid:
                    sample.append(f"{rid}|{t}|{d}")
                if len(sample) >= 8:
                    break
            ctx.log(f"post.open_{self.what}.no_entry", sample=sample)
            return StepResult.skipped("no_entry")

        # Log PRZED tapem — hierarchy/settle poniżej potrafią trwać sekundy.
        # target_scraper już mówi „Lista … — otwieram" przed krokiem.
        if not ctx.scratch.get("target_scraper_active"):
            _sheet.operator_say(ctx, "Otwieram listę…")
        ctx.ht.tap(*target, pause_kind="tap")
        ctx.humanizer.pause("between_steps")
        # Czekamy tylko na zakończenie animacji tapu; weryfikacja i tak
        # idzie przez kolejny hierarchy + identify.
        settle_for_animation(ctx, max_wait_s=2.0, fallback_wait_s=1.0)
        time.sleep(_OPEN_SETTLE_S)

        after = flatten_hierarchy(ctx.device.hierarchy(force=True))
        rows_open = _count_rows(after)
        landed = ctx.navigator.identify().top
        signal = _sheet_signal(after, landed)

        if not rows_open and not signal:
            settle_for_animation(ctx, max_wait_s=1.5, fallback_wait_s=1.0)
            time.sleep(0.35)
            after = flatten_hierarchy(ctx.device.hierarchy(force=True))
            rows_open = _count_rows(after)
            landed = ctx.navigator.identify().top
            signal = _sheet_signal(after, landed)

        if not rows_open and not signal:
            ctx.log(f"post.open_{self.what}.nothing_opened", baseline=baseline,
                    landed=landed)
            return StepResult.skipped("nothing_opened", landed=landed)

        # Expand + POTWIERDZENIE — bez tego nie wolno scrollować
        # (stdout: tylko „Otwieram listę…" wyżej; expand milczy — dump w JSONL)
        exp = _sheet.expand_sheet_confirmed(ctx, max_pulls=3)
        landed = ctx.navigator.identify().top
        # Szczegóły tylko do events.jsonl — panel ma te kindy w QUIET
        ctx.log(f"post.open_{self.what}.expand_check",
                confirmed=exp.get("confirmed"),
                full_screen=exp.get("full_screen"),
                rows_before=exp.get("rows_before"),
                rows_after=exp.get("rows_after"),
                sheet_top_before=exp.get("sheet_top_before"),
                sheet_top_after=exp.get("sheet_top_after"),
                sheet_top_pct=exp.get("sheet_top_pct"),
                sheet_h=exp.get("sheet_h"),
                screen_h=exp.get("screen_h"),
                handle_y=exp.get("handle_y"),
                top_src=exp.get("top_src"),
                pulls_done=exp.get("pulls_done"),
                y_from=exp.get("y_from"), y_to=exp.get("y_to"),
                landed=landed)

        if not exp.get("full_screen"):
            ctx.log(f"post.open_{self.what}.not_expanded",
                    landed=landed, full_screen=False,
                    **{k: exp.get(k) for k in (
                        "rows_before", "rows_after", "sheet_top_after",
                        "sheet_top_pct", "sheet_h", "screen_h", "pulls_done")})
            return StepResult.skipped(
                "not_expanded", landed=landed, full_screen=False,
                rows_before=exp.get("rows_before"),
                rows_after=exp.get("rows_after"),
                sheet_top_after=exp.get("sheet_top_after"),
                sheet_top_pct=exp.get("sheet_top_pct"))

        rows_after = int(exp.get("rows_after") or 0)
        # Komentarze bywają puste w pierwszej klatce po expand — dociągnięcie UI
        if rows_after <= 0:
            time.sleep(0.85)
            flat3 = flatten_hierarchy(ctx.device.hierarchy(force=True))
            rows_after = _count_rows(flat3)
            if rows_after <= 0:
                # Diagnostyka: jakie ID widać na sheetcie (bez pełnego dumpa)
                sample = []
                for e in flat3:
                    rid = (e.get("resource-id") or "").split("/")[-1]
                    t = (e.get("text") or "")[:28]
                    if rid and (t or "user" in rid or "comment" in rid
                                or "name" in rid):
                        sample.append(f"{rid}|{t}")
                    if len(sample) >= 12:
                        break
                ctx.log(f"post.open_{self.what}.empty_after_expand",
                        full_screen=True, landed=landed, sample=sample,
                        sheet_top=exp.get("sheet_top_after"),
                        sheet_top_pct=exp.get("sheet_top_pct"))
                return StepResult.skipped(
                    "empty_after_expand", landed=landed, full_screen=True,
                    rows_after=0, sheet_top_pct=exp.get("sheet_top_pct"))

        ctx.log(f"post.open_{self.what}.expanded_confirmed",
                full_screen=True,
                rows_before=exp.get("rows_before"),
                rows_after=rows_after,
                sheet_top=exp.get("sheet_top_after"),
                sheet_top_pct=exp.get("sheet_top_pct"),
                sheet_h=exp.get("sheet_h"),
                screen_h=exp.get("screen_h"),
                pulls_done=exp.get("pulls_done"))

        ctx.scratch["engagement_baseline"] = baseline
        return StepResult.ok(
            landed=landed, baseline=baseline,
            rows=rows_after,
            rows_before=exp.get("rows_before"),
            rows_after=rows_after,
            expanded=True,
            expand_confirmed=True,
            full_screen=True,
            sheet_top_after=exp.get("sheet_top_after"),
            sheet_top_pct=exp.get("sheet_top_pct"),
            sheet_h=exp.get("sheet_h"),
            screen_h=exp.get("screen_h"),
            pulls_done=exp.get("pulls_done"),
            y_from=exp.get("y_from"), y_to=exp.get("y_to"))


@register
class OpenLikers(_OpenEngagement):
    name = "post.open_likers"
    what = "likers"

    def _find_entry(self, flat):
        return _post.find_likers_entry(flat)


@register
class OpenComments(_OpenEngagement):
    name = "post.open_comments"
    what = "comments"

    def _find_entry(self, flat):
        return _post.find_comments_entry(flat)
