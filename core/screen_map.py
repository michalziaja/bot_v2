"""ScreenMap — ładowanie mapy UI i rozpoznawanie ekranów.

Port `calibration/recognize.py` na klasę. Rozpoznawanie po presence
resource-id, z obsługą `wins_over` dla overlayów (bottom sheet nad
reels_feed, search_results nad search_input_active itp.).

Nie zależy od telefonu — dostaje set(ids) i zwraca match.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Overlays — allowlista konkretnych ID (nigdy generyczne słowa jak "bottom_sheet",
# bo IG reels ma je permanentnie w drzewie).
POPUP_HINTS = {
    "android_alert":      ["android:id/button1", "android:id/alertTitle"],
    "system_perm_dialog": ["com.android.permissioncontroller:id/permission_message",
                           "com.android.permissioncontroller:id/permission_allow_button"],
    "ig_dialog":          ["ig_dialog_positive_button", "ig_dialog_negative_button"],
    "rate_limit":         ["action_try_later", "rate_limit_dialog"],
    "suspicious_login":   ["challenge_two_factor", "suspicious_login"],
    "save_login_prompt":  ["save_login_prompt", "not_now_text"],
    "share_sheet":        ["bs_action_sheet_row", "share_sheet_container"],
}

# Systemowe pakiety które mogą przykryć IG a NIE są "foreign_app"
IME_HINTS   = ("keyboard", "inputmethod", "ime", "honeyboard", "swiftkey", "gboard")
BENIGN_PKG  = ("instagram", "launcher")   # systemui traktujemy specjalnie w Navigatorze


@dataclass
class ScreenState:
    """Wynik rozpoznania."""
    package: str | None
    overlay: str | None            # nazwa z POPUP_HINTS, "keyboard", "foreign_app:<pkg>" lub None
    top: str | None                # nazwa najlepiej pasującego ekranu (po wins_over)
    hits: int                      # ile fingerprintów matchowało
    matches: list                  # [(screen_name, [matched_ids])] posortowane
    ambiguous: bool = False
    unknown: bool = False

    def as_log(self) -> dict:
        return {"pkg": self.package, "overlay": self.overlay,
                "top": self.top, "hits": self.hits,
                "ambiguous": self.ambiguous, "unknown": self.unknown}


@dataclass
class ScreenMap:
    meta: dict
    anchors: dict
    fingerprints: dict
    activities: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "ScreenMap":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            meta         = data.get("meta", {}),
            anchors      = data.get("anchors", {}),
            fingerprints = data.get("fingerprints", {}),
            activities   = data.get("activities", {}),
        )

    def anchor_bounds(self, name: str) -> str | None:
        a = self.anchors.get(name)
        return a.get("bounds") if a else None

    def anchor_center(self, name: str) -> tuple[int, int] | None:
        """(cx, cy) na podstawie bounds z kotwicy, None jeśli nie ma."""
        b = self.anchor_bounds(name)
        return _parse_bounds_center(b) if b else None

    def detect_overlay(self, ids: set, pkg: str | None) -> str | None:
        for name, hints in POPUP_HINTS.items():
            if any(any(h in i for i in ids) for h in hints):
                return name
        if pkg:
            pl = pkg.lower()
            if any(k in pl for k in IME_HINTS):
                return "keyboard"
            if "systemui" in pl:
                # traktowane osobno w Navigatorze (drabinka L4b)
                return "system_ui"
            if not any(k in pl for k in BENIGN_PKG):
                return f"foreign_app:{pkg}"
        return None

    def recognize(self, ids: set) -> list[tuple[str, list[str]]]:
        """Zwraca [(screen_name, [matched_ids])] posortowane malejąco po liczbie hitów,
        z zastosowaniem wins_over (overlay ekranów gasi swoje bazy)."""
        raw = []
        for screen, fp in self.fingerprints.items():
            must = fp.get("must_have_any") or []
            hits = [m for m in must if m in ids]
            if hits:
                raw.append((screen, hits, fp.get("wins_over") or []))
        matched_names = {s for s, _, _ in raw}
        suppressed = set()
        for s, _, wins in raw:
            for beaten in wins:
                if beaten in matched_names:
                    suppressed.add(beaten)
        results = [(s, h) for s, h, _ in raw if s not in suppressed]
        results.sort(key=lambda t: -len(t[1]))
        return results

    def identify(self, ids: set, pkg: str | None) -> ScreenState:
        overlay = self.detect_overlay(ids, pkg)
        matches = self.recognize(ids)
        top     = matches[0][0] if matches else None
        hits    = len(matches[0][1]) if matches else 0
        ambiguous = (len(matches) > 1
                     and matches[0] and matches[1]
                     and len(matches[0][1]) == len(matches[1][1]))
        return ScreenState(
            package = pkg,
            overlay = overlay,
            top     = top,
            hits    = hits,
            matches = matches,
            ambiguous = ambiguous,
            unknown = not matches,
        )

    def is_screen(self, ids: set, pkg: str | None, screen: str) -> bool:
        """Szybki check czy rozpoznany ekran to `screen` (bez overlayu).

        Tańsza alternatywa dla pełnego `identify()` gdy interesuje nas tylko
        jeden konkretny ekran. Uwzględnia overlaye (zwraca False gdy stoi
        dialog/keyboard/foreign_app), więc pasuje do guardów w krokach.
        """
        if self.detect_overlay(ids, pkg):
            return False
        matches = self.recognize(ids)
        if not matches:
            return False
        return matches[0][0] == screen

    def has_signature(self, flat: list[dict], screen: str) -> bool:
        """Szybki check po spłaszczonym drzewie, czy obecny ekran to `screen`.

        Nie zastępuje `identify()` gdy potrzebujemy overlay/pkg, ale idealnie
        nadaje się do guardów typu „czy wciąż na reels_feed" lub „czy otworzył
        się search_results". Zwraca False gdy brak matchu lub jest overlay.
        """
        ids = ids_from_flat(flat)
        pkg = dominant_package(flat)
        return self.is_screen(ids, pkg, screen)


def _parse_bounds_center(s: str) -> tuple[int, int] | None:
    """[x1,y1][x2,y2] → (cx, cy)."""
    try:
        a, b = s.split("][")
        x1, y1 = (int(v) for v in a.strip("[").split(","))
        x2, y2 = (int(v) for v in b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None


def flatten_hierarchy(node) -> list[dict]:
    """Rekurencyjnie spłaszcza drzewo hierarchii uiautodev do listy węzłów.
    Każdy węzeł ma: class, resource-id, content-desc, text, bounds, package, clickable.

    Gdy dostanie już spłaszczoną listę (testy / reuse) — zwraca ją bez zmian.
    """
    if isinstance(node, list):
        return node
    out: list[dict] = []
    _walk(node or {}, out)
    return out


def _walk(node: dict, out: list[dict]) -> None:
    p = node.get("properties") or {}
    if p:
        # focused/checked/selected — potrzebne m.in. search.type_query
        # (czy pole ma focus; która opcja IME jest zaznaczona). Bez tego
        # `_field_focused` zawsze zwracało False → zbędny tap → dialog IME.
        out.append({
            "class":        p.get("class"),
            "resource-id":  p.get("resource-id"),
            "content-desc": p.get("content-desc"),
            "text":         p.get("text"),
            "bounds":       p.get("bounds"),
            "package":      p.get("package"),
            "clickable":    p.get("clickable") == "true",
            "focused":      (p.get("focused") or "").lower() == "true",
            "checked":      (p.get("checked") or "").lower() == "true",
            "selected":     (p.get("selected") or "").lower() == "true",
        })
    for c in node.get("children") or []:
        _walk(c, out)


def ids_from_flat(flat: list[dict]) -> set:
    return {n["resource-id"] for n in flat if n.get("resource-id")}


def dominant_package(flat: list[dict]) -> str | None:
    pkgs = [n.get("package") for n in flat if n.get("package")]
    if not pkgs:
        return None
    return max(set(pkgs), key=pkgs.count)
