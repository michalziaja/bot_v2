"""Live dump Tags przez uiautodev (bez scrcpy) — account technature699 / IG main."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from v2.config.schema import load_account
from v2.core.device import UiAuto2Device
from v2.core.screen_map import ScreenMap, flatten_hierarchy
from v2.core.uiauto_server import ensure_running

SAMPLES = Path(__file__).resolve().parents[1] / "calibration" / "samples"
SCROLLABLE_TAB_ID = "com.instagram.android:id/scrollable_tab_layout"
TAB_ALIASES = ("znaczniki", "tags", "tagi")
QUERY = "fitness"


def _center(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None


def _parse_bounds(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return x1, y1, x2, y2
    except Exception:
        return None


def _tab_buttons(flat):
    out = []
    for e in flat:
        if "Button" not in (e.get("class") or ""):
            continue
        t = (e.get("text") or "").strip()
        if t:
            out.append(e)
    return out


def main():
    account = load_account("technature699")
    hw = account.device["_hardware"]
    ensure_running(hw["uiautodev"]["base_url"])
    # serial = adb_id z devices/*.json (jak w cli.build_context)
    serial = hw["adb_id"]
    pkg = account.device["app_clone_package"]
    device = UiAuto2Device(serial=serial,
                           base_url=hw["uiautodev"]["base_url"],
                           ig_package=pkg)
    sm = ScreenMap.load(Path(__file__).resolve().parents[1] / "calibration" /
                        f"{hw['model']}_draft.json")
    # Bez HumanTouch/profilu — surowe gesty device (kalibracja dump)
    def tap(x, y):
        device.tap(int(x), int(y))
        time.sleep(0.35)

    def swipe(x1, y1, x2, y2, duration_ms=280):
        device.swipe(int(x1), int(y1), int(x2), int(y2), duration_ms=duration_ms)
        time.sleep(0.45)

    print("pkg_fg", device.current_pkg())
    if device.current_pkg() != pkg:
        device.shell(
            f"am start -n {pkg}/com.instagram.mainactivity.InstagramMainActivity")
        time.sleep(3)
    print("pkg_fg2", device.current_pkg())

    # search tab — kotwica z mapy; fallback z sample A50
    c = sm.anchor_center("nav.search_tab") or (756, 2277)
    print("tap_search", c)
    tap(*c)
    time.sleep(1.0)

    # focus + type
    c = sm.anchor_center("search.input") or (587, 155)
    tap(*c)
    time.sleep(0.3)
    device.shell(f'input text "{QUERY}"')
    time.sleep(0.6)
    device.shell("input keyevent 66")
    time.sleep(1.5)

    flat = flatten_hierarchy(device.hierarchy(force=True))
    tabs = _tab_buttons(flat)
    print("tabs0", [(e.get("text"), e.get("bounds")) for e in tabs])

    aliases = {a.casefold() for a in TAB_ALIASES}
    found = None
    for i in range(7):
        for e in tabs:
            if (e.get("text") or "").casefold() in aliases:
                found = e
                break
        if found:
            break
        # swipe HSV w lewo
        b = None
        for e in flat:
            if (e.get("resource-id") or "") == SCROLLABLE_TAB_ID:
                b = _parse_bounds(e.get("bounds"))
                break
        if b:
            x1, y1, x2, y2 = b
            y = (y1 + y2) // 2
            swipe(int(x1 + (x2 - x1) * 0.85), y,
                  int(x1 + (x2 - x1) * 0.15), y)
        else:
            swipe(900, 288, 180, 288)
        flat = flatten_hierarchy(device.hierarchy(force=True))
        tabs = _tab_buttons(flat)
        print(f"tabs{i+1}", [e.get("text") for e in tabs])

    SAMPLES.mkdir(parents=True, exist_ok=True)
    if not found:
        tree = device.hierarchy(force=True)
        flat = flatten_hierarchy(tree)
        (SAMPLES / "45_search_tags_tab_MISS.flat.json").write_text(
            json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
        (SAMPLES / "45_search_tags_tab_MISS.hierarchy.json").write_text(
            json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
        print("MISS")
        return 2

    print("FOUND", found.get("text"), found.get("bounds"))
    c = _center(found.get("bounds"))
    tap(*c)
    time.sleep(1.0)

    tree = device.hierarchy(force=True)
    flat = flatten_hierarchy(tree)
    (SAMPLES / "45_search_tags_tab.flat.json").write_text(
        json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
    (SAMPLES / "45_search_tags_tab.hierarchy.json").write_text(
        json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")

    print("tabs_final", [e.get("text") for e in _tab_buttons(flat)])
    for e in flat:
        rid = e.get("resource-id") or ""
        text = (e.get("text") or "").strip()
        desc = (e.get("content-desc") or "").strip()
        blob = f"{rid} {text} {desc}".lower()
        if any(k in blob for k in ("#", "hashtag", "keyword", "tag")) and (
                text or "hashtag" in rid.lower() or "keyword" in rid.lower()):
            print("ROW", rid.split("/")[-1], "|", text[:70], "|", desc[:40])
    print("OK n=", len(flat))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
