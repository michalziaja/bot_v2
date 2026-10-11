"""Parser dumpów Insights (slim/hierarchia) + seed z `calibration/recon_pro_dash`.

Nie jest krokiem schedulera. Służy store'owi: zrzut → punkty / okna / wiersze.
Seria dzienna: hierarchia nie ma tooltipów — ładujemy ślad z `CHART_SERIES.json`
(linia z 27_chart.jpg), bez sinusoidy. Drill-in pozycji: `ITEM_SERIES.json`
(30_typical_post_*d.jpg — „Ten post” + „Twój typowy post”).
"""
from __future__ import annotations

import io
import json
import re
from datetime import date, timedelta
from pathlib import Path

from v2.data.pro_dash_store import CONTENT_KINDS, content_id

# Etykiety IG → kind store.
_TYPE_LABELS = {
    "posty": "post",
    "relacje": "story",
    "rolki": "reel",
}

# Katalog recon — względny od korzenia v2.
RECON_DIR = Path(__file__).resolve().parent.parent / "calibration" / "recon_pro_dash"

# Chip zakresu / taby / sort — nie są wierszem zawartości.
_CHROME_TEXT = {
    "ogólne", "zawartość", "odbiorcy", "statystyki",
    "najnowsze", "wyświetlenia", "widzowie", "obserwatorzy",
    "cała zawartość", "posty", "relacje", "rolki",
    "30 dni", "14 dni", "7 dni", "60 dni", "90 dni", "180 dni",
}

_AGE_RE = re.compile(r"^(\d+)\s*(dni|dzień|tyg\.?)$", re.I)
_NUM_RE = re.compile(r"^-?\d[\d\s]*$")

# Wiadomo z trial fetcha, który wiersz jest postem / relacją (filtr + caption).
_POST_VIEWS = {379, 394, 42, 54, 278}
_STORY_VIEWS = {147, 172, 162, 179, 196}

# Miniatura: kwadrat po lewej od wieku / captions (ekran 1080).
_THUMB_X0 = 42
_THUMB_SIZE = 126
_ROW_MIN_Y = 650


def parse_bounds(raw) -> list[int] | None:
    """bounds jako [x1,y1,x2,y2] — string `[a,b][c,d]` albo lista."""
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            return [int(x) for x in raw]
        except (TypeError, ValueError):
            return None
    if not raw:
        return None
    s = str(raw).replace("][", ",").replace("[", "").replace("]", "")
    parts = [p for p in s.split(",") if p.strip()]
    if len(parts) != 4:
        return None
    try:
        return [int(float(p)) for p in parts]
    except (TypeError, ValueError):
        return None


def parse_ig_number(text) -> int | None:
    """`2,001` / `2,0 tys.` / `-10` → int. Procentów nie ruszamy."""
    s = str(text or "").strip().replace("\xa0", " ")
    if not s or "%" in s:
        return None
    neg = s.startswith("-") or s.startswith("−")
    s = s.lstrip("-−").strip()
    if "tys" in s.lower():
        m = re.search(r"(\d+(?:[.,]\d+)?)", s)
        if not m:
            return None
        val = int(round(float(m.group(1).replace(",", ".")) * 1000))
        return -val if neg else val
    digits = s.replace(" ", "").replace(",", "")
    if not digits.isdigit():
        return None
    return -int(digits) if neg else int(digits)


def load_slim(path: Path) -> list[dict]:
    """Węzły z `*.slim.json` (lista w `nodes`)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return list(raw.get("nodes") or [])
    return list(raw) if isinstance(raw, list) else []


def parse_overview_kpis(nodes: list[dict]) -> dict:
    """Trzy karty Ogólne: Wyświetlenia / Obserwatorzy netto / Interakcje.

    Liczbę bierzemy z tej samej kolumny (overlap X), pod etykietą — inaczej
    „Wyświetlenia” zgarnia „-10” z karty obok (live dump 10_live_overview).
    """
    out: dict = {}
    texts = [(n, (n.get("text") or "").strip()) for n in nodes
             if (n.get("text") or "").strip()]
    labels = {
        "wyświetlenia": "views",
        "obserwatorzy netto": "follows",
        "interakcje": "interactions",
        "widzowie": "viewers",
    }
    used_ids: set[int] = set()
    for i, (n, t) in enumerate(texts):
        key = labels.get(t.casefold())
        if not key or key in out:
            continue
        lb = parse_bounds(n.get("bounds")) or [0, 0, 0, 0]
        best = None
        for j, (n2, t2) in enumerate(texts):
            if j == i or j in used_ids:
                continue
            num = parse_ig_number(t2)
            if num is None:
                continue
            nb = parse_bounds(n2.get("bounds")) or [0, 0, 0, 0]
            # wartość pod etykietą, ten sam pas X (karta)
            if nb[1] < lb[1] - 8 or nb[1] - lb[1] > 180:
                continue
            if not _x_overlap(lb, nb, slop=40):
                continue
            dist = abs(nb[1] - lb[1])
            if best is None or dist < best[0]:
                best = (dist, j, num)
        if best:
            used_ids.add(best[1])
            out[key] = best[2]
    # zakres z chipa „30 dni” / „7 dni”
    for _n, t in texts:
        m = re.match(r"^(\d+)\s*dni$", t.strip(), re.I)
        if m:
            out["range_days"] = int(m.group(1))
            out["range_label"] = t.strip()
            break
    return out


def _x_overlap(a: list[int], b: list[int], slop: int = 0) -> bool:
    """Czy dwa prostokąty nachodzą na osi X (z luzem)."""
    return not (a[2] + slop < b[0] or b[2] + slop < a[0])


def parse_content_rows(nodes: list[dict], *, default_kind: str | None = None) -> list[dict]:
    """Wiersze Zawartość z slim: wiek + views + 3/4 liczniki + opcjonalny caption.

    Terminator wiersza = etykieta „Wyświetlenia” poniżej chipów sortu (y>650).
    """
    seq: list[tuple[dict, str]] = []
    for n in nodes:
        t = (n.get("text") or "").strip()
        if not t:
            continue
        seq.append((n, t))

    rows: list[dict] = []
    i = 0
    while i < len(seq):
        n, t = seq[i]
        y = _node_y(n)
        if y < _ROW_MIN_Y:
            i += 1
            continue
        if t.casefold() == "wyświetlenia":
            i += 1
            continue
        caption = None
        if len(t) > 18 or "\n" in t:
            caption = t
            i += 1
            if i >= len(seq):
                break
            n, t = seq[i]
        if not _AGE_RE.match(t):
            i += 1
            continue
        age = t
        age_bounds = n.get("bounds")
        i += 1
        # zbierz aż do etykiety Wyświetlenia tego wiersza
        nums: list[int] = []
        views = None
        while i < len(seq):
            n2, t2 = seq[i]
            if t2.casefold() == "wyświetlenia" and _node_y(n2) >= _ROW_MIN_Y:
                i += 1
                break
            if _AGE_RE.match(t2) or (len(t2) > 18 or "\n" in t2):
                break
            num = parse_ig_number(t2)
            if num is None:
                i += 1
                continue
            # pierwsza (zwykle największa, po prawej) = wyświetlenia
            if views is None:
                views = num
            else:
                nums.append(num)
            i += 1
        if views is None:
            continue
        kind = default_kind or _guess_kind(caption, views, nums)
        likes = nums[0] if len(nums) >= 1 else 0
        if kind == "story":
            row = {
                "kind": "story",
                "age": age,
                "caption": caption,
                "views": views,
                "likes": likes,
                "shares": nums[1] if len(nums) >= 2 else 0,
                "replies": nums[2] if len(nums) >= 3 else 0,
                "comments": None,
                "saves": None,
                "age_bounds": age_bounds,
            }
        else:
            row = {
                "kind": kind,
                "age": age,
                "caption": caption,
                "views": views,
                "likes": likes,
                "comments": nums[1] if len(nums) >= 2 else 0,
                "shares": nums[2] if len(nums) >= 3 else 0,
                "saves": nums[3] if len(nums) >= 4 else 0,
                "age_bounds": age_bounds,
            }
        row["id"] = content_id(kind, caption, views, age)
        rows.append(row)
    return _dedup_rows(rows)


def thumb_bounds_from_age(age_bounds, *, size: int = _THUMB_SIZE) -> list[int] | None:
    """Kwadrat miniatury na lewo od tekstu wieku."""
    xy = parse_bounds(age_bounds)
    if not xy:
        return None
    top = max(0, xy[1] - 20)
    return [_THUMB_X0, top, _THUMB_X0 + size, top + size]


def crop_jpeg(jpg: bytes, bounds) -> bytes | None:
    """Wytnij JPEG z dumpa ekranu. None gdy brak PIL / złe bounds."""
    try:
        from PIL import Image
    except ImportError:
        return None
    xy = parse_bounds(bounds)
    if not xy or not jpg:
        return None
    try:
        img = Image.open(io.BytesIO(jpg)).convert("RGB")
    except Exception:
        return None
    w, h = img.size
    x1, y1, x2, y2 = xy
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    buf = io.BytesIO()
    img.crop((x1, y1, x2, y2)).save(buf, format="JPEG", quality=82)
    return buf.getvalue()


def parse_views_by_type(nodes: list[dict]) -> dict[str, int]:
    """Blok „Wyświetlenia według typu”: Posty / Relacje / Rolki → int.

    Liczbę bierzemy z najbliższego węzła pod etykietą (ten sam pas, niżej).
    """
    texts = [(n, (n.get("text") or "").strip()) for n in nodes
             if (n.get("text") or "").strip()]
    out: dict[str, int] = {}
    for i, (n, t) in enumerate(texts):
        kind = _TYPE_LABELS.get(t.casefold())
        if not kind or kind in out:
            continue
        lb = parse_bounds(n.get("bounds")) or [0, 0, 0, 0]
        best = None
        for j, (n2, t2) in enumerate(texts):
            if j == i:
                continue
            num = parse_ig_number(t2)
            if num is None:
                continue
            nb = parse_bounds(n2.get("bounds")) or [0, 0, 0, 0]
            if nb[1] < lb[1] - 4 or nb[1] - lb[1] > 120:
                continue
            dist = abs(nb[1] - lb[1]) + abs(nb[0] - lb[2])
            if best is None or dist < best[0]:
                best = (dist, num)
        if best:
            out[kind] = best[1]
    return out


def parse_item_daily(nodes: list[dict]) -> list[dict]:
    """Punkty dzienne z drill-in pozycji — tylko gdy IG pokaże datę + liczbę.

    Hierarchia zwykle nie ma tooltipów wykresu (jak Ogólne). Bez dat
    nie zgadujemy sinusoidy.
    """
    texts = [(n, (n.get("text") or "").strip()) for n in nodes
             if (n.get("text") or "").strip()]
    points: list[dict] = []
    for i, (n, t) in enumerate(texts):
        day = _parse_axis_day(t)
        if day is None:
            continue
        lb = parse_bounds(n.get("bounds")) or [0, 0, 0, 0]
        best = None
        for j, (n2, t2) in enumerate(texts):
            if j == i:
                continue
            num = parse_ig_number(t2)
            if num is None:
                continue
            nb = parse_bounds(n2.get("bounds")) or [0, 0, 0, 0]
            dist = abs(nb[0] - lb[0]) + abs(nb[1] - lb[1])
            if dist > 220:
                continue
            if best is None or dist < best[0]:
                best = (dist, num)
        if best:
            points.append({
                "date": day,
                "views": best[1],
                "source": "live_tooltips",
            })
    points.sort(key=lambda p: p["date"])
    return points


def _parse_axis_day(text: str, *, year: int = 2026) -> str | None:
    """`3 wrz` / `6 sie` → YYYY-MM-DD. Śmieci → None."""
    s = (text or "").strip().casefold()
    months = {
        "sty": 1, "lut": 2, "mar": 3, "kwi": 4, "maj": 5, "cze": 6,
        "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "paz": 10,
        "lis": 11, "gru": 12,
    }
    # tesseract skleja „5wrz”; spacja opcjonalna, miesiąc z mapy
    m = re.match(r"^(\d{1,2})\s*([a-ząćęłńóśźż]{3})$", s)
    if not m:
        return None
    mon = months.get(m.group(2))
    if mon is None:
        return None
    try:
        return date(year, mon, int(m.group(1))).isoformat()
    except ValueError:
        return None


def load_type_chart_series(kind: str, recon_dir: Path | None = None) -> dict:
    """Opcjonalny ślad wykresu po filtrze typu: `CONTENT_SERIES_<kind>.json`."""
    k = (kind or "").strip().lower()
    if k not in CONTENT_KINDS:
        return {"daily": [], "source": None}
    root = Path(recon_dir or RECON_DIR)
    path = root / f"CONTENT_SERIES_{k}.json"
    if not path.exists():
        return {"daily": [], "source": None}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"daily": [], "source": None}
    points = []
    for rec in raw.get("daily") or []:
        if not isinstance(rec, dict) or not rec.get("date"):
            continue
        points.append({
            "date": rec["date"],
            "views": rec.get("views"),
            "source": rec.get("source") or raw.get("source") or "image_trace",
        })
    points.sort(key=lambda p: p["date"])
    return {
        "daily": points,
        "source": raw.get("source") or "image_trace",
        "note_pl": raw.get("note_pl") or "",
    }


def load_overview_windows(recon_dir: Path | None = None) -> dict:
    """Live KPI 7/14/30 z `OVERVIEW_WINDOWS.json` (follows/interakcje/widzowie)."""
    root = Path(recon_dir or RECON_DIR)
    path = root / "OVERVIEW_WINDOWS.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    windows = raw.get("windows") if isinstance(raw, dict) else None
    if not isinstance(windows, dict):
        return {}
    out: dict[str, dict] = {}
    for key in ("7", "14", "30"):
        snap = windows.get(key) or windows.get(int(key))
        if isinstance(snap, dict) and snap:
            out[key] = snap
    return out


def load_item_chart_series(recon_dir: Path | None = None) -> list[dict]:
    """Ślad wykresu drill-in: `ITEM_SERIES.json` → daily per pozycja.

    Bez pliku / bez jpg w rekordzie nie zgadujemy linii.
    """
    root = Path(recon_dir or RECON_DIR)
    path = root / "ITEM_SERIES.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []
    default_src = raw.get("source") or "image_trace"
    out: list[dict] = []
    for rec in raw.get("items") or []:
        if not isinstance(rec, dict):
            continue
        jpg = rec.get("jpg") or raw.get("jpg")
        if jpg and not (root / str(jpg)).exists():
            continue
        points = []
        for p in rec.get("daily") or []:
            if not isinstance(p, dict) or not p.get("date"):
                continue
            points.append({
                "date": p["date"],
                "views": p.get("views"),
                "source": p.get("source") or rec.get("source") or default_src,
            })
        points.sort(key=lambda x: x["date"])
        by_win = _points_by_window(rec.get("series_by_window"), default_src)
        typical = _normalize_point_list(rec.get("typical_daily"), default_src)
        typical_win = _points_by_window(rec.get("typical_by_window"), default_src)
        chart_kind = rec.get("chart_kind") or (
            "line" if len(points) >= 2 else (rec.get("chart_kind") or "none"))
        # pusta relacja / donut — zapisujemy wprost, nie zgadujemy linii
        if len(points) < 2 and chart_kind in ("donut", "none"):
            out.append({
                "id": rec.get("id") or "",
                "kind": (rec.get("kind") or "post").strip().lower(),
                "views": rec.get("views"),
                "caption_head": rec.get("caption_head") or "",
                "daily_mode": rec.get("daily_mode"),
                "daily": [],
                "typical_daily": [],
                "series_by_window": {},
                "typical_by_window": {},
                "chart_kind": chart_kind,
                "window_gaps": rec.get("window_gaps") or {},
                "ig_windows_identical": False,
                "source": rec.get("source") or default_src,
            })
            continue
        if len(points) < 2:
            continue
        # gdy nie ma per-okno — ta sama linia IG (chip nie zmienia overlayu)
        if not by_win and points:
            by_win = {k: list(points) for k in ("7", "14", "30")}
        if not typical_win and typical:
            typical_win = {k: list(typical) for k in ("7", "14", "30")}
        out.append({
            "id": rec.get("id") or "",
            "kind": (rec.get("kind") or "post").strip().lower(),
            "views": rec.get("views"),
            "caption_head": rec.get("caption_head") or "",
            "daily_mode": rec.get("daily_mode") or "cumulative",
            "daily": points,
            "typical_daily": typical,
            "series_by_window": by_win,
            "typical_by_window": typical_win,
            "chart_kind": rec.get("chart_kind") or "line",
            "window_gaps": rec.get("window_gaps") or {},
            "ig_windows_identical": bool(rec.get("ig_windows_identical")),
            "source": rec.get("source") or default_src,
        })
    return out


def _normalize_point_list(raw, default_src: str) -> list[dict]:
    """Lista {date, views} — tylko daty, bez zgadywania."""
    points = []
    for p in raw or []:
        if not isinstance(p, dict) or not p.get("date"):
            continue
        points.append({
            "date": p["date"],
            "views": p.get("views"),
            "source": p.get("source") or default_src,
        })
    points.sort(key=lambda x: x["date"])
    return points


def _points_by_window(raw, default_src: str) -> dict[str, list[dict]]:
    """Mapa okna 7/14/30 → seria datowana (ślad IG, nie wycinek KPI)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for key in ("7", "14", "30"):
        pts = _normalize_point_list(raw.get(key) or raw.get(int(key)), default_src)
        if len(pts) >= 2:
            out[key] = pts
    return out


def attach_item_series(rows: list[dict], recon_dir: Path | None = None) -> list[dict]:
    """Doklej ślad drill-in do wierszy seeda (id / views / początek captions)."""
    series = load_item_chart_series(recon_dir)
    if not series:
        return rows
    for rec in series:
        hit = _match_item_row(rows, rec)
        if hit is None:
            continue
        if rec.get("daily"):
            hit["daily"] = rec["daily"]
        if rec.get("daily_mode"):
            hit["daily_mode"] = rec["daily_mode"]
        if rec.get("typical_daily"):
            hit["typical_daily"] = rec["typical_daily"]
        if rec.get("series_by_window"):
            hit["series_by_window"] = rec["series_by_window"]
        if rec.get("typical_by_window"):
            hit["typical_by_window"] = rec["typical_by_window"]
        if rec.get("chart_kind"):
            hit["chart_kind"] = rec["chart_kind"]
        if rec.get("window_gaps"):
            hit["window_gaps"] = rec["window_gaps"]
        if rec.get("ig_windows_identical"):
            hit["ig_windows_identical"] = True
    return rows


def _match_item_row(rows: list[dict], rec: dict) -> dict | None:
    """Najpierw id, potem kind+views, na końcu początek captions."""
    want_id = (rec.get("id") or "").strip()
    if want_id:
        for row in rows:
            if row.get("id") == want_id:
                return row
    kind = rec.get("kind")
    views = rec.get("views")
    if views is not None:
        for row in rows:
            if row.get("kind") == kind and row.get("views") == views:
                return row
    head = (rec.get("caption_head") or "").strip().casefold()
    if head:
        for row in rows:
            cap = (row.get("caption") or "").split("\n")[0].strip().casefold()
            if cap.startswith(head) or head in cap:
                return row
    return None


def type_totals_from_rows(rows: list[dict]) -> dict[str, int]:
    """Suma views wierszy per kind — dokładniejsza niż „1,1 tys.”."""
    out: dict[str, int] = {}
    for r in rows or []:
        kind = (r.get("kind") or "").strip().lower()
        if kind not in CONTENT_KINDS:
            continue
        v = r.get("views")
        if v is None:
            continue
        try:
            out[kind] = out.get(kind, 0) + int(v)
        except (TypeError, ValueError):
            continue
    return out


def views_by_type_from_trial(raw) -> dict[str, int]:
    """Mapa trial/overview `views_by_type` → kind → int."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for label, val in raw.items():
        kind = _TYPE_LABELS.get(str(label).casefold())
        if not kind:
            continue
        num = parse_ig_number(val) if not isinstance(val, int) else val
        if num is not None:
            out[kind] = num
    return out


def seed_content_daily(*, rows: list[dict], views_by_type,
                       as_of_date: str,
                       recon_dir: Path | None = None) -> dict[str, list[dict]]:
    """Seria typu do seeda: ślad per typ jeśli jest, inaczej jeden punkt as_of.

    Rolki bez wierszy i bez śladu = pusta lista (nie zero na 30 dniach).
    """
    out: dict[str, list[dict]] = {k: [] for k in CONTENT_KINDS}
    for kind in CONTENT_KINDS:
        traced = load_type_chart_series(kind, recon_dir)
        if traced.get("daily"):
            out[kind] = traced["daily"]
    sums = type_totals_from_rows(rows)
    by_type = views_by_type_from_trial(views_by_type)
    for kind in CONTENT_KINDS:
        if out[kind]:
            continue
        # puste rolki — nie wstawiamy zera jako „serii”
        if kind == "reel" and not sums.get(kind):
            continue
        views = sums.get(kind)
        if views is None:
            views = by_type.get(kind)
        if views is None:
            continue
        if kind == "reel" and views == 0:
            continue
        out[kind] = [{
            "date": as_of_date,
            "views": views,
            "source": "as_of_total",
        }]
    return out


def load_chart_series(recon_dir: Path | None = None) -> dict:
    """Dzienna seria wyświetleń z `CHART_SERIES.json` (ślad 27_chart.jpg)."""
    root = Path(recon_dir or RECON_DIR)
    path = root / "CHART_SERIES.json"
    if not path.exists():
        return {"daily": [], "source": None, "as_of_date": None}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"daily": [], "source": None, "as_of_date": None}
    points = []
    for rec in raw.get("daily") or []:
        if not isinstance(rec, dict) or not rec.get("date"):
            continue
        points.append({
            "date": rec["date"],
            "views": rec.get("views"),
            "follows": rec.get("follows"),
            "interactions": rec.get("interactions"),
            "source": rec.get("source") or raw.get("source") or "image_trace",
        })
    points.sort(key=lambda p: p["date"])
    return {
        "daily": points,
        "source": raw.get("source") or "image_trace",
        "as_of_date": raw.get("as_of_date"),
        "range_label": raw.get("range_label") or "",
        "window_views": raw.get("window_views"),
        "note_pl": raw.get("note_pl") or "",
        "live_tooltips": bool(raw.get("live_tooltips")),
        "axis": raw.get("axis") or {},
    }


def _window_from_daily(points: list[dict], days: int, *,
                       as_of: str, as_of_date: str,
                       extra: dict | None = None) -> dict:
    """Snapshot okna 7/14/30 — suma views z tej samej serii, nie osobny KPI."""
    anchor = date.fromisoformat(as_of_date)
    by = {p["date"]: p for p in points}
    total = 0
    filled = 0
    first = last = None
    for i in range(days):
        key = (anchor - timedelta(days=days - 1 - i)).isoformat()
        rec = by.get(key)
        if rec is None or rec.get("views") is None:
            continue
        filled += 1
        total += int(rec["views"] or 0)
        if first is None:
            first = key
        last = key
    out = {
        "days": days,
        "as_of": as_of,
        "as_of_date": as_of_date,
        "range_label": _range_label_pl(first, last) if first and last else f"{days} dni",
        "views": total if filled else None,
        "follows": None,
        "interactions": None,
    }
    if extra:
        for k, v in extra.items():
            if v is not None:
                out[k] = v
    return out


def _range_label_pl(start_iso: str, end_iso: str) -> str:
    """`2026-08-06` + `2026-09-04` → `6 sie – 4 wrz` (jak chip IG)."""
    months = ("sty", "lut", "mar", "kwi", "maj", "cze",
              "lip", "sie", "wrz", "paź", "lis", "gru")

    def _one(iso: str) -> str:
        d = date.fromisoformat(iso)
        return f"{d.day} {months[d.month - 1]}"

    return f"{_one(start_iso)} – {_one(end_iso)}"


def seed_scrape_from_recon(recon_dir: Path | None = None) -> dict:
    """Złóż scrape z TRIAL_FETCH + slimów + serii z CHART_SERIES.

    Okno 30: snapshot KPI z trial (2,001 / −10 / 13).
    Okna 7/14: to samo `daily[]` — wycinek, nie trzy słupki KPI.
    Seria: ślad linii z 27_chart.jpg (hierarchia bez dat). Bez sinusoidy.
    Zawartość: Post/Relacja z trial + 11_live_content; Rolki = empty state.
    """
    root = Path(recon_dir or RECON_DIR)
    trial_path = root / "TRIAL_FETCH.json"
    trial = {}
    if trial_path.exists():
        trial = json.loads(trial_path.read_text(encoding="utf-8"))
    as_of = "2026-09-05T20:48:00"
    ov = trial.get("overview") or {}
    aud = trial.get("audience") or {}

    rows = _seed_content_rows(root, trial)
    series = load_chart_series(root)
    daily = series.get("daily") or []
    as_of_date = series.get("as_of_date") or "2026-09-04"

    axis = series.get("axis") or {}
    sparse = {
        "window_days": 30,
        "as_of": as_of,
        "axis": {
            "ymin": axis.get("ymin", 0),
            "ymax": axis.get("ymax", 626),
            "x_labels": axis.get("x_labels") or ["6 sie", "20 sie", "3 wrz"],
        },
        "note": series.get("note_pl") or "",
        "source": series.get("source") or "image_trace",
        "points_n": len(daily),
    }

    content_daily = seed_content_daily(
        rows=rows,
        views_by_type=ov.get("views_by_type"),
        as_of_date=as_of_date,
        recon_dir=root,
    )

    gaps = [
        "seria dzienna: hierarchia bez tooltipów — 30 punktów z image-trace "
        "27_chart.jpg (6 sie–4 wrz); live tooltipi nie dały liczb",
        "obserwacje/interakcje w czasie: brak wykresu w recon — nie wymyślono serii",
        "okna 7/14: wycinek tej samej serii (chip live nie przełączył się w trial)",
        "Zawartość: lista Posty/Relacje/Rolki nie ma wykresu — seria typu to "
        "suma okna na as_of (albo CONTENT_SERIES_<typ>.json gdy ślad filtra Ogólne)",
        "drill-in: każdy post/relacja z ITEM_SERIES (40_* albo 30_typical); "
        "14 na Zawartość może nie istnieć — wtedy luka albo identyczne JPG",
        "Rolki: empty state z 25_reels — brak serii, nie wstawiono zera",
    ]

    window30_extra = {
        "range_label": series.get("range_label") or "6 sie – 4 wrz",
        "views": parse_ig_number(ov.get("views")) or 2001,
        "follows": ov.get("net_followers", -10),
        "interactions": ov.get("interactions", 13),
        "viewers": ov.get("viewers", 267),
        "followers_pct": ov.get("followers_pct"),
        "nonfollowers_pct": ov.get("nonfollowers_pct"),
        "views_by_type": ov.get("views_by_type"),
        "interactions_by_type": ov.get("interactions_by_type"),
        "profile_activity": ov.get("profile_activity"),
        "followers_now": aud.get("followers"),
    }
    # live 7/14 (i odświeżone 30) z OVERVIEW_WINDOWS — nie tylko wycinek daily
    live_ov = load_overview_windows(root)
    windows = {
        "7": _window_from_daily(daily, 7, as_of=as_of, as_of_date=as_of_date,
                               extra=live_ov.get("7")),
        "14": _window_from_daily(daily, 14, as_of=as_of, as_of_date=as_of_date,
                                extra=live_ov.get("14")),
        "30": _window_from_daily(daily, 30, as_of=as_of, as_of_date=as_of_date,
                                extra={**window30_extra, **(live_ov.get("30") or {})}),
    }

    return {
        "as_of": as_of,
        "source": "seed",
        "ig_username": (trial.get("account") or "jobsaver.app"),
        "gaps": gaps,
        "daily": daily,
        "windows": windows,
        "chart_sparse": sparse,
        "content_daily": content_daily,
        "content": rows,
        "audience": {
            "followers": aud.get("followers"),
            "delta": aud.get("delta"),
            "gender": aud.get("gender"),
            "age": aud.get("age"),
            "countries": aud.get("countries"),
            "cities": aud.get("cities"),
            "peak_hours": aud.get("peak_hours"),
            "as_of": as_of,
        },
        "_recon_dir": str(root),
        "series_source": series.get("source") or "image_trace",
    }


def apply_seed_thumbs(store, scrape: dict, recon_dir: Path | None = None) -> dict:
    """Wytnij miniatury z JPG recon i zapisz obok JSON."""
    root = Path(recon_dir or scrape.get("_recon_dir") or RECON_DIR)
    by_views = {int(r["views"]): r for r in scrape.get("content") or []
                if r.get("views") is not None}
    # 1) dokładne bounds z 11_live_content (lista mieszana)
    mixed = root / "11_live_content.slim.json"
    if mixed.exists():
        for row in parse_content_rows(load_slim(mixed)):
            v = row.get("views")
            if v in by_views and row.get("age_bounds"):
                by_views[v]["age_bounds"] = row["age_bounds"]
                _maybe_crop(store, by_views[v], root / "11_live_content.jpg")
    # 2) listy po filtrze — równy rytm wierszy pod chipami
    _crop_filtered_list(store, by_views, root / "26_posts.jpg",
                        views_order=(379, 394, 42, 54, 278))
    _crop_filtered_list(store, by_views, root / "24_stories.jpg",
                        views_order=(147, 172, 162, 179, 196))
    # 3) dociągnięty 11b (ostatni post 278)
    scrolled = root / "11b_live_content_scrolled.jpg"
    slim_b = root / "11b_live_content_scrolled.slim.json"
    if scrolled.exists() and slim_b.exists():
        for row in parse_content_rows(load_slim(slim_b)):
            v = row.get("views")
            if v == 278 and v in by_views:
                by_views[v]["age_bounds"] = row.get("age_bounds")
                _maybe_crop(store, by_views[v], scrolled)
    return scrape


def _seed_content_rows(root: Path, trial: dict) -> list[dict]:
    """Typed listy Post/Relacja/Rolka — trial + parse slim (gdy jest)."""
    parsed: list[dict] = []
    slim = root / "11_live_content.slim.json"
    if slim.exists():
        parsed = parse_content_rows(load_slim(slim))
    slim_b = root / "11b_live_content_scrolled.slim.json"
    if slim_b.exists():
        parsed = _dedup_rows(parsed + parse_content_rows(load_slim(slim_b)))

    # Uzupełnij z trial, gdy parser coś zgubił
    by_views = {r.get("views"): r for r in parsed}
    for raw in (trial.get("content") or {}).get("rows") or []:
        v = parse_ig_number(raw.get("views"))
        if v is None or v in by_views:
            continue
        kind = _guess_kind(raw.get("caption"), v, None)
        rec = {
            "kind": kind,
            "age": raw.get("age") or "",
            "caption": raw.get("caption"),
            "views": v,
            "likes": raw.get("like") or 0,
            "comments": raw.get("comment"),
            "shares": raw.get("share") or 0,
            "saves": raw.get("save"),
            "id": content_id(kind, raw.get("caption"), v, raw.get("age")),
        }
        parsed.append(rec)
        by_views[v] = rec

    # Rolki: empty state z 25_reels — nie zgadujemy wierszy
    # ślad drill-in (Ten post) — tylko gdy jest ITEM_SERIES + jpg
    return attach_item_series(parsed, root)


def _crop_filtered_list(store, by_views: dict, jpg_path: Path,
                        views_order: tuple) -> None:
    """Gdy slim filtra nie ma tekstów wierszy — stały rytm pod chipami (y≈680)."""
    if not jpg_path.exists():
        return
    jpg = jpg_path.read_bytes()
    top = 680
    pitch = 188
    for i, views in enumerate(views_order):
        row = by_views.get(views)
        if not row or row.get("thumb"):
            continue
        y = top + i * pitch
        bounds = [_THUMB_X0, y, _THUMB_X0 + _THUMB_SIZE, y + _THUMB_SIZE]
        blob = crop_jpeg(jpg, bounds)
        if not blob:
            continue
        rel = store.save_thumb(row["id"], blob)
        row["thumb"] = rel


def _maybe_crop(store, row: dict, jpg_path: Path) -> None:
    if row.get("thumb") or not jpg_path.exists():
        return
    bounds = thumb_bounds_from_age(row.get("age_bounds"))
    blob = crop_jpeg(jpg_path.read_bytes(), bounds)
    if not blob:
        return
    row["thumb"] = store.save_thumb(row["id"], blob)


def _guess_kind(caption, views, nums) -> str:
    try:
        v = int(views)
    except (TypeError, ValueError):
        v = None
    if v in _POST_VIEWS:
        return "post"
    if v in _STORY_VIEWS:
        return "story"
    if caption:
        return "post"
    if nums is not None and len(nums) >= 4:
        return "post"
    if nums is not None and len(nums) == 3:
        return "story"
    return "post"


def _dedup_rows(rows: list[dict]) -> list[dict]:
    seen: set = set()
    out = []
    for r in rows:
        key = (r.get("kind"), r.get("views"), caption_key_safe(r.get("caption")))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def caption_key_safe(caption) -> str:
    return (caption or "").split("\n")[0].strip()[:60].casefold()


def _node_y(n: dict) -> int:
    xy = parse_bounds(n.get("bounds"))
    return xy[1] if xy else 0
