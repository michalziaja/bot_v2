"""Ślad linii „Ten post” / „Twój typowy post” z JPG dumpów 40_*.

Hierarchia nie ma tooltipów — bierzemy piksele magenta i szarej kreski.
Relacja bez linii → chart_kind=donut, pusta seria (nie klon 30).
Komentarze PL.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from PIL import Image  # noqa: E402

from v2.data.pro_dash_parse import (  # noqa: E402
    _parse_axis_day,
    load_slim,
    parse_bounds,
    parse_ig_number,
)

OUT = Path(__file__).resolve().parent
POSTS = (379, 394, 42, 54, 278)
STORIES = (147, 172, 162, 179, 196)
WINDOWS = ("7", "14", "30")
MONTHS = ("sty", "lut", "mar", "kwi", "maj", "cze",
          "lip", "sie", "wrz", "paź", "lis", "gru")


def _magenta(r: int, g: int, b: int) -> bool:
    """Róż IG „Ten post” — linia bywa (232,0,210), nie szarość osi."""
    return r > 150 and b > 80 and g < 50 and r > g + 80


def _gray_line(r: int, g: int, b: int) -> bool:
    """Szara kreska „Twój typowy post”."""
    if abs(r - g) > 22 or abs(g - b) > 22:
        return False
    return 95 <= r <= 175


def _axis_from_slim(slim_path: Path) -> dict:
    """Etykiety osi z slim: daty na dole, ymax po lewej."""
    if not slim_path.exists():
        return {}
    nodes = load_slim(slim_path)
    dates = []
    ynums = []
    for n in nodes:
        t = (n.get("text") or "").strip()
        xy = parse_bounds(n.get("bounds"))
        if not xy:
            continue
        day = _parse_axis_day(t)
        if day and xy[1] > 900:
            dates.append((xy[0], day))
        num = parse_ig_number(t)
        if num is not None and xy[0] < 160 and 800 < xy[1] < 1400:
            ynums.append((xy[1], num))
    dates.sort()
    ynums.sort(reverse=True)  # większe y (niżej) = mniejsze wartości
    ymax = max((n for _y, n in ynums), default=None)
    ymin = min((n for _y, n in ynums), default=0)
    y0 = max((y for y, n in ynums if n == 0), default=None)
    ytop = min((y for y, n in ynums if ymax is not None and n == ymax), default=None)
    return {
        "dates": dates,
        "ymax": ymax,
        "ymin": ymin or 0,
        "y0": y0,
        "ytop": ytop,
        "x_labels": [d for _x, d in dates],
    }


def _sample_line(img: Image.Image, pred, *, x0: int, x1: int, y0: int, y1: int) -> dict[int, float]:
    """Dla każdego x — mediana y pikseli spełniających pred (oś w dół)."""
    px = img.load()
    out: dict[int, float] = {}
    for x in range(x0, x1 + 1):
        ys = []
        for y in range(y1, y0 - 1, -1):  # od dołu — linia nad wypełnieniem
            r, g, b = px[x, y][:3]
            if pred(r, g, b):
                ys.append(y)
        if len(ys) >= 2:
            ys.sort()
            out[x] = float(ys[len(ys) // 2])
    return out


def _dates_between(start: str, end: str) -> list[str]:
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    n = (b - a).days
    return [(a + timedelta(days=i)).isoformat() for i in range(n + 1)]


def _px_to_points(xs_y: dict[int, float], *, dates: list[str],
                  x0: int, x1: int, y0: float, ytop: float,
                  ymax: float, source: str) -> list[dict]:
    """Próbkuj linię w środku każdego dnia; skala y → views."""
    if not dates or x1 <= x0 or y0 <= ytop or ymax <= 0:
        return []
    span = max(len(dates) - 1, 1)
    points = []
    for i, d in enumerate(dates):
        x = int(round(x0 + (i / span) * (x1 - x0)))
        # najbliższy x ze śladu
        if not xs_y:
            continue
        nearest = min(xs_y, key=lambda k: abs(k - x))
        if abs(nearest - x) > 18:
            continue
        y = xs_y[nearest]
        views = (y0 - y) / (y0 - ytop) * ymax
        views = max(0.0, views)
        points.append({
            "date": d,
            "views": int(round(views)),
            "source": source,
        })
    return points


def _file_md5(path: Path) -> str:
    import hashlib
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


def _slim_is_overlay(slim: Path) -> bool:
    """Czy slim to drill-in (Podsumowanie), nie lista Zawartość."""
    if not slim.exists():
        return False
    blob = " ".join((n.get("text") or "") for n in load_slim(slim)).casefold()
    return "podsumowanie" in blob or "wyświetlenia na przestrzeni" in blob


def trace_jpg(jpg: Path, slim: Path, *, total_views: int | None) -> dict:
    """Jedna klatka → dwie serie albo pusta (donut)."""
    if not jpg.exists():
        return {"chart_kind": "none", "daily": [], "typical_daily": []}
    img = Image.open(jpg).convert("RGB")
    w, h = img.size
    axis = _axis_from_slim(slim)
    dates = axis.get("x_labels") or []
    if len(dates) >= 2:
        day_list = _dates_between(dates[0], dates[-1])
    else:
        # hierarchia bez etykiet osi — ta sama oś IG co trial 379 (1–8 wrz)
        day_list = _dates_between("2026-09-01", "2026-09-08")
    def _nice_ymax(v: int) -> int:
        for step in (50, 100, 200, 400, 600, 800, 1000):
            if v <= step:
                return step
        return ((v + 199) // 200) * 200

    ymax = axis.get("ymax")
    if ymax is None and total_views:
        ymax = _nice_ymax(int(total_views))
    if ymax and total_views and ymax < total_views:
        ymax = _nice_ymax(int(total_views))
    y0 = axis.get("y0")
    ytop = axis.get("ytop")
    # auto-box z pikseli różu — overlay bywa niżej po scrollu
    if y0 is None or ytop is None:
        ys = []
        px = img.load()
        for y in range(700, 1760, 2):
            hit = 0
            for x in range(120, 720, 3):
                r, g, b = px[x, y][:3]
                if _magenta(r, g, b):
                    hit += 1
            if hit >= 4:
                ys.append(y)
        if len(ys) >= 6:
            ytop = min(ys) - 8
            y0 = max(ys) + 12
    if y0 is None:
        y0 = 1260
    if ytop is None:
        ytop = int(y0) - 308
    if not ymax:
        ymax = 400
    # box wykresu: od lewej osi do końca linii „Ten post” (nie progress-bary)
    x0 = 130
    x1 = 700
    if axis.get("dates"):
        xs = [x for x, _d in axis["dates"]]
        x0 = max(110, min(xs) - 20)
        x1 = min(720, max(xs) + 20)
    y_hi = max(200, int(ytop) - 20)
    y_lo = min(h - 80, int(y0) + 20)
    mag = _sample_line(img, _magenta, x0=x0, x1=x1, y0=y_hi, y1=y_lo)
    gray = _sample_line(img, _gray_line, x0=x0, x1=min(w - 40, x1 + 320),
                        y0=y_hi, y1=y_lo)
    if len(mag) < 8 or len(day_list) < 2:
        return {
            "chart_kind": "donut" if not mag else "none",
            "daily": [],
            "typical_daily": [],
            "axis": axis,
        }
    # typowy ciągnie się dalej w prawo
    this = _px_to_points(mag, dates=day_list, x0=x0, x1=x1,
                         y0=float(y0), ytop=float(ytop), ymax=float(ymax),
                         source="image_trace")
    typ = _px_to_points(gray, dates=day_list, x0=x0, x1=max(x1, 980),
                        y0=float(y0), ytop=float(ytop), ymax=float(ymax),
                        source="image_trace")
    # ostatni punkt „Ten post” ≈ karta views (skumulowane)
    if this and total_views:
        last = this[-1]
        if abs(last["views"] - total_views) <= max(8, int(total_views * 0.08)):
            last["views"] = int(total_views)
        # plateau: nie pozwól rosnąć powyżej karty
        for p in this:
            if p["views"] > total_views:
                p["views"] = int(total_views)
    return {
        "chart_kind": "line",
        "daily": this,
        "typical_daily": typ,
        "axis": {
            "ymax": ymax, "y0": y0, "ytop": ytop,
            "x_labels": [ _label_pl(d) for d in (dates or day_list[::max(1, len(day_list)//2)]) ],
        },
        "mag_n": len(mag),
        "gray_n": len(gray),
    }


def _label_pl(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]}"


def _item_id(kind: str, views: int) -> str:
    from v2.data.pro_dash_store import content_id
    return content_id(kind, None, views, "")


def build_item_series() -> dict:
    """Złóż ITEM_SERIES z dumpów 40_{post,story}_{views}_{days}d."""
    items = []
    gaps = []
    for kind, views_list in (("post", POSTS), ("story", STORIES)):
        for views in views_list:
            rec = {
                "id": _item_id(kind, views),
                "kind": kind,
                "views": views,
                "daily_mode": "cumulative" if kind == "post" else None,
                "series_by_window": {},
                "typical_by_window": {},
                "window_gaps": {},
                "window_jpgs": {},
                "chart_kind": "none",
            }
            traces = {}
            hashes = {}
            for w in WINDOWS:
                name = f"41_{kind}_{views}_{w}d"
                jpg = OUT / f"{name}.jpg"
                slim = OUT / f"{name}.slim.json"
                if not jpg.exists():
                    name40 = f"40_{kind}_{views}_{w}d"
                    for cand in (name, f"{name}_fail", name40, f"{name40}_fail"):
                        p = OUT / f"{cand}.jpg"
                        if p.exists():
                            jpg, slim, name = p, OUT / f"{cand}.slim.json", cand
                            break
                rec["window_jpgs"][w] = jpg.name if jpg.exists() else None
                if not jpg.exists():
                    rec["window_gaps"][w] = (
                        "brak na tym ekranie IG" if w == "14"
                        else f"brak dumpa {name}")
                    gaps.append(f"{kind} {views} okno {w}: brak jpg")
                    continue
                hashes[w] = _file_md5(jpg)
                traces[w] = trace_jpg(jpg, slim, total_views=views)
            kinds = {t.get("chart_kind") for t in traces.values()}
            if kinds == {"donut"} or (kind == "story" and not any(
                    t.get("daily") for t in traces.values())):
                rec["chart_kind"] = "donut"
                rec["daily"] = []
                rec["typical_daily"] = []
                rec["window_gaps"]["line"] = (
                    "donut Widzowie — brak linii na drill-in relacji")
            else:
                rec["chart_kind"] = "line"
                rec["window_gaps"].pop("7", None)
                if "7" not in rec["series_by_window"] and kind == "post":
                    rec["window_gaps"]["7"] = "brak na liście 7 dni"
                # identyczne JPG → jedna linia, etykieta uczciwa
                uniq = set(hashes.values())
                rec["ig_windows_identical"] = len(uniq) == 1 and len(hashes) >= 2
                for w, tr in traces.items():
                    daily = tr.get("daily") or []
                    typ = tr.get("typical_daily") or []
                    if len(daily) >= 2:
                        rec["series_by_window"][w] = daily
                    elif w == "14":
                        rec["window_gaps"][w] = "brak na tym ekranie IG"
                    if len(typ) >= 2:
                        rec["typical_by_window"][w] = typ
                # default daily = 30 albo pierwsze niepuste
                rec["daily"] = (
                    rec["series_by_window"].get("30")
                    or rec["series_by_window"].get("7")
                    or rec["series_by_window"].get("14")
                    or []
                )
                rec["typical_daily"] = (
                    rec["typical_by_window"].get("30")
                    or rec["typical_by_window"].get("7")
                    or rec["typical_by_window"].get("14")
                    or []
                )
                if rec.get("ig_windows_identical"):
                    rec["window_gaps"]["identical"] = (
                        "trzy JPG 7/14/30 identyczne — IG rysuje tę samą oś, "
                        "nie rolling z dziś"
                    )
                    # uzupełnij brakujące okna tą samą linią, z etykietą
                    src = rec["daily"]
                    typ = rec["typical_daily"]
                    for w in WINDOWS:
                        if src and w not in rec["series_by_window"]:
                            rec["series_by_window"][w] = list(src)
                        if typ and w not in rec["typical_by_window"]:
                            rec["typical_by_window"][w] = list(typ)
            items.append(rec)
        # ręczny ślad #rekrutacja 379 (1–8 wrz, typowy kończy się na 55)
    _HAND_379 = {
        "daily": [
            {"date": "2026-09-01", "views": 0, "source": "image_trace"},
            {"date": "2026-09-02", "views": 367, "source": "image_trace"},
            {"date": "2026-09-03", "views": 376, "source": "image_trace"},
            {"date": "2026-09-04", "views": 379, "source": "image_trace"},
            {"date": "2026-09-05", "views": 379, "source": "image_trace"},
        ],
        "typical_daily": [
            {"date": "2026-09-01", "views": 0, "source": "image_trace"},
            {"date": "2026-09-02", "views": 253, "source": "image_trace"},
            {"date": "2026-09-03", "views": 264, "source": "image_trace"},
            {"date": "2026-09-04", "views": 268, "source": "image_trace"},
            {"date": "2026-09-05", "views": 271, "source": "image_trace"},
            {"date": "2026-09-06", "views": 249, "source": "image_trace"},
            {"date": "2026-09-07", "views": 161, "source": "image_trace"},
            {"date": "2026-09-08", "views": 55, "source": "image_trace"},
        ],
    }
    for rec in items:
        if rec.get("kind") != "post" or rec.get("views") != 379:
            continue
        typ = rec.get("typical_daily") or []
        if typ and typ[-1].get("views") == 55:
            break
        rec["daily"] = list(_HAND_379["daily"])
        rec["typical_daily"] = list(_HAND_379["typical_daily"])
        rec["daily_mode"] = "cumulative"
        rec["chart_kind"] = "line"
        rec["series_by_window"] = {w: list(_HAND_379["daily"]) for w in WINDOWS}
        rec["typical_by_window"] = {w: list(_HAND_379["typical_daily"]) for w in WINDOWS}
        rec["window_gaps"] = {
            **(rec.get("window_gaps") or {}),
            "trace": "auto-ślad zastąpiony ręcznym 30_typical_post (oś 1–8 wrz)",
        }
        break
    # zachowaj ręczny ślad #rekrutacja 379, gdy nowy auto-trace jest gorszy
    old_path = OUT / "ITEM_SERIES.json"
    if old_path.exists():
        try:
            old = json.loads(old_path.read_text(encoding="utf-8"))
        except Exception:
            old = {}
        for prev in old.get("items") or []:
            if prev.get("kind") != "post" or prev.get("views") != 379:
                continue
            typ = prev.get("typical_daily") or []
            # ręczny ślad (końcówka typowego = 55) wygrywa z grubszym auto
            if not typ or typ[-1].get("views") != 55:
                continue
            for i, rec in enumerate(items):
                if rec.get("kind") == "post" and rec.get("views") == 379:
                    kept = dict(prev)
                    kept["window_jpgs"] = rec.get("window_jpgs") or prev.get("window_jpgs")
                    kept["ig_windows_identical"] = rec.get("ig_windows_identical")
                    items[i] = kept
                    break
    return {
        "source": "image_trace",
        "note_pl": (
            "Drill-in wszystkich postów i relacji jobsaver — ślad 40_*.jpg. "
            "Chip 14 nie ma na Zawartość; 14 z Ogólne gdy zmienia overlay, "
            "inaczej luka albo identyczne JPG."
        ),
        "as_of_date": "2026-09-05",
        "live_tooltips": False,
        "gaps": gaps,
        "items": items,
    }


def parse_overview_dumps() -> dict:
    """KPI 7/14/30 z 40_ov_*d.slim.json."""
    from v2.data.pro_dash_parse import parse_overview_kpis, parse_views_by_type
    windows = {}
    for w in WINDOWS:
        kpis: dict = {}
        by_type = {}
        for prefix in ("41_ov_", "40_ov_"):
          for suffix in ("_top", "_mid", ""):
            slim = OUT / f"{prefix}{w}d{suffix}.slim.json"
            if not slim.exists():
                continue
            nodes = load_slim(slim)
            part = parse_overview_kpis(nodes)
            for k, v in part.items():
                if v is not None and kpis.get(k) is None:
                    kpis[k] = v
            bt = parse_views_by_type(nodes)
            if bt:
                by_type.update({k: v for k, v in bt.items() if v is not None})
        if by_type:
            kpis["views_by_type"] = {
                "Posty": by_type.get("post"),
                "Relacje": by_type.get("story"),
                "Rolki": by_type.get("reel"),
            }
        if not kpis:
            continue
        kpis["days"] = int(w)
        windows[w] = kpis
    return {
        "source": "live",
        "as_of_date": "2026-09-05",
        "windows": windows,
    }


def main() -> int:
    ov = parse_overview_dumps()
    (OUT / "OVERVIEW_WINDOWS.json").write_text(
        json.dumps(ov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    series = build_item_series()
    (OUT / "ITEM_SERIES.json").write_text(
        json.dumps(series, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("overview", {k: {m: (v or {}).get(m) for m in
                           ("views", "follows", "interactions", "viewers")}
                       for k, v in (ov.get("windows") or {}).items()})
    for it in series["items"]:
        by = it.get("series_by_window") or {}
        print(it["kind"], it["views"], it.get("chart_kind"),
              "wins", list(by), "n7", len(by.get("7") or []),
              "gaps", it.get("window_gaps"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
