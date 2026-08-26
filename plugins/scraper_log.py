"""Krotkie, ludzkie komunikaty z przebiegu scrapera targetow (stdout / panel).

Wzorzec jak `reel_log`, ale z prefixem `target_scraper` — panel koloruje
plugin po nazwie; tu bez ANSI (bot pisze do pliku/pipe).
"""
from __future__ import annotations
import sys

# Prefix = id pluginu — ten sam co w stepcall / panelu
_PREFIX = "target_scraper"


def say(msg: str) -> None:
    """Jedna linia statusu — zawsze z prefixem target_scraper z przodu."""
    if not (msg or "").strip():
        print(flush=True)
        return
    try:
        print(f"  {_PREFIX} {msg}", flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        line = f"  {_PREFIX} {msg}\n"
        sys.stdout.buffer.write(line.encode(enc, errors="replace"))
        sys.stdout.flush()


def target_line(target: str | None, i: int | None = None,
                n: int | None = None) -> str:
    """Start targetu: „Target @nick 1/3”."""
    from v2.plugins.progress_log import with_progress
    who = f"@{target}" if target else "@?"
    return with_progress(f"Target {who}", i, n)


def grid_item_line(label: str, i: int, n: int | None = None) -> str:
    """Kafelek w siatce: „Post 1/3” / „Rolka 2/3” (już było inline)."""
    from v2.plugins.progress_log import xy
    frac = xy(i, n) if n is not None else str(i)
    return f"{label} {frac}"
