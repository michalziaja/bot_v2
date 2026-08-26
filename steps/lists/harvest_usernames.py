"""list.harvest_usernames — zbierz nicki z DOWOLNEJ otwartej listy.

Jeden krok zamiast osobnego zbieracza dla każdego rodzaju listy: lajkujący
posta, lajkujący rolki, obserwujący, obserwowani, komentujący. Wszystkie są
tą samą listą wierszy — różnią się tylko nazwą ekranu i ID nicku.

Nie otwiera listy ani jej nie zamyka; robi to wołający, bo tylko on wie,
skąd przyszedł i dokąd wraca.

Zapisuje `has_ring` per nick — aktywna relacja jest widoczna tylko tutaj,
a później decyduje o wyborze kandydatów do interakcji.
"""
from __future__ import annotations

import random
import time

from v2.core.screen_map import flatten_hierarchy
from v2.data.stores import RawProfilesStore
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels import _sheet

ALL_USERNAME_IDS = (_sheet.LIKER_USERNAME_IDS
                    + _sheet.COMMENT_USERNAME_IDS)


@register
class HarvestUsernames:
    name = "list.harvest_usernames"
    requires_screen = None

    def run(self, ctx, params):
        rng = random.Random()
        limit = int(params.get("limit", 20))
        max_scrolls = int(params.get("max_scrolls", 20))
        source = params.get("source") or "list"
        username_ids = tuple(params.get("username_ids") or ALL_USERNAME_IDS)
        only_with_ring = bool(params.get("only_with_story_ring", False))
        # None = forever; 0 = bez dedupu historii; >0 = okno dni
        dedup_days = params.get("dedup_days")
        # Wyklucz nicki (np. from_target / autor posta) — casefold, bez @
        exclude = {
            str(x).strip().lstrip("@").casefold()
            for x in (params.get("exclude") or [])
            if x
        }

        seen: dict = {}
        stall = 0
        scrolls_done = 0
        w, h = ctx.account.device["_hardware"]["resolution"]

        # Oddech po expand — RecyclerView dociąga wiersze przed pierwszym dumpem
        time.sleep(_sheet._BREATH_BEFORE_HARVEST_S)

        for _ in range(max_scrolls):
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
            rows = _sheet.collect_rows(flat, username_ids)
            # Dump widocznych nicków (pierwszy ekran + po scrollach) — diagnoza
            if scrolls_done == 0 or (scrolls_done > 0 and rows):
                ctx.log("list.harvest_usernames.dump", source=source,
                        scroll=scrolls_done, visible=len(rows),
                        sample=[r["username"] for r in rows[:8]])
            added = 0
            for r in rows:
                u = r["username"]
                if u in seen:
                    continue
                if u.casefold() in exclude:
                    continue
                if only_with_ring and r["ring"] is not True:
                    continue
                seen[u] = r["ring"]
                added += 1
                if len(seen) >= limit:
                    break
            if len(seen) >= limit:
                break

            if added == 0:
                stall += 1
                if stall >= 2:
                    break
            else:
                stall = 0

            # Scroll LISTY (nie sheet handle) — dump kolejnej porcji wierszy
            ctx.ht.swipe(w // 2, int(h * 0.78), w // 2, int(h * 0.32),
                         duration_ms=int(rng.uniform(320, 420)), pause_kind="tap")
            ctx.device.wait_for_stable_ui(max_wait_s=1.2)
            # Krótki oddech po swipe — wiersze dopływają po stable_ui
            time.sleep(0.12)
            scrolls_done += 1

        if not seen:
            ctx.log("list.harvest_usernames.empty", source=source,
                    scrolls=scrolls_done, username_ids=list(username_ids)[:6])
            return StepResult.skipped("no_usernames", source=source)

        store = RawProfilesStore(ctx.account.account_id,
                                 ctx.events.file_path.parent.parent.parent)
        saved = store.add_rows(
            [{"username": u, "has_ring": ring} for u, ring in seen.items()],
            source=source, dedup_days=dedup_days, **(params.get("extra") or {}))

        # Ile zebranych, a ile odrzuconych jako już znane (dedup store)
        dupes_skipped = max(0, len(seen) - saved)
        with_ring = sum(1 for v in seen.values() if v)
        ctx.log("list.harvest_usernames.done", source=source, collected=len(seen),
                saved=saved, with_ring=with_ring, dupes_skipped=dupes_skipped,
                scrolls=scrolls_done, dedup_days=dedup_days)
        return StepResult.ok(collected=len(seen), saved=saved, with_ring=with_ring,
                            dupes_skipped=dupes_skipped, scrolls=scrolls_done)
