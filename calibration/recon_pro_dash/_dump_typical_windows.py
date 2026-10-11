"""Zrzut wykresu „Ten post / Twój typowy post” dla 7 / 14 / 30 dni.

Na Zawartość chip Okres = 7 / 30 / 180 / 1 rok / 2 lata — bez 14.
14 ustawiamy na Ogólne (sheet 7/14/30), potem wracamy do posta.
Bez drugiego scrcpy, bez publikacji. Komentarze PL.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.calibration.recon_pro_dash._recon import (  # noqa: E402
    acquire_lock,
    connect,
    dump,
    find_tap,
    parse_bounds,
    tap_node,
    unlock,
    wake,
)
from v2.core.screen_map import flatten_hierarchy  # noqa: E402

WANT_VIEWS = "379"
WANT_HEAD = "#rekrutacja"


def _flat(dev) -> list:
    return flatten_hierarchy(dev.hierarchy(force=True))


def _blob(flat) -> str:
    return " ".join(
        f"{n.get('text') or ''} {n.get('content-desc') or ''}" for n in flat
    ).casefold()


def _tap_text(dev, text: str, *, y_min: int = 0, y_max: int = 9999) -> bool:
    needle = text.casefold()
    for n in _flat(dev):
        t = (n.get("text") or "").casefold()
        d = (n.get("content-desc") or "").casefold()
        if needle not in t and needle not in d:
            continue
        xy = parse_bounds(n.get("bounds"))
        if not xy:
            continue
        cy = (xy[1] + xy[3]) // 2
        if cy < y_min or cy > y_max:
            continue
        return tap_node(dev, n)
    return False


def _dismiss_sheet(dev) -> None:
    """Zamknij sheet Okres / typ — tap w lewo albo Wstecz."""
    blob = _blob(_flat(dev))
    if "okres" in blob or "cała zawartość" in blob:
        # tap w lewą kolumnę poza sheetem
        dev.tap(200, 900)
        time.sleep(0.5)


def _on_tabs(flat) -> bool:
    b = _blob(flat)
    return "ogólne" in b and "zawartość" in b


def _on_typical(flat) -> bool:
    b = _blob(flat)
    return "ten post" in b or "twój typowy post" in b


def _leave_item(dev) -> None:
    for _ in range(5):
        flat = _flat(dev)
        if _on_tabs(flat) and not _on_typical(flat):
            return
        if not _tap_text(dev, "Wstecz"):
            dev.press_back()
        time.sleep(0.6)
    _dismiss_sheet(dev)


def _current_range_chip(flat) -> str:
    for n in flat:
        t = (n.get("text") or "").strip()
        xy = parse_bounds(n.get("bounds")) or [0, 0, 0, 0]
        if xy[1] < 380 or xy[1] > 520:
            continue
        if t.endswith("dni") or t in ("1 rok", "2 lata"):
            return t
    return ""


def _set_range_content(dev, label: str) -> bool:
    """Chip Okres na Zawartość (7/30/180…)."""
    _leave_item(dev)
    _dismiss_sheet(dev)
    if not _tap_text(dev, "Zawartość", y_min=200, y_max=360):
        return False
    time.sleep(0.5)
    chip = _current_range_chip(_flat(dev))
    if chip == label:
        return True
    if not _tap_text(dev, chip or "dni", y_min=380, y_max=520):
        return False
    time.sleep(0.5)
    if _tap_text(dev, label):
        time.sleep(0.8)
        return True
    _dismiss_sheet(dev)
    return False


def _set_range_overview(dev, label: str) -> bool:
    """Sheet Okres na Ogólne — tu jest 14 dni."""
    _leave_item(dev)
    _dismiss_sheet(dev)
    if not _tap_text(dev, "Ogólne", y_min=200, y_max=360):
        return False
    time.sleep(0.6)
    chip = _current_range_chip(_flat(dev))
    if chip == label:
        return True
    if not _tap_text(dev, chip or "dni", y_min=380, y_max=520):
        if not _tap_text(dev, "Okres"):
            return False
    time.sleep(0.5)
    ok = _tap_text(dev, label)
    time.sleep(0.8)
    _dismiss_sheet(dev)
    return ok


def _ensure_posts_filter(dev) -> bool:
    _leave_item(dev)
    _dismiss_sheet(dev)
    if not _tap_text(dev, "Zawartość", y_min=200, y_max=360):
        return False
    time.sleep(0.5)
    flat = _flat(dev)
    # już Posty — nie otwieraj sheetu drugi raz
    for n in flat:
        t = (n.get("text") or "").strip()
        xy = parse_bounds(n.get("bounds")) or [0, 0, 0, 0]
        if t == "Posty" and 380 <= xy[1] <= 520:
            return True
    for probe in ("Cała zawartość", "Relacje", "Rolki", "Posty"):
        if _tap_text(dev, probe, y_min=380, y_max=520):
            time.sleep(0.4)
            break
    ok = _tap_text(dev, "Posty")
    time.sleep(0.8)
    _dismiss_sheet(dev)
    return ok


def _tap_379(dev) -> bool:
    """Tap w wiersz 379 — najpierw liczba, potem caption zaczynający się od tagu."""
    flat = _flat(dev)
    for n in flat:
        if (n.get("text") or "").strip() != WANT_VIEWS:
            continue
        xy = parse_bounds(n.get("bounds"))
        if not xy or xy[1] < 620:
            continue
        # środek wiersza, nie prawa krawędź
        dev.tap(540, (xy[1] + xy[3]) // 2)
        time.sleep(1.4)
        return True
    for n in flat:
        t = (n.get("text") or "").strip()
        if not t.startswith(WANT_HEAD):
            continue
        xy = parse_bounds(n.get("bounds"))
        if not xy or xy[1] < 620:
            continue
        return tap_node(dev, n)
    # twardy fallback z dumpa 7d
    dev.tap(540, 720)
    time.sleep(1.4)
    return True


def _wait_typical(dev) -> bool:
    for _ in range(6):
        if _on_typical(_flat(dev)):
            return True
        time.sleep(0.4)
    return False


def dump_window(dev, days: int, name: str) -> dict:
    print(f"\n=== {days} dni → {name} ===")
    label = f"{days} dni"
    if days == 14:
        ok_range = _set_range_overview(dev, label)
        print(f"  zakres Ogólne 14: {ok_range}")
    else:
        ok_range = _set_range_content(dev, label)
        print(f"  zakres Zawartość {label}: {ok_range}")
    if not _ensure_posts_filter(dev):
        dump(dev, f"{name}_list_fail", f"brak listy Posty ({days})")
        print("  FAIL lista Posty")
        return {"ok": False, "name": name}
    dump(dev, f"{name}_list", f"lista Posty przed tapem ({days} dni)")
    _tap_379(dev)
    if not _wait_typical(dev):
        # drugi strzał w znany wiersz
        _tap_379(dev)
        if not _wait_typical(dev):
            dump(dev, f"{name}_not_overlay", f"po tapie 379 brak Ten post ({days})")
            print("  FAIL overlay")
            return {"ok": False, "name": name}
    meta = dump(dev, name, f"LIVE: typowy post #rekrutacja 379 — okno {days} dni")
    axis = [
        (n.get("text") or "").strip()
        for n in _flat(dev)
        if (n.get("text") or "").strip()
    ]
    print(f"  ok nodes={len(meta.get('nodes') or [])}")
    _leave_item(dev)
    return {"ok": True, "name": name, "range_ok": ok_range}


def main() -> int:
    lock = acquire_lock()
    try:
        dev = connect()
        print("wake", wake(dev), "unlock", unlock(dev), "pkg", dev.current_pkg())
        _leave_item(dev)
        _dismiss_sheet(dev)
        if not _on_tabs(_flat(dev)):
            n, _ = find_tap(_flat(dev), rid="com.instagram.android:id/profile_tab")
            if n:
                tap_node(dev, n)
                time.sleep(1.0)
            _tap_text(dev, "Pulpit profesjonalny")
            time.sleep(1.0)
            _tap_text(dev, "Wyświetlenia")
            time.sleep(1.0)
        if not _on_tabs(_flat(dev)):
            dump(dev, "30_typical_nav_fail", "brak tabów Insights")
            return 1
        results = [
            dump_window(dev, 7, "30_typical_post_7d"),
            dump_window(dev, 14, "30_typical_post_14d"),
            dump_window(dev, 30, "30_typical_post_30d"),
        ]
        print("\n=== podsumowanie ===")
        for r in results:
            print(r)
        return 0 if all(r.get("ok") for r in results) else 2
    finally:
        try:
            lock.release()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
