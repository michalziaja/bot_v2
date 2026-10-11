"""Cennik Anthropic (USD / 1M tokenów) — szacunek kosztu w statystykach.

Stawki z oficjalnego arkusza (platform.claude.com, 2026-08).
Dopasowanie po prefiksie ID modelu (dłuższy prefiks wygrywa).
Stub / nieznany model → koszt 0 i flaga `priced=False`.
"""
from __future__ import annotations

# (prefix ID, input USD/MTok, output USD/MTok) — kolejność: dłuższe / nowsze pierwsze
_RATES: tuple[tuple[str, float, float], ...] = (
    ("claude-haiku-4-5", 1.0, 5.0),
    ("claude-haiku-3-5", 0.80, 4.0),
    ("claude-haiku-3.5", 0.80, 4.0),
    ("claude-sonnet-5", 2.0, 10.0),
    ("claude-sonnet-4", 3.0, 15.0),
    ("claude-opus-4.1", 15.0, 75.0),
    ("claude-opus-4-1", 15.0, 75.0),
    ("claude-opus-5", 5.0, 25.0),
    ("claude-opus-4.8", 5.0, 25.0),
    ("claude-opus-4.7", 5.0, 25.0),
    ("claude-opus-4.6", 5.0, 25.0),
    ("claude-opus-4.5", 5.0, 25.0),
    ("claude-opus-4", 15.0, 75.0),
    ("claude-fable-5", 10.0, 50.0),
)


def rates_for(model: str | None) -> tuple[float, float] | None:
    """(input_usd_per_mtok, output_usd_per_mtok) albo None gdy nie znamy modelu."""
    m = str(model or "").strip().lower()
    if not m or m.startswith("stub"):
        return (0.0, 0.0)
    for prefix, inn, out in _RATES:
        if m.startswith(prefix) or prefix in m:
            return (inn, out)
    # krótkie aliasy z panelu
    if "haiku" in m:
        return (1.0, 5.0)
    if "sonnet" in m:
        return (3.0, 15.0)
    if "opus" in m:
        return (5.0, 25.0)
    if "fable" in m:
        return (10.0, 50.0)
    return None


def cost_usd(model: str | None, tokens_in: int, tokens_out: int) -> tuple[float, bool]:
    """Koszt USD za dany call. Drugi element: True gdy stawka jest znana."""
    rates = rates_for(model)
    if rates is None:
        return 0.0, False
    inn, out = rates
    usd = (max(0, int(tokens_in)) / 1_000_000.0) * inn + (
        max(0, int(tokens_out)) / 1_000_000.0) * out
    return round(usd, 6), True


def apply_costs(totals: dict) -> dict:
    """Uzupełnij cost_usd na totals i na każdym slocie by_model (in-place)."""
    by_model = totals.get("by_model") if isinstance(totals.get("by_model"), dict) else {}
    total = 0.0
    unknown = False
    for model, slot in by_model.items():
        if not isinstance(slot, dict):
            continue
        tin = int(slot.get("tokens_in") or 0)
        tout = int(slot.get("tokens_out") or 0)
        usd, ok = cost_usd(model, tin, tout)
        slot["cost_usd"] = usd
        if not ok:
            unknown = True
        total += usd
    totals["cost_usd"] = round(total, 6)
    totals["cost_unknown"] = unknown
    return totals
