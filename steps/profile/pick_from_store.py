"""profile.pick_from_store — wybiera profile do interakcji ze źródła.

Źródła (`params.source_kind`):
  - `target_candidates` — lista po filtrze HTTP (TargetCandidatesStore; default)
  - `raw_profiles` — surowa pula po scrape (RawProfilesStore)

Wyklucza już odwiedzone (VisitedProfilesStore, dedup_days).
Wynik → ctx.scratch['profile_queue'].
"""
from __future__ import annotations
import random

from v2.data.stores import (
    RawProfilesStore, TargetCandidatesStore, VisitedProfilesStore,
)
from v2.runner.step import StepResult
from v2.steps.registry import register


@register
class ProfilePickFromStore:
    name = "profile.pick_from_store"
    requires_screen = None

    def run(self, ctx, params):
        limit = int(params.get("limit", 5))
        dedup_days = int(params.get("dedup_days", 30))
        min_age_min = int(params.get("min_age_minutes", 5))
        # raw: kolejność z pliku (najnowsze); candidates: opcjonalny shuffle
        shuffle = bool(params.get("shuffle", True))
        source_kind = (params.get("source_kind")
                       or params.get("kind")
                       or "target_candidates")

        root = ctx.events.file_path.parent.parent.parent
        vis = VisitedProfilesStore(ctx.account.account_id, root)
        excluded = vis.visited_within(dedup_days)
        age_s = min_age_min * 60

        if source_kind == "raw_profiles":
            store = RawProfilesStore(ctx.account.account_id, root)
            # Okno jak visited — hardening gdy visited-miss nie złapał nicka
            interacted_s = dedup_days * 86400.0 if dedup_days > 0 else 0.0
            # Bufor większy gdy shuffle — raw i tak jest newest-first
            pool = store.pick(
                limit=limit * 3 if shuffle else limit,
                exclude_usernames=excluded,
                not_added_within_s=age_s,
                skip_interacted_within_s=interacted_s,
            )
            available = len(store.unique_usernames())
        elif source_kind == "target_candidates":
            store = TargetCandidatesStore(ctx.account.account_id, root)
            pool = store.pick(
                limit=limit * 3,
                exclude_usernames=excluded,
                not_added_within_s=age_s,
            )
            available = len(store.read_all())
        else:
            ctx.log("profile.pick_from_store.unsupported",
                    source_kind=source_kind)
            return StepResult.skipped("unsupported_source",
                                      source_kind=source_kind)

        if shuffle:
            random.shuffle(pool)
        picks = pool[:limit]

        ctx.scratch["profile_queue"] = picks
        ctx.log("profile.pick_from_store",
                source_kind=source_kind,
                available_in_store=available,
                already_visited=len(excluded),
                picked=len(picks),
                usernames=[p["username"] for p in picks])
        if not picks:
            return StepResult.skipped(
                "no_candidates", source_kind=source_kind,
                available=available, already_visited=len(excluded))
        return StepResult.ok(
            picked=len(picks), source_kind=source_kind,
            usernames=[p["username"] for p in picks])
