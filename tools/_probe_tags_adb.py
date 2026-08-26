"""Surowy ADB dump Tags — bez scrcpy/build_context (klon androjb wypada z FG)."""
from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "calibration" / "samples"
PKG = "com.instagram.android"
TMP_XML = "/data/local/tmp/uidump_tags.xml"


def sh(*args: str) -> str:
    r = subprocess.run(["adb", "shell", *args], capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


def adb(*args: str) -> str:
    r = subprocess.run(["adb", *args], capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


def focus() -> str:
    out = sh("dumpsys", "window")
    m = re.search(r"mCurrentFocus=Window\{[^ ]+ u0 ([^/]+)/([^ }]+)", out)
    return f"{m.group(1)}/{m.group(2)}" if m else "?"


def dump_xml() -> ET.Element | None:
    sh("uiautomator", "dump", TMP_XML)
    local = Path(__file__).resolve().parent / "_uidump_tags.xml"
    adb("pull", TMP_XML, str(local))
    if not local.exists() or local.stat().st_size < 50:
        print("dump failed", local)
        return None
    return ET.parse(local).getroot()


def buttons(root: ET.Element) -> list[tuple[str, str]]:
    out = []
    for n in root.iter("node"):
        cls = n.get("class") or ""
        text = (n.get("text") or "").strip()
        if "Button" in cls and text:
            out.append((text, n.get("bounds") or ""))
    return out


def center(bounds: str):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def flat_nodes(root: ET.Element) -> list[dict]:
    rows = []
    for n in root.iter("node"):
        rows.append({
            "class": n.get("class") or "",
            "resource-id": n.get("resource-id") or "",
            "content-desc": n.get("content-desc") or "",
            "text": n.get("text") or "",
            "bounds": n.get("bounds") or "",
            "package": n.get("package") or "",
            "clickable": (n.get("clickable") or "").lower() == "true",
            "selected": (n.get("selected") or "").lower() == "true",
        })
    return rows


def main() -> int:
    print("focus0", focus())
    # upewnij się że IG main żyje
    adb("shell", "am", "start", "-n",
        f"{PKG}/com.instagram.mainactivity.InstagramMainActivity")
    time.sleep(3)
    print("focus1", focus())

    # search tab
    sh("input", "tap", "756", "2277")
    time.sleep(1.5)
    print("focus2", focus())

    # focus input + type + enter
    sh("input", "tap", "587", "155")
    time.sleep(0.6)
    sh("input", "text", "fitness")
    time.sleep(0.8)
    sh("input", "keyevent", "66")
    time.sleep(2.0)
    print("focus3", focus())

    root = dump_xml()
    if root is None:
        return 1
    tabs = buttons(root)
    print("tabs0", [t[0] for t in tabs])

    aliases = {"znaczniki", "tags", "tagi"}
    found = None
    for swipe in range(7):
        for text, bounds in tabs:
            if text.casefold() in aliases:
                found = (text, bounds)
                break
        if found:
            break
        # swipe left on tab bar (~y 288 z sample 44)
        sh("input", "swipe", "900", "288", "180", "288", "280")
        time.sleep(0.5)
        root = dump_xml()
        if root is None:
            return 1
        tabs = buttons(root)
        print(f"tabs{swipe+1}", [t[0] for t in tabs])

    if not found:
        # zapisz miss
        import json
        OUT.mkdir(parents=True, exist_ok=True)
        flat = flat_nodes(root)
        (OUT / "45_search_tags_tab_MISS.flat.json").write_text(
            json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
        print("MISS — brak Znaczniki")
        return 2

    print("FOUND", found)
    c = center(found[1])
    if c:
        sh("input", "tap", str(c[0]), str(c[1]))
        time.sleep(1.0)

    root = dump_xml()
    if root is None:
        return 1
    import json
    OUT.mkdir(parents=True, exist_ok=True)
    flat = flat_nodes(root)
    (OUT / "45_search_tags_tab.flat.json").write_text(
        json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
    # surowy XML też
    local = Path(__file__).resolve().parent / "_uidump_tags.xml"
    if local.exists():
        (OUT / "45_search_tags_tab.hierarchy.xml").write_bytes(local.read_bytes())

    print("tabs_final", [t[0] for t in buttons(root)])
    # interesujące wiersze
    for e in flat:
        rid = e["resource-id"]
        text = (e["text"] or "").strip()
        desc = (e["content-desc"] or "").strip()
        blob = f"{rid} {text} {desc}".lower()
        if any(k in blob for k in ("#", "hashtag", "keyword", "tag")) and (
                text or "hashtag" in rid.lower() or "keyword" in rid.lower()):
            print("ROW", rid.split("/")[-1], "|", text[:60], "|", desc[:40])
    print("saved 45_search_tags_tab.flat.json", "n=", len(flat))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
