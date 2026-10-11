"""Dump ekranu do recon logowania kasia21sum — hierarchia + flat + activity.

Komentarze PL. Nie startuje drugiego scrcpy; korzysta z uiauto z CLI.
Hasła IG nigdy nie zapisujemy ani nie drukujemy (redakcja w dumpach).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.config import credentials
from v2.core.screen_map import dominant_package, flatten_hierarchy

SERIAL = "R58M74B5FRN"
PKG = "com.instagram.androib"
STOCK_PKG = "com.instagram.android"
BASE = "http://127.0.0.1:20242"
OUT = Path(__file__).resolve().parent
W, H = 1080, 2340

# Komentarz: te same markery co cloner/job.py — pidof to NIE sukces launchu
_FOCUS_LINE_MARKERS = (
    "mResumedActivity",
    "mFocusedActivity",
    "topResumedActivity",
    "mCurrentFocus",
    "mFocusedApp",
)
_RE_PKG_ACT = re.compile(r"([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.$]+)")
_CRASH_HINT = "Resources$NotFoundException"
_CRASH_RES = "#0x7f082060"

# Komentarz: hasło ładujemy tylko do redakcji dumpów — nigdy na stdout
_CREDS = credentials.get("kasia21sum") or {}
_SECRETS = [s for s in (_CREDS.get("ig_password"),) if s]


def redact(obj):
    """Zamień hasło na ***REDACTED*** w drzewie JSON / stringach."""
    if not _SECRETS:
        return obj
    if isinstance(obj, str):
        out = obj
        for sec in _SECRETS:
            if sec and sec in out:
                out = out.replace(sec, "***REDACTED***")
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    return obj


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


def node_at(flat: list, x: int, y: int) -> dict | None:
    """Najmniejszy węzeł zawierający punkt tapnięcia."""
    best = None
    best_area = None
    for n in flat:
        b = parse_bounds(n.get("bounds"))
        if not b:
            continue
        x1, y1, x2, y2 = b
        if x1 <= x <= x2 and y1 <= y <= y2:
            area = max(1, (x2 - x1) * (y2 - y1))
            if best_area is None or area < best_area:
                best, best_area = n, area
    return best


def _shot_ext(data: bytes) -> str:
    """jpg vs png po magicznych bajtach."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "jpg"


def visible_texts(flat: list) -> list[str]:
    """Unikalne widoczne teksty / content-desc (bez pustych)."""
    out: list[str] = []
    seen: set[str] = set()
    for n in flat:
        for key in ("text", "content-desc"):
            t = (n.get(key) or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
    return out


def visible_rids(flat: list) -> list[str]:
    """Unikalne resource-id z drzewa."""
    out: list[str] = []
    seen: set[str] = set()
    for n in flat:
        rid = (n.get("resource-id") or "").strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
    return out


def slim_nodes(flat: list) -> list[dict]:
    """Skrót klikalnych / z tekstem — do logu i PLAYBOOK."""
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
    return slim


def _pick_focus_lines(blob: str) -> list[str]:
    """Linie focused/resumed z dumpsys — bez recents."""
    hits = []
    for raw in (blob or "").splitlines():
        line = raw.strip()
        if any(m in line for m in _FOCUS_LINE_MARKERS):
            hits.append(line)
    return hits


def _parse_pkg_act(lines: list[str]) -> tuple[str, str]:
    """Pierwszy pakiet/activity z linii focus (nie NotificationShade)."""
    for line in lines:
        m = _RE_PKG_ACT.search(line)
        if m:
            return m.group(1), m.group(2)
    return "", ""


def package_owns_resumed(package: str, dump: str) -> str:
    """Linia focused/resumed klona albo pusty string (pidof nie wystarczy)."""
    if not package or not dump:
        return ""
    needle = f"{package}/"
    for raw in dump.splitlines():
        line = raw.strip()
        if needle not in line:
            continue
        if any(m in line for m in _FOCUS_LINE_MARKERS):
            return line
    return ""


def dump_activity(dev) -> dict:
    """mCurrentFocus / mResumedActivity + pidof (pidof tylko diagnostyka)."""
    win = ""
    act = ""
    try:
        win = dev.shell(
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' | head -8"
        )
    except Exception as e:
        win = f"<err window: {e}>"
    try:
        act = dev.shell(
            "dumpsys activity activities | grep -E "
            "'mResumedActivity|topResumedActivity|mFocusedActivity' | head -8"
        )
    except Exception as e:
        act = f"<err activity: {e}>"
    blob = f"{win}\n{act}"
    lines = _pick_focus_lines(blob)
    focused_pkg, focused_act = _parse_pkg_act(lines)
    try:
        pid_clone = (dev.shell(f"pidof {PKG}") or "").strip()
    except Exception:
        pid_clone = ""
    try:
        pid_stock = (dev.shell(f"pidof {STOCK_PKG}") or "").strip()
    except Exception:
        pid_stock = ""
    try:
        current_pkg = dev.current_pkg()
    except Exception:
        current_pkg = None
    resumed = package_owns_resumed(PKG, blob)
    return redact({
        "mCurrentFocus": next((l for l in lines if "mCurrentFocus" in l), ""),
        "mFocusedApp": next((l for l in lines if "mFocusedApp" in l), ""),
        "mResumedActivity": next((l for l in lines if "mResumedActivity" in l), ""),
        "topResumedActivity": next((l for l in lines if "topResumedActivity" in l), ""),
        "focus_lines": lines,
        "focused_package": focused_pkg,
        "focused_activity": focused_act,
        "current_pkg": current_pkg,
        "pidof_clone": pid_clone,
        "pidof_stock": pid_stock,
        "clone_owns_focus": bool(resumed),
        "clone_resumed_line": resumed,
        "stock_on_focus": STOCK_PKG in (focused_pkg or "") and focused_pkg == STOCK_PKG,
        "raw_window": win,
        "raw_activity": act,
        "note_pl": "sukces launchu = clone_owns_focus, NIE pidof",
    })


def extract_fatal_logcat(text: str) -> str:
    """Wycinek FATAL / InflateException / Resources$NotFoundException."""
    if not text:
        return ""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ("FATAL EXCEPTION" in ln or "InflateException" in ln
                or _CRASH_HINT in ln):
            start = i
            break
    if start is None:
        art = [ln for ln in lines if "AndroidRuntime" in ln]
        return "\n".join(art[-40:]) if art else ""
    return "\n".join(lines[start: min(len(lines), start + 50)])


def dump_fatal_logcat(dev) -> str:
    """logcat AndroidRuntime + crash buffer — pusto gdy cisza."""
    raw = ""
    try:
        raw = dev.shell("logcat -d -t 300 -s AndroidRuntime:E") or ""
    except Exception as e:
        raw = f"<err logcat runtime: {e}>"
    fatal = extract_fatal_logcat(raw)
    if fatal:
        return redact(fatal)
    crash = ""
    try:
        crash = dev.shell("logcat -d -t 100 -b crash") or ""
    except Exception as e:
        crash = f"<err logcat crash: {e}>"
    extra = extract_fatal_logcat(crash)
    return redact(extra or (crash.strip()[:2500] if crash else ""))


def clear_logcat(dev) -> None:
    """Czyść logcat przed launch — FATAL ma być z tego odpalenia."""
    try:
        dev.shell("logcat -c")
    except Exception:
        pass


def detect_special_screen(texts: list[str]) -> dict:
    """Captcha / suspicious / kod — tylko detekcja, bez omijania."""
    blob = " ".join(texts).lower()
    return {
        "verification_code": any(h in blob for h in (
            "wprowadź kod", "wprowadz kod", "enter code",
            "sprawdź pocztę", "sprawdz poczte", "kod potwierdz",
            "kod wysłany", "kod wyslany",
        )),
        "captcha": any(h in blob for h in (
            "captcha", "nie jestem robotem", "i'm not a robot",
            "sprawdzamy, czy to ty",
            "wpisz widoczne znaki", "wpisz znaki",
            "real human behind this login",
            "wygeneruj nowy kod",
        )),
        "suspicious_login": any(h in blob for h in (
            "podejrzan", "suspicious", "nie rozpoznajemy",
            "to nie ja", "confirm it's you", "confirm its you",
            "challenge", "nietypowe logowanie",
        )),
        "splash_crash_hint": False,
    }


def dump(dev, name: str, note: str = "", ctx=None, extra: dict | None = None) -> dict:
    """Zapisz hierarchię + flat + activity + screenshot + skrót."""
    time.sleep(0.35)
    hier: dict = {}
    hier_err = ""
    try:
        hier = dev.hierarchy(force=True) or {}
    except Exception as e:
        hier_err = str(e)
        hier = {"_error": hier_err}
    hier = redact(hier)
    (OUT / f"{name}.hierarchy.json").write_text(
        json.dumps(hier, ensure_ascii=False), encoding="utf-8")

    # Komentarz: błąd hierarchii → pusta lista, nie ukrywamy wyjątku w extra
    try:
        flat = flatten_hierarchy(hier) if isinstance(hier, dict) else []
    except Exception:
        flat = []
    if not isinstance(flat, list):
        flat = []
    flat = redact(flat)
    (OUT / f"{name}.flat.json").write_text(
        json.dumps(flat, ensure_ascii=False), encoding="utf-8")

    activity = dump_activity(dev)
    (OUT / f"{name}.activity.json").write_text(
        json.dumps(activity, ensure_ascii=False, indent=2), encoding="utf-8")

    shot_name = ""
    shot_len = 0
    try:
        jpg = dev.screenshot() or b""
        shot_len = len(jpg)
        ext = _shot_ext(jpg)
        shot_name = f"{name}.{ext}"
        (OUT / shot_name).write_bytes(jpg)
    except Exception as e:
        shot_name = f"{name}.screenshot_error.txt"
        (OUT / shot_name).write_text(str(e), encoding="utf-8")

    identify_top = None
    overlay = None
    if ctx is not None:
        try:
            st = ctx.navigator.identify()
            identify_top = st.top
            overlay = st.overlay
        except Exception as e:
            identify_top = f"<identify err: {e}>"

    texts = visible_texts(flat)
    rids = visible_rids(flat)
    special = detect_special_screen(texts)
    fatal = ""
    # Komentarz: FATAL tylko gdy launch nie trzyma focusu albo widać crash
    if (not activity.get("clone_owns_focus")) or extra and extra.get("want_fatal"):
        fatal = dump_fatal_logcat(dev)
    if fatal and (_CRASH_HINT in fatal or _CRASH_RES in fatal):
        special["splash_crash_hint"] = True

    meta = redact({
        "name": name,
        "note_pl": note,
        "pkg": activity.get("current_pkg"),
        "identify_top": identify_top,
        "overlay": overlay,
        "focused_package": activity.get("focused_package"),
        "focused_activity": activity.get("focused_activity"),
        "dominant_package": dominant_package(flat),
        "clone_owns_focus": activity.get("clone_owns_focus"),
        "visible_texts": texts[:80],
        "resource_ids": rids[:80],
        "nodes": slim_nodes(flat),
        "special": special,
        "fatal_logcat": fatal,
        "screenshot": shot_name,
        "screenshot_bytes": shot_len,
        "hierarchy_error": hier_err,
        "extra": extra or {},
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    (OUT / f"{name}.slim.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[dump] {name}  focus={meta.get('focused_package')}/"
        f"{meta.get('focused_activity')}  top={identify_top}  "
        f"dom={meta.get('dominant_package')}  nodes={len(meta.get('nodes') or [])}  "
        f"clone_focus={meta.get('clone_owns_focus')}  shot={shot_name}",
        flush=True,
    )
    return meta


class ActionRecorder:
    """Zapis tap/swipe z HumanTouch — 'co kliknięto' do STEP_MAP."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.events: list[dict] = []
        self._last_flat: list = []
        self._ht_tap = ctx.ht.tap
        self._ht_firm = ctx.ht.firm_tap
        self._ht_swipe = ctx.ht.swipe
        self._dev_tap = ctx.device.tap

    def refresh_flat(self) -> None:
        try:
            hier = self.ctx.device.hierarchy()
            self._last_flat = flatten_hierarchy(hier)
        except Exception:
            self._last_flat = []

    def install(self) -> None:
        rec = self

        def tap(x, y, pause_kind="tap"):
            rec._record_tap("ht.tap", x, y)
            return rec._ht_tap(x, y, pause_kind=pause_kind)

        def firm_tap(x, y, hold_ms=140, pause_kind="tap"):
            rec._record_tap("ht.firm_tap", x, y)
            return rec._ht_firm(x, y, hold_ms=hold_ms, pause_kind=pause_kind)

        def swipe(x1, y1, x2, y2, duration_ms=None, pause_kind="swipe"):
            rec.events.append(redact({
                "kind": "ht.swipe",
                "from": [int(x1), int(y1)],
                "to": [int(x2), int(y2)],
            }))
            return rec._ht_swipe(x1, y1, x2, y2,
                                 duration_ms=duration_ms, pause_kind=pause_kind)

        def dev_tap(x, y):
            rec._record_tap("device.tap", x, y)
            return rec._dev_tap(x, y)

        rec.ctx.ht.tap = tap  # type: ignore[method-assign]
        rec.ctx.ht.firm_tap = firm_tap  # type: ignore[method-assign]
        rec.ctx.ht.swipe = swipe  # type: ignore[method-assign]
        rec.ctx.device.tap = dev_tap  # type: ignore[method-assign]

    def _record_tap(self, kind: str, x: int, y: int) -> None:
        self.refresh_flat()
        n = node_at(self._last_flat, int(x), int(y))
        label = ""
        rid = ""
        if n:
            label = (n.get("text") or n.get("content-desc") or "").strip()
            rid = (n.get("resource-id") or "").strip()
        self.events.append(redact({
            "kind": kind,
            "xy": [int(x), int(y)],
            "text": label,
            "rid": rid,
            "cls": (n.get("class") if n else "") or "",
        }))
        shown = label or rid or f"{x},{y}"
        print(f"  [click] {kind} ({x},{y}) → {shown}", flush=True)

    def pop_events(self) -> list[dict]:
        ev = list(self.events)
        self.events.clear()
        return ev


def append_jsonl(path: Path, rec: dict) -> None:
    """Jedna linia logu recon (append-only)."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(redact(rec), ensure_ascii=False) + "\n")
