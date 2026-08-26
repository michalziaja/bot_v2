"""Losowość akcji — jedno miejsce, dwa formaty zapisu.

Wszędzie, gdzie bot decyduje „lajkować / obserwować / nie", przechodzi przez
`roll()`. Bez tego każda akcja spełniająca warunki byłaby wykonywana zawsze —
człowiek tak nie działa.

Obsługujemy dwa zapisy, bo katalog pluginów i starsze flow YAML używają innych:

    {"mode": "p",       "value": 0.6}          ← katalog pluginów (v2/plugins)
    {"mode": "formula", "value": "score/100"}
    {"p": 0.6}                                  ← starsze flow YAML
    {"formula": "score/100"}

Format `{mode, value}` jest właściwy: przy wyborze klucza (`p` vs `formula`)
zmiana trybu w panelu zostawiała po głębokim scaleniu OBA klucze naraz i nie
dało się stwierdzić, który obowiązuje.

Formuły są celowo ograniczone do zamkniętej listy nazw — żadnego `eval`,
bo treść ustawień pochodzi z pliku konfiguracyjnego edytowalnego z panelu.
"""
from __future__ import annotations

import random
from typing import Any

# Dozwolone formuły: nazwa → funkcja liczącą prawdopodobieństwo z kontekstu.
# Kontekst to wartości znane w chwili decyzji (np. wynik AI dla treści).
FORMULAS = {
    "score/100":  lambda v: _num(v.get("score")) / 100.0,
    "score/200":  lambda v: _num(v.get("score")) / 200.0,
    "scoring":    lambda v: _num(v.get("scoring")),
}


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def probability(chance: dict | None, values: dict | None = None) -> tuple[float, str]:
    """Zwróć (p, jak_policzone). Brak konfiguracji = zawsze przechodzi."""
    values = values or {}
    if not chance:
        return 1.0, "no_chance"

    mode = chance.get("mode")
    if mode == "p":
        return _clamp(_num(chance.get("value"))), "p"
    if mode == "formula":
        name = str(chance.get("value") or "")
        fn = FORMULAS.get(name)
        if fn is None:
            return 1.0, f"unknown_formula:{name}"
        return _clamp(fn(values)), f"formula:{name}"

    # starszy zapis (flow YAML)
    if "p" in chance:
        return _clamp(_num(chance["p"])), "p_legacy"
    if "formula" in chance:
        name = str(chance["formula"] or "")
        fn = FORMULAS.get(name)
        if fn is None:
            return 1.0, f"unknown_formula:{name}"
        return _clamp(fn(values)), f"formula_legacy:{name}"

    return 1.0, "empty"


def roll(chance: dict | None, values: dict | None = None,
         rng: random.Random | None = None) -> dict:
    """Rzut. Zwraca `{pass, p, roll, how}` — komplet do zalogowania,
    żeby dało się później odtworzyć, czemu akcja wypadła albo nie."""
    p, how = probability(chance, values)
    r = (rng or random).random()
    return {"pass": r < p, "p": round(p, 3), "roll": round(r, 3), "how": how}


def _clamp(p: float) -> float:
    return max(0.0, min(1.0, p))
