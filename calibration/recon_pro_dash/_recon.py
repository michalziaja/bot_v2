"""Helper recon panelu profesjonalnego — jobsaver, bez scrcpy.

Komentarze PL. Nie startuje drugiego scrcpy; korzysta z istniejącego uiauto.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.core.device import UiAuto2Device
from v2.core.device_lock import DeviceBusy, DeviceLock
from v2.core.screen_map import flatten_hierarchy

SERIAL = "R58M74B5FRN"
PKG = "com.instagram.androia"
BASE = "http://127.0.0.1:20242"
OUT = Path(__file__).resolve().parent
W, H = 1080, 2340


def connect() -> UiAuto2Device:
    return UiAuto2Device(serial=SERIAL, base_url=BASE, ig_package=PKG)


def acquire_lock() -> DeviceLock:
    """Zajmij serial jobsavera — stary lock technature699 jest martwy."""
    lock = DeviceLock(Path("/home/michal/bot_v2/data"), SERIAL)
    lock.acquire(owner="jobsaver", session_id="recon-pro-dash")
    return lock


def parse_bounds(b):
    """bounds bywa stringiem [x1,y1][x2,y2] albo listą."""
    if isinstance(b, (list, tuple)) and len(b) == 4:
        return [int(x) for x in b]
    if not b:
        return None
    s = str(b).replace("][", ",").replace("[", "").replace("]", "")
    parts = [int(x) for x in s.split(",") if x.strip()]
    return parts if len(parts) == 4 else None


def center(bounds) -> tuple[int, int] | None:
    xy = parse_bounds(bounds)
    if not xy:
        return None
    x1, y1, x2, y2 = xy
    return (x1 + x2) // 2, (y1 + y2) // 2


def wake(dev: UiAuto2Device) -> str:
    if (dev.dumpsys_power() or "").strip() and dev.screen_is_on():
        return "already_on"
    dev.wake_up_key()
    time.sleep(0.8)
    if dev.screen_is_on():
        return "wake_up_key"
    dev.press_power()
    time.sleep(0.8)
    return "power" if dev.screen_is_on() else "FAIL"


def unlock(dev: UiAuto2Device) -> str:
    if dev.screen_is_unlocked():
        return "already_unlocked"
    # swipe w górę — jak system.unlock
    dev.swipe(W // 2, int(H * 0.85), W // 2, int(H * 0.17), duration_ms=250)
    time.sleep(0.8)
    return "swipe_up" if dev.screen_is_unlocked() else "FAIL_maybe_pin"


def dump(dev: UiAuto2Device, name: str, note: str = "") -> dict:
    """Zapisz hierarchię + screenshot + skrót widocznych tekstów."""
    time.sleep(0.4)
    hier = dev.hierarchy(force=True)
    jpg = dev.screenshot()
    (OUT / f"{name}.hierarchy.json").write_text(
        json.dumps(hier, ensure_ascii=False), encoding="utf-8")
    (OUT / f"{name}.jpg").write_bytes(jpg)
    flat = flatten_hierarchy(hier)
    slim = []
    for n in flat:
        t = (n.get("text") or "").strip()
        d = (n.get("content-desc") or "").strip()
        rid = (n.get("resource-id") or "").strip()
        if not (t or d or (rid and n.get("clickable"))):
            continue
        slim.append({
            "text": t, "desc": d, "rid": rid,
            "click": bool(n.get("clickable")),
            "cls": (n.get("class") or "").rsplit(".", 1)[-1],
            "bounds": n.get("bounds"),
            "pkg": n.get("package"),
        })
    meta = {
        "name": name,
        "note_pl": note,
        "pkg": dev.current_pkg(),
        "activity": _activity(dev),
        "nodes": slim,
    }
    (OUT / f"{name}.slim.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[dump] {name}  pkg={meta['pkg']}  act={meta['activity']}  "
          f"nodes={len(slim)}  jpg={len(jpg)}")
    return meta


def _activity(dev: UiAuto2Device) -> str:
    try:
        return (dev._req("GET", "/current_activity") or {}).get("activity") or ""
    except Exception:
        out = dev.shell("dumpsys window | grep mCurrentFocus | head -1")
        return out


def find_tap(flat, *, rid=None, text=None, desc=None, contains=None):
    """Znajdź pierwszy klikalny (albo z tekstem) węzeł."""
    for n in flat:
        r = (n.get("resource-id") or "")
        t = (n.get("text") or "")
        d = (n.get("content-desc") or "")
        blob = f"{t} {d}".lower()
        if rid and rid not in r:
            continue
        if text and text.lower() not in t.lower():
            continue
        if desc and desc.lower() not in d.lower():
            continue
        if contains and contains.lower() not in blob:
            continue
        c = center(n.get("bounds"))
        if c:
            return n, c
    return None, None


def tap_node(dev, n) -> bool:
    c = center(n.get("bounds") if isinstance(n, dict) else None)
    if not c:
        return False
    dev.tap(*c)
    time.sleep(1.2)
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        d = connect()
        print("pkg", d.current_pkg())
        print("screen_on", d.screen_is_on(), "unlocked", d.screen_is_unlocked())
        print("power", d.dumpsys_power())
        print("ver", d.ig_version(PKG))
    else:
        print("usage: status")
