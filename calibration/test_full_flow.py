"""Full test flow — wake device → unlock → launch IG → home → profile → refresh
                    → home → close IG → sleep screen.

Verbose narration to terminal at every step.
Pauses documented inline (`# PAUSE: <what/why>`) — in the real bot these come
from the humanizer profile, here they are hardcoded but marked.

Run:  python test_full_flow.py
"""
from __future__ import annotations
import sys, time, json, random
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from recognize import (snapshot, recognize, detect_overlay, load_map,
                       cmd, shell)

SERIAL   = "R58M74B5FRN"
MAP      = load_map()
IG_PKG   = "com.instagram.android"
NAV      = {"home_feed": (108, 2277), "profile_self": (972, 2277),
            "reels_feed": (324, 2277), "search_top": (756, 2277),
            "direct_inbox": (540, 2277)}

# ============================================================
# UI helpers — chatty logging
# ============================================================
STEP_NUM = [0]

def _ts(): return time.strftime("%H:%M:%S")

def hdr(title):
    STEP_NUM[0] += 1
    print()
    print("=" * 68)
    print(f"  KROK {STEP_NUM[0]}  |  {title}")
    print("=" * 68)

def say(msg):        print(f"[{_ts()}]   {msg}")
def ok(msg):         print(f"[{_ts()}]   OK   {msg}")
def warn(msg):       print(f"[{_ts()}]   !    {msg}")
def fail(msg):       print(f"[{_ts()}]   FAIL {msg}")

def pause(seconds, why=""):
    """Wait `seconds` (float). Print reason so user knows why we're idle."""
    if why: say(f"pauza {seconds:.1f}s ({why})")
    time.sleep(seconds)

# ============================================================
# Device primitives
# ============================================================
def sh(command):
    """Run adb shell command; return stdout stripped."""
    r = shell(command)
    return (r.get("output") or "").strip() if isinstance(r, dict) else ""

def screen_is_on():
    """Read Display Power state from dumpsys."""
    out = sh("dumpsys power | grep -E 'Display Power|mWakefulness='")
    #   Display Power: state=ON  |  mWakefulness=Awake
    is_on = "state=ON" in out or "Awake" in out
    say(f"stan ekranu: {'WŁĄCZONY' if is_on else 'WYGASZONY'}  (raw: {out.strip() or 'brak'})")
    return is_on

def screen_is_unlocked():
    """Detect lockscreen via keyguard state."""
    out = sh("dumpsys window | grep -E 'mDreamingLockscreen|mShowingLockscreen|mKeyguardShowing'")
    locked_hints = ("mShowingLockscreen=true", "mKeyguardShowing=true", "mDreamingLockscreen=true")
    is_locked = any(h in out for h in locked_hints)
    say(f"blokada: {'ZABLOKOWANY' if is_locked else 'ODBLOKOWANY'}  (raw: {out.strip() or 'brak'})")
    return not is_locked

def wake_up():
    say("wysyłam KEYCODE_WAKEUP")
    sh("input keyevent 224")

def press_power():
    say("wysyłam KEYCODE_POWER (usypianie)")
    sh("input keyevent 26")

def press_home():
    say("wysyłam KEYCODE_HOME")
    sh("input keyevent 3")

def swipe(x1, y1, x2, y2, duration_ms=350, why=""):
    if why: say(f"swipe {why}: ({x1},{y1}) → ({x2},{y2}) w {duration_ms}ms")
    sh(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")

def tap(x, y, why=""):
    if why: say(f"tap {why} @ ({x},{y})")
    cmd("tap", {"x": x, "y": y})

def launch_ig():
    say(f"uruchamiam Instagram (monkey → {IG_PKG})")
    sh(f"monkey -p {IG_PKG} -c android.intent.category.LAUNCHER 1")

def force_stop_ig():
    say(f"zamykam Instagram (am force-stop {IG_PKG})")
    sh(f"am force-stop {IG_PKG}")

def current_pkg():
    """Foreground package name."""
    out = sh("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' | head -1")
    # try to extract package
    import re
    m = re.search(r"([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.]+", out)
    return m.group(1) if m else None

# ============================================================
# Screen recognition wrappers
# ============================================================
def identify():
    ids, flat, pkg = snapshot()
    overlay = detect_overlay(ids, pkg)
    matches = recognize(ids, MAP)
    return {"pkg": pkg, "overlay": overlay,
            "top": matches[0][0] if matches else None,
            "hits": len(matches[0][1]) if matches else 0,
            "flat": flat}

def wait_for_screen(target, timeout_s=15.0, poll=0.7):
    """Poll identify() until top == target and no overlay, or timeout.
    Returns True on success. Doesn't try to recover — for that see mini_flow.ensure_screen."""
    say(f"czekam aż ekran = {target} (timeout {timeout_s}s)")
    deadline = time.time() + timeout_s
    last_top = None
    while time.time() < deadline:
        s = identify()
        if s["top"] != last_top:
            say(f"  widzę: top={s['top']!s:22s} hits={s['hits']} overlay={s['overlay']!s:12s} pkg={s['pkg']}")
            last_top = s["top"]
        if s["top"] == target and not s["overlay"]:
            return True
        time.sleep(poll)
    return False

# ============================================================
# High-level steps
# ============================================================
STAT_IDS = {
    "posts":     f"{IG_PKG}:id/profile_header_familiar_post_count_value",
    "followers": f"{IG_PKG}:id/profile_header_familiar_followers_value",
    "following": f"{IG_PKG}:id/profile_header_familiar_following_value",
}
def read_profile_stats(flat):
    out = {}
    for k, rid in STAT_IDS.items():
        val = None
        for n in flat:
            if n.get("resource-id") == rid:
                raw = (n.get("text") or "").strip().replace(",", "").replace(" ", "")
                try: val = int(raw)
                except: pass
                break
        out[k] = val
    return out

# ============================================================
# THE FLOW
# ============================================================
def main():
    print("\n" + "#" * 68)
    print(f"#  TEST FLOW  |  urządzenie {SERIAL}  |  start {time.strftime('%H:%M:%S')}")
    print("#" * 68)

    # --------------------------------------------------------
    hdr("Sprawdzenie ekranu — czy się świeci")
    if not screen_is_on():
        warn("ekran wygaszony — budzę")
        wake_up()
        # PAUSE: 0.8s — animacja włączenia + wykrywanie fingerprint sensora
        pause(0.8, "animacja włączenia ekranu")
        if not screen_is_on():
            fail("nie udało się wybudzić ekranu — abort"); return
        ok("ekran wybudzony")
    else:
        ok("ekran już świeci — pomijam wake")

    # --------------------------------------------------------
    hdr("Sprawdzenie blokady — czy odblokowany")
    if not screen_is_unlocked():
        warn("zablokowany — odblokowuję (swipe up po pełnej wysokości)")
        # Samsung A50 (1080x2340): swipe od dołu do środka
        swipe(540, 2000, 540, 400, duration_ms=250, why="unlock swipe up")
        # PAUSE: 1.0s — animacja odblokowania + pierwsze wyrenderowanie launchera
        pause(1.0, "animacja odblokowania")
        if not screen_is_unlocked():
            warn("po swipe wciąż zablokowany — może wymagany PIN (nie obsłużone w tym teście)")
            fail("odblokowanie nieudane — abort"); return
        ok("odblokowany")
    else:
        ok("ekran już odblokowany")

    pause(0.8, "krótka pauza po wybudzeniu — jak człowiek by się orientował gdzie jest")

    # --------------------------------------------------------
    hdr("Uruchomienie Instagram")
    pkg = current_pkg()
    say(f"foreground obecnie: {pkg}")
    if pkg != IG_PKG:
        launch_ig()
        # PAUSE: 3.0s — splash screen + ładowanie feedu z sieci
        pause(3.0, "splash + ładowanie feedu")
    else:
        ok("IG już w foreground")

    # --------------------------------------------------------
    hdr("Potwierdzenie że jesteśmy na stronie głównej")
    if wait_for_screen("home_feed", timeout_s=12):
        ok("home_feed potwierdzony — startujemy właściwy flow")
    else:
        warn("home_feed nie wykryty — próbuję tap w home_tab")
        tap(*NAV["home_feed"], why="feed_tab (fallback)")
        pause(1.5, "settle po tapie w home")
        if not wait_for_screen("home_feed", timeout_s=8):
            fail("nie mogę dotrzeć do home_feed — abort"); return

    # PAUSE: człowiek chwilę patrzy na feed przed przejściem dalej
    pause(1.5, "przegląd feedu przed przejściem")

    # --------------------------------------------------------
    hdr("Wejście w profil")
    tap(*NAV["profile_self"], why="profile_tab")
    # PAUSE: 1.4s — nawigacja + załadowanie tabu profilu
    pause(1.4, "przejście do profilu")

    if not wait_for_screen("profile_self", timeout_s=8):
        fail("nie widzę profile_self — abort"); return
    ok("jesteśmy na profilu")

    # PAUSE: człowiek patrzy chwilę na swój profil
    pause(1.2, "chwila na profilu przed odświeżeniem")

    # --------------------------------------------------------
    hdr("Odświeżenie profilu (swipe down = pull-to-refresh)")
    swipe(540, 700, 540, 1500, duration_ms=400, why="pull-to-refresh na profilu")
    # PAUSE: 2.0s — animacja spinnera + fetch danych z sieci
    pause(2.0, "spinner refresh + fetch danych")
    if not wait_for_screen("profile_self", timeout_s=6):
        warn("po refresh straciliśmy profile_self — nietypowe")
    else:
        ok("odświeżony, wciąż na profile_self")

    # --------------------------------------------------------
    hdr("Odczyt statystyk profilu (posty / obserwujący / obserwacje)")
    _, flat, _ = snapshot()
    stats = read_profile_stats(flat)
    for k, v in stats.items():
        marker = "OK  " if v is not None else "MISS"
        print(f"[{_ts()}]   {marker} {k:11s} = {v}")
    ok(f"statystyki: {stats}")

    # PAUSE: chwila jak człowiek przetrawił co widzi na profilu
    pause(1.5, "chwila po odczycie statystyk")

    # --------------------------------------------------------
    hdr("Powrót na stronę główną")
    tap(*NAV["home_feed"], why="feed_tab")
    # PAUSE: 1.4s — nawigacja + załadowanie feedu
    pause(1.4, "przejście do home")

    if not wait_for_screen("home_feed", timeout_s=8):
        fail("nie wróciliśmy na home_feed — abort"); return
    ok("wróciliśmy na home_feed")

    # PAUSE: chwila na home
    pause(1.0, "chwila na home przed zamknięciem")

    # --------------------------------------------------------
    hdr("Zamknięcie Instagram")
    force_stop_ig()
    # PAUSE: 1.2s — apka gaśnie, wracamy do launchera
    pause(1.2, "zamykanie IG")

    pkg = current_pkg()
    say(f"foreground po zamknięciu: {pkg}")
    if pkg and IG_PKG not in pkg:
        ok("IG zamknięty")
    else:
        warn("IG wciąż w foreground — dodatkowy HOME key")
        press_home()
        pause(0.8, "wracam na launcher")

    # --------------------------------------------------------
    hdr("Wygaszenie ekranu")
    press_power()
    # PAUSE: 0.8s — animacja wygaszania
    pause(0.8, "wygaszanie ekranu")
    if screen_is_on():
        warn("ekran wciąż aktywny — może była animacja; próbuję ponownie")
        press_power(); pause(0.8, "drugie wygaszenie")
    if not screen_is_on():
        ok("ekran wygaszony — koniec sesji")
    else:
        warn("ekran nie chce się wygasić")

    print()
    print("#" * 68)
    print(f"#  KONIEC  |  {time.strftime('%H:%M:%S')}  |  wykonano {STEP_NUM[0]} kroków")
    print("#" * 68)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nprzerwane ręcznie")
