"""Prymitywy planowania wspólne dla harmonogramu.

Historia (ważna przy czytaniu starych planów na dysku): do 2026-08 plan był
generowany OSOBNO DLA KAŻDEGO KONTA i slot był punktem w czasie
(`{"time": "14:20"}`), bez informacji, jak długo sesja potrwa. Przy jednym
telefonie i kilku kontach to nie mogło działać — planner konta A nie wiedział
nic o koncie B, a kolizję „rozwiązywał" dopiero runtime, gubiąc slot
(`device_busy`, bez ponowienia).

Dziś plan powstaje RAZ NA URZĄDZENIE i slot jest odcinkiem o policzonym
czasie trwania — patrz `scheduler/device_planner.py`. Tutaj zostały tylko
kawałki wspólne dla obu warstw. Stare pliki `data/accounts/<id>/schedule/`
nie są już czytane ani kasowane.
"""
from __future__ import annotations

import random
from datetime import date as date_cls, timedelta

from v2.plugins.store import DEFAULT_SCENARIO


def pick_scenario(schedule_cfg: dict, rng: random.Random) -> str:
    """Losowo wybierz JEDEN scenariusz z listy skonfigurowanej dla konta.

    Pusta lista (nic nie skonfigurowano) = `DEFAULT_SCENARIO` — zachowanie
    sprzed wprowadzenia rotacji, tylko nazwane wprost zamiast przez `None`.
    """
    scenarios = [s for s in (schedule_cfg.get("scenarios") or []) if s]
    if not scenarios:
        return DEFAULT_SCENARIO
    return rng.choice(scenarios)


def dates_ahead(start: str, days: int) -> list[str]:
    """`days` kolejnych dat ISO począwszy od `start` (włącznie) — do
    generowania/odczytu podglądu tygodnia."""
    d0 = date_cls.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(days)]


def hhmm_to_min(hhmm: str) -> int:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return hh * 60 + mm


def min_to_hhmm(minutes: float) -> str:
    """Minuty od północy → `HH:MM`. Powyżej doby przycinamy do 23:59 —
    blok nie może przeciekać na następny dzień, bo plan jest per dzień."""
    total = int(min(max(minutes, 0), 24 * 60 - 1))
    return f"{total // 60:02d}:{total % 60:02d}"
