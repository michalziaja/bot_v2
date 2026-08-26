"""Kontynuacja kalibracji Tags: BACK z clips → search_results → swipe → dump."""
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
ALIASES = {"znaczniki", "tags", "tagi"}


def _center(bounds):
    a, b = str(bounds).split("][")
    x1, y1 = map(int, a.strip("[").split(","))
    x2, y2 = map(int, b.strip("]").split(","))
    return (x1 + x2) // 2, (y1 + y2) // 2


def _parse(bounds):
    a, b = str(bounds).split("][")
    x1, y1 = map(int, a.strip("[").split(","))
    x2, y2 = map(int, b.strip("]").split(","))
    return x1, y1, x2, y2


def _y1(bounds):
    try:
        return int(str(bounds).split(",")[1].split("]")[0])
    except Exception:
        return 9999


def dump_info(device, label):
    flat = flatten_hierarchy(device.hierarchy(force=True))
    has_tabs = any("scrollable_tab_layout" in (e.get("resource-id") or "") for e in flat)
    has_clips = any("clips_viewer" in (e.get("resource-id") or "") for e in flat)
    top_btns = [
        (e.get("text") or "")
        for e in flat
        if "Button" in (e.get("class") or "")
        and (e.get("text") or "").strip()
        and _y1(e.get("bounds")) < 400
    ]
    print(label, "tabs?", has_tabs, "clips?", has_clips, "top_btns", top_btns[:8])
    return flat


def main():
    account = load_account("technature699")
    hw = account.device["_hardware"]
    ensure_running(hw["uiautodev"]["base_url"])
    device = UiAuto2Device(
        serial=hw["adb_id"],
        base_url=hw["uiautodev"]["base_url"],
        ig_package=account.device["app_clone_package"],
    )
    sm = ScreenMap.load(
        Path(__file__).resolve().parents[1] / "calibration" / f"{hw['model']}_draft.json"
    )

    flat = dump_info(device, "start")
    for i in range(8):
        if any("scrollable_tab_layout" in (e.get("resource-id") or "") for e in flat):
            break
        if any(
            "action_bar_search_edit_text" in (e.get("resource-id") or "") for e in flat
        ) and not any("clips_viewer" in (e.get("resource-id") or "") for e in flat):
            break
        device.press_back()
        time.sleep(0.75)
        flat = dump_info(device, f"back{i}")

    if not any("scrollable_tab_layout" in (e.get("resource-id") or "") for e in flat):
        c = sm.anchor_center("nav.search_tab") or (756, 2277)
        device.tap(*c)
        time.sleep(1.2)
        flat = dump_info(device, "search_tap")
        c = sm.anchor_center("search.input") or (587, 155)
        device.tap(*c)
        time.sleep(0.35)
        device.shell("input keyevent 123")
        device.shell("input keyevent " + " ".join(["67"] * 24))
        device.shell('input text "fitness"')
        time.sleep(1.0)
        flat = dump_info(device, "typed")
        for e in flat:
            rid = e.get("resource-id") or ""
            t = (e.get("text") or "").strip()
            if t and any(k in rid for k in ("keyword", "hashtag", "search")):
                print("SUG", rid.split("/")[-1], t[:50], e.get("bounds"))
        device.shell("input keyevent 66")
        time.sleep(2.0)
        flat = dump_info(device, "enter")

    for i in range(8):
        flat = flatten_hierarchy(device.hierarchy(force=True))
        tabs = []
        for e in flat:
            if "Button" not in (e.get("class") or ""):
                continue
            t = (e.get("text") or "").strip()
            if not t:
                continue
            if 150 <= _y1(e.get("bounds")) <= 400:
                tabs.append(e)
        print("Ytabs", [e.get("text") for e in tabs])
        found = next(
            (e for e in tabs if (e.get("text") or "").casefold() in ALIASES), None
        )
        if found:
            print("FOUND", found.get("text"), found.get("bounds"))
            device.tap(*_center(found.get("bounds")))
            time.sleep(1.2)
            tree = device.hierarchy(force=True)
            flat = flatten_hierarchy(tree)
            SAMPLES.mkdir(parents=True, exist_ok=True)
            (SAMPLES / "45_search_tags_tab.flat.json").write_text(
                json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (SAMPLES / "45_search_tags_tab.hierarchy.json").write_text(
                json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print("SAVED n=", len(flat))
            for e in flat:
                rid = e.get("resource-id") or ""
                text = (e.get("text") or "").strip()
                desc = (e.get("content-desc") or "").strip()
                blob = f"{rid} {text} {desc}".lower()
                if any(k in blob for k in ("#", "hashtag", "keyword", "tag")) and (
                    text or "hashtag" in rid.lower() or "keyword" in rid.lower()
                ):
                    print(
                        "ROW",
                        rid.split("/")[-1],
                        "|",
                        text[:70],
                        "|",
                        desc[:40],
                    )
            return 0

        bar = None
        for e in flat:
            if (e.get("resource-id") or "").endswith("scrollable_tab_layout"):
                bar = e.get("bounds")
                break
        if not bar:
            print("no tab bar — stop")
            SAMPLES.mkdir(parents=True, exist_ok=True)
            tree = device.hierarchy(force=True)
            flat = flatten_hierarchy(tree)
            (SAMPLES / "45_search_tags_tab_MISS.flat.json").write_text(
                json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return 2
        x1, y1, x2, y2 = _parse(bar)
        y = (y1 + y2) // 2
        device.swipe(
            int(x1 + (x2 - x1) * 0.85),
            y,
            int(x1 + (x2 - x1) * 0.15),
            y,
            duration_ms=280,
        )
        time.sleep(0.5)

    print("NOT FOUND")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
