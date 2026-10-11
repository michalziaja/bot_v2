"""Drugi przejazd: karty Ogólne od góry (7/14/30) bez zjeżdżania poza KPI.

Uruchom gdy lock wolny. Bez scrcpy, bez publikacji. Komentarze PL.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.calibration.recon_pro_dash._dump_typical_windows import (  # noqa: E402
    _current_range_chip,
    _dismiss_sheet,
    _flat,
    _leave_item,
    _on_tabs,
    _set_range_overview,
    _tap_text,
)
from v2.calibration.recon_pro_dash._recon import (  # noqa: E402
    acquire_lock,
    connect,
    dump,
    find_tap,
    tap_node,
    unlock,
    wake,
)

W = 1080


def dump_top(dev, days: int) -> dict:
    """Chip na Ogólne, swipe w górę, zrzut kart KPI."""
    name = f"40_ov_{days}d_top"
    print(f"\n=== Ogólne TOP {days} → {name} ===")
    _leave_item(dev)
    _dismiss_sheet(dev)
    ok = _set_range_overview(dev, f"{days} dni")
    _tap_text(dev, "Ogólne", y_min=200, y_max=360)
    time.sleep(0.6)
    # wróć na górę — karty Wyświetlenia / Obserwatorzy netto / Interakcje
    for _ in range(3):
        dev.swipe(W // 2, 650, W // 2, 1750, duration_ms=320)
        time.sleep(0.35)
    print("  chip", _current_range_chip(_flat(dev)), "range", ok)
    meta = dump(dev, name, f"LIVE: Ogólne karty KPI od góry, {days} dni")
    return {"ok": ok, "name": name, "nodes": len(meta.get("nodes") or [])}


def main() -> int:
    lock = acquire_lock()
    try:
        dev = connect()
        print("wake", wake(dev), "unlock", unlock(dev))
        _leave_item(dev)
        if not _on_tabs(_flat(dev)):
            n, _ = find_tap(_flat(dev), rid="com.instagram.android:id/profile_tab")
            if n:
                tap_node(dev, n)
                time.sleep(1.0)
            _tap_text(dev, "Pulpit profesjonalny")
            time.sleep(1.0)
            _tap_text(dev, "Wyświetlenia")
            time.sleep(1.0)
        for days in (7, 14, 30):
            dump_top(dev, days)
        return 0
    finally:
        try:
            lock.release()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
