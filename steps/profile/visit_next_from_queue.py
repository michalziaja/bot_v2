"""profile.visit_next_from_queue — pomocnik do iteracji bez `for_each` w YAML.

Bierze pierwszy username z ctx.scratch['profile_queue'], wykonuje pełny
mini-pipeline: search → tap wynik → profile.visit → profile.exit. Zwraca
StepResult z payloadem { username, action, remaining_in_queue }. Jeśli
kolejka pusta → skipped('queue_empty', skip_until='session.end').

Powtórz go N razy w flow — analogicznie jak dziś reels.watch × 5 w
reels_niche_harvest.yml. Docelowo zastąpi go native for_each w FlowRunner.
"""
from __future__ import annotations
from v2.runner.step import StepResult
from v2.steps.registry import register, get as get_step


@register
class VisitNextFromQueue:
    name = "profile.visit_next_from_queue"
    requires_screen = None

    def run(self, ctx, params):
        queue = ctx.scratch.get("profile_queue") or []
        if not queue:
            return StepResult.skipped("queue_empty",
                                      skip_until=params.get("skip_until", "session.end"))

        entry = queue.pop(0)
        username = entry.get("username")
        ctx.log("profile.visit_next.start", username=username, remaining=len(queue))

        # baseline_screen ustawi search.pick_result — wraca na search_results
        # search.open — dostosuj się nawet gdy jesteśmy w reels
        for step_name, step_params in [
            ("search.open",       {}),
            ("search.type_query", {"query": f"@{username}"}),
            ("search.pick_result", {"username": username, "prefer_exact": True}),
            ("profile.visit",     params.get("visit") or {}),
            ("profile.exit",      {}),
        ]:
            cls = get_step(step_name)
            step = cls()
            res = step.run(ctx, step_params)
            ctx.log("profile.visit_next.sub", step=step_name, status=res.status,
                    reason=(res.payload or {}).get("reason"))
            if res.status == "failed":
                # spróbuj wyjść na home_feed żeby kolejny username startował z czystej pozycji
                ctx.navigator.ensure("home_feed", max_steps=4)
                return StepResult.failed(f"{step_name} failed",
                                         username=username,
                                         sub_error=res.error)
            # jeśli visit zwrócił skip_until=session.end (np. daily limit) — propaguj
            if res.status == "skipped" and (res.payload or {}).get("skip_until") == "session.end":
                return StepResult.skipped("session_end_from_visit",
                                          username=username,
                                          skip_until="session.end")

        return StepResult.ok(username=username, remaining=len(queue))
