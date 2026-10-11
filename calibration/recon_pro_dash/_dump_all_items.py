"""Live: Ogólne 7/14/30 + każdy post i relacja (wykres 7/14/30).

Zawartość nie ma chipa 14 — 14 ustawiamy na Ogólne, potem drill-in.
Rolki: jeden dump empty state, bez zgadywania. Bez drugiego scrcpy,
bez publikacji. Komentarze PL.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.calibration.recon_pro_dash._dump_typical_windows import (  # noqa: E402
    _blob,
    _current_range_chip,
    _dismiss_sheet,
    _flat,
    _leave_item,
    _on_tabs,
    _on_typical,
    _set_range_content,
    _set_range_overview,
    _tap_text,
)
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
from v2.data.pro_dash_parse import parse_overview_kpis  # noqa: E402

# Kolejność z ostatniego recon (filtr Posty / Relacje, Najnowsze).
POSTS = (379, 394, 42, 54, 278)
STORIES = (147, 172, 162, 179, 196)
W, H = 1080, 2340
OUT = Path(__file__).resolve().parent
LOG = OUT / "40_ALL_ITEMS_LOG.json"


def _ensure_filter(dev, label: str) -> bool:
    """Chip typu na Zawartość: Posty / Relacje / Rolki."""
    _leave_item(dev)
    _dismiss_sheet(dev)
    if not _tap_text(dev, "Zawartość", y_min=200, y_max=360):
        return False
    time.sleep(0.5)
    flat = _flat(dev)
    for n in flat:
        t = (n.get("text") or "").strip()
        xy = parse_bounds(n.get("bounds")) or [0, 0, 0, 0]
        if t == label and 380 <= xy[1] <= 520:
            return True
    for probe in ("Cała zawartość", "Posty", "Relacje", "Rolki"):
        if _tap_text(dev, probe, y_min=380, y_max=520):
            time.sleep(0.4)
            break
    ok = _tap_text(dev, label)
    time.sleep(0.8)
    _dismiss_sheet(dev)
    return ok


def _set_window(dev, days: int) -> bool:
    """7/30 na Zawartość; 14 tylko na Ogólne (brak chipa w overlayu)."""
    label = f"{days} dni"
    if days == 14:
        return _set_range_overview(dev, label)
    return _set_range_content(dev, label)


def _find_views_xy(dev, views: int):
    """Bounds liczby wyświetleń na liście (y>620, preferuj prawą kolumnę)."""
    want = str(int(views))
    hits = []
    for n in _flat(dev):
        if (n.get("text") or "").strip() != want:
            continue
        xy = parse_bounds(n.get("bounds"))
        if not xy or xy[1] < 620:
            continue
        hits.append(xy)
    if not hits:
        return None
    hits.sort(key=lambda b: (-b[0], b[1]))
    return hits[0]


def _tap_views_row(dev, views: int, index: int) -> bool:
    """Tap w wiersz po liczbie wyświetleń; scroll listy gdy wiersz poza ekranem."""
    for _ in range(5):
        xy = _find_views_xy(dev, views)
        if xy:
            dev.tap(540, (xy[1] + xy[3]) // 2)
            time.sleep(1.3)
            return True
        # dociągnięcie listy — starsze posty / relacje
        dev.swipe(W // 2, 1850, W // 2, 900, duration_ms=360)
        time.sleep(0.55)
    y = 680 + index * 188 + 90
    if y > 2100:
        y = 2100
    dev.tap(540, y)
    time.sleep(1.3)
    return True


def _on_story_detail(flat) -> bool:
    b = _blob(flat)
    return "podsumowanie" in b or "multimedia – relacje" in b


def _on_post_detail(flat) -> bool:
    """Overlay posta: legenda bywa tylko na JPG, w hierarchii jest Podsumowanie."""
    b = _blob(flat)
    if _on_typical(flat):
        return True
    return (
        "wyświetlenia na przestrzeni" in b
        or ("podsumowanie" in b and "obserwowanie" in b)
        or ("podsumowanie" in b and "widzowie" in b)
    )


def _scroll_chart(dev) -> None:
    """Swipe w górę — wykres + legenda (post) albo donut (relacja)."""
    dev.swipe(W // 2, 1900, W // 2, 700, duration_ms=420)
    time.sleep(0.7)


def _wait_detail(dev, kind: str) -> bool:
    for _ in range(7):
        flat = _flat(dev)
        if kind == "post" and _on_post_detail(flat):
            return True
        if kind == "story" and _on_story_detail(flat):
            return True
        time.sleep(0.35)
    return False


def dump_overview(dev, days: int) -> dict:
    """Chip Okres na Ogólne + zrzut kart KPI (views/follows/interactions/viewers)."""
    name = f"40_ov_{days}d"
    print(f"\n=== Ogólne {days} dni → {name} ===")
    _leave_item(dev)
    _dismiss_sheet(dev)
    ok = _set_range_overview(dev, f"{days} dni")
    print(f"  zakres Ogólne: {ok} chip={_current_range_chip(_flat(dev))}")
    if not _tap_text(dev, "Ogólne", y_min=200, y_max=360):
        print("  FAIL tab Ogólne")
    time.sleep(0.7)
    # zostań na górze — karty Wyświetlenia / Obserwatorzy / Interakcje
    dev.swipe(W // 2, 700, W // 2, 1700, duration_ms=280)
    time.sleep(0.45)
    meta = dump(dev, name, f"LIVE: Ogólne KPI okno {days} dni")
    # drugi zrzut niżej — Widzowie
    dev.swipe(W // 2, 1600, W // 2, 900, duration_ms=280)
    time.sleep(0.45)
    dump(dev, f"{name}_mid", f"LIVE: Ogólne Widzowie okno {days} dni")
    from v2.data.pro_dash_parse import load_slim
    slim_path = OUT / f"{name}.slim.json"
    kpis = {}
    if slim_path.exists():
        kpis = parse_overview_kpis(load_slim(slim_path))
    print(f"  kpi {kpis}")
    return {"ok": ok, "name": name, "kpis": kpis, "nodes": len(meta.get("nodes") or [])}


def dump_item(dev, kind: str, views: int, index: int, days: int) -> dict:
    """Otwórz wiersz, zrzut wykresu (albo donut), wróć na listę."""
    tag = "post" if kind == "post" else "story"
    name = f"40_{tag}_{views}_{days}d"
    print(f"  → {name}")
    if not _tap_views_row(dev, views, index):
        return {"ok": False, "name": name, "reason": "tap"}
    if not _wait_detail(dev, kind):
        _tap_views_row(dev, views, index)
        if not _wait_detail(dev, kind):
            dump(dev, f"{name}_fail", f"brak overlayu {kind} {views} ({days})")
            print("    FAIL overlay")
            _leave_item(dev)
            return {"ok": False, "name": name, "reason": "overlay"}
    _scroll_chart(dev)
    if kind == "post" and not _on_typical(_flat(dev)):
        _scroll_chart(dev)
    meta = dump(dev, name, f"LIVE: {kind} {views} wykres okno {days} dni")
    blob = _blob(_flat(dev))
    chart = (
        "line" if ("ten post" in blob or "twój typowy" in blob)
        else ("donut" if "widzowie" in blob or "podsumowanie" in blob else "unknown")
    )
    print(f"    ok chart={chart} nodes={len(meta.get('nodes') or [])}")
    _leave_item(dev)
    time.sleep(0.35)
    return {"ok": True, "name": name, "chart": chart, "views": views, "kind": kind, "days": days}


def dump_kind_window(dev, kind: str, views_list: tuple, days: int) -> list[dict]:
    """Filtr typu + wszystkie wiersze dla jednego okna."""
    label = "Posty" if kind == "post" else "Relacje"
    print(f"\n=== {label} okno {days} ===")
    if days == 14:
        print("  ogolne14", _set_range_overview(dev, "14 dni"))
    else:
        print("  zawartosc", _set_window(dev, days), _current_range_chip(_flat(dev)))
    if not _ensure_filter(dev, label):
        dump(dev, f"40_{kind}_list_{days}d_fail", f"brak listy {label}")
        print("  FAIL filtr", label)
        return [{"ok": False, "kind": kind, "days": days, "reason": "filter"}]
    dump(dev, f"40_{kind}_list_{days}d", f"lista {label} przed tapami ({days} dni)")
    out = []
    for i, views in enumerate(views_list):
        # po powrocie upewnij się, że filtr i lista żyją
        if not _on_tabs(_flat(dev)):
            _leave_item(dev)
            _ensure_filter(dev, label)
        rec = dump_item(dev, kind, views, i, days)
        out.append(rec)
    return out


def dump_reels_empty(dev) -> dict:
    """Rolki: empty state, bez fałszywej serii."""
    print("\n=== Rolki (empty) ===")
    _set_window(dev, 30)
    if not _ensure_filter(dev, "Rolki"):
        return {"ok": False, "reason": "filter"}
    meta = dump(dev, "40_reels_empty", "LIVE: Rolki empty state — bez wykresu")
    return {"ok": True, "name": "40_reels_empty", "nodes": len(meta.get("nodes") or [])}


def _save_log(payload: dict) -> None:
    LOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")


def main() -> int:
    lock = acquire_lock()
    results: dict = {"overview": [], "post": [], "story": [], "reel": None}
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
            dump(dev, "40_nav_fail", "brak tabów Insights")
            return 1

        for days in (7, 14, 30):
            rec = dump_overview(dev, days)
            results["overview"].append(rec)
            _save_log(results)

        for days in (7, 14, 30):
            results["post"].extend(dump_kind_window(dev, "post", POSTS, days))
            _save_log(results)
            results["story"].extend(dump_kind_window(dev, "story", STORIES, days))
            _save_log(results)

        results["reel"] = dump_reels_empty(dev)
        _save_log(results)
        ok_posts = sum(1 for r in results["post"] if r.get("ok"))
        ok_stories = sum(1 for r in results["story"] if r.get("ok"))
        print(f"\n=== koniec posty {ok_posts}/{len(results['post'])} "
              f"relacje {ok_stories}/{len(results['story'])} ===")
        return 0 if ok_posts >= 10 and ok_stories >= 5 else 2
    finally:
        try:
            lock.release()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
