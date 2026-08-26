"""Live dump: search results → swipe tabs → Znaczniki/Tags → sample 45.

Best-effort kalibracja pod search.switch_results_tab / collect_tag_variants.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from v2.cli import build_context
from v2.core.screen_map import flatten_hierarchy
from v2.plugins import stepcall
from v2.plugins.registry import load_all as load_plugins
from v2.steps import registry as steps
from v2.steps.registry import load_all

SAMPLES = Path(__file__).resolve().parents[1] / "calibration" / "samples"
QUERY = "fitness"
TAB_ALIASES = ("znaczniki", "tags", "tagi")
SCROLLABLE_TAB_ID = "com.instagram.android:id/scrollable_tab_layout"
MAX_SWIPES = 6


def _step(ctx, name, params=None):
    return stepcall.call(steps, ctx, name, params or {}, source="probe")


def _center(bounds: str):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None


def _parse_bounds(bounds: str):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return x1, y1, x2, y2
    except Exception:
        return None


def _visible_tab_buttons(flat):
    out = []
    for e in flat:
        if "Button" not in (e.get("class") or ""):
            continue
        text = (e.get("text") or "").strip()
        if not text:
            continue
        out.append({"text": text, "bounds": e.get("bounds"),
                    "selected": bool(e.get("selected"))})
    return out


def _find_tab(flat, aliases):
    for e in flat:
        if "Button" not in (e.get("class") or ""):
            continue
        text = (e.get("text") or "").strip()
        if text.casefold() in aliases:
            return e
    return None


def _tab_bar_bounds(flat):
    for e in flat:
        if (e.get("resource-id") or "") == SCROLLABLE_TAB_ID:
            return _parse_bounds(e.get("bounds") or "")
    return None


def _swipe_tabs_left(ctx, flat):
    """Przesuń HorizontalScrollView w lewo (kolejne zakładki z prawej)."""
    b = _tab_bar_bounds(flat)
    if b:
        x1, y1, x2, y2 = b
        y = (y1 + y2) // 2
        # swipe w lewo: treść w prawo → widać zakładki po prawej
        ctx.ht.swipe(int(x1 + (x2 - x1) * 0.82), y,
                     int(x1 + (x2 - x1) * 0.18), y,
                     duration_ms=280, pause_kind="tap")
    else:
        try:
            w, h = ctx.account.device["_hardware"]["resolution"]
        except Exception:
            w, h = 1080, 2340
        y = int(h * 0.12)
        ctx.ht.swipe(int(w * 0.85), y, int(w * 0.15), y,
                     duration_ms=280, pause_kind="tap")
    time.sleep(0.45)


def _save_sample(prefix: str, tree, flat):
    SAMPLES.mkdir(parents=True, exist_ok=True)
    (SAMPLES / f"{prefix}.hierarchy.json").write_text(
        json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    (SAMPLES / f"{prefix}.flat.json").write_text(
        json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", prefix)


def main():
    load_all()
    load_plugins()
    ctx = build_context("a50_clone3", speed=2.5)

    # NotificationShade potrafi blokować — BACK na start
    for _ in range(2):
        ctx.device.press_back()
        time.sleep(0.3)

    for name in ("system.wake", "system.unlock", "system.ensure_ig_running"):
        r = _step(ctx, name)
        print(name, r.status, getattr(r, "error", None) or "")
        if r.status == "failed":
            return 1

    for name, params in (
        ("search.open", {}),
        ("search.type_query", {"query": QUERY, "clear_first": True, "submit": True}),
    ):
        r = _step(ctx, name, params)
        print(name, r.status, r.payload if r.status == "ok" else r.error)
        if r.status != "ok":
            return 1

    top = ctx.navigator.identify().top
    print("top_after_search", top)
    if top != "search_results":
        print("WARN: nie na search_results — dump i tak")

    aliases = {a.casefold() for a in TAB_ALIASES}
    found = None
    for i in range(MAX_SWIPES + 1):
        tree = ctx.device.hierarchy(force=True)
        flat = flatten_hierarchy(tree)
        tabs = _visible_tab_buttons(flat)
        print(f"swipe={i} tabs={[t['text'] for t in tabs]}")
        found = _find_tab(flat, aliases)
        if found:
            break
        if i < MAX_SWIPES:
            _swipe_tabs_left(ctx, flat)

    if not found:
        # dump tego, co widać — do notatki kalibracyjnej
        tree = ctx.device.hierarchy(force=True)
        flat = flatten_hierarchy(tree)
        _save_sample("45_search_tags_tab_MISS", tree, flat)
        print("FAIL: brak zakładki Znaczniki/Tags po swipe")
        return 2

    c = _center(found.get("bounds"))
    print("tap_tab", found.get("text"), c)
    if c:
        ctx.ht.tap(*c, pause_kind="tap")
        time.sleep(0.9)

    tree = ctx.device.hierarchy(force=True)
    flat = flatten_hierarchy(tree)
    _save_sample("45_search_tags_tab", tree, flat)

    # Szybki podgląd wierszy z # / hashtag / keyword
    interesting = []
    for e in flat:
        rid = (e.get("resource-id") or "")
        text = (e.get("text") or "").strip()
        desc = (e.get("content-desc") or "").strip()
        blob = f"{rid} {text} {desc}".lower()
        if any(k in blob for k in ("#", "hashtag", "keyword", "tag", "post")):
            if text or "hashtag" in rid.lower() or "keyword" in rid.lower():
                interesting.append({
                    "rid": rid.split("/")[-1] if rid else "",
                    "text": text[:80],
                    "desc": desc[:80],
                    "cls": (e.get("class") or "").split(".")[-1],
                    "bounds": e.get("bounds"),
                })
    print("INTERESTING", json.dumps(interesting[:40], ensure_ascii=False, indent=2))
    print("tabs_final", [t["text"] for t in _visible_tab_buttons(flat)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
