"""search.switch_results_tab — przełącz zakładkę na pasku wyników wyszukiwania.

Na ekranie `search_results` pasek to HorizontalScrollView
`scrollable_tab_layout`. Przyciski zakładek to `android.widget.Button`
**bez** resource-id — match po `text` (PL/EN). Zakładka Znaczniki/Tags
często jest poza ekranem → swipe w lewo po bounds tab bara.

Live (sample 45_search_tags_tab, A50 PL): Dla Ciebie / Konta /
Bez personalizacji / Audio / Znaczniki.
"""
from __future__ import annotations

import time

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register

# Klucz wewnętrzny → aliasy tekstu Button (casefold)
RESULTS_TAB_ALIASES: dict[str, tuple[str, ...]] = {
    "for_you": ("dla ciebie", "for you"),
    "accounts": ("konta", "accounts"),
    "unpersonalized": (
        "bez personalizacji",
        "unpersonalized",
        "without personalization",
    ),
    "audio": ("audio",),
    "tags": ("znaczniki", "tags", "tagi"),
    "places": ("miejsca", "places"),
    "reels": ("rolki", "reels"),
}

SCROLLABLE_TAB_ID = "com.instagram.android:id/scrollable_tab_layout"
SEARCH_TAB_BAR_ID = "com.instagram.android:id/search_tab_bar_layout"

# Pasek zakładek na A50 ~ y 230–346 — filtrujemy Buttony w tym pasie
_TAB_Y_MIN = 150
_TAB_Y_MAX = 420


@register
class SearchSwitchResultsTab:
    name = "search.switch_results_tab"
    requires_screen = None  # identify w run — soft path gdy dump flaky

    def run(self, ctx, params):
        tab = str(params.get("tab") or "tags").strip().casefold()
        aliases = RESULTS_TAB_ALIASES.get(tab)
        if not aliases:
            return StepResult.failed(
                f"nieznana zakładka '{tab}' "
                f"(znane: {sorted(RESULTS_TAB_ALIASES)})")
        max_swipes = int(params.get("max_swipes", 6))
        settle_s = float(params.get("settle_s", 0.75))

        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        visible0 = _visible_tab_labels(flat)
        ctx.log("search.switch_results_tab.start",
                tab=tab, visible=visible0)

        target = None
        swipes = 0
        for attempt in range(max_swipes + 1):
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
            target = _find_tab_button(flat, aliases)
            if target:
                break
            if attempt >= max_swipes:
                break
            if not _swipe_tabs_left(ctx, flat):
                ctx.log("search.switch_results_tab.no_tab_bar",
                        attempt=attempt + 1)
                break
            swipes += 1
            time.sleep(0.4)

        if not target:
            visible = _visible_tab_labels(
                flatten_hierarchy(ctx.device.hierarchy(force=True)))
            ctx.log("search.switch_results_tab.not_found",
                    tab=tab, aliases=list(aliases), visible=visible,
                    swipes=swipes)
            return StepResult.failed(
                f"brak zakładki '{tab}' po {swipes} swipe "
                f"(widoczne: {', '.join(visible) or '—'})",
                tab=tab, visible=visible, swipes=swipes)

        c = _center(target.get("bounds"))
        if not c:
            return StepResult.failed(
                f"nie mogę wycentrować zakładki '{tab}'",
                tab=tab, bounds=target.get("bounds"))

        label = (target.get("text") or "").strip()
        ctx.ht.tap(*c, pause_kind="tap")
        time.sleep(settle_s)

        flat_after = flatten_hierarchy(ctx.device.hierarchy(force=True))
        selected = _tab_looks_selected(flat_after, aliases)
        # Soft OK: tap wykonany; selected bywa flaky na Button bez state
        ctx.log("search.switch_results_tab.ok",
                tab=tab, label=label, swipes=swipes, selected=selected,
                xy=list(c))
        ctx.scratch["search_results_tab"] = tab
        return StepResult.ok(
            tab=tab, label=label, swipes=swipes, selected=selected)


def _find_tab_button(flat: list[dict], aliases: tuple[str, ...]):
    """Button w pasie tab bara z tekstem ∈ aliasów."""
    want = {a.casefold() for a in aliases}
    for e in _iter_tab_buttons(flat):
        text = (e.get("text") or "").strip().casefold()
        if text in want:
            return e
    return None


def _iter_tab_buttons(flat: list[dict]):
    for e in flat:
        if "Button" not in (e.get("class") or ""):
            continue
        text = (e.get("text") or "").strip()
        if not text:
            continue
        y = _bounds_y1(e.get("bounds"))
        if y is None or not (_TAB_Y_MIN <= y <= _TAB_Y_MAX):
            continue
        yield e


def _visible_tab_labels(flat: list[dict]) -> list[str]:
    out, seen = [], set()
    for e in _iter_tab_buttons(flat):
        t = (e.get("text") or "").strip()
        k = t.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _tab_looks_selected(flat: list[dict], aliases: tuple[str, ...]) -> bool:
    want = {a.casefold() for a in aliases}
    for e in _iter_tab_buttons(flat):
        text = (e.get("text") or "").strip().casefold()
        if text in want and e.get("selected"):
            return True
    return False


def _swipe_tabs_left(ctx, flat: list[dict]) -> bool:
    """Swipe w lewo po scrollable_tab_layout — kolejne zakładki z prawej."""
    b = _tab_bar_bounds(flat)
    if not b:
        # fallback: kotwica z mapy / typowy pas A50
        c = None
        try:
            c = ctx.screen_map.anchor_center("search.results_tab_bar")
        except Exception:
            c = None
        if c:
            y = c[1]
            try:
                w = int(ctx.account.device["_hardware"]["resolution"][0])
            except Exception:
                w = 1080
            ctx.ht.swipe(int(w * 0.85), y, int(w * 0.15), y,
                         duration_ms=280, pause_kind="tap")
            return True
        return False
    x1, y1, x2, y2 = b
    y = (y1 + y2) // 2
    # treść w prawo → widać zakładki po prawej krawędzi
    ctx.ht.swipe(int(x1 + (x2 - x1) * 0.85), y,
                 int(x1 + (x2 - x1) * 0.15), y,
                 duration_ms=280, pause_kind="tap")
    return True


def _tab_bar_bounds(flat: list[dict]):
    for rid in (SCROLLABLE_TAB_ID, SEARCH_TAB_BAR_ID):
        for e in flat:
            if (e.get("resource-id") or "") == rid:
                return _parse_bounds(e.get("bounds"))
    return None


def _bounds_y1(bounds) -> int | None:
    try:
        return int(str(bounds).split(",")[1].split("]")[0])
    except Exception:
        return None


def _parse_bounds(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return x1, y1, x2, y2
    except Exception:
        return None


def _center(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None
