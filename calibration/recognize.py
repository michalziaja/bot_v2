"""Lightweight screen recognizer + popup detector + recovery skeleton.

Uses the calibration map (fingerprints) — matches by resource-id presence.
Doesn't need to know the layout, only ids. Fast and jitter-proof."""
from __future__ import annotations
import json, time, urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:20242/api/android/R58M74B5FRN"
MAP_PATH = Path(__file__).with_name("SM_A505FN_draft.json")

# ---- HTTP ----
def _req(method, path, body=None, raw=False, timeout=15):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
        if raw: return b
        if not b: return {}
        try:    return json.loads(b)
        except: return {"_raw": b.decode(errors="replace")}

def hierarchy(): return _req("GET", "/hierarchy")
def cmd(name, params=None): return _req("POST", f"/command/{name}", body=params or {})
def shell(c):    return _req("POST", "/shell", body={"command": c})

# ---- flat traversal ----
def _walk(node, out):
    p = node.get("properties") or {}
    if p:
        out.append({
            "class":        p.get("class"),
            "resource-id":  p.get("resource-id"),
            "content-desc": p.get("content-desc"),
            "text":         p.get("text"),
            "bounds":       p.get("bounds"),
            "package":      p.get("package"),
        })
    for c in node.get("children") or []:
        _walk(c, out)

def snapshot():
    """Return (id_set, flat, package). One dump call."""
    h = hierarchy()
    flat = []
    _walk(h, flat)
    ids = {n["resource-id"] for n in flat if n.get("resource-id")}
    pkgs = [n.get("package") for n in flat if n.get("package")]
    pkg = max(set(pkgs), key=pkgs.count) if pkgs else None
    return ids, flat, pkg

# ---- popup / sheet / overlay detection ----
# Overlays: strict allowlist of ids that ONLY appear during real overlays.
# Never use generic tokens like "bottom_sheet" — IG reels tree contains them permanently.
POPUP_HINTS = {
    "android_alert":       ["android:id/button1", "android:id/alertTitle"],
    "system_perm_dialog":  ["com.android.permissioncontroller:id/permission_message",
                            "com.android.permissioncontroller:id/permission_allow_button"],
    "ig_dialog":           ["ig_dialog_positive_button", "ig_dialog_negative_button"],
    "rate_limit":          ["action_try_later", "rate_limit_dialog"],
    "suspicious_login":    ["challenge_two_factor", "suspicious_login"],
    "save_login_prompt":   ["save_login_prompt", "not_now_text"],
    "share_sheet":         ["bs_action_sheet_row", "share_sheet_container"],
}

# Packages that are legitimate overlays over IG (keyboards, IMEs) — not "foreign apps"
IME_HINTS = ("keyboard", "inputmethod", "ime", "honeyboard", "swiftkey", "gboard")
BENIGN_PKG = ("instagram", "systemui", "launcher")

def detect_overlay(ids, pkg):
    for name, hints in POPUP_HINTS.items():
        if any(any(h in i for i in ids) for h in hints):
            return name
    if pkg:
        pl = pkg.lower()
        if any(k in pl for k in IME_HINTS):
            return "keyboard"
        if not any(k in pl for k in BENIGN_PKG):
            return f"foreign_app:{pkg}"
    return None

# ---- fingerprint match ----
def load_map():
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))

def recognize(ids, mp=None):
    """Return list of (screen, hits) sorted by score.
    Screens with 'wins_over' entries beat their listed base screens on tie/near-tie."""
    mp = mp or load_map()
    results = []
    for screen, fp in mp["fingerprints"].items():
        must = fp.get("must_have_any") or []
        hits = [m for m in must if m in ids]
        if hits:
            results.append((screen, hits, fp.get("wins_over") or []))
    # Remove base screens that are beaten by any present overlay
    matched_names = {s for s, _, _ in results}
    suppressed = set()
    for s, _, wins in results:
        for beaten in wins:
            if beaten in matched_names:
                suppressed.add(beaten)
    results = [(s, h) for s, h, _ in results if s not in suppressed]
    results.sort(key=lambda t: -len(t[1]))
    return results

def identify(verbose=False):
    """One-shot: dump → try to recognize → return dict describing state."""
    ids, flat, pkg = snapshot()
    overlay = detect_overlay(ids, pkg)
    matches = recognize(ids)
    state = {
        "package":  pkg,
        "overlay":  overlay,
        "matches":  [(s, len(h)) for s, h in matches[:3]],
        "top":      matches[0][0] if matches else None,
        "top_hits": matches[0][1] if matches else [],
        "ambiguous": len(matches) > 1 and matches[0] and matches[1] and len(matches[0][1]) == len(matches[1][1]),
        "unknown":  not matches,
    }
    if verbose:
        _print_state(state)
    return state

def _print_state(s):
    if s["overlay"]:
        print(f"  OVERLAY: {s['overlay']}  (top screen under: {s['top']})")
    elif s["unknown"]:
        print(f"  UNKNOWN  pkg={s['package']}")
    elif s["ambiguous"]:
        print(f"  AMBIGUOUS  {s['matches']}")
    else:
        print(f"  {s['top']}  hits={len(s['top_hits'])}  pkg={s['package']}")

# ---- recovery ladder ----
def recover(target_screen="home_feed", max_steps=5):
    """Escalation:
       1. detect overlay → dismiss with press_back once
       2. if foreign app → monkey relaunch IG, feed_tab
       3. if IG but unknown → press_back up to 3 times, re-check
       4. if still unknown → HOME key + monkey relaunch
       (5. AI Vision recovery_identify — hook here later)
    """
    for step in range(max_steps):
        s = identify(verbose=True)
        if s["top"] == target_screen and not s["overlay"]:
            print(f"[recover] reached {target_screen} after {step} steps"); return True
        if s["overlay"]:
            print(f"[recover] dismiss overlay: back"); cmd("back"); time.sleep(1.2); continue
        if s["package"] and "instagram" not in s["package"]:
            print(f"[recover] foreign app → relaunch IG")
            shell("monkey -p com.instagram.android -c android.intent.category.LAUNCHER 1")
            time.sleep(3); continue
        if s["unknown"] or s["ambiguous"]:
            print(f"[recover] press back (try {step+1})"); cmd("back"); time.sleep(1.2); continue
        if s["top"] != target_screen:
            print(f"[recover] on {s['top']}, need {target_screen} → tap home tab")
            cmd("tap", {"x": 108, "y": 2277}); time.sleep(1.2); continue
    print("[recover] gave up"); return False

# ---- live loop ----
def watch(interval=2.0):
    """Sample screen every `interval` s; print only when changes."""
    print(f"watching every {interval}s — Ctrl+C to stop\n")
    last_key = None
    while True:
        try:
            s = identify()
            key = (s["overlay"] or s["top"] or "UNKNOWN") + f"|{s['package']}"
            if key != last_key:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] ", end="")
                _print_state(s)
                last_key = key
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped"); return
        except Exception as e:
            print(f"[error] {e}"); time.sleep(interval)

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "watch"
    if   mode == "watch":     watch(float(sys.argv[2]) if len(sys.argv)>2 else 2.0)
    elif mode == "identify":  _print_state(identify())
    elif mode == "recover":   recover(sys.argv[2] if len(sys.argv)>2 else "home_feed")
    else: print("usage: recognize.py [watch|identify|recover] [args]")
