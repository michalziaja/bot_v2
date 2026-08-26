"""system.stats_snapshot — wejdź w profil, refresh, odczytaj posty/followers/following.

Respectuje toggle z configu (`toggles.steps.stats_snapshot.enabled` +
`on_session_start` / `on_session_end`). Params flow-a mogą wymusić „when: start/end”.
"""
from __future__ import annotations
from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register


STAT_IDS = {
    "posts":     "com.instagram.android:id/profile_header_familiar_post_count_value",
    "followers": "com.instagram.android:id/profile_header_familiar_followers_value",
    "following": "com.instagram.android:id/profile_header_familiar_following_value",
}


@register
class StatsSnapshot:
    name = "system.stats_snapshot"
    requires_screen = None  # sam sobie doprowadzi do profile_self

    def run(self, ctx, params):
        if not ctx.account.step_enabled("stats_snapshot"):
            return StepResult.skipped("disabled_in_config")

        # opcjonalnie odfiltruj po momencie
        moment = params.get("when")   # None / "session_start" / "session_end"
        if moment:
            opt_key = f"on_{moment}"
            if not ctx.account.step_option("stats_snapshot", opt_key, True):
                return StepResult.skipped(f"disabled_for_{moment}")

        if not ctx.navigator.ensure("profile_self"):
            return StepResult.failed("nie mogę doprowadzić do profile_self")

        # pull-to-refresh w środku headera
        w, h = ctx.account.device["_hardware"]["resolution"]
        ctx.ht.swipe(w // 2, int(h * 0.30), w // 2, int(h * 0.64),
                     duration_ms=400, pause_kind="after_refresh")

        # potwierdź że nadal na profilu (refresh mógł coś schrzanić)
        if not ctx.navigator.ensure("profile_self", max_steps=3):
            return StepResult.failed("po refresh straciliśmy profile_self")

        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        stats = {k: None for k in STAT_IDS}
        for n in flat:
            rid = n.get("resource-id")
            for k, target_rid in STAT_IDS.items():
                if rid == target_rid:
                    t = (n.get("text") or "").strip().replace(",", "").replace(" ", "")
                    try:    stats[k] = int(t)
                    except: pass
        # log statystyk jako osobny event (do później czytania z panelu)
        ctx.log("stats_snapshot", **stats)
        # dodatkowo trzymamy w scratch — inne kroki mogą użyć
        ctx.scratch["stats"] = stats

        if all(v is None for v in stats.values()):
            return StepResult.failed("nie odczytano żadnej statystyki", stats=stats)
        return StepResult.ok(**stats)
