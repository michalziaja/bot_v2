"""UiAuto2Device — adapter na REST uiautodev + adb shell.

Cała komunikacja z telefonem przechodzi przez ten obiekt. Wszystkie
inne warstwy dostają go w Context — nigdy nie wołają HTTP/adb same.

Nie ładuje mapy UI ani nie decyduje o niczym — tylko primitives.
"""
from __future__ import annotations
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class DeviceError(Exception):
    pass


# Komentarz: ten sam prefiks co cloner.identity.PACKAGE_PREFIX — stock + klony
_IG_PKG_PREFIX = "com.instagram.androi"


def ig_ps_has_package(ps_out: str, package: str) -> bool:
    """Czy `ps` pokazuje proces dokładnie tego pakietu (albo `pkg:usługa`).

    `pidof com.instagram.android` na Androidzie patrzy w comm (15 znaków:
    `com.instagram.a`) i myli stock z `androia` / `androib`. Tu ostatnia
    kolumna NAME: równa pakietowi albo `pakiet:`.
    """
    pkg = (package or "").strip().lower()
    if not pkg:
        return False
    for raw in (ps_out or "").splitlines():
        line = raw.strip()
        if not line or line.upper().startswith("USER"):
            continue
        name = line.split()[-1].lower()
        if name == pkg or name.startswith(pkg + ":"):
            return True
    return False


def _normalize_resource_ids(node: dict, ig_package: str) -> None:
    """Podmienia prefiks `resource-id` klona App Clonera na kanoniczny
    `com.instagram.android:id/…` — fingerprinty w ScreenMap i selektory
    zaszyte w krokach są pisane pod oryginalny pakiet; bez tego żaden z nich
    nie trafiłby na dumpie z `com.instagram.androip`/`.androjb`."""
    prefix_from = f"{ig_package}:id/"
    p = node.get("properties")
    if p:
        rid = p.get("resource-id")
        if rid and rid.startswith(prefix_from):
            p["resource-id"] = "com.instagram.android:id/" + rid[len(prefix_from):]
    for c in node.get("children") or ():
        _normalize_resource_ids(c, ig_package)


@dataclass
class UiAuto2Device:
    serial: str
    base_url: str = "http://127.0.0.1:20242"
    ig_package: str = "com.instagram.android"
    _http_timeout: float = 15.0
    _last_hierarchy_ts: float = field(default=0.0, init=False)
    _last_hierarchy: dict = field(default_factory=dict, init=False)
    # Cache read-only dumpów: 400 ms zamiast 200 ms — identify/harvest często
    # wołają hierarchy kilka razy w jednej iteracji. Gest (tap/swipe/back)
    # i tak invaliduje cache, więc bezpieczne.
    _cache_ms: int = field(default=400, init=False)

    # ---- HTTP low level ----
    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/android/{self.serial}{path}"

    def _req(self, method: str, path: str, body: dict | None = None, raw: bool = False, timeout: float | None = None):
        req = urllib.request.Request(
            self._url(path), method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self._http_timeout) as r:
                b = r.read()
        except Exception as e:
            raise DeviceError(f"HTTP {method} {path} failed: {e}") from e
        if raw:
            return b
        if not b:
            return {}
        try:
            return json.loads(b)
        except json.JSONDecodeError:
            return {"_raw": b.decode(errors="replace")}

    # ---- basic queries ----
    def hierarchy(self, force: bool = False) -> dict:
        """Cache'owany dump drzewa UI (default 200ms). force=True omija cache."""
        now = time.monotonic()
        if not force and self._last_hierarchy and (now - self._last_hierarchy_ts) * 1000 < self._cache_ms:
            return self._last_hierarchy
        h = self._req("GET", "/hierarchy")
        if self.ig_package != "com.instagram.android":
            _normalize_resource_ids(h, self.ig_package)
        self._last_hierarchy = h
        self._last_hierarchy_ts = now
        return h

    def invalidate_hierarchy(self):
        self._last_hierarchy = {}
        self._last_hierarchy_ts = 0.0

    def screenshot(self) -> bytes:
        return self._req("GET", "/screenshot/0", raw=True)

    # ---- shell ----
    def shell(self, command: str) -> str:
        r = self._req("POST", "/shell", body={"command": command})
        if isinstance(r, dict):
            return (r.get("output") or "").strip()
        return ""

    def su_shell(self, command: str) -> str:
        """Komenda jako root (`su -c`) — Magisk. Jedyny punkt ADB/root poza airplane."""
        # Komentarz: escape pojedynczego cudzysłowu w POSIX su -c '...'
        escaped = (command or "").replace("'", "'\"'\"'")
        return self.shell(f"su -c '{escaped}'")

    # ---- gestures ----
    def tap(self, x: int, y: int):
        self._req("POST", "/command/tap", body={"x": int(x), "y": int(y)})
        self.invalidate_hierarchy()

    def firm_tap(self, x: int, y: int, hold_ms: int = 140):
        """Lekko dłuższe przytrzymanie (swipe w miejscu) — pewniejsze otwarcie kafelka.

        Nie mylić z long-press (~1s), który otwiera menu kontekstowe.
        """
        ms = max(80, int(hold_ms))
        self.shell(f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {ms}")
        self.invalidate_hierarchy()

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350):
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")
        self.invalidate_hierarchy()

    def press_home(self):     self.shell("input keyevent 3");  self.invalidate_hierarchy()
    def press_back(self):     self._req("POST", "/command/back", body={}); self.invalidate_hierarchy()
    def press_power(self):    self.shell("input keyevent 26"); self.invalidate_hierarchy()
    def wake_up_key(self):    self.shell("input keyevent 224"); self.invalidate_hierarchy()

    # ---- state queries ----
    _RE_FG = re.compile(r"([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.$]+")

    def current_pkg(self) -> str | None:
        """Aktualny foreground package (przez dumpsys window).

        OxygenOS: lockscreen / panel to `NotificationShade` bez `pkg/activity`
        — `head -1` wtedy zwraca None mimo żywego IG w `mFocusedApp`.
        """
        out = self.shell(
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' | head -8"
        )
        if not (out or "").strip():
            out = self.shell(
                "dumpsys activity activities | "
                "grep -E 'mResumedActivity|mFocusedApp|topResumedActivity' | head -8"
            )
        found: list[str] = []
        for line in (out or "").splitlines():
            m = self._RE_FG.search(line)
            if m:
                found.append(m.group(1))
        if not found:
            return None
        # Komentarz: SystemUI/shade nie wygrywa z aplikacją pod spodem
        for pkg in found:
            if pkg != "com.android.systemui":
                return pkg
        return found[0]

    def dismiss_keyguard(self) -> None:
        """Zdejmij keyguard + zwiń shade (OnePlus: swipe na locku bywa no-op)."""
        self.shell("wm dismiss-keyguard")
        self.shell("cmd statusbar collapse")
        self.invalidate_hierarchy()

    def dumpsys_power(self) -> str:
        """Surowy wycinek `dumpsys power` — pusty string = ADB/telefon nie odpowiada."""
        return self.shell("dumpsys power | grep -E 'Display Power|mWakefulness='")

    def screen_is_on(self) -> bool:
        out = self.dumpsys_power()
        return "state=ON" in out or "Awake" in out

    def screen_is_unlocked(self) -> bool:
        out = self.shell("dumpsys window | grep -E 'mDreamingLockscreen|mShowingLockscreen|mKeyguardShowing'")
        locked = ("mShowingLockscreen=true" in out or
                  "mKeyguardShowing=true"   in out or
                  "mDreamingLockscreen=true" in out)
        return not locked

    def ig_version(self, package: str) -> str | None:
        out = self.shell(f"dumpsys package {package} | grep versionName= | head -1")
        m = re.search(r"versionName=(\S+)", out)
        return m.group(1) if m else None

    # ---- app control ----
    def _is_ig_package(self, package: str) -> bool:
        """Stock `com.instagram.android` albo klon `com.instagram.androi*`."""
        return bool(package) and package.strip().lower().startswith(_IG_PKG_PREFIX)

    def list_ig_packages(self) -> list[str]:
        """Zainstalowane pakiety Instagram (stock + klony, bez lite)."""
        out = self.shell("pm list packages com.instagram.androi")
        found: list[str] = []
        seen: set[str] = set()
        for line in (out or "").splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            pkg = line.split(":", 1)[1].strip()
            key = pkg.lower()
            if self._is_ig_package(pkg) and key not in seen:
                seen.add(key)
                found.append(pkg)
        return found

    def stop_other_instagrams(self, target: str, *, wait_s: float = 2.0) -> str:
        """Force-stop każdego IG oprócz targetu (także zminimalizowanego).

        Pusty string = OK. Inaczej `sole_ig_pid` / `no_parallel_ig` — nie startuj.
        """
        target_l = (target or "").strip().lower()
        others = sorted(
            p for p in self.list_ig_packages() if p.strip().lower() != target_l
        )
        for pkg in others:
            self.force_stop_app(pkg)
        if others and wait_s > 0:
            time.sleep(min(0.3, float(wait_s)))
        deadline = time.monotonic() + max(0.0, float(wait_s))
        leftover: list[str] = []
        while True:
            ps_out = self.shell("ps -A") or self.shell("ps") or ""
            leftover = [p for p in others if ig_ps_has_package(ps_out, p)]
            if not leftover:
                break
            # Komentarz: stock bywa uparty — drugi force-stop zanim oddamy błąd
            for pkg in leftover:
                self.force_stop_app(pkg)
            if time.monotonic() >= deadline:
                return "sole_ig_pid: inne IG nadal mają PID: " + ", ".join(leftover)
            time.sleep(0.15)
        focus = self.current_pkg()
        if focus and focus.strip().lower() != target_l and self._is_ig_package(focus):
            return (
                f"no_parallel_ig: inny IG nadal w focus ({focus}) — nie startuję"
            )
        return ""

    def launch_app(self, package: str, *, wait_s: float = 2.0):
        """Start LAUNCHER. Najpierw twardo zamyka inne IG (tylko 1 na telefon)."""
        err = self.stop_other_instagrams(package, wait_s=wait_s)
        if err:
            raise DeviceError(err)
        # monkey bywa cichy na klonach; am start jest drugim strzałem.
        self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        self.shell(
            f"am start -a android.intent.action.MAIN "
            f"-c android.intent.category.LAUNCHER -p {package}"
        )
        self.invalidate_hierarchy()

    def force_stop_app(self, package: str):
        self.shell(f"am force-stop {package}")
        self.invalidate_hierarchy()

    # ---- stability helper ----
    def wait_for_stable_ui(self, max_wait_s: float = 3.0, poll_s: float = 0.35, stable_iters: int = 2) -> bool:
        """Czekaj aż dwa (default) kolejne dumpy są identyczne — czyli UI się ustabilizowało.
        Zwraca True jeśli zdążył, False jeśli timeout."""
        prev_ids: set | None = None
        stable = 0
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            self.invalidate_hierarchy()
            from v2.core.screen_map import flatten_hierarchy, ids_from_flat
            ids = ids_from_flat(flatten_hierarchy(self.hierarchy()))
            if prev_ids is not None and ids == prev_ids:
                stable += 1
                if stable >= stable_iters:
                    return True
            else:
                stable = 0
            prev_ids = ids
            time.sleep(poll_s)
        return False
