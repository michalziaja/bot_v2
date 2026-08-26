"""Siatka postów na profilu — wykrycie kafelków i zrzut do analizy AI.

Kafelków NIE liczymy matematycznie z szerokości ekranu. Marginesy i paddingi
kontenera nie pokrywają się z faktycznymi granicami postów: wyliczona siatka
ucinała pierwszy wiersz i rozjeżdżała numerację, a numer kafelka to jedyne,
po czym potem wskazujemy, w co tapnąć. Dlatego bierzemy realne `bounds`
z drzewa — po ID kafelka albo po geometrii (kwadrat o boku ~szerokość/3,
standardowy układ IG).

Kolejność zwracanych kafelków jest kolejnością CZYTANIA (wiersz po wierszu,
lewo→prawo) — ta sama, którą rysujemy na obrazie dla modelu i którą model
odsyła w `tiles`.

Pozycjonowanie przed zrzutem — port ze starego
`instagram_bot/actions/profile_interaction.py::_capture_grid`:
najpierw ZAWSZE `scrolls` przewinięć w dół (chowa avatar/bio/highlighty),
dopiero potem dump kafelków + crop do ich bounds. Bez wstępnego scrolla
AI dostawało highlighty jako „kafelki 1–4".
"""
from __future__ import annotations

import re
import time

from v2.core.composite import annotate_grid
from v2.core.screen_map import flatten_hierarchy
from v2.steps.post._post import TILE_IDS

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

# Ile odchyłki od kwadratu ekran/3 tolerujemy, licząc kafelek za kafelek.
_SHAPE_TOL = 0.35
# Ile pikseli różnicy w Y wciąż znaczy „ten sam wiersz".
_ROW_TOL = 0.35
# Pauza po scrollu — jak stary `_pause("after_scroll_grid", (1.3, 1.7))`.
SCROLL_SETTLE_S = 1.4

# Port `HumanTouch.swipe_scroll_likers_list` (Y ~1750→650 na 2340 px):
# mocny swipe chowa header; delikatny 0.62→0.42 zostawiał highlighty w kadrze.
_SCROLL_Y0 = 0.78
_SCROLL_Y1 = 0.30
_SCROLL_MS = 520


def parse_bounds(bounds: str | None) -> tuple[int, int, int, int] | None:
    m = _BOUNDS_RE.match(bounds or "")
    if not m:
        return None
    return tuple(int(g) for g in m.groups())            # type: ignore[return-value]


def find_grid_tiles(flat: list[dict], screen_w: int,
                    tiles_count: int = 12) -> list[tuple[int, int, int, int]]:
    """Bounds kafelków w kolejności czytania, maksymalnie `tiles_count`."""
    cell = max(screen_w // 3, 1)
    tol = cell * _SHAPE_TOL

    raw: set[tuple[int, int, int, int]] = set()
    for e in flat:
        b = parse_bounds(e.get("bounds"))
        if not b:
            continue
        x1, y1, x2, y2 = b
        bw, bh = x2 - x1, y2 - y1
        if bw <= 0 or bh <= 0:
            continue
        # Kształt sprawdzamy ZAWSZE, także dla elementów z ID kafelka —
        # kontener całej siatki bywa tym samym ID i wszedłby jako jeden
        # wielki „kafelek". ID jedynie rozluźnia tolerancję, bo kafelek
        # rolki bywa lekko wyższy niż kwadrat.
        limit = tol * (1.5 if (e.get("resource-id") or "") in TILE_IDS else 1.0)
        if abs(bw - cell) > limit or abs(bh - cell) > limit:
            continue
        raw.add((x1, y1, x2, y2))

    if not raw:
        return []

    rows: list[list[tuple[int, int, int, int]]] = []
    for b in sorted(raw, key=lambda t: (t[1], t[0])):
        for row in rows:
            if abs(row[0][1] - b[1]) < cell * _ROW_TOL:
                row.append(b)
                break
        else:
            rows.append([b])
    rows.sort(key=lambda r: r[0][1])
    for row in rows:
        row.sort(key=lambda b: b[0])

    return [b for row in rows for b in row][:tiles_count]


def estimate_grid_tiles(screen_w: int, screen_h: int, tiles_count: int,
                        top_pct: float = 0.15) -> list[tuple[int, int, int, int]]:
    """Zgadywana siatka 3 kolumn, gdy drzewo nie oddało żadnych kafelków.

    Port fallbacku ze starego bota. Jest to OSTATNIA deska ratunku i wolno jej
    użyć tylko wtedy, gdy wiemy, że profil w ogóle ma posty — inaczej rysujemy
    dwanaście ramek na pustym ekranie i model kategoryzowałby szum.
    """
    cell = max(screen_w // 3, 1)
    top = int(screen_h * top_pct)
    rows = -(-tiles_count // 3)
    tiles = [(c * cell, top + r * cell, (c + 1) * cell, top + (r + 1) * cell)
             for r in range(rows) for c in range(3)][:tiles_count]
    return [t for t in tiles if t[3] <= screen_h]


def scroll_profile_toward_grid(ctx) -> None:
    """Jeden swipe w dół jak stary `swipe_scroll_likers_list` — chowa nagłówek."""
    sw, sh = ctx.account.device["_hardware"]["resolution"]
    ctx.ht.swipe(sw // 2, int(sh * _SCROLL_Y0), sw // 2, int(sh * _SCROLL_Y1),
                 duration_ms=_SCROLL_MS, pause_kind="tap")
    time.sleep(SCROLL_SETTLE_S)


def scroll_profile_toward_header(ctx) -> None:
    """Odwrotność `scroll_profile_toward_grid` — powrót pod follow button."""
    sw, sh = ctx.account.device["_hardware"]["resolution"]
    ctx.ht.swipe(sw // 2, int(sh * _SCROLL_Y1), sw // 2, int(sh * _SCROLL_Y0),
                 duration_ms=_SCROLL_MS, pause_kind="tap")


def capture_grid(ctx, out_path, *, tiles_count: int = 9,
                 scrolls: int = 1, max_extra_scrolls: int = 3,
                 allow_estimate: bool = False) -> dict:
    """Zrzut siatki z numeracją — port starego `_capture_grid`.

    Kolejność (jak w `instagram_bot/.../profile_interaction.py`):
      1. ZAWSZE `scrolls` przewinięć w dół (domyślnie 1 = `max_scroll_for_grid`)
         — nagłówek/bio/highlighty poza viewport, zanim w ogóle dumpujemy UI.
      2. Odczyt kafelków z hierarchy.
      3. Dodatkowe scrolle (max `max_extra_scrolls`), gdy RecyclerView nie oddał
         jeszcze N kafelków — bierzemy najlepszy wynik.
      4. Screencap + crop do bounds kafelków (`annotate_grid`).

    Zwraca `{"path", "tiles", "scrolls", "estimated"}`.
    """
    w, h = ctx.account.device["_hardware"]["resolution"]
    want = max(1, int(tiles_count))

    def _read_tiles():
        return find_grid_tiles(
            flatten_hierarchy(ctx.device.hierarchy(force=True)), w, want)

    used_scrolls = 0

    # 1) Wstępny scroll ZAWSZE — NIE pomijamy gdy „już widać 9 kafelków":
    #    przed scrolllem „kafelkami" bywają highlighty (outer_container ~ekran/3).
    for _ in range(max(int(scrolls), 0)):
        scroll_profile_toward_grid(ctx)
        used_scrolls += 1

    tiles = _read_tiles()

    # 2) Dociągnięcie wierszy (RecyclerView materializuje tylko widoczne)
    extra = 0
    while len(tiles) < want and extra < max(int(max_extra_scrolls), 0):
        scroll_profile_toward_grid(ctx)
        used_scrolls += 1
        extra += 1
        cand = _read_tiles()
        if len(cand) > len(tiles):
            tiles = cand

    estimated = False
    if not tiles and allow_estimate:
        tiles = estimate_grid_tiles(w, h, want)
        estimated = bool(tiles)
        ctx.log("profile.grid.estimated", tiles=len(tiles))

    if not tiles:
        ctx.log("profile.grid.no_tiles", scrolls=used_scrolls)
        return {"path": None, "tiles": [], "scrolls": used_scrolls,
                "estimated": False}

    png = ctx.device.screenshot()
    path = annotate_grid(png, tiles, out_path)
    ctx.log("profile.grid.captured", tiles=len(tiles), wanted=want,
            scrolls=used_scrolls, estimated=estimated, path=path)
    return {"path": path, "tiles": tiles, "scrolls": used_scrolls,
            "estimated": estimated}
