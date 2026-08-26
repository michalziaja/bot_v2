"""reels.analyze_frames_ai — wywołuje AI Vision w trybie reel_niche.

Wejścia do modelu (niezależne flagi w `ai`):
  - klatki (`use_frames` + composite z watch) — obraz
  - opis (`read_caption` + caption) — tekst

Możliwe kombinacje: tylko obraz / tylko opis (text-only, frames=[]) /
oba razem. Brak obu → SKIP `no_ai_inputs`.

Wynik trafia do `ctx.scratch['reel_ai']` — kolejne kroki (like, harvest_likers)
czytają stamtąd bez ponownego wołania AI (cache per iteracja rolki).
Jeśli w scratch już jest reel_ai — SKIP (bezpłatnie).
"""
from __future__ import annotations
from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.ai.vision import AiVision
from v2.steps.reels._guards import skip_if_ad
from v2.steps.reels.read_caption_comments import CAPTION_IDS


@register
class AnalyzeFramesAi:
    name = "reels.analyze_frames_ai"
    requires_screen = None  # bez wpływu na ekran, akcja obliczeniowa

    def run(self, ctx, params):
        skip = skip_if_ad(ctx)
        if skip: return skip
        if "reel_ai" in ctx.scratch and not params.get("force", False):
            return StepResult.skipped("already_analyzed", **ctx.scratch["reel_ai"])

        # Discovery zostawiło accepted=false — nie wołaj Vision aż operator zatwierdzi
        from v2.plugins.store import niche_is_accepted
        if not niche_is_accepted(ctx.account):
            ctx.log("niche_not_accepted", where="reels.analyze_frames_ai")
            return StepResult.skipped("niche_not_accepted")

        ai_opts = params.get("ai") or {}
        # Domyślnie jak wcześniej: klatki + opis włączone
        want_frames = bool(ai_opts.get("use_frames", True))
        want_caption = bool(ai_opts.get("read_caption", True))

        reel      = ctx.scratch.get("reel") or {}
        composite = ctx.scratch.get("reel_composite") if want_frames else None

        # Opis: tylko gdy włączony; bez dump-fallback przy celowym OFF
        if want_caption:
            caption, caption_source = _ensure_caption(ctx)
        else:
            caption, caption_source = None, "disabled"

        # Brak obu wejść → nie wołaj API
        if not composite and not caption:
            from v2.plugins.reel_log import say
            say("AI: brak wejsc (klatki i opis wylaczone lub puste) — pomijam")
            ctx.log("reels.analyze_frames_ai.no_ai_inputs",
                    author=reel.get("author"),
                    use_frames=want_frames, read_caption=want_caption,
                    has_composite=bool(composite), has_caption=bool(caption))
            return StepResult.skipped("no_ai_inputs")

        comments = _format_comments(ctx.scratch.get("reel_top_comments") or [])

        # Pełny brief niszy + wykluczenia ROLEK (plugin.ai.exclusions | niche)
        from v2.ai.niche_inputs import effective_exclusions, niche_prompt_fields
        niche = ctx.account.niche or {}
        niche_fields = niche_prompt_fields(
            niche, exclusions=effective_exclusions(niche, ai_opts))

        # Composite → lista ścieżek; bez obrazu = pusta lista (text-only)
        frame_list = [composite] if composite else []
        podpis = caption if caption else "(brak — nie udało się odczytać)"

        vision = AiVision(account=ctx.account.raw)
        result = vision.run("reel_niche", inputs={
            **niche_fields,
            "autor":        reel.get("author") or "",
            "podpis":       podpis,
            "komentarze":   comments or "(brak)",
            "frames":       frame_list,
        # `ai` to sekcja ustawień pluginu — niesie `strictness`, `exclusions`
        # i ewentualny `prompt_override`. Bez przekazania tych pól byłyby martwe.
        }, opts=ai_opts)
        ctx.scratch["reel_ai"] = result
        ctx.scratch["reel_caption_source"] = caption_source
        ctx.log("reels.analyze_frames_ai", author=reel.get("author"),
                caption_source=caption_source, caption_chars=len(caption or ""),
                frames_sent=len(frame_list),
                top_comments=len(ctx.scratch.get("reel_top_comments") or []),
                **_summary(result))
        return StepResult.ok(caption_source=caption_source,
                             frames_sent=len(frame_list), **_summary(result))


def _ensure_caption(ctx, flat: list[dict] | None = None) -> tuple[str | None, str]:
    """Opis rolki + skąd go mamy: `step` / `dump` / `missing`.

    Dobranie z drzewa jest tanie (jeden dump, bez gestów) i ratuje przypadek,
    w którym opis nie był czytany osobnym krokiem albo panel się nie rozwinął.
    Zwinięty panel pokazuje pierwszą linię opisu — mniej niż pełny tekst, ale
    to wciąż konkret dla modelu zamiast pustego pola.
    """
    caption = ctx.scratch.get("reel_caption")
    if caption:
        return caption, "step"
    try:
        if flat is None:
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    except Exception as exc:
        ctx.log("reels.analyze_frames_ai.caption_dump_failed", error=str(exc))
        return None, "missing"
    for n in flat:
        if n.get("resource-id") in CAPTION_IDS:
            t = (n.get("text") or "").strip() or (n.get("content-desc") or "").strip()
            if t:
                ctx.scratch["reel_caption"] = t
                return t, "dump"
    ctx.log("reels.analyze_frames_ai.no_caption", author=(ctx.scratch.get("reel") or {}).get("author"))
    return None, "missing"


def _format_comments(top: list[dict], limit: int = 5) -> str:
    """Top komentarze jako `@nick: treść` w osobnych liniach."""
    lines = []
    for c in top[:limit]:
        user = (c.get("user") or "").strip()
        text = (c.get("text") or "").strip()
        if text:
            lines.append(f"@{user}: {text}" if user else text)
    return "\n".join(lines)


def _summary(r: dict) -> dict:
    return {
        "niche_score": r.get("niche_score"),
        "verdict":     r.get("verdict"),
        "reasons":     (r.get("reasons") or [])[:2],
        "stub":        r.get("_stub", False),
    }
