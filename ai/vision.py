"""AI Vision — jeden plugin, wiele trybów
(reel_niche / profile_grid / comment_gen / recovery_identify /
 niche_discover_posts / reels / account / merge).

Ma dwa tryby działania:
  - REAL: woła Anthropic API (klucz + opcjonalnie workspace ID przy identity-linked)
  - STUB: zwraca deterministyczną odpowiedź (do testów bez API i płacenia)

Przełącznik: `account.ai.stub = true` w configu wymusza STUB.
Bez klucza w env też fallback do STUB (z warningiem w log).
"""
from __future__ import annotations
import base64
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from v2.config.secrets import get_api_key, get_workspace_id
from v2.ai.pricing import apply_costs, cost_usd

PROMPTS_DIR = Path(__file__).parent / "prompts"

# TYMCZASOWO: pełny prompt + surowa odpowiedź w terminalu przy profile_grid.
# Wyłączyć / usunąć, gdy nie będzie już potrzebne do strojenia promptu.
_DUMP_PROFILE_GRID_IO = True


@dataclass
class AiVision:
    account: dict = field(default_factory=dict)   # dostęp do account.ai.*
    ctx: Any = None                                # opcjonalnie: session_stats + events
    _client: Any = None                            # anthropic.Anthropic lazy
    _stub: bool = False

    def __post_init__(self):
        ai_cfg = (self.account or {}).get("ai") or {}
        forced_stub = bool(ai_cfg.get("stub"))
        if forced_stub or not _api_key():
            self._stub = True

    # ---- entry point ----
    def run(self, mode: str, inputs: dict, opts: dict | None = None) -> dict:
        """`opts` to sekcja `ai`/`grid_ai` z ustawień pluginu: pozwala nadpisać
        prompt użytkownika i wstrzyknąć instrukcję rygoru (`strictness`)."""
        prompt = _apply_opts(_load_prompt(mode), opts or {})
        strict = (opts or {}).get("strictness")
        inputs = {
            **inputs,
            "strictness_hint": _strictness_hint(strict, mode=mode),
            # discovery finalize/merge — osobny ton (luźny → restrykcyjny)
            "precision_hint": inputs.get("precision_hint")
            or _precision_hint(strict or inputs.get("precision")),
        }
        sys_prompt = _format(prompt["system"], inputs)
        user_prompt = _format(prompt["user"], inputs)
        frame_paths = list(inputs.get("frames") or [])
        # TYMCZASOWO: operator widzi, co poszło do modelu przy profile_interact
        if mode == "profile_grid" and _DUMP_PROFILE_GRID_IO:
            _dump_profile_grid_prompt(sys_prompt, user_prompt, len(frame_paths))

        if self._stub:
            result = _stub_response(mode, inputs, prompt)
            # stub: 0 tokenów, model="stub" — panel i tak pokaże sumę
            result = _attach_usage(result, _usage_stub(mode))
            _print_ai_response(mode, result)
            return result
        result = self._call_anthropic(
            mode, inputs, prompt, sys_prompt, user_prompt, frame_paths)
        # Tokeny + koszt → session_stats / events (nie stub)
        _record_session_ai_usage(self.ctx, result)
        return result

    # ---- real call ----
    def _call_anthropic(self, mode: str, inputs: dict, prompt: dict,
                        sys_prompt: str, user_prompt: str,
                        frame_paths: list) -> dict:
        try:
            from anthropic import Anthropic
        except ImportError:
            # brak SDK — degrade do stub (odpowiedź wypisze _print_ai_response)
            result = _stub_response(mode, inputs, prompt,
                                    note="anthropic SDK not installed")
            result = _attach_usage(result, _usage_stub(mode, note="no_sdk"))
            _print_ai_response(mode, result)
            return result

        if self._client is None:
            # klucz z env albo z config/secrets.json (env wygrywa);
            # workspace ID przy kluczu identity-linked
            self._client = Anthropic(**_anthropic_client_kwargs())

        # pobierz model per tryb z accountu (fallback: model_default z YAML promptu)
        model_map = ((self.account or {}).get("ai") or {}).get("model_per_mode") or {}
        model_short = model_map.get(mode) or "haiku"
        model_id = _resolve_model(model_short, prompt.get("model_default"))

        content_parts: list[dict] = [{"type": "text", "text": user_prompt}]
        for frame_path in frame_paths:
            data = Path(frame_path).read_bytes()
            content_parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _mime_from_path(frame_path),
                    "data": base64.b64encode(data).decode(),
                }
            })

        try:
            resp = self._client.messages.create(
                model=model_id,
                max_tokens=int(prompt.get("max_tokens", 400)),
                system=sys_prompt,
                messages=[{"role": "user", "content": content_parts}],
            )
        except Exception as e:
            # Identity-linked key bez workspace — nie wywalaj całej kolejki
            if _is_workspace_id_error(e):
                print(
                    "[vision] Brak anthropic-workspace-id (klucz identity-linked). "
                    "Ustaw ANTHROPIC_WORKSPACE_ID albo w panelu Ustawienia → "
                    "workspace (wrkspc_… z console.anthropic.com).",
                    flush=True,
                )
                result = {
                    "_error": "anthropic_workspace_id",
                    "_note": str(e),
                    "tiles": {},
                }
                result = _attach_usage(result, _usage_stub(mode, note="workspace_id"))
                _print_ai_response(mode, result)
                return result
            raise
        # zwykle odpowiedź to jeden blok text
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        usage = _extract_usage(resp, model_id=model_id, mode=mode)
        result = _attach_usage(_parse_json_response(text, mode), usage)
        # TYMCZASOWO: surowy tekst modelu, zanim poleci czytelne podsumowanie
        if mode == "profile_grid" and _DUMP_PROFILE_GRID_IO:
            _dump_profile_grid_raw(text)
        _print_ai_response(mode, result)
        return result


# ============================================================
# helpers — usage / tokeny
# ============================================================
def empty_usage_totals() -> dict:
    """Pusty akumulator na sesję / discovery (tokeny + koszt per model)."""
    return {
        "model": "",
        "models": [],
        "tokens_in": 0,
        "tokens_out": 0,
        "calls": 0,
        "usage_missing": False,
        "by_mode": {},
        "by_model": {},
        "cost_usd": 0.0,
        "cost_unknown": False,
    }


def accumulate_usage(totals: dict, result_or_usage: dict | None,
                     *, mode: str | None = None) -> dict:
    """Dodaj usage z jednego calla do `totals` (in-place). Zwraca totals.

    Akceptuje wynik `AiVision.run` (z `_usage`) albo sam dict usage.
    Brak usage → tokens 0 + `usage_missing=True` (bez crasha).
    """
    if totals is None:
        totals = empty_usage_totals()
    usage = None
    if isinstance(result_or_usage, dict):
        usage = result_or_usage.get("_usage")
        if usage is None and ("tokens_in" in result_or_usage
                              or "input_tokens" in result_or_usage):
            usage = result_or_usage
    if not isinstance(usage, dict):
        totals["usage_missing"] = True
        usage = {
            "model": "unknown",
            "mode": mode or "unknown",
            "tokens_in": 0,
            "tokens_out": 0,
            "missing": True,
        }
    tin = _as_nonneg_int(usage.get("tokens_in", usage.get("input_tokens")))
    tout = _as_nonneg_int(usage.get("tokens_out", usage.get("output_tokens")))
    model = str(usage.get("model") or "unknown")
    m = str(usage.get("mode") or mode or "unknown")
    totals["tokens_in"] = int(totals.get("tokens_in") or 0) + tin
    totals["tokens_out"] = int(totals.get("tokens_out") or 0) + tout
    totals["calls"] = int(totals.get("calls") or 0) + 1
    if usage.get("missing"):
        totals["usage_missing"] = True
    models = list(totals.get("models") or [])
    if model and model not in models:
        models.append(model)
    totals["models"] = models
    # „główny” model = najczęstszy w by_mode; na razie ostatni / pierwszy unikalny
    totals["model"] = models[0] if len(models) == 1 else (
        "+".join(models) if models else "")
    by = dict(totals.get("by_mode") or {})
    slot = dict(by.get(m) or {
        "model": model, "tokens_in": 0, "tokens_out": 0, "calls": 0,
    })
    slot["model"] = model
    slot["tokens_in"] = int(slot.get("tokens_in") or 0) + tin
    slot["tokens_out"] = int(slot.get("tokens_out") or 0) + tout
    slot["calls"] = int(slot.get("calls") or 0) + 1
    by[m] = slot
    totals["by_mode"] = by
    # per model (do statystyk / kosztu)
    by_m = dict(totals.get("by_model") or {})
    mslot = dict(by_m.get(model) or {
        "tokens_in": 0, "tokens_out": 0, "calls": 0, "cost_usd": 0.0,
    })
    mslot["tokens_in"] = int(mslot.get("tokens_in") or 0) + tin
    mslot["tokens_out"] = int(mslot.get("tokens_out") or 0) + tout
    mslot["calls"] = int(mslot.get("calls") or 0) + 1
    by_m[model] = mslot
    totals["by_model"] = by_m
    apply_costs(totals)
    return totals


def merge_usage_totals(dst: dict, src: dict | None) -> dict:
    """Zsumuj dwa akumulatory usage (agregat sesji). In-place na dst."""
    if not isinstance(dst, dict):
        dst = empty_usage_totals()
    if not isinstance(src, dict):
        return dst
    dst.setdefault("by_model", {})
    dst.setdefault("by_mode", {})
    dst.setdefault("models", [])
    dst["tokens_in"] = int(dst.get("tokens_in") or 0) + int(src.get("tokens_in") or 0)
    dst["tokens_out"] = int(dst.get("tokens_out") or 0) + int(src.get("tokens_out") or 0)
    dst["calls"] = int(dst.get("calls") or 0) + int(src.get("calls") or 0)
    if src.get("usage_missing"):
        dst["usage_missing"] = True
    models = list(dst.get("models") or [])
    for m in src.get("models") or []:
        if m and m not in models:
            models.append(m)
    dst["models"] = models
    dst["model"] = models[0] if len(models) == 1 else (
        "+".join(models) if models else "")
    for key in ("by_mode", "by_model"):
        bucket = dst.setdefault(key, {})
        raw = src.get(key) or {}
        if not isinstance(raw, dict):
            continue
        for name, slot in raw.items():
            if not name or not isinstance(slot, dict):
                continue
            cur = dict(bucket.get(name) or {
                "tokens_in": 0, "tokens_out": 0, "calls": 0,
            })
            cur["tokens_in"] = int(cur.get("tokens_in") or 0) + int(slot.get("tokens_in") or 0)
            cur["tokens_out"] = int(cur.get("tokens_out") or 0) + int(slot.get("tokens_out") or 0)
            cur["calls"] = int(cur.get("calls") or 0) + int(slot.get("calls") or 0)
            if slot.get("model"):
                cur["model"] = slot.get("model")
            bucket[name] = cur
        dst[key] = bucket
    apply_costs(dst)
    return dst


def _record_session_ai_usage(ctx, result: dict | None) -> None:
    """Dopisz call do session_stats + event `ai.usage` (pomija stub / 0 tokenów)."""
    if ctx is None or not isinstance(result, dict):
        return
    usage = result.get("_usage")
    if not isinstance(usage, dict):
        return
    model = str(usage.get("model") or "")
    if model.startswith("stub"):
        return
    tin = int(usage.get("tokens_in") or 0)
    tout = int(usage.get("tokens_out") or 0)
    if tin <= 0 and tout <= 0:
        return
    ss = getattr(ctx, "session_stats", None)
    if ss is not None and hasattr(ss, "add_ai_usage"):
        try:
            ss.add_ai_usage(result, mode=usage.get("mode"))
        except Exception:
            pass
    log = getattr(ctx, "log", None)
    if callable(log):
        usd, priced = cost_usd(model, tin, tout)
        try:
            log("ai.usage", model=model, mode=usage.get("mode"),
                tokens_in=tin, tokens_out=tout, cost_usd=usd, priced=priced)
        except Exception:
            pass


def _as_nonneg_int(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def _extract_usage(resp: Any, *, model_id: str, mode: str) -> dict:
    """Wyciągnij tokeny z odpowiedzi Anthropic (lub kompatybilnego obiektu).

    Anthropic: `usage.input_tokens` / `usage.output_tokens`.
    Alternatywy (OpenAI-like): `prompt_tokens` / `completion_tokens`.
    Brak usage → 0 + missing (warning na stdout).
    """
    usage_obj = getattr(resp, "usage", None)
    tin = tout = None
    if usage_obj is not None:
        tin = getattr(usage_obj, "input_tokens", None)
        if tin is None and isinstance(usage_obj, dict):
            tin = usage_obj.get("input_tokens", usage_obj.get("prompt_tokens"))
        else:
            tin = tin if tin is not None else getattr(
                usage_obj, "prompt_tokens", None)
        tout = getattr(usage_obj, "output_tokens", None)
        if tout is None and isinstance(usage_obj, dict):
            tout = usage_obj.get("output_tokens",
                                 usage_obj.get("completion_tokens"))
        else:
            tout = tout if tout is not None else getattr(
                usage_obj, "completion_tokens", None)
    # czasem usage leży bezpośrednio na resp
    if tin is None:
        tin = getattr(resp, "input_tokens", None)
    if tout is None:
        tout = getattr(resp, "output_tokens", None)

    missing = tin is None and tout is None
    if missing:
        print(f"[vision] WARNING: brak usage w odpowiedzi API ({mode}) "
              f"— tokens_in/out=0", flush=True)
    return {
        "model": model_id,
        "mode": mode,
        "tokens_in": _as_nonneg_int(tin),
        "tokens_out": _as_nonneg_int(tout),
        "missing": missing,
    }


def _usage_stub(mode: str, note: str = "stub") -> dict:
    """Usage dla trybu stub / degradacji — 0 tokenów, czytelny model."""
    return {
        "model": f"stub:{note}",
        "mode": mode,
        "tokens_in": 0,
        "tokens_out": 0,
        "missing": False,
        "stub": True,
    }


def _attach_usage(result: dict, usage: dict) -> dict:
    """Doklej `_usage` do wyniku (nie mutuj jeśli to nie dict)."""
    if not isinstance(result, dict):
        return {"_raw": result, "_usage": usage}
    out = dict(result)
    out["_usage"] = usage
    return out


# ============================================================
# helpers — log / prompt
# ============================================================
def _dump_profile_grid_prompt(sys_prompt: str, user_prompt: str,
                              n_frames: int) -> None:
    """TYMCZASOWO: system + user prompt siatki w terminalu (prefix pluginu)."""
    from v2.plugins.profile_log import say
    say("===== AI PROMPT (profile_grid) — tymczasowo =====")
    say(f"— klatek: {n_frames}")
    say("— system —")
    _say_multiline(say, sys_prompt)
    say("— user —")
    _say_multiline(say, user_prompt)
    say("===== KONIEC PROMPT =====")


def _dump_profile_grid_raw(text: str) -> None:
    """TYMCZASOWO: surowa odpowiedź modelu (przed parse JSON)."""
    from v2.plugins.profile_log import say
    say("===== AI RAW (profile_grid) — tymczasowo =====")
    _say_multiline(say, text or "(pusta)")
    say("===== KONIEC RAW =====")


def _say_multiline(say, body: str) -> None:
    """Każda linia osobno — panel koloruje prefix profile_interact."""
    lines = (body or "").splitlines()
    if not lines:
        say("(puste)")
        return
    for line in lines:
        say(line)


def _print_ai_response(mode: str, result: dict) -> None:
    """Odpowiedź AI na stdout — dla reel_niche / profile_grid czytelnie, bez JSON."""
    usage = result.get("_usage") if isinstance(result, dict) else None
    payload = {k: v for k, v in (result or {}).items() if k != "_usage"} \
        if isinstance(result, dict) else result

    # Engagement rolek — bez JSON, z prefixem reels_niche
    if mode == "reel_niche" and isinstance(payload, dict):
        from v2.plugins.reel_log import say, say_ai_result
        say("")
        say_ai_result(payload)
        if usage:
            say(f"(model={usage.get('model')}, "
                f"in={usage.get('tokens_in', 0)}, "
                f"out={usage.get('tokens_out', 0)})")
        say("")
        return

    # Siatka profilu — liczby + Kafelek N — niche: <reason> (prefix profile_interact)
    # Zawsze tutaj (nie tylko z visit) — każdy caller vision.run("profile_grid")
    # widzi powody; visit woła to samo idempotentnie (`_grid_ai_said`).
    if mode == "profile_grid" and isinstance(payload, dict):
        from v2.plugins.profile_log import say, say_grid_ai_result
        say("")
        say_grid_ai_result(payload)
        # Przenieś flagę na pełny result (payload to kopia bez _usage)
        if isinstance(result, dict) and payload.get("_grid_ai_said"):
            result["_grid_ai_said"] = True
        # TYMCZASOWO: sparsowany JSON (stub i real po parse)
        if _DUMP_PROFILE_GRID_IO:
            dump = {k: v for k, v in payload.items()
                    if k not in ("_grid_ai_said",)}
            try:
                body = json.dumps(dump, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                body = str(dump)
            say("===== AI JSON (profile_grid) — tymczasowo =====")
            _say_multiline(say, body)
            say("===== KONIEC JSON =====")
        if usage:
            say(f"(model={usage.get('model')}, "
                f"in={usage.get('tokens_in', 0)}, "
                f"out={usage.get('tokens_out', 0)})")
        say("")
        return

    # Pozostale tryby (discovery) — JSON jak wczesniej
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(result)
    _print_ai_exchange(mode, response=text, usage=usage, result=payload)


def _print_ai_exchange(mode, *, response, usage=None, result=None):
    """Wypisz odpowiedź AI (prompt celowo pomijamy)."""
    def _out(s: str) -> None:
        try:
            print(s, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            sys.stdout.buffer.write((s + "\n").encode(enc, errors="replace"))
            sys.stdout.flush()

    _out(f"\n===== AI RESPONSE ({mode}) =====")
    if isinstance(result, dict):
        verdict = result.get("verdict")
        score = result.get("niche_score")
        if verdict is not None or score is not None:
            _out(f"score: {score}   decyzja: {verdict}")
    _out(response or "(pusta)")
    if usage:
        _out(
            f"--- usage: model={usage.get('model')}  "
            f"tokens_in={usage.get('tokens_in', 0)}  "
            f"tokens_out={usage.get('tokens_out', 0)} ---"
        )
    _out("===== END AI RESPONSE =====\n")


def _api_key() -> str | None:
    """Klucz do Anthropic API — env ma pierwszeństwo nad config/secrets.json."""
    return get_api_key()


def _workspace_id() -> str | None:
    """ID workspace (wrkspc_…) — wymagane przy identity-linked API key."""
    return get_workspace_id()


def _anthropic_client_kwargs() -> dict:
    """Argumenty klienta SDK: klucz + opcjonalny nagłówek workspace."""
    kwargs: dict = {"api_key": _api_key()}
    ws = _workspace_id()
    if ws:
        kwargs["default_headers"] = {"anthropic-workspace-id": ws}
    return kwargs


def _is_workspace_id_error(exc: BaseException) -> bool:
    """400: identity-linked key wymaga nagłówka anthropic-workspace-id."""
    msg = str(exc).lower()
    return ("anthropic-workspace-id" in msg
            or "identity-linked api key" in msg)


# Instrukcja rygoru wstrzykiwana do promptu jako {strictness_hint}.
# Trzymana w kodzie, nie w configu — to element kontraktu z modelem,
# a nie pokrętło do kręcenia z panelu.
_STRICTNESS_HINTS = {
    "loose": "Bądź liberalny: dopuszczaj treści tematycznie pokrewne, nawet gdy "
             "związek z niszą jest pośredni. Odrzucaj tylko jednoznaczne wykluczenia.",
    "balanced": "Oceniaj wyważenie: dopasowanie ma być wyraźne, ale nie wymagaj "
                "dosłownego pokrycia z opisem niszy.",
    "strict": "Bądź rygorystyczny: uznawaj dopasowanie tylko gdy związek z niszą "
              "jest jednoznaczny i widoczny. Przy wątpliwości oceniaj negatywnie.",
}

# Siatka: rygor = audience_fit. Kafelki = bezpieczny like, NIE „czy o niszy”.
_STRICTNESS_HINTS_GRID = {
    "loose": "Audience: bywaj hojny (partial raczej niż off). Kafelki: lajkuj "
             "prawie wszystko poza listą wykluczeń — lifestyle = safe + should_like.",
    "balanced": "Audience: sygnał grupy ma być wiarygodny, ale NIE wymagaj postów "
                "o niszy. Kafelki: zwykłe zdjęcia tej osoby = safe + should_like; "
                "skipped tylko gdy nieczytelne. Nie skipuj za „brak tematu niszy”.",
    "strict": "Audience: off gdy to wyraźnie inna grupa. Kafelki NADAL niezależne: "
              "portret/hobby/lifestyle = safe + should_like, chyba że wykluczenie "
              "albo ewidentna reklama/spam.",
}

# Ton discovery (finalize / merge) — {precision_hint}
_PRECISION_HINTS = {
    "loose": (
        "## Precyzja: LUŹNA\n"
        "Szeroki zakres: unia motywów, dużo keywords/hashtags. "
        "Description = nisza jako kategoria (tematy/formaty), NIE opis autora/profilu. "
        "NIE generuj exclusions."
    ),
    "balanced": (
        "## Precyzja: ZRÓWNOWAŻONA\n"
        "Bierz częste / powtarzalne motywy. Keywords i hashtags po deduplikacji "
        "i rozsądnym limicie. Description o niszy OGÓŁEM (bez „Autor…/konto…”). "
        "NIE generuj exclusions."
    ),
    "strict": (
        "## Precyzja: RESTRYKCYJNA\n"
        "Tylko część wspólna / silnie powtórzone sygnały (>=2 źródła przy merge). "
        "Krótkie description (1–2 zdania) o kategorii niszy, wąskie audience. "
        "Outliery → reasons — nie rozciągaj niszy. NIE generuj exclusions."
    ),
}


def _strictness_hint(level: str | None, mode: str | None = None) -> str:
    """Rygor do promptu — siatka ma osobną tabelę (lajk ≠ treść niszy)."""
    table = _STRICTNESS_HINTS_GRID if mode == "profile_grid" else _STRICTNESS_HINTS
    return table.get(level or "balanced", table["balanced"])


def _precision_hint(level: str | None) -> str:
    return _PRECISION_HINTS.get(level or "balanced", _PRECISION_HINTS["balanced"])


def _apply_opts(prompt: dict, opts: dict) -> dict:
    """Nadpisz treść promptu użytkownika, jeśli ustawiono `prompt_override`.

    Sekcja `system` (format odpowiedzi JSON) NIE jest nadpisywalna — inaczej
    własny prompt mógłby zepsuć parsowanie i wywalić cały krok.
    """
    override = (opts.get("prompt_override") or "").strip()
    if not override:
        return prompt
    return {**prompt, "user": override}


def _load_prompt(mode: str) -> dict:
    p = PROMPTS_DIR / f"{mode}.yml"
    if not p.exists():
        raise AiError(f"brak prompt template: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _format(template: str, inputs: dict) -> str:
    """Podstawia {klucz} z inputs. Braki zostawia jako pustki."""
    def repl(m):
        k = m.group(1)
        v = inputs.get(k, "")
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        return str(v)
    return re.sub(r"\{(\w+)\}", repl, template)


def _mime_from_path(p) -> str:
    p = str(p).lower()
    if p.endswith(".png"):  return "image/png"
    if p.endswith(".jpg") or p.endswith(".jpeg"): return "image/jpeg"
    return "image/png"


def _resolve_model(short: str, fallback: str | None) -> str:
    """'haiku' → 'claude-haiku-4-5-...' ; 'sonnet' → 'claude-sonnet-5' ; 'opus' → 'claude-opus-5'."""
    m = {
        "haiku":  "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-5",
        "opus":   "claude-opus-5",
        "fable":  "claude-fable-5",
    }
    return m.get(short.lower(), short) or fallback or "claude-haiku-4-5-20251001"


def _parse_json_response(text: str, mode: str) -> dict:
    """Znajdź pierwszy blok {...} w tekście, sparsuj.

    Przy obcięciu przez max_tokens (brak domykającego `}`) próbuje
    naprawić fragment — description/keywords zwykle są przed reasons.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        # zdejmij ogrodzenie markdown, jeśli model je dodał
        raw = re.sub(r"^```(?:json)?\s*", "", raw, count=1, flags=re.I)
        raw = re.sub(r"\s*```\s*$", "", raw)

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass  # spadnij do naprawy obciętego JSON

    # brak pełnego {...} — typowy efekt max_tokens (stdout: tokens_out == limit)
    start = raw.find("{")
    if start < 0:
        return {"error": "no_json_in_response", "raw": raw[:500]}
    fragment = raw[start:]
    repaired = _repair_truncated_json(fragment)
    if repaired is not None:
        repaired["_json_repaired"] = True
        return repaired
    return {"error": "json_decode: truncated_or_invalid", "raw": raw[:500]}


def _repair_truncated_json(fragment: str) -> dict | None:
    """Domknij obcięty JSON (otwarty string / tablica / obiekt).

    Zwraca dict albo None gdy naprawa się nie uda.
    """
    s = (fragment or "").rstrip()
    if not s.startswith("{"):
        return None

    def _close(raw: str) -> str:
        stack: list[str] = []
        in_str = False
        escape = False
        for ch in raw:
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                stack.append("}")
            elif ch == "[":
                stack.append("]")
            elif ch in "}]" and stack and stack[-1] == ch:
                stack.pop()
        out = raw
        if in_str:
            out += '"'
        # trailing comma przed domknięciem — json.loads tego nie zje
        out = re.sub(r",\s*$", "", out.rstrip())
        while stack:
            out += stack.pop()
        return out

    for candidate in (_close(s), _close(re.sub(r",\s*$", "", s.rstrip()))):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _stub_partial_from_captions(inputs: dict, note: str) -> dict:
    """Heurystyka partial (posts/reels) z ponumerowanych captionów / seed."""
    text = (inputs.get("captions_numbered") or "") + "\n" + (inputs.get("seed") or "")
    words = [w.strip("#.,").lower() for w in text.split() if len(w.strip("#.,")) > 3]
    uniq = []
    for w in words:
        if w not in uniq:
            uniq.append(w)
        if len(uniq) >= 8:
            break
    keywords = uniq[:6] or ["stub"]
    hashtags = [f"#{k}" for k in keywords[:4]]
    return {
        "themes": keywords[:3] or ["stub_theme"],
        "audience_signals": ["osoby zainteresowane: " + (", ".join(keywords[:2]) or "tematem")],
        "keywords": keywords,
        "hashtags": hashtags,
        "exclusions": [],
        "notes": ["stub_heurystyka z captionów/seed"],
        "_stub": True,
        "_note": note,
    }


def _stub_niche_final(inputs: dict, note: str, *, with_source: bool) -> dict:
    """Finalny kontrakt niszy ze stub partiali / seed."""
    seed = (inputs.get("seed") or "").strip()
    bio = (inputs.get("bio") or "").strip()
    base = seed or bio or "nisza odkryta heurystycznie (stub)"
    keywords: list[str] = []
    for key in ("posts_partial_json", "reels_partial_json", "account_results_json"):
        raw = inputs.get(key) or ""
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict):
                for k in it.get("keywords") or []:
                    if k and k not in keywords:
                        keywords.append(str(k))
    if not keywords:
        keywords = [w for w in base.lower().split() if len(w) > 3][:5] or ["stub"]
    out = {
        "audience": "grupa zainteresowana: " + ", ".join(keywords[:3]),
        "description": base[:280] if len(base) > 10 else
                       f"Nisza wokół tematów: {', '.join(keywords[:4])}.",
        "keywords": keywords[:12],
        "hashtags": [f"#{k.lstrip('#')}" for k in keywords[:8]],
        "exclusions": [],
        "reasons": ["stub_mode — wynik heurystyczny"],
        "_stub": True,
        "_note": note,
    }
    if with_source:
        out["source_account"] = inputs.get("source_account") or "own"
    return out


def _stub_response(mode: str, inputs: dict, prompt: dict, note: str = "stub_mode") -> dict:
    """Deterministyczna odpowiedź do testów — bazuje na tym co jest w inputs."""
    if mode == "reel_niche":
        opis      = (inputs.get("opis") or "").lower()
        autor     = (inputs.get("autor") or "").lower()
        exclus    = [str(x).lower() for x in (inputs.get("wykluczenia") or [])]
        if any(x in autor for x in exclus if x):
            return {"niche_score": 5,  "verdict": "off",     "reasons": ["autor pasuje do wykluczeń"], "_stub": True, "_note": note}
        if opis and len(opis) > 3:
            score = min(90, 40 + len(opis) % 60)
            return {"niche_score": score, "verdict": "match", "reasons": ["heurystyczny stub — opis niszy niepusty"], "_stub": True, "_note": note}
        return {"niche_score": 30, "verdict": "partial", "reasons": ["brak wyraźnego sygnału"], "_stub": True, "_note": note}
    if mode == "profile_grid":
        u = (inputs.get("username") or "user").lower()
        exclus = [str(x).lower() for x in (inputs.get("wykluczenia") or [])]
        opis   = (inputs.get("opis") or "").lower()
        h = sum(ord(c) for c in u) % 100
        tiles: dict = {}
        # audience-fit: kafelki safe/skipped/excluded (nie „czy treść niszy”)
        cats = ["safe", "skipped", "safe", "skipped", "safe", "skipped"] if opis else \
               ["skipped"] * 6
        if any(x in u for x in exclus if x):
            cats = ["excluded", "excluded", "skipped", "skipped", "safe", "skipped"]
        elif h % 7 == 0:
            cats[2] = "excluded"
        for i, c in enumerate(cats, 1):
            tiles[str(i)] = {
                "category": c,
                "should_like": c == "safe",
                "reason": f"stub_hash={h}",
            }
        audience = "match" if opis else "partial"
        return {
            "audience_fit": audience,
            "audience_reason": "stub — bio/opis niszy" if opis else "stub — brak opisu",
            "tiles": tiles, "_stub": True, "_note": note,
        }
    if mode == "comment_gen":
        return {"comment": "", "confidence": 0.0, "_stub": True, "_note": note}
    if mode == "recovery_identify":
        return {"screen_type": "unknown", "recommended_action": "back", "_stub": True, "_note": note}
    if mode in ("niche_discover_posts", "niche_discover_reels",
                "niche_discover_batch"):  # alias starej nazwy
        return _stub_partial_from_captions(inputs, note)
    if mode in ("niche_discover_account", "niche_discover_finalize"):
        return _stub_niche_final(inputs, note, with_source=True)
    if mode == "niche_discover_merge":
        return _stub_niche_final(inputs, note, with_source=False)
    return {"_stub": True, "_note": note, "_mode": mode}
