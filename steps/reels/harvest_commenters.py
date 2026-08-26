"""reels.harvest_commenters — zbiera username'y z sheet-a komentarzy rolki.

Uses `ctx.scratch['reel_top_comments']` jeśli już był `reels.read_caption_comments`;
w przeciwnym razie sam otwiera sheet, zbiera username'y (rozszerza pole widoczne
scrollem), zapisuje do RawProfilesStore, zamyka.
"""
from __future__ import annotations
import time
from v2.core.screen_map import flatten_hierarchy
from v2.data.stores import RawProfilesStore
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels import _sheet
from v2.steps.reels._guards import skip_if_ad

COMMENT_USER_ID = _sheet.COMMENT_USERNAME_IDS[0]


@register
class HarvestCommenters:
    name = "reels.harvest_commenters"
    requires_screen = "reels_feed"

    def run(self, ctx, params):
        skip = skip_if_ad(ctx)
        if skip: return skip
        limit = int(params.get("limit", 15))
        reel = ctx.scratch.get("reel") or {}
        ai   = ctx.scratch.get("reel_ai") or {}

        # `keep_sheet_open` — wołający (interakcja „na gorąco") pracuje dalej na
        # tej liście i sam ją zamknie w swoim `finally`.
        keep_open = bool(params.get("keep_sheet_open"))

        # jeśli już mamy top_comments z read_caption_comments, użyjmy
        pre = ctx.scratch.get("reel_top_comments") or []
        if pre and not keep_open:
            usernames = [c["user"] for c in pre if c.get("user")]
        else:
            _sheet.operator_say(ctx, "Otwieram listę komentujących…")
            if not _sheet.open_comments_sheet(ctx):
                return StepResult.skipped("no_comments_entry")
            try:
                # Expand → potwierdź → dopiero scroll (nigdy na half-sheet)
                info = _sheet.expand_sheet_confirmed(ctx, max_pulls=3)
                ctx.log("reels.harvest_commenters.expand_check",
                        confirmed=info.get("confirmed"),
                        rows_before=info.get("rows_before"),
                        rows_after=info.get("rows_after"),
                        sheet_top_after=info.get("sheet_top_after"),
                        pulls_done=info.get("pulls_done"))
                if not info.get("confirmed"):
                    ctx.log("reels.harvest_commenters.not_expanded")
                    return StepResult.skipped("not_expanded", **{
                        k: info.get(k) for k in (
                            "rows_before", "rows_after", "sheet_top_after")
                    })
                ctx.log("reels.harvest_commenters.expanded_confirmed",
                        rows_before=info.get("rows_before"),
                        rows_after=info.get("rows_after"),
                        sheet_top=info.get("sheet_top_after"))
                # Oddech po expand — zanim scroll zbierze pierwszą klatkę
                time.sleep(_sheet._BREATH_BEFORE_HARVEST_S)
                usernames = _scroll_and_collect_users(ctx, limit)
            finally:
                if not keep_open:
                    _sheet.return_to_reels(ctx)

        store = RawProfilesStore(ctx.account.account_id,
                                 ctx.events.file_path.parent.parent.parent)
        # extra/dedup_days — np. target_scraper (from_target, content_kind)
        extra = dict(params.get("extra") or {})
        batch = usernames[:limit]
        n = store.add_many(
            batch, source="reel_commenters",
            dedup_days=params.get("dedup_days"),
            from_author=reel.get("author"),
            niche_score=ai.get("niche_score"),
            verdict=ai.get("verdict"),
            **extra)
        dupes_skipped = max(0, len(batch) - n)
        ctx.log("reels.harvest_commenters.done", collected=len(usernames), saved=n,
                dupes_skipped=dupes_skipped)
        return StepResult.ok(collected=len(usernames), saved=n,
                            dupes_skipped=dupes_skipped)


def _scroll_and_collect_users(ctx, limit: int) -> list[str]:
    seen, seen_set = [], set()
    w, h = ctx.account.device["_hardware"]["resolution"]
    for _ in range(15):
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        added = 0
        for n in flat:
            if n.get("resource-id") == COMMENT_USER_ID:
                u = (n.get("text") or "").strip()
                if u and u not in seen_set:
                    seen.append(u); seen_set.add(u); added += 1
                    if len(seen) >= limit:
                        return seen
        if added == 0: break
        ctx.ht.swipe(w // 2, int(h * 0.75), w // 2, int(h * 0.35),
                     duration_ms=350, pause_kind="tap")
        time.sleep(0.35)
    return seen
