"""Kontrakt pluginu: `Budget`, `PluginResult`, protokół `Plugin`.

Plugin to pętla nad krokami z własnym budżetem. Kroki (`v2/steps/`) zostają
bez zmian — plugin ich używa, nie duplikuje ich logiki.

Podział odpowiedzialności za zatrzymanie:
  - `Budget`  — ile jednostek, ile czasu, czy user nacisnął Stop
  - `limits`  — dzienne/godzinowe limity akcji; sprawdza je PLUGIN tuż przed
                akcją, bo tylko on wie, jaki `kind` właśnie wykonuje
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from v2.runner.context import Context


STOP_FILENAME = "STOP"


def stop_path_for(data_root: Path, account_id: str) -> Path:
    return Path(data_root) / "accounts" / account_id / STOP_FILENAME


@dataclass
class Budget:
    """Ile pracy wolno wykonać w jednym zadaniu.

    `max_items` jest LOSOWANE z zakresu przy starcie zadania — stała liczba
    rolek co sesję to wzorzec widoczny z zewnątrz.
    """
    max_items: int | None = None
    max_duration_s: float | None = None
    started_at: float = field(default_factory=time.monotonic)
    items_done: int = 0
    stop_path: Path | None = None

    # ---- konstrukcja ----
    @classmethod
    def from_settings(cls, settings: dict, task_budget: dict | None = None,
                      *, stop_path: Path | None = None,
                      rng: random.Random | None = None,
                      max_items_override: int | None = None) -> "Budget":
        """Złóż budżet z sekcji `budget` ustawień pluginu + nadpisań z zadania.

        Zadanie w kolejce podaje zakres (`max_items: {min, max}`) albo konkretną
        liczbę (`max_items: 12`); ustawienia pluginu — zakres do wylosowania.
        """
        merged = {**(settings.get("budget") or {}), **(task_budget or {})}
        r = rng or random

        items = merged.get("max_items")
        if max_items_override is not None:
            items = int(max_items_override)
        elif isinstance(items, dict):
            lo, hi = int(items.get("min", 0)), int(items.get("max", 0))
            if hi < lo:
                lo, hi = hi, lo
            items = r.randint(lo, hi) if hi > 0 else None
        elif items is not None:
            items = int(items)

        minutes = merged.get("max_duration_min")
        return cls(
            max_items=items,
            max_duration_s=float(minutes) * 60 if minutes else None,
            stop_path=stop_path,
        )

    # ---- odpytywanie ----
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def user_stopped(self) -> bool:
        """Miękki stop z panelu — plik STOP obok danych konta."""
        return bool(self.stop_path and self.stop_path.exists())

    def stop_reason(self) -> str | None:
        """None = można kontynuować. Sprawdzane PRZED każdą jednostką pracy."""
        if self.user_stopped():
            return "user"
        if self.max_items is not None and self.items_done >= self.max_items:
            return "items"
        if self.max_duration_s is not None and self.elapsed() >= self.max_duration_s:
            return "duration"
        return None

    def tick(self, n: int = 1) -> None:
        self.items_done += n

    def as_log(self) -> dict:
        return {"items_done": self.items_done, "max_items": self.max_items,
                "elapsed_s": round(self.elapsed(), 1),
                "max_duration_s": self.max_duration_s}


@dataclass
class PluginResult:
    status: str                     # ok | failed
    items_done: int = 0
    stopped_by: str = "items"       # items|duration|limit|empty|error|user
    stats: dict = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def done(cls, budget: "Budget", stopped_by: str, **stats) -> "PluginResult":
        # stopped_by=error przez done() = awaria pętli (np. dwa swipe fail),
        # nie sukces. Inaczej kolejka/panel pokazywałyby status=ok przy failu.
        if stopped_by == "error":
            err = stats.pop("error", None) or "plugin stopped_by=error"
            return cls.failed(err, budget, **stats)
        return cls("ok", items_done=budget.items_done, stopped_by=stopped_by, stats=stats)

    @classmethod
    def failed(cls, error: str, budget: "Budget | None" = None, **stats) -> "PluginResult":
        return cls("failed", items_done=budget.items_done if budget else 0,
                   stopped_by="error", stats=stats, error=error)

    def as_log(self) -> dict:
        return {"status": self.status, "items_done": self.items_done,
                "stopped_by": self.stopped_by, "error": self.error, **self.stats}


class Plugin(Protocol):
    """Każdy plugin: atrybuty klasowe + `run`. Rejestracja przez
    `v2.plugins.registry.register`."""
    id: str
    entry_screen: str
    exit_screen: str

    def run(self, ctx: "Context", s: dict, budget: Budget) -> PluginResult: ...


# ============================================================
# pomocnicze — wspólne dla implementacji pluginów
# ============================================================
def gate_params(gate: dict) -> dict:
    """Sekcja `gate` z katalogu → params kroku `reels.watch`.

    Wspólne dla content_search / reels_niche. `max_likes=0` = brak górnego
    limitu → nie przekazujemy (wczesna bramka w watch ocenia min_* i max_*).
    """
    out = {"min_likes": gate.get("min_likes", 0),
           "min_comments": gate.get("min_comments", 0),
           "on_unknown": gate.get("on_unknown", "skip")}
    if gate.get("max_likes"):
        out["max_likes"] = gate["max_likes"]
    return out


def draw_range(value: Any, rng: random.Random | None = None, default: int = 0) -> int:
    """Zamień `{min, max}` na wylosowaną liczbę całkowitą; skalar → int.

    Ustawienia z katalogu podają zakresy wszędzie tam, gdzie stała wartość
    byłaby wzorcem — ten helper jest jedynym miejscem, które je rozwija.
    Zawsze zwraca int (liczniki sztuk); zakresy float (sekundy, ułamki)
    losuje się osobno przez `rng.uniform`.
    """
    r = rng or random
    if isinstance(value, dict):
        lo, hi = int(value.get("min", default)), int(value.get("max", default))
        if hi < lo:
            lo, hi = hi, lo
        return r.randint(lo, hi)
    if value is None:
        return default
    return int(value)
