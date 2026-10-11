"""Odczyt wykresu „Wyświetlenia na przestrzeni czasu” z overlayu posta.

IG rysuje linie na Canvas — hierarchia nie ma punktów dziennych, a krótki
tap nie zostawia tooltipu (zrzut po tapie jest pusty). Źródło prawdy to
**to, co widać na wykresie**: oś Y (0/200/400…) + oś X (1 wrz…) z OCR
kadru oraz piksele linii magenta („Ten post”) i szarej kreski
(„Twój typowy post”). Skala Y jest z etykiet, nie z dopasowania do KPI.
"""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path

from PIL import Image

from v2.core.screen_map import flatten_hierarchy
from v2.data.pro_dash_parse import parse_bounds, parse_ig_number, _parse_axis_day

# Etykiety osi Y na tym wykresie — nie mylić z tooltipem.
_AXIS_Y = {0, 200, 400}
# Chip Okres w OCR wpada w kadr — to nie są wyświetlenia.
_CHIP_DAYS = {7, 14, 30}
# Typowe ticki osi Y Insights (OCR lewego paska).
_Y_TICKS = {0, 50, 100, 150, 200, 250, 300, 400, 500, 600, 800, 1000, 1500, 2000}

_MONTHS = {
    "sty": 1, "lut": 2, "mar": 3, "kwi": 4, "maj": 5, "cze": 6,
    "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "paz": 10,
    "lis": 11, "gru": 12,
}

# Tesseract: jeden token daty albo liczba.
_WORD_RE = re.compile(
    r"^(\d{1,3}(?:[ \u00a0]\d{3})*|\d+|ten|post|twój|twoj|typowy|"
    r"sty|lut|mar|kwi|maj|cze|lip|sie|wrz|paź|paz|lis|gru)$",
    re.I,
)


def item_chart_bounds(nodes: list[dict]) -> list[int] | None:
    """Prostokąt wykresu: pod filtrami Wszyscy…, nad legendą albo źródłami.

    Legenda „Ten post” bywa narysowana na Canvas (brak w hierarchii) —
    wtedy dolna krawędź to „Główne źródła wyświetleń”.
    """
    filters_bottom = None
    legend_top = None
    sources_top = None
    left, right = 42, 1038
    for n in nodes or []:
        t = (n.get("text") or "").strip().casefold()
        d = (n.get("desc") or "").strip().casefold()
        b = parse_bounds(n.get("bounds"))
        if not b:
            continue
        if t in ("wszyscy", "obserwatorzy") or d == "wszyscy":
            filters_bottom = max(filters_bottom or 0, b[3])
        if t == "ten post":
            legend_top = b[1] if legend_top is None else min(legend_top, b[1])
        if t.startswith("główne źródła") or t.startswith("glowne zrodla"):
            sources_top = b[1] if sources_top is None else min(sources_top, b[1])
        if t in ("wyświetlenia na przestrzeni czasu",
                 "wyswietlenia na przestrzeni czasu"):
            left = min(left, b[0])
    if filters_bottom is None:
        return None
    y1 = filters_bottom + 8
    y2 = None
    if legend_top is not None:
        y2 = legend_top - 8
    elif sources_top is not None:
        y2 = sources_top - 8
    if y2 is None or y2 - y1 < 80:
        return None
    return [left, y1, right, y2]


def parse_tesseract_tsv(tsv: str) -> list[dict]:
    """Wiersze TSV tesseracta → {text, x, y, w, h} (środek x/y)."""
    out = []
    lines = (tsv or "").splitlines()
    if not lines:
        return out
    header = lines[0].split("\t")
    idx = {name: i for i, name in enumerate(header)}
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < 12:
            continue
        try:
            conf = float(cols[idx.get("conf", 10)])
            left = int(cols[idx.get("left", 6)])
            top = int(cols[idx.get("top", 7)])
            width = int(cols[idx.get("width", 8)])
            height = int(cols[idx.get("height", 9)])
        except (TypeError, ValueError, KeyError):
            continue
        text = (cols[idx.get("text", 11)] if "text" in idx else cols[-1] or "").strip()
        if not text or conf < 0:
            continue
        out.append({
            "text": text,
            "x": left + width // 2,
            "y": top + height // 2,
            "left": left, "top": top, "w": width, "h": height,
            "conf": conf,
        })
    return out


def parse_tooltip_words(words: list[dict], *, tap_x: int,
                        chart_h: int, chart_w: int = 0,
                        item_total: int | None = None) -> dict:
    """Z TSV po tapie: data + liczba Ten post + opcjonalnie typowy post.

    Liczby 0/200/400 przy lewej osi Y odrzucamy. Datę bierzemy z dołu
    wykresu (oś X). Chip 7/14/30 i karta KPI (suma) nie są punktami linii.
    Ten post = tokeny w górnej połowie kadru; typowy — w dolnej.
    """
    dates = []
    nums = []
    for px, day in axis_from_words(words):
        dates.append({"date": day, "x": px, "y": 0})
    for w in words or []:
        num = parse_ig_number(w["text"])
        if num is None:
            continue
        if num in _AXIS_Y and w["x"] < 90:
            continue
        if num in _CHIP_DAYS:
            continue
        if item_total is not None and num == item_total and chart_w:
            if w["x"] < chart_w * 0.55:
                continue
        nums.append({**w, "views": num})

    date_hit = _nearest(dates, tap_x, key="x")
    y_cut = chart_h * 0.78 if chart_h else 10**9
    value_cands = [n for n in nums if n["y"] < y_cut and n["views"] not in _AXIS_Y]
    if not value_cands:
        value_cands = [n for n in nums if n["views"] not in _AXIS_Y]

    mid_y = (chart_h * 0.45) if chart_h else 0
    upper = [n for n in value_cands if n["y"] <= mid_y] or value_cands
    lower = [n for n in value_cands if n["y"] > mid_y]
    this_v = _nearest(upper, tap_x, key="x")
    typical_v = _nearest(lower, tap_x, key="x") if lower else None
    if typical_v is this_v:
        typical_v = None

    out = {"date": date_hit["date"] if date_hit else None,
           "this_views": this_v["views"] if this_v else None,
           "typical_views": typical_v["views"] if typical_v else None}
    return out


def merge_samples(samples: list[dict], *, axis: list[tuple[int, str]]) -> dict:
    """Zbierz tapnięcia → serie {date: views} dla Ten post / typowy.

    Brak daty w OCR → interpolacja z osi (x → dzień) z pierwszego zrzutu.
    Przy wielu tapach w ten sam dzień zostaje wartość najbliższa środkowi dnia.
    """
    this_pts: dict[str, list] = {}
    typ_pts: dict[str, list] = {}
    for s in samples or []:
        day = s.get("date")
        if not day and axis:
            day = date_at_x(s.get("tap_x") or 0, axis)
        if not day:
            continue
        if s.get("this_views") is not None:
            this_pts.setdefault(day, []).append(
                (abs((s.get("tap_x") or 0) - (s.get("date_x") or s.get("tap_x") or 0)),
                 s["this_views"]))
        if s.get("typical_views") is not None:
            typ_pts.setdefault(day, []).append((0, s["typical_views"]))

    def _pick(bucket: dict) -> list[dict]:
        out = []
        for day, hits in sorted(bucket.items()):
            hits.sort()
            out.append({"date": day, "views": hits[0][1],
                        "source": "live_tooltips"})
        return out

    this_list = _pick(this_pts)
    # Ten post jest skumulowany — pojedynczy spadek to śmieć OCR
    this_list = _smooth_cumulative(this_list)
    return {"this_post": this_list, "typical": _pick(typ_pts)}


def _smooth_cumulative(points: list[dict]) -> list[dict]:
    """IG „Ten post” nie spada — dziura w dół to OCR, bierzemy running max."""
    out = []
    acc = 0
    for p in points:
        v = int(p.get("views") or 0)
        acc = max(acc, v)
        rec = dict(p)
        rec["views"] = acc
        out.append(rec)
    return out


def date_at_x(x: int, axis: list[tuple[int, str]]) -> str | None:
    """Liniowa interpolacja dnia między etykietami osi X."""
    if not axis:
        return None
    ordered = sorted((int(px), d) for px, d in axis if d)
    if not ordered:
        return None
    if x <= ordered[0][0]:
        return ordered[0][1]
    if x >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, d0), (x1, d1) in zip(ordered, ordered[1:]):
        if x1 == x0:
            continue
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            a = date.fromisoformat(d0)
            b = date.fromisoformat(d1)
            delta = (b - a).days
            return (a + timedelta(days=round(t * delta))).isoformat()
    return ordered[-1][1]


def axis_from_words(words: list[dict], *, year: int = 2026) -> list[tuple[int, str]]:
    """Etykiety `1 wrz` z TSV — para (x środka, YYYY-MM-DD)."""
    # sklej „1” + „wrz” gdy tesseract rozdzieli
    glued = list(words or [])
    out = []
    used = set()
    for i, w in enumerate(glued):
        day = _parse_axis_day(w["text"], year=year)
        if day:
            out.append((w["x"], day))
            continue
        if not str(w["text"]).isdigit() or i in used:
            continue
        nxt = glued[i + 1] if i + 1 < len(glued) else None
        if not nxt:
            continue
        combo = f"{w['text']} {nxt['text']}"
        day = _parse_axis_day(combo, year=year)
        if day:
            out.append(((w["x"] + nxt["x"]) // 2, day))
            used.add(i + 1)
    out.sort()
    return out


def ocr_tsv(jpg: bytes) -> str:
    """tesseract TSV; pusty string gdy brak binarki."""
    if not jpg:
        return ""
    try:
        img = Image.open(io.BytesIO(jpg)).convert("RGB")
    except Exception:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = Path(tmp.name)
        img.save(path, format="PNG")
    try:
        proc = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", "pol+eng",
             "--psm", "6", "tsv"],
            capture_output=True, text=True, timeout=20,
        )
        return proc.stdout or ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _is_magenta(r: int, g: int, b: int) -> bool:
    """Róż IG „Ten post” — nie szarość osi ani siatki."""
    return r > 150 and b > 80 and g < 50 and r > g + 80


def _is_gray_dash(r: int, g: int, b: int) -> bool:
    """Szara kreska „Twój typowy post” (przerywana)."""
    if abs(r - g) > 22 or abs(g - b) > 22:
        return False
    return 95 <= r <= 175


def _sample_line_ys(img: Image.Image, pred, *,
                    x0: int, x1: int, y0: int, y1: int,
                    y_floor: int | None = None) -> dict[int, float]:
    """Dla każdego x — górna krawędź linii (min y). `y_floor` odcina oś X."""
    px = img.load()
    w, h = img.size
    xa, xb = max(0, x0), min(w - 1, x1)
    ya, yb = max(0, min(y0, y1)), min(h - 1, max(y0, y1))
    if y_floor is not None:
        yb = min(yb, int(y_floor) - 1)
    out: dict[int, float] = {}
    for x in range(xa, xb + 1):
        ys = []
        for y in range(yb, ya - 1, -1):
            r, g, b = px[x, y][:3]
            if pred(r, g, b):
                ys.append(y)
        if len(ys) >= 2:
            out[x] = float(min(ys))  # górna krawędź = wartość na wykresie
    return out


def _y_ticks_from_words(words: list[dict], *, origin_y: int) -> list[tuple[int, int]]:
    """OCR lewego paska → (y ekranu, wartość)."""
    ticks = []
    for w in words or []:
        if w["x"] > 90:
            continue
        num = parse_ig_number(w["text"])
        if num is None or num not in _Y_TICKS:
            continue
        ticks.append((origin_y + int(w["y"]), int(num)))
    ticks.sort(key=lambda t: t[0])
    return ticks


def _views_at_y(y: float, ticks: list[tuple[int, int]]) -> int | None:
    """Liniowa skala z ticków osi Y (0 na dole, ymax na górze)."""
    if len(ticks) < 2:
        return None
    by_val = sorted(ticks, key=lambda t: t[1])
    y0, _vmin = by_val[0]
    yhi, vmax = by_val[-1]
    if y0 == yhi or vmax <= 0:
        return None
    views = (y0 - y) / (y0 - yhi) * vmax
    return max(0, int(round(views)))


def _dates_between(start: str, end: str) -> list[str]:
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    n = (b - a).days
    if n < 0:
        a, b, n = b, a, -n
    return [(a + timedelta(days=i)).isoformat() for i in range(n + 1)]


def _x_for_day(day: str, *, x0: int, x1: int,
               d0: str, d1: str) -> int | None:
    """X ekranu: dzień 0 = lewy brzeg linii, ostatni dzień = prawa etykieta osi."""
    t0 = date.fromisoformat(d0)
    t1 = date.fromisoformat(d1)
    span = max((t1 - t0).days, 1)
    t = (date.fromisoformat(day) - t0).days / span
    if t < -0.05 or t > 1.05:
        return None
    t = min(1.0, max(0.0, t))
    return int(round(x0 + t * (x1 - x0)))


def _points_from_line(xs_y: dict[int, float], *, dates: list[str],
                      x0: int, x1: int, d0: str, d1: str,
                      ticks: list[tuple[int, int]],
                      source: str, max_dx: int) -> list[dict]:
    """Jeden punkt na dzień — x z osi dat, y z najbliższego piksela linii."""
    if not dates or not xs_y or len(ticks) < 2 or x1 <= x0:
        return []
    out = []
    for day in dates:
        x = _x_for_day(day, x0=x0, x1=x1, d0=d0, d1=d1)
        if x is None:
            continue
        nearest = min(xs_y, key=lambda k, target=x: abs(k - target))
        if abs(nearest - x) > max_dx:
            continue
        views = _views_at_y(xs_y[nearest], ticks)
        if views is None:
            continue
        out.append({"date": day, "views": views, "source": source})
    return out


def _interp_holes(points: list[dict]) -> list[dict]:
    """Kreska przerywana: dziura (brak dnia albo 0 w środku) z sąsiadów."""
    if len(points) < 3:
        return points
    by = {p["date"]: p for p in points}
    dates = _dates_between(points[0]["date"], points[-1]["date"])
    known = [p for p in points if int(p.get("views") or 0) > 0]
    if len(known) < 2:
        return points
    out = []
    for day in dates:
        hit = by.get(day)
        if hit and int(hit.get("views") or 0) > 0:
            out.append(hit)
            continue
        prev = [p for p in known if p["date"] < day]
        nxt = [p for p in known if p["date"] > day]
        if not prev or not nxt:
            if hit:
                out.append(hit)
            continue
        a, b = prev[-1], nxt[0]
        span = max((date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days, 1)
        t = (date.fromisoformat(day) - date.fromisoformat(a["date"])).days / span
        views = int(round(a["views"] + t * (b["views"] - a["views"])))
        out.append({"date": day, "views": views, "source": a.get("source") or "chart_pixels"})
    return out


def trace_overlay_image(img: Image.Image, nodes: list[dict], *,
                        item_total: int | None = None) -> dict:
    """Kadr overlayu → serie z pikseli linii i OCR osi (bez telefonu)."""
    bounds = item_chart_bounds(nodes)
    if not bounds:
        return {"ok": False, "reason": "brak bounds wykresu",
                "this_post": [], "typical": [], "source": "chart_pixels"}
    x1, y1, x2, y2 = bounds
    w, h = img.size
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 80 or y2 - y1 < 80:
        return {"ok": False, "reason": "za mały kadr wykresu",
                "this_post": [], "typical": [], "source": "chart_pixels"}

    crop = img.crop((x1, y1, x2, y2))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=90)
    words = parse_tesseract_tsv(ocr_tsv(buf.getvalue()))
    axis_local = axis_from_words(words)
    axis = [(x1 + px, day) for px, day in axis_local]
    ticks = _y_ticks_from_words(words, origin_y=y1)
    if len(axis) < 2:
        return {"ok": False, "reason": "OCR nie odczytał osi X",
                "this_post": [], "typical": [], "source": "chart_pixels"}
    if len(ticks) < 2:
        return {"ok": False, "reason": "OCR nie odczytał osi Y",
                "this_post": [], "typical": [], "source": "chart_pixels"}

    day_list = _dates_between(axis[0][1], axis[-1][1])
    y0_tick = max((y for y, v in ticks if v == 0), default=y2 - 20)
    # lewy brzeg pola wykresu (za etykietami 400/200/0), nie środek „1 wrz”
    plot_x0 = x1 + 58
    plot_x1 = max(px for px, _ in axis)
    mag = _sample_line_ys(
        img, _is_magenta, x0=plot_x0, x1=min(plot_x1, x2 - 8),
        y0=y1 + 8, y1=y2 - 36, y_floor=y0_tick)
    gray = _sample_line_ys(
        img, _is_gray_dash, x0=plot_x0, x1=min(w - 8, plot_x1),
        y0=y1 + 8, y1=y2 - 36, y_floor=y0_tick)
    if len(mag) < 8:
        return {"ok": False, "reason": "brak linii Ten post na kadrze",
                "this_post": [], "typical": [], "source": "chart_pixels"}

    mag_xs = sorted(mag)
    x_left = mag_xs[0]
    x_right = axis[-1][0]
    d0, d1 = axis[0][1], axis[-1][1]
    this_post = _points_from_line(
        mag, dates=day_list, x0=x_left, x1=x_right, d0=d0, d1=d1,
        ticks=ticks, source="chart_pixels", max_dx=22)
    gray_right = max(gray) if gray else x_right
    typical = _points_from_line(
        gray, dates=day_list, x0=x_left, x1=max(x_right, gray_right),
        d0=d0, d1=d1, ticks=ticks, source="chart_pixels", max_dx=36)
    typical = _interp_holes(typical)
    this_post = _smooth_cumulative(this_post)
    if this_post and item_total:
        last = this_post[-1]
        if abs(last["views"] - item_total) <= max(8, int(item_total * 0.08)):
            last["views"] = int(item_total)
        for p in this_post:
            if p["views"] > item_total:
                p["views"] = int(item_total)
    return {
        "ok": bool(this_post),
        "reason": "" if this_post else "za mało punktów linii",
        "this_post": this_post,
        "typical": typical,
        "axis": [{"x": a[0], "date": a[1]} for a in axis],
        "source": "chart_pixels",
    }


def trace_overlay_jpeg(jpg: bytes, nodes: list[dict], *,
                       item_total: int | None = None) -> dict:
    """JPEG ekranu → serie jak `trace_overlay_image`."""
    if not jpg:
        return {"ok": False, "reason": "brak zrzutu",
                "this_post": [], "typical": [], "source": "chart_pixels"}
    try:
        img = Image.open(io.BytesIO(jpg)).convert("RGB")
    except Exception:
        return {"ok": False, "reason": "zły JPEG",
                "this_post": [], "typical": [], "source": "chart_pixels"}
    return trace_overlay_image(img, nodes, item_total=item_total)


def sample_item_chart(dev, nodes: list[dict], *,
                      n_taps: int = 16, pause_s: float = 0.28) -> dict:
    """Zrzut overlayu → serie z osi i pikseli linii (nie tap+OCR całego kadru).

    `n_taps`/`pause_s` zostają w sygnaturze (CLI/fetch) — ignorowane:
    krótki tap nie zostawia tooltipu na Canvas.
    """
    del n_taps, pause_s
    jpg = dev.screenshot()
    total = _overlay_views_total(nodes)
    return trace_overlay_jpeg(jpg, nodes, item_total=total)


def _overlay_views_total(nodes: list[dict]) -> int | None:
    """Suma z karty overlayu (`desc: Wyświetlenia 379`)."""
    for n in nodes or []:
        d = (n.get("desc") or "").strip()
        if d.casefold().startswith("wyświetlenia") or d.casefold().startswith("wyswietlenia"):
            parts = d.split()
            if parts:
                return parse_ig_number(parts[-1])
    return None


def _nodes(dev) -> list[dict]:
    """Zrzut tekstów z hierarchii po tapie — tooltip czasem wychodzi poza Canvas."""
    out = []
    for n in flatten_hierarchy(dev.hierarchy(force=True)):
        out.append({
            "text": (n.get("text") or "").strip(),
            "desc": (n.get("content-desc") or "").strip(),
            "bounds": n.get("bounds"),
        })
    return out


def _tooltip_from_nodes(nodes: list[dict], *, baseline: list[dict]) -> dict:
    """Nowe teksty po tapie vs dump przed tapem."""
    base = {(n.get("text") or "") for n in baseline or []}
    dates, nums = [], []
    for n in nodes or []:
        t = (n.get("text") or "").strip()
        if not t or t in base:
            continue
        day = _parse_axis_day(t)
        if day:
            dates.append(day)
            continue
        num = parse_ig_number(t)
        if num is not None and num not in _AXIS_Y:
            nums.append(num)
    return {
        "date": dates[0] if dates else None,
        "this_views": nums[0] if nums else None,
    }


def _nearest(items: list[dict], x: int, *, key: str):
    if not items:
        return None
    return min(items, key=lambda it: abs(int(it.get(key) or 0) - x))


__all__ = (
    "item_chart_bounds",
    "parse_tesseract_tsv",
    "parse_tooltip_words",
    "merge_samples",
    "date_at_x",
    "axis_from_words",
    "sample_item_chart",
    "trace_overlay_image",
    "trace_overlay_jpeg",
)
