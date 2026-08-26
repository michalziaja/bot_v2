"""reels.harvest_likers — tap 'Polubione przez X i innych', scroll listy,
zbierz username'y do limitu, zapisz do RawProfilesStore, back.

Gate/match/chance jak w reels.like — plugin sam interpretuje.
"""
from __future__ import annotations
import time
from v2.core.screen_map import flatten_hierarchy
from v2.data.stores import RawProfilesStore
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels import _sheet
from v2.steps.reels._guards import skip_if_ad

USERNAME_IDS = _sheet.LIKER_USERNAME_IDS
LIKERS_HEADER_IDS = ("com.instagram.android:id/like_and_view_count_header_container",
                     "com.instagram.android:id/like_count_text")


@register
class HarvestLikers:
    name = "reels.harvest_likers"
    requires_screen = "reels_feed"

    def run(self, ctx, params):
        skip = skip_if_ad(ctx)
        if skip: return skip
        # match/chance jak w like
        match  = params.get("match") or {}
        chance = params.get("chance") or {}
        limit  = int(params.get("limit", 20))

        ai = ctx.scratch.get("reel_ai") or {}
        if match:
            mres = _eval_match(ai, match)
            if not mres["pass"]:
                ctx.log("reels.harvest_likers.match_skip", **mres)
                return StepResult.skipped("match_failed", **mres)

        reel = ctx.scratch.get("reel") or {}

        # Wołamy w bloku który ZAWSZE zamknie sheet — inaczej następny krok
        # (np. swipe_next) klika w sheet zamiast w rolkę.
        _sheet.operator_say(ctx, "Otwieram listę lajkujących…")
        opened = _sheet.open_likers_sheet(ctx)
        if not opened:
            ctx.log("reels.harvest_likers.no_entry")
            return StepResult.skipped("no_likers_entry")

        keep_open = bool(params.get("keep_sheet_open"))
        try:
            # Expand → potwierdź pełny sheet → dopiero scroll/harvest
            info = _sheet.expand_sheet_confirmed(ctx, max_pulls=3)
            ctx.log("reels.harvest_likers.expand_check",
                    confirmed=info.get("confirmed"),
                    rows_before=info.get("rows_before"),
                    rows_after=info.get("rows_after"),
                    sheet_top_after=info.get("sheet_top_after"),
                    pulls_done=info.get("pulls_done"))
            if not info.get("confirmed"):
                ctx.log("reels.harvest_likers.not_expanded")
                return StepResult.skipped("not_expanded", **{
                    k: info.get(k) for k in (
                        "rows_before", "rows_after", "sheet_top_after")
                })
            ctx.log("reels.harvest_likers.expanded_confirmed",
                    rows_before=info.get("rows_before"),
                    rows_after=info.get("rows_after"),
                    sheet_top=info.get("sheet_top_after"))

            # Oddech po expand — lista dociąga wiersze zanim force dump / scroll
            time.sleep(_sheet._BREATH_BEFORE_HARVEST_S)

            if not ctx.navigator.ensure("reel_likers_list", max_steps=3):
                return StepResult.failed("nie doszliśmy do reel_likers_list po tap")

            rows = _scroll_and_collect(ctx, limit)

            store = RawProfilesStore(ctx.account.account_id,
                                     ctx.events.file_path.parent.parent.parent)
            # extra/dedup_days — np. target_scraper (from_target, content_kind)
            extra = dict(params.get("extra") or {})
            n = store.add_rows(
                rows, source="reel_likers",
                dedup_days=params.get("dedup_days"),
                from_author=reel.get("author"),
                niche_score=ai.get("niche_score"),
                verdict=ai.get("verdict"),
                **extra)

            with_ring = sum(1 for r in rows if r.get("has_ring"))
            dupes_skipped = max(0, len(rows) - n)
            ctx.log("reels.harvest_likers.done", collected=len(rows), saved=n,
                    with_ring=with_ring, dupes_skipped=dupes_skipped,
                    store=str(store.file_path))
            return StepResult.ok(collected=len(rows), saved=n, with_ring=with_ring,
                                dupes_skipped=dupes_skipped)

        finally:
            # ZAWSZE wróć do reels_feed — chyba że wołający właśnie zamierza
            # pracować na tej liście dalej (interakcja „na gorąco") i sam ją
            # zamknie w swoim `finally`.
            if not keep_open:
                _sheet.return_to_reels(ctx)


def _scroll_and_collect(ctx, limit: int) -> list[dict]:
    """Przewiń listę i zbierz wiersze `{username, has_ring}`.

    Aktywna relacja jest zapisywana razem z nickiem, bo to jedyny moment,
    w którym ją widać — później, przy wyborze kandydatów do interakcji,
    nie ma już z czego jej odczytać.
    """
    seen: list[dict] = []
    seen_set: set[str] = set()
    w, h = ctx.account.device["_hardware"]["resolution"]
    max_scrolls = 20
    stall = 0
    for _ in range(max_scrolls):
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        added = 0
        for row in _sheet.collect_rows(flat, USERNAME_IDS):
            u = row["username"]
            if u in seen_set:
                continue
            seen.append({"username": u, "has_ring": row["ring"]})
            seen_set.add(u)
            added += 1
            if len(seen) >= limit:
                return seen
        if added == 0:
            stall += 1
            if stall >= 2: break
        else:
            stall = 0
        ctx.ht.swipe(w // 2, int(h * 0.75), w // 2, int(h * 0.35),
                     duration_ms=350, pause_kind="tap")
        time.sleep(0.4)
    return seen


def _eval_match(ai: dict, match: dict) -> dict:
    if not ai:
        return {"pass": False, "why": "no_ai"}
    verdict = ai.get("verdict")
    score   = ai.get("niche_score") or 0
    allow   = set(match.get("allow_verdict") or ["match", "partial"])
    if verdict not in allow:
        return {"pass": False, "why": f"verdict={verdict}"}
    min_score = match.get("min_niche_score", 0)
    if score < min_score:
        return {"pass": False, "why": f"score<{min_score}", "score": score}
    return {"pass": True, "verdict": verdict, "score": score}
