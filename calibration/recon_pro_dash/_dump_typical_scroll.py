"""Dociągnięcie wykresu typowego posta: tap 379, swipe, dump 7/14/30.

14 dni tylko na Ogólne. Komentarze PL. Bez drugiego scrcpy.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.calibration.recon_pro_dash._dump_typical_windows import (  # noqa: E402
    _blob,
    _current_range_chip,
    _dismiss_sheet,
    _ensure_posts_filter,
    _flat,
    _leave_item,
    _on_tabs,
    _set_range_content,
    _set_range_overview,
    _tap_379,
    _tap_text,
)
from v2.calibration.recon_pro_dash._recon import (  # noqa: E402
    acquire_lock,
    connect,
    dump,
    unlock,
    wake,
)

W, H = 1080, 2340


def _scroll_chart(dev) -> None:
    """Swipe w górę — wykres + legenda + etykiety osi."""
    # od środka kart w dół, żeby nie złapać tabów
    dev.swipe(W // 2, 1900, W // 2, 700, duration_ms=420)
    time.sleep(0.8)


def _has_legend(dev) -> bool:
    b = _blob(_flat(dev))
    return "ten post" in b or "twój typowy post" in b


def one(dev, days: int, name: str) -> dict:
    print(f"\n=== scroll {days} → {name} ===")
    label = f"{days} dni"
    if days == 14:
        print("  ogolne14", _set_range_overview(dev, label))
    else:
        print("  zawartosc", _set_range_content(dev, label), _current_range_chip(_flat(dev)))
    _ensure_posts_filter(dev)
    time.sleep(0.4)
    print("  chip", _current_range_chip(_flat(dev)))
    _tap_379(dev)
    time.sleep(1.2)
    _scroll_chart(dev)
    if not _has_legend(dev):
        _scroll_chart(dev)
    meta = dump(dev, name, f"LIVE: typowy post 379 wykres+legenda okno {days} dni")
    b = _blob(_flat(dev))
    print("  legend", "ten post" in b, "typowy" in b, "nodes", len(meta.get("nodes") or []))
    _leave_item(dev)
    return {"name": name, "legend": "ten post" in b}


def main() -> int:
    lock = acquire_lock()
    try:
        dev = connect()
        print("wake", wake(dev), "unlock", unlock(dev))
        _leave_item(dev)
        _dismiss_sheet(dev)
        if not _on_tabs(_flat(dev)):
            dump(dev, "31_nav_fail", "brak tabów")
            return 1
        out = [
            one(dev, 7, "31_typical_post_7d"),
            one(dev, 14, "31_typical_post_14d"),
            one(dev, 30, "31_typical_post_30d"),
        ]
        print(out)
        return 0 if all(r["legend"] for r in out) else 2
    finally:
        try:
            lock.release()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
