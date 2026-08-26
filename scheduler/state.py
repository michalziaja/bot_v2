"""Ustawienia harmonogramu — jedno miejsce dla całej instalacji.

Trzy rzeczy, których nie da się sensownie trzymać per konto:

**Master switch.** Do tej pory „harmonogram włączony" było rozproszone po
kontach (`schedule.enabled`) i nie dawało się zatrzymać całości jednym ruchem.
Gorzej: włączony harmonogram nie blokował ręcznego startu, więc kliknięcie
„Uruchom kolejkę" mogło zająć urządzenie tuż przed slotem i przepalić
zaplanowaną sesję. Dziś `schedule.enabled` w koncie znaczy „to konto
uczestniczy w planie", a `enabled` tutaj — „plan jest egzekwowany".

**Godziny działania.** Odgórne, wspólne dla wszystkich kont. Gdy każde konto
miało własne `active_hours`, plan urządzenia był sumą rozjeżdżonych okien
i nie dało się odpowiedzieć na proste pytanie „o której telefon śpi".

**Długość cyklu.** Plan nie jest układany na konkretne daty w nieskończoność,
tylko jako powtarzalny wzorzec 1, 3 albo 7 dni. Konkretna data materializuje
się z tego wzorca dopiero w dniu wykonania (patrz `device_planner.materialize`).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

STATE_FILENAME = "scheduler.json"

# 7 dni jest wyrównane do dni tygodnia (indeks = `date.weekday()`), więc
# „poniedziałek" w planie to naprawdę poniedziałek. Krótsze cykle liczą się
# modulo z numeru dnia — wtedy chodzi o rytm, nie o kalendarz.
CYCLE_CHOICES = (1, 3, 7)

DEFAULTS = {
    "enabled": False,
    "changed_at": None,
    "active_hours": [9, 22],
    "cycle_days": 1,
}


def state_path(data_root: Path) -> Path:
    return Path(data_root) / STATE_FILENAME


def _clean(raw: dict) -> dict:
    """Wartości spoza zakresu cofamy do domyślnych — uszkodzony plik nie może
    ani włączyć automatyki, ani wygenerować planu na 0-godzinne okno."""
    hours = raw.get("active_hours") or DEFAULTS["active_hours"]
    try:
        h1, h2 = int(hours[0]), int(hours[1])
    except (TypeError, ValueError, IndexError):
        h1, h2 = DEFAULTS["active_hours"]
    h1 = min(max(h1, 0), 23)
    h2 = min(max(h2, h1 + 1), 24)

    cycle = raw.get("cycle_days")
    cycle = int(cycle) if cycle in CYCLE_CHOICES else DEFAULTS["cycle_days"]

    return {
        "enabled": bool(raw.get("enabled")),
        "changed_at": raw.get("changed_at"),
        "active_hours": [h1, h2],
        "cycle_days": cycle,
    }


def read(data_root: Path) -> dict:
    p = state_path(data_root)
    if not p.exists():
        return dict(DEFAULTS)
    try:
        return _clean(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return dict(DEFAULTS)


def is_enabled(data_root: Path) -> bool:
    return read(data_root)["enabled"]


def active_hours(data_root: Path) -> tuple[int, int]:
    h = read(data_root)["active_hours"]
    return h[0], h[1]


def cycle_days(data_root: Path) -> int:
    return read(data_root)["cycle_days"]


def update(data_root: Path, **fields) -> dict:
    """Zapis częściowy — panel zmienia jedno pole naraz."""
    s = read(data_root)
    for k in ("enabled", "active_hours", "cycle_days"):
        if k in fields and fields[k] is not None:
            s[k] = fields[k]
    s = _clean(s)
    s["changed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    p = state_path(data_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return s


def set_enabled(data_root: Path, enabled: bool) -> dict:
    return update(data_root, enabled=enabled)
