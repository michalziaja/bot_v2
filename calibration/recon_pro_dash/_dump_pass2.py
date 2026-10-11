"""Drugi przejazd live: karty Ogólne od góry + każdy post/relacja.

Wstecz po rid. 14: chip na Ogólne, lista na 30 (Zawartość nie ma 14).
Bez scrcpy, bez publikacji. Komentarze PL.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.calibration.recon_pro_dash._dump_all_items import (  # noqa: E402
    POSTS,
    STORIES,
    _ensure_filter,
    _find_views_xy,
    _on_post_detail,
    _on_story_detail,
    _scroll_chart,
    _tap_views_row,
)
from v2.calibration.recon_pro_dash._dump_typical_windows import (  # noqa: E402
    _blob,
    _current_range_chip,
    _dismiss_sheet,
    _flat,
    _on_tabs,
    _set_range_content,
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
from v2.data.pro_dash_parse import parse_overview_kpis  # noqa: E402

W = 1080
OUT = Path(__file__).resolve().parent
LOG = OUT / "41_PASS2_LOG.json"


def leave(dev) -> bool:
    """Wyjście z overlayu — Wstecz po rid, nie po tekście legendy."""
    for _ in range(10):
        flat = _flat(dev)
        b = _blob(flat)
        if "ogólne" in b and "zawartość" in b and "podsumowanie" not in b:
            _dismiss_sheet(dev)
            return True
        n, _ = find_tap(flat, rid="action_bar_button_back")
        if n:
            tap_node(dev, n)
        else:
            if not _tap_text(dev, "Wstecz"):
                dev.press_back()
        time.sleep(0.55)
    return "ogólne" in _blob(_flat(dev))


def scroll_top(dev) -> None:
    """Trzy swipy w dół ekranu = powrót na górę Ogólne."""
    for _ in range(3):
        dev.swipe(W // 2, 620, W // 2, 1800, duration_ms=300)
        time.sleep(0.3)


def dump_overview_top(dev, days: int) -> dict:
    """Karty Wyświetlenia / Obserwatorzy / Interakcje + Widzowie."""
    leave(dev)
    _tap_text(dev, "Ogólne", y_min=200, y_max=360)
    time.sleep(0.4)
    scroll_top(dev)
    ok = _set_range_overview(dev, f"{days} dni")
    scroll_top(dev)
    print(f"  ov {days} chip={_current_range_chip(_flat(dev))} ok={ok}")
    meta = dump(dev, f"41_ov_{days}d_top", f"PASS2 Ogólne karty {days} dni")
    # Widzowie pod foldem
    dev.swipe(W // 2, 1650, W // 2, 950, duration_ms=280)
    time.sleep(0.4)
    dump(dev, f"41_ov_{days}d_mid", f"PASS2 Ogólne Widzowie {days} dni")
    from v2.data.pro_dash_parse import load_slim as _ls
    kpis = {}
    for suf in ("_top", "_mid"):
        p = OUT / f"41_ov_{days}d{suf}.slim.json"
        if p.exists():
            part = parse_overview_kpis(_ls(p))
            for k, v in part.items():
                if v is not None:
                    kpis[k] = v
    print(f"  kpi {kpis}")
    return {"days": days, "ok": ok, "kpis": kpis, "nodes": len(meta.get("nodes") or [])}


def dump_one(dev, kind: str, views: int, index: int, days: int) -> dict:
    """Tap wiersza, zrzut, weryfikacja liczby views na overlayu."""
    tag = "post" if kind == "post" else "story"
    name = f"41_{tag}_{views}_{days}d"
    if not _find_views_xy(dev, views):
        _tap_views_row(dev, views, index)
    else:
        _tap_views_row(dev, views, index)
    time.sleep(0.3)
    flat = _flat(dev)
    ok_det = _on_post_detail(flat) if kind == "post" else _on_story_detail(flat)
    if not ok_det:
        dump(dev, f"{name}_fail", f"PASS2 brak overlay {kind} {views} {days}")
        leave(dev)
        return {"ok": False, "name": name, "reason": "overlay", "views": views}
    _scroll_chart(dev)
    meta = dump(dev, name, f"PASS2 {kind} {views} okno {days}")
    blob = _blob(_flat(dev))
    has_views = str(views) in blob
    print(f"    {name} views_on_screen={has_views} nodes={len(meta.get('nodes') or [])}")
    leave(dev)
    return {"ok": True, "name": name, "views": views, "kind": kind, "days": days,
            "views_on_screen": has_views}


def dump_kind(dev, kind: str, days: int) -> list[dict]:
    label = "Posty" if kind == "post" else "Relacje"
    views_list = POSTS if kind == "post" else STORIES
    print(f"\n=== {label} {days} ===")
    leave(dev)
    if days == 14:
        _set_range_overview(dev, "14 dni")
        # lista musi pokazać wszystkie wiersze — chip 30 na Zawartość
        _set_range_content(dev, "30 dni")
    elif days == 7:
        _set_range_content(dev, "7 dni")
    else:
        _set_range_content(dev, "30 dni")
    if not _ensure_filter(dev, label):
        dump(dev, f"41_{kind}_list_{days}d_fail", f"PASS2 brak {label}")
        print("  FAIL filtr")
        return [{"ok": False, "kind": kind, "days": days, "reason": "filter"}]
    dump(dev, f"41_{kind}_list_{days}d", f"PASS2 lista {label} {days}")
    out = []
    for i, views in enumerate(views_list):
        if not _on_tabs(_flat(dev)):
            leave(dev)
            _ensure_filter(dev, label)
        rec = dump_one(dev, kind, views, i, days)
        out.append(rec)
    return out


def main() -> int:
    lock = acquire_lock()
    log: dict = {"overview": [], "post": [], "story": []}
    try:
        dev = connect()
        print("wake", wake(dev), "unlock", unlock(dev), "pkg", dev.current_pkg())
        if not leave(dev):
            n, _ = find_tap(_flat(dev), rid="com.instagram.android:id/profile_tab")
            if n:
                tap_node(dev, n)
                time.sleep(1.0)
            _tap_text(dev, "Pulpit profesjonalny")
            time.sleep(1.0)
            _tap_text(dev, "Wyświetlenia")
            time.sleep(1.1)
        leave(dev)
        for days in (7, 14, 30):
            log["overview"].append(dump_overview_top(dev, days))
            LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n")
        for days in (7, 30, 14):
            log["post"].extend(dump_kind(dev, "post", days))
            LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n")
            log["story"].extend(dump_kind(dev, "story", days))
            LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n")
        ok_p = sum(1 for r in log["post"] if r.get("ok"))
        ok_s = sum(1 for r in log["story"] if r.get("ok"))
        print(f"\n=== PASS2 posty {ok_p}/{len(log['post'])} relacje {ok_s}/{len(log['story'])} ===")
        return 0 if ok_p >= 7 and ok_s >= 5 else 2
    finally:
        try:
            lock.release()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
