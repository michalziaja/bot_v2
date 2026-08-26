"""reels.like — silnik decyzyjny gate/match/chance zaszyty w pluginie.

Params z YAML:
  gate:   {min_likes: 100, min_comments: 35, max_age_hours: 48}
      warunki bezwzględne z UI. Jeśli któryś nie znany (None) → decyzja per config.
  match:  {min_niche_score: 55, allow_verdict: [match, partial]}
      wymaga wcześniejszego reels.analyze_frames_ai (bierze z ctx.scratch['reel_ai']).
  chance: {p: 0.5}  |  {formula: "score/100"}
      losowość humanizująca.

Wynik:
  aborted_by_gate — nie przeszło warstwy 1
  skipped         — przeszło gate, ale match=off lub chance nie trafił
  ok              — wykonany lajk
  failed          — wykryto błąd akcji
"""
from __future__ import annotations
import random
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels._guards import skip_if_ad


@register
class LikeReel:
    name = "reels.like"
    requires_screen = "reels_feed"

    def run(self, ctx, params):
        skip = skip_if_ad(ctx)
        if skip: return skip
        reel = ctx.scratch.get("reel") or {}
        ai   = ctx.scratch.get("reel_ai") or {}

        # ---- Warstwa 1: gate ----
        gate = params.get("gate") or {}
        gate_result = _eval_gate(reel, gate)
        if not gate_result["pass"]:
            ctx.log("reels.like.gate", **gate_result, author=reel.get("author"))
            return StepResult("aborted_by_gate", payload={"reason": gate_result["why"], "reel": reel})

        # ---- Warstwa 2: match ----
        match = params.get("match") or {}
        match_result = _eval_match(ai, match)
        if not match_result["pass"]:
            ctx.log("reels.like.match", **match_result)
            return StepResult.skipped("match_failed", **match_result)

        # ---- Warstwa 3: chance ----
        chance = params.get("chance") or {}
        rng = random.Random()
        chance_result = _eval_chance(ai, chance, rng)
        if not chance_result["pass"]:
            ctx.log("reels.like.chance", **chance_result)
            return StepResult.skipped("chance_missed", **chance_result)

        # ---- Akcja: double-tap w środku wideo ----
        w, h = ctx.account.device["_hardware"]["resolution"]
        cx, cy = w // 2, int(h * 0.5)
        ctx.device.tap(cx, cy)
        ctx.device.tap(cx, cy)
        ctx.humanizer.pause("tap")

        ctx.log("reels.like.done",
                author=reel.get("author"),
                gate=gate_result, match=match_result, chance=chance_result)
        # Ludzki stdout tylko gdy caller podał prefix (warmup);
        # niche / content_search mówią „Polubiono” z własnego logu.
        log_prefix = params.get("log_prefix")
        if log_prefix is not None:
            from v2.plugins.reel_log import say
            who = reel.get("author")
            say(f"Polubiono @{who}" if who else "Polubiono", prefix=log_prefix)
        return StepResult.ok(author=reel.get("author"),
                             niche_score=ai.get("niche_score"),
                             p=chance_result.get("p"))


def _eval_gate(reel: dict, gate: dict) -> dict:
    if not gate:
        return {"pass": True, "why": "no_gate"}
    reasons = []
    # min_likes
    if "min_likes" in gate:
        v = reel.get("likes")
        if v is None:
            reasons.append("likes_unknown")
        elif v < gate["min_likes"]:
            reasons.append(f"likes<{gate['min_likes']}")
    if "min_comments" in gate:
        v = reel.get("comments")
        if v is None:
            reasons.append("comments_unknown")
        elif v < gate["min_comments"]:
            reasons.append(f"comments<{gate['min_comments']}")
    ok = not reasons
    return {"pass": ok, "why": ",".join(reasons) or "ok"}


def _eval_match(ai: dict, match: dict) -> dict:
    if not match:
        return {"pass": True, "why": "no_match"}
    if not ai:
        return {"pass": False, "why": "no_ai_analysis"}
    verdict = ai.get("verdict")
    score   = ai.get("niche_score") or 0
    allow   = set(match.get("allow_verdict") or ["match", "partial"])
    if verdict not in allow:
        return {"pass": False, "why": f"verdict={verdict}", "score": score}
    min_score = match.get("min_niche_score", 0)
    if score < min_score:
        return {"pass": False, "why": f"score<{min_score}", "score": score}
    return {"pass": True, "verdict": verdict, "score": score}


def _eval_chance(ai: dict, chance: dict, rng: random.Random) -> dict:
    """Delegat do wspólnego `v2.core.chance` — obsługuje zapis z katalogu
    pluginów (`{mode, value}`) oraz starszy z flow YAML (`{p}`/`{formula}`)."""
    from v2.core.chance import roll as chance_roll
    return chance_roll(chance, {"score": ai.get("niche_score")}, rng)
