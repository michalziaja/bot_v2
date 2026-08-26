"""Wspólny format postępu X/Y + sync do SessionStats.

Zasada: ten sam `(current, total)` trafia do `say(...)` i do
`ctx.session_stats.set_progress(...)` — jeden helper, zero rozjazdu
między terminalem a session_stats.json.
"""
from __future__ import annotations

from typing import Any, Callable


def xy(current: int | None, total: int | None) -> str:
    """Fragment „2/3”; pusty gdy brak liczb."""
    if current is None:
        return ""
    if total is None:
        return str(int(current))
    return f"{int(current)}/{int(total)}"


def with_progress(msg: str, current: int | None = None,
                  total: int | None = None) -> str:
    """Doklej „ 2/3” do komunikatu (jeśli mamy liczby)."""
    suffix = xy(current, total)
    if not suffix:
        return msg
    base = (msg or "").rstrip()
    return f"{base} {suffix}"


def announce_progress(
    ctx,
    say_fn: Callable[[str], None] | None,
    *,
    msg: str,
    phase: str,
    current: int,
    total: int | None,
    label: str | None = None,
    detail: str | None = None,
    sub: dict | None = None,
) -> str:
    """Wypisz linię operatora + zapisz progress w SessionStats (+ event).

    `say_fn` — np. `reel_log.say` / `content_search_log.say`.
    Zwraca treść wiadomości (bez prefixu pluginu).
    """
    text = msg if msg is not None else ""
    if say_fn is not None and text.strip():
        say_fn(text)
    ss = getattr(ctx, "session_stats", None) if ctx is not None else None
    if ss is not None:
        try:
            ss.set_progress(
                phase=phase, current=current, total=total,
                label=label, detail=detail, sub=sub)
        except Exception:
            # Statystyki nie mogą wywalić pętli pluginu
            pass
    return text


def sync_plugin_stats(ctx, stats: dict | None, *,
                      items_done: int | None = None,
                      max_items: int | None = None) -> None:
    """Po ++ w lokalnym `stats` — zsynchronizuj counters/budget w JSON."""
    ss = getattr(ctx, "session_stats", None) if ctx is not None else None
    if ss is None:
        return
    try:
        if stats is not None:
            ss.set_counters(stats)
        if items_done is not None:
            ss.sync_budget(items_done, max_items)
    except Exception:
        pass


def progress_sub(*, phase: str, current: int, total: int | None,
                 label: str | None = None,
                 detail: str | None = None) -> dict[str, Any]:
    """Zagnieżdżony licznik (hot / harvest / scroll) pod `progress.sub`."""
    out: dict[str, Any] = {
        "phase": phase,
        "label": label or phase,
        "current": int(current),
        "total": int(total) if total is not None else None,
    }
    if detail is not None:
        out["detail"] = detail
    return out
