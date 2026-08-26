"""Współdzielone helpery dla kroków system.* operujących na popupach IG bez
resource-id (login, popupy po zalogowaniu, wpisanie kodu weryfikacyjnego).

Wydzielone z `login_if_needed.py`, żeby `enter_verification_code_if_needed.py`
nie duplikował tej samej logiki dopasowania.
"""
from __future__ import annotations

# Nie tylko `android.widget.Button` — część przycisków IG to klikalny
# `FrameLayout` z osobnym `TextView` na tekst (potwierdzone na żywo
# 2026-08-02, `calibration/samples/54_feature_tip_navigation`).
BUTTON_CLASSES = ("Button", "FrameLayout")


def find_field(flat: list[dict], ids: tuple, hints: tuple, cls_hint) -> dict | None:
    """ID najpierw (gdyby dana wersja IG je miała), potem (klasa + treść
    opisu) — dla popupów bez resource-id jest to jedyny sposób.

    `cls_hint` — string albo tuple fragmentów klasy. Gdy pasuje kilka węzłów,
    klikalny wygrywa (nie chcemy tapnąć w statyczny label zamiast jego
    kontenera).
    """
    classes = (cls_hint,) if isinstance(cls_hint, str) else cls_hint
    for e in flat:
        if (e.get("resource-id") or "") in ids:
            return e
    matches = [e for e in flat
              if any(c in (e.get("class") or "") for c in classes)
              and any(h in normalize_ws((e.get("content-desc") or "") + " " + (e.get("text") or ""))
                     for h in hints)]
    if not matches:
        return None
    return next((e for e in matches if e.get("clickable")), matches[0])


def normalize_ws(s: str) -> str:
    """Android bywa niełamliwą spacją (`\\xa0`) w podpisach przycisków —
    potwierdzone na żywo w „Podczas używania\\xa0aplikacji" (dialog uprawnień
    lokalizacji). Bez normalizacji dopasowanie po zwykłej spacji nie trafia."""
    return s.replace("\xa0", " ").lower()


def center(bounds):
    a, b = str(bounds).split("][")
    x1, y1 = map(int, a.strip("[").split(","))
    x2, y2 = map(int, b.strip("]").split(","))
    return (x1 + x2) // 2, (y1 + y2) // 2


def escape_input_text(s: str) -> str:
    return s.replace(" ", "%s").replace('"', '\\"').replace("'", "\\'")


def settle_for_animation(ctx, max_wait_s: float = 2.0, fallback_wait_s: float = 1.0):
    """Czekaj na koniec animacji UI — preferowanie klatki video (szybsze,
    nie wymaga dumpów hierarchii), z fallbackiem na `wait_for_stable_ui`.

    Używaj TAM, gdzie stabilność hierarchii nie jest krytyczna (np. po tapie
    otwierającym viewer, po swipe, po rozwiń sheet). Nie używaj gdy czekasz na
    pojawienie się konkretnego elementu / modalu / klawiatury — wtedy potrzebny
    jest pełny dump.
    """
    fb = getattr(ctx, "fb", None)
    if fb and fb.is_live(max_age_ms=1500):
        fb.get_stable_frame(stability_ms=200, timeout_s=max_wait_s)
        return
    ctx.device.wait_for_stable_ui(max_wait_s=fallback_wait_s)
