"""Live mini-flow: profile stats snapshot + reels smoke.
Steps: open_profile → refresh → read_stats → return_home → open_reels → swipe_next_reel.
Each step confirms via fingerprint; failures escalate through the recovery ladder."""
from __future__ import annotations
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from recognize import (snapshot, recognize, detect_overlay, load_map,
                       cmd, shell, hierarchy)

MAP = load_map()
NAV = {  # tap centers taken from calibration anchors
    "home_feed":     (108,  2277),
    "reels_feed":    (324,  2277),
    "direct_inbox":  (540,  2277),
    "search_top":    (756,  2277),
    "profile_self":  (972,  2277),
}
NAV_TAP_BY_SCREEN = {  # which nav_tab returns us to a given screen
    "home_feed":     NAV["home_feed"],
    "reels_feed":    NAV["reels_feed"],
    "profile_self":  NAV["profile_self"],
    "search_top":    NAV["search_top"],
    "direct_inbox":  NAV["direct_inbox"],
}

# --- events log ---
EVENTS = []
def event(kind, **kw):
    e = {"t": time.strftime("%H:%M:%S"), "kind": kind, **kw}
    EVENTS.append(e)
    print(f"[{e['t']}] {kind:22s} " + " ".join(f"{k}={v}" for k,v in kw.items()))

# --- identify + ladder ---
def identify():
    ids, flat, pkg = snapshot()
    overlay = detect_overlay(ids, pkg)
    matches = recognize(ids, MAP)
    top = matches[0][0] if matches else None
    hits = len(matches[0][1]) if matches else 0
    return {"pkg": pkg, "overlay": overlay, "top": top, "hits": hits,
            "matches": matches, "flat": flat, "ids": ids}

BLOCKING_OVERLAYS = {"rate_limit", "suspicious_login", "challenge_two_factor"}

def ensure_screen(target, max_steps=6):
    """Recovery ladder — return True when at target with no overlay."""
    for step in range(max_steps):
        s = identify()
        event("identify",
              step=step, target=target, top=s["top"], hits=s["hits"],
              overlay=s["overlay"], pkg=s["pkg"])

        # L0 — clean match
        if s["top"] == target and not s["overlay"]:
            return True

        # blocking overlays — never dismiss
        if s["overlay"] in BLOCKING_OVERLAYS:
            event("blocking_overlay_abort", overlay=s["overlay"])
            return False

        # L1 — keyboard → back
        if s["overlay"] == "keyboard":
            event("recover.L1", action="back_dismiss_ime"); cmd("back"); time.sleep(0.9); continue

        # L2 — dismissable overlay → back
        if s["overlay"] and not s["overlay"].startswith("foreign_app"):
            event("recover.L2", action="back_dismiss_overlay", overlay=s["overlay"])
            cmd("back"); time.sleep(1.1); continue

        # L3 — foreign app → relaunch IG
        if s["overlay"] and s["overlay"].startswith("foreign_app"):
            event("recover.L3", action="monkey_relaunch")
            shell("monkey -p com.instagram.android -c android.intent.category.LAUNCHER 1")
            time.sleep(3); continue

        # L4 — IG but unknown/nothing
        if s["top"] is None:
            event("recover.L4", action="back_probe")
            cmd("back"); time.sleep(1.0); continue

        # L5 — wrong IG screen → tap the target's nav tab if known
        if target in NAV_TAP_BY_SCREEN:
            x, y = NAV_TAP_BY_SCREEN[target]
            event("recover.L5", action="tap_nav", target=target, xy=(x,y))
            cmd("tap", {"x": x, "y": y}); time.sleep(1.5); continue

        # nothing else to try
        event("recover.exhausted", top=s["top"]); return False

    event("recover.max_steps", target=target); return False

# --- primitive gestures ---
def tap_nav(screen): x,y = NAV[screen]; cmd("tap", {"x":x,"y":y}); time.sleep(1.4)
def swipe(x1,y1,x2,y2,duration_ms=350):
    shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}"); time.sleep(0.5)

# --- stats extraction ---
STAT_IDS = {
    "posts":     "com.instagram.android:id/profile_header_familiar_post_count_value",
    "followers": "com.instagram.android:id/profile_header_familiar_followers_value",
    "following": "com.instagram.android:id/profile_header_familiar_following_value",
}
def read_profile_stats(flat):
    got = {}
    for k, rid in STAT_IDS.items():
        for n in flat:
            if n.get("resource-id") == rid:
                raw = (n.get("text") or "").strip().replace(",", "").replace(" ", "")
                try:    got[k] = int(raw)
                except: got[k] = None
                break
        else:
            got[k] = None
    return got

# --- reel identity (to prove swipe changed the reel) ---
def read_reel_marker(flat):
    """Best-effort: author name / title / any distinctive text."""
    for rid in ("com.instagram.android:id/clips_author_username",
                "com.instagram.android:id/reel_viewer_title"):
        for n in flat:
            if n.get("resource-id") == rid and n.get("text"):
                return n["text"].strip()
    return None

# --- steps ---
def step_open_profile():
    event("step.start", name="open_profile")
    tap_nav("profile_self")
    return ensure_screen("profile_self")

def step_refresh_profile():
    event("step.start", name="refresh_profile")
    # pull-to-refresh — swipe down in the profile area, avoid the tabs
    swipe(540, 700, 540, 1500, duration_ms=400)
    time.sleep(1.5)  # let refresh spinner finish
    return ensure_screen("profile_self")

def step_read_stats():
    event("step.start", name="read_stats")
    if not ensure_screen("profile_self"): return None
    _, flat, _ = snapshot()
    stats = read_profile_stats(flat)
    event("stats", **stats)
    return stats

def step_return_home():
    event("step.start", name="return_home")
    tap_nav("home_feed")
    return ensure_screen("home_feed")

def step_open_reels():
    event("step.start", name="open_reels")
    tap_nav("reels_feed")
    return ensure_screen("reels_feed")

def step_swipe_next_reel():
    event("step.start", name="swipe_next_reel")
    if not ensure_screen("reels_feed"): return False
    _, flat_before, _ = snapshot()
    before = read_reel_marker(flat_before)
    event("reel_before", marker=before)
    swipe(540, 1800, 540, 800, duration_ms=300)
    time.sleep(1.5)
    if not ensure_screen("reels_feed"): return False
    _, flat_after, _ = snapshot()
    after = read_reel_marker(flat_after)
    event("reel_after", marker=after)
    changed = (before is None) or (after != before)
    event("swipe_result", changed=changed)
    return changed

# --- main ---
def main():
    ok = True
    # start: ensure we're somewhere in IG on home before beginning
    if not ensure_screen("home_feed"):
        event("prep_failed"); return
    event("prep_ok")

    if not step_open_profile():      event("FAIL", step="open_profile");     ok=False; return
    if not step_refresh_profile():   event("FAIL", step="refresh_profile");  ok=False
    stats = step_read_stats()
    if stats is None or all(v is None for v in stats.values()):
                                     event("FAIL", step="read_stats");       ok=False
    if not step_return_home():       event("FAIL", step="return_home");      ok=False; return
    if not step_open_reels():        event("FAIL", step="open_reels");       ok=False; return
    if not step_swipe_next_reel():   event("FAIL", step="swipe_next_reel");  ok=False

    event("DONE", ok=ok)
    # save events
    out = Path(__file__).with_name("mini_flow_last_run.json")
    out.write_text(json.dumps(EVENTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ log: {out}")

if __name__ == "__main__":
    main()
