"""Store Insights pulpitu profesjonalnego — `accounts/<alias>/pro_dash/`.

To NIE jest `#statystyki` / `session_stats` / `system.stats_snapshot`.
Zapisujemy to, co IG pokazuje w Statystykach (jobsaver-style), żeby kolejne
scrape'y **dopisywały dni**, a nie tylko nadpisywały rolling 7/14/30.

Kształt `store.json`::

    {
      "version": 1,
      "account": "jobsaver",
      "ig_username": "jobsaver.app",
      "updated_at": "2026-09-05T20:48:00",
      "source": "seed" | "live" | "merge",
      "gaps": ["seria dzienna: brak w hierarchii …"],
      "daily": {
        "2026-09-04": {
          "date": "2026-09-04",
          "views": 12,
          "follows": -1,
          "interactions": 0,
          "source": "scrape" | "derived",
          "updated_at": "…"
        }
      },
      "windows": {
        "7"|"14"|"30": {
          "days": 30,
          "as_of": "2026-09-05T20:48:00",
          "as_of_date": "2026-09-05",
          "range_label": "6 sie – 4 wrz",
          "views": 2001,
          "follows": -10,
          "interactions": 13,
          "viewers": 267,
          …
        }
      },
      "chart_sparse": { "axis": {…}, "points": […], "note": "…" },
      "content_daily": {
        "post"|"reel"|"story": { "YYYY-MM-DD": { "date", "views", "source" } }
      },
      "content": { "post": […], "reel": […], "story": […] },
      "audience": {…}
    }

`content_daily[typ]` to seria wyświetleń **tego typu** (ten sam model upsert
co overview). Źródła: ślad wykresu po filtrze Ogólne→typ, suma
`item.daily` (gdy jest drill-in), albo suma okna na `as_of` (kolejne dni).
Nie mieszamy sumy okna z dzienną serią. Puste rolki = `{}`, bez sinusoidy.

Miniatury: `pro_dash/thumbs/<id>.jpg` (ścieżka względna w `item.thumb`).
Drill-in: `item.series_by_window` / `item.typical_by_window` (7/14/30)
osobno od `item.daily` — linia „Twój typowy post” nie miesza się z „Ten post”.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# Okna, które IG pokazuje chipami (180 = Meta One — nie zbieramy).
WINDOW_DAYS = (7, 14, 30)
CONTENT_KINDS = ("post", "reel", "story")
STORE_VERSION = 1
STORE_NAME = "store.json"

# Pola metryk dziennych — upsert nadpisuje tylko podane klucze.
_DAY_METRICS = ("views", "follows", "interactions")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _today() -> date:
    return date.today()


def _parse_date(value) -> date | None:
    """YYYY-MM-DD albo ISO z czasem → data; śmieci → None."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _atomic_write_json(path: Path, data: dict) -> None:
    """tmp + replace — jak PhraseUsageStore / publish_store."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def empty_store(account: str, ig_username: str = "") -> dict:
    """Pusty dokument — daily/windows gotowe na kolejne scrape'y."""
    return {
        "version": STORE_VERSION,
        "account": account,
        "ig_username": ig_username or "",
        "updated_at": None,
        "source": None,
        "gaps": [],
        "daily": {},
        "windows": {},
        "chart_sparse": None,
        "content_daily": {k: {} for k in CONTENT_KINDS},
        "content": {k: [] for k in CONTENT_KINDS},
        "audience": None,
    }


def content_id(kind: str, caption: str | None, views, age: str | None = "") -> str:
    """Stabilny id wiersza — kind + slug captions + views."""
    k = (kind or "post").strip().lower()
    if k not in CONTENT_KINDS:
        k = "post"
    head = (caption or "").split("\n")[0].strip() or (age or "") or str(views or "")
    slug = re.sub(r"[^a-z0-9]+", "_", head.casefold())
    slug = slug.strip("_")[:36] or "item"
    try:
        v = int(views)
    except (TypeError, ValueError):
        v = 0
    return f"{k}_{slug}_{v}"


def caption_key(caption: str | None) -> str:
    """Klucz dopasowania przy kolejnym scrape (zmieniają się views)."""
    return (caption or "").split("\n")[0].strip()[:60].casefold()


class ProDashStore:
    """JSON + miniatury pod `data/accounts/<alias>/pro_dash/`."""

    def __init__(self, account_id: str, data_root: Path):
        self.account_id = account_id
        self.data_root = Path(data_root)
        self.dir = self.data_root / "accounts" / account_id / "pro_dash"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path = self.dir / STORE_NAME
        self.thumbs_dir = self.dir / "thumbs"

    @property
    def file_path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def is_empty(self, data: dict | None = None) -> bool:
        """Brak okien, dni i zawartości — karta byłaby pusta."""
        doc = data if data is not None else self.read()
        if doc.get("windows") or doc.get("daily"):
            return False
        content = doc.get("content") or {}
        return not any(content.get(k) for k in CONTENT_KINDS)

    def read(self) -> dict:
        """Pełny dump; brak pliku → pusty szablon."""
        out = empty_store(self.account_id)
        if not self._path.exists():
            return out
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return out
        if not isinstance(raw, dict):
            return out
        out.update({k: raw[k] for k in raw})
        out.setdefault("daily", {})
        out.setdefault("windows", {})
        out.setdefault("gaps", [])
        content = out.get("content")
        if not isinstance(content, dict):
            content = {}
        for k in CONTENT_KINDS:
            content.setdefault(k, [])
        out["content"] = content
        # seria per typ — ten sam kształt co daily[]
        cd = out.get("content_daily")
        if not isinstance(cd, dict):
            cd = {}
        for k in CONTENT_KINDS:
            bucket = cd.get(k)
            cd[k] = bucket if isinstance(bucket, dict) else {}
        out["content_daily"] = cd
        return out

    def write(self, data: dict) -> dict:
        """Atomowy zapis. Zwraca to, co poszło na dysk."""
        data = dict(data)
        data["version"] = STORE_VERSION
        data["account"] = self.account_id
        data["updated_at"] = _now_iso()
        _atomic_write_json(self._path, data)
        return data

    def upsert_days(self, points: list[dict], *, as_of: str | None = None) -> dict:
        """Dopisz / nadpisz punkty dzienne. Overlap aktualizuje, nowe daty dołączają.

        Każdy punkt: `{date: YYYY-MM-DD, views?, follows?, interactions?, source?}`.
        Brak metryki w nowym punkcie nie kasuje starej wartości.
        """
        doc = self.read()
        doc["daily"] = upsert_daily_points(doc.get("daily") or {}, points,
                                           as_of=as_of)
        return self.write(doc)

    def rolling(self, days: int, as_of: date | None = None) -> dict:
        """Suma views/follows/interactions z serii dziennej w oknie N dni."""
        return rolling_from_daily(self.read().get("daily") or {}, days,
                                  as_of=as_of)

    def upsert_window(self, days: int, snapshot: dict) -> dict:
        """Zapisz karty KPI, które IG pokazał dla okna 7/14/30 (`as_of`)."""
        doc = self.read()
        doc["windows"] = dict(doc.get("windows") or {})
        doc["windows"][str(int(days))] = normalize_window(days, snapshot)
        return self.write(doc)

    def save_thumb(self, item_id: str, blob: bytes) -> str:
        """Zapisz JPEG miniatura → `thumbs/<id>.jpg`. Zwraca ścieżkę względną."""
        safe = _safe_item_id(item_id)
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        path = self.thumbs_dir / f"{safe}.jpg"
        path.write_bytes(blob)
        return f"thumbs/{safe}.jpg"

    def thumb_path(self, item_id: str) -> Path | None:
        """Absolutna ścieżka miniatury albo None."""
        rec = self.get_item(item_id)
        rel = (rec or {}).get("thumb") if rec else None
        if not rel:
            rel = f"thumbs/{_safe_item_id(item_id)}.jpg"
        path = self.dir / rel
        return path if path.exists() else None

    def get_item(self, item_id: str) -> dict | None:
        doc = self.read()
        want = _safe_item_id(item_id)
        for kind in CONTENT_KINDS:
            for row in doc.get("content", {}).get(kind) or []:
                if row.get("id") == want:
                    return row
        return None

    def merge_scrape(self, scrape: dict) -> dict:
        """Wlej wynik seeda / live: dni, okna, zawartość, luki, audience.

        Kolejne dni: `upsert_days`. Snapshot okna zostaje (przydatny gdy
        seria jest dziurawa). Opcjonalnie próbuje wyprowadzić wczorajszy
        punkt z nakładających się okien 7 — tylko gdy liczby się zgadzają.
        """
        doc = self.read()
        as_of = scrape.get("as_of") or _now_iso()
        src = scrape.get("source") or "merge"
        if scrape.get("ig_username"):
            doc["ig_username"] = scrape["ig_username"]
        doc["source"] = src

        # 1) punkty dzienne (gdy scrape je ma — zwykle nie przy pierwszym seedzie)
        if scrape.get("daily"):
            doc["daily"] = upsert_daily_points(
                doc.get("daily") or {}, scrape["daily"], as_of=as_of)

        # 2) snapshoty kart KPI
        windows_in = scrape.get("windows") or {}
        doc["windows"] = dict(doc.get("windows") or {})
        derived = None
        for key, snap in windows_in.items():
            days = int(key)
            if days not in WINDOW_DAYS:
                continue
            norm = normalize_window(days, {**snap, "as_of": snap.get("as_of") or as_of})
            prev_w = doc["windows"].get(str(days))
            # live bez liczby nie kasuje lepszego snapshotu (seed 2001 vs dump bez 2,001)
            if prev_w:
                norm = _merge_window_keep_numbers(prev_w, norm)
            if days == 7:
                derived = maybe_derive_last_day(doc, norm)
            doc["windows"][str(days)] = norm
        if derived:
            doc["daily"] = upsert_daily_points(
                doc.get("daily") or {}, [derived], as_of=as_of)

        # 3) oś wykresu (nie udajemy 30-punktowej sinusoidy)
        if scrape.get("chart_sparse"):
            doc["chart_sparse"] = scrape["chart_sparse"]

        # 4) wiersze zawartości + historia / daily pozycji
        for row in scrape.get("content") or []:
            _upsert_content_row(doc, row, as_of=as_of)

        # 5) seria per typ (ślad / live / suma item.daily / as_of)
        doc["content_daily"] = _merge_content_daily(
            doc.get("content_daily") or {},
            scrape,
            doc.get("content") or {},
            as_of=as_of,
        )

        if scrape.get("audience"):
            doc["audience"] = scrape["audience"]

        gaps = list(doc.get("gaps") or [])
        # gdy mamy serię dzienną — wyrzuć stare „brak w hierarchii / czekają na live”
        if len(doc.get("daily") or {}) >= 2:
            stale = (
                "seria dzienna: brak w hierarchii",
                "okna 7 i 14: brak dumpa",
                "live: seria dzienna nadal może nie być",
            )
            gaps = [g for g in gaps if not any(str(g).startswith(p) for p in stale)]
        for g in scrape.get("gaps") or []:
            if g and g not in gaps:
                gaps.append(g)
        doc["gaps"] = gaps
        return self.write(doc)

    def public_view(self, *, as_of: date | None = None) -> dict:
        """Dokument + rolling 7/14/30 z serii (do UI / API)."""
        doc = self.read()
        anchor = as_of or _window_anchor(doc) or _today()
        rolling = {str(d): rolling_from_daily(doc.get("daily") or {}, d,
                                              as_of=anchor)
                   for d in WINDOW_DAYS}
        # 7/14/30 na tej samej serii typu — nie trzy osobne KPI
        content_rolling = {}
        cd = doc.get("content_daily") or {}
        if not any(cd.get(k) for k in CONTENT_KINDS):
            cd = _derived_content_daily(doc)
        for kind in CONTENT_KINDS:
            content_rolling[kind] = {
                str(d): rolling_from_daily(cd.get(kind) or {}, d, as_of=anchor)
                for d in WINDOW_DAYS
            }
        return {
            **doc,
            "content_daily": cd,
            "rolling": rolling,
            "content_rolling": content_rolling,
            "content_series_meta": _content_series_meta(cd),
            "as_of_date": anchor.isoformat(),
        }


def upsert_daily_points(daily: dict, points: list[dict],
                        *, as_of: str | None = None) -> dict:
    """Czysta funkcja merge dni — overlap aktualizuje, nowe daty dołączają."""
    out = {str(k): dict(v) for k, v in (daily or {}).items()
           if isinstance(v, dict)}
    ts = as_of or _now_iso()
    for raw in points or []:
        if not isinstance(raw, dict):
            continue
        day = _parse_date(raw.get("date") or raw.get("day"))
        if day is None:
            continue
        key = day.isoformat()
        prev = dict(out.get(key) or {})
        rec = {
            **prev,
            "date": key,
            "updated_at": ts,
        }
        for m in _DAY_METRICS:
            if m in raw and raw[m] is not None:
                rec[m] = _as_int(raw[m])
        if raw.get("source"):
            rec["source"] = raw["source"]
        elif not rec.get("source"):
            rec["source"] = "scrape"
        out[key] = rec
    return dict(sorted(out.items()))


def rolling_from_daily(daily: dict, days: int,
                       as_of: date | None = None) -> dict:
    """Suma metryk z ostatnich `days` kalendarzowych (nie „ostatnie N punktów”).

    Dzień bez wpisu = dziura, nie zero. `complete` mówi, czy seria jest pełna.
    Rolling views to suma znanych dni — UI pokazuje też snapshot kart IG.
    """
    days = int(days)
    anchor = as_of or _today()
    views = follows = interactions = 0
    filled = 0
    points: list[dict] = []
    for i in range(days):
        d = (anchor - timedelta(days=days - 1 - i)).isoformat()
        row = (daily or {}).get(d)
        if isinstance(row, dict) and any(row.get(m) is not None for m in _DAY_METRICS):
            filled += 1
            views += int(row.get("views") or 0)
            follows += int(row.get("follows") or 0)
            interactions += int(row.get("interactions") or 0)
            points.append({
                "date": d,
                "views": row.get("views"),
                "follows": row.get("follows"),
                "interactions": row.get("interactions"),
                "source": row.get("source"),
            })
        else:
            points.append({
                "date": d, "views": None, "follows": None,
                "interactions": None, "source": None,
            })
    return {
        "days": days,
        "as_of_date": anchor.isoformat(),
        "views": views if filled else None,
        "follows": follows if filled else None,
        "interactions": interactions if filled else None,
        "points_filled": filled,
        "complete": filled == days,
        "points": points,
    }


def _merge_window_keep_numbers(old: dict, new: dict) -> dict:
    """Nowe pole wygrywa tylko gdy nie jest None — dziurawy live nie zeruje seeda."""
    out = dict(old)
    for k, v in (new or {}).items():
        if v is None or v == "":
            continue
        out[k] = v
    return out


def normalize_window(days: int, snapshot: dict) -> dict:
    """Ujednolicony snapshot kart KPI dla jednego chipa 7/14/30."""
    snap = dict(snapshot or {})
    as_of = snap.get("as_of") or _now_iso()
    as_of_date = snap.get("as_of_date") or str(as_of)[:10]
    out = {
        "days": int(days),
        "as_of": as_of,
        "as_of_date": as_of_date,
        "range_label": snap.get("range_label") or "",
        "views": _as_int(snap.get("views")),
        "follows": _as_int(snap.get("follows") if snap.get("follows") is not None
                           else snap.get("net_followers")),
        "interactions": _as_int(snap.get("interactions")),
        "viewers": _as_int(snap.get("viewers")),
    }
    for extra in ("followers_pct", "nonfollowers_pct", "views_by_type",
                  "interactions_by_type", "profile_activity", "followers_now"):
        if snap.get(extra) is not None:
            out[extra] = snap[extra]
    return out


def maybe_derive_last_day(doc: dict, new_win7: dict) -> dict | None:
    """Wczorajszy punkt z nakładki dwóch okien 7 — tylko gdy liczby spójne.

    new_7 - old_7 + dzień, który wypadł z okna = nowy dzień.
    Bez dnia, który wypadł, albo gdy wynik ujemny / większy niż nowe 7 —
    nie zgadujemy (żadnej sinusoidy).
    """
    prev = (doc.get("windows") or {}).get("7")
    if not isinstance(prev, dict):
        return None
    prev_d = _parse_date(prev.get("as_of_date") or prev.get("as_of"))
    new_d = _parse_date(new_win7.get("as_of_date") or new_win7.get("as_of"))
    old_v = _as_int(prev.get("views"))
    new_v = _as_int(new_win7.get("views"))
    if prev_d is None or new_d is None or old_v is None or new_v is None:
        return None
    if (new_d - prev_d).days != 1:
        return None
    dropped = (prev_d - timedelta(days=6)).isoformat()
    dropped_row = (doc.get("daily") or {}).get(dropped) or {}
    dropped_v = _as_int(dropped_row.get("views"))
    if dropped_v is None:
        return None
    last = new_v - old_v + dropped_v
    if last < 0 or last > new_v:
        return None
    return {
        "date": new_d.isoformat(),
        "views": last,
        "source": "derived",
    }


def _upsert_content_row(doc: dict, row: dict, *, as_of: str) -> None:
    """Dodaj / zaktualizuj wiersz Post/Rolka/Relacja i dopisz historię views."""
    kind = (row.get("kind") or "post").strip().lower()
    if kind not in CONTENT_KINDS:
        kind = "post"
    content = doc.setdefault("content", {k: [] for k in CONTENT_KINDS})
    bucket = list(content.get(kind) or [])
    cid = row.get("id") or content_id(
        kind, row.get("caption"), row.get("views"), row.get("age"))
    cid = _safe_item_id(cid)
    idx = _find_content_index(bucket, cid, kind, row)
    hist_point = {
        "as_of": as_of,
        "views": _as_int(row.get("views")),
        "likes": _as_int(row.get("likes")),
        "comments": _as_int(row.get("comments")),
        "shares": _as_int(row.get("shares")),
        "saves": _as_int(row.get("saves")),
    }
    incoming_daily = _as_daily_points(row.get("daily"))
    if idx is None:
        rec = _normalize_content(kind, cid, row, as_of)
        rec["history"] = [hist_point]
        rec["daily"] = upsert_daily_points({}, incoming_daily, as_of=as_of)
        _merge_item_window_series(rec, row, as_of=as_of)
        bucket.append(rec)
    else:
        rec = dict(bucket[idx])
        rec.update({k: v for k, v in _normalize_content(kind, cid, row, as_of).items()
                    if v is not None and k not in (
                        "history", "daily", "typical_daily",
                        "series_by_window", "typical_by_window",
                        "window_gaps")})
        if row.get("daily_mode"):
            rec["daily_mode"] = row["daily_mode"]
        if row.get("chart_kind"):
            rec["chart_kind"] = row["chart_kind"]
        if row.get("window_gaps"):
            rec["window_gaps"] = {**(rec.get("window_gaps") or {}),
                                  **row["window_gaps"]}
        if row.get("ig_windows_identical"):
            rec["ig_windows_identical"] = True
        # nie kasuj istniejącej miniatury, gdy nowy scrape jej nie ma
        if row.get("thumb"):
            rec["thumb"] = row["thumb"]
        hist = list(rec.get("history") or [])
        last = hist[-1] if hist else None
        if not last or last.get("as_of") != as_of or last.get("views") != hist_point["views"]:
            hist.append(hist_point)
        rec["history"] = hist
        # dzienna seria pozycji (drill-in) — upsert, nie nadpisuj całości
        if incoming_daily:
            rec["daily"] = upsert_daily_points(rec.get("daily") or {}, incoming_daily,
                                               as_of=as_of)
        elif not isinstance(rec.get("daily"), dict):
            rec["daily"] = {}
        _merge_item_window_series(rec, row, as_of=as_of)
        bucket[idx] = rec
    content[kind] = bucket
    doc["content"] = content


def _find_content_index(bucket: list, cid: str, kind: str, row: dict) -> int | None:
    """Najpierw id, potem kind+caption (views mogły urosnąć)."""
    for i, rec in enumerate(bucket):
        if rec.get("id") == cid:
            return i
    key = caption_key(row.get("caption"))
    if not key:
        return None
    for i, rec in enumerate(bucket):
        if caption_key(rec.get("caption")) == key:
            return i
    return None


def _normalize_content(kind: str, cid: str, row: dict, as_of: str) -> dict:
    return {
        "id": cid,
        "kind": kind,
        "age": row.get("age") or "",
        "caption": row.get("caption"),
        "views": _as_int(row.get("views")),
        "likes": _as_int(row.get("likes")),
        "comments": _as_int(row.get("comments")),
        "shares": _as_int(row.get("shares")),
        "saves": _as_int(row.get("saves")),
        "replies": _as_int(row.get("replies")),
        "thumb": row.get("thumb"),
        "updated_at": as_of,
        "daily": {},
        "daily_mode": row.get("daily_mode") or None,
        "typical_daily": {},
        "series_by_window": {},
        "typical_by_window": {},
        "chart_kind": row.get("chart_kind") or None,
        "window_gaps": dict(row.get("window_gaps") or {}),
        "ig_windows_identical": bool(row.get("ig_windows_identical")),
        "history": list(row.get("history") or []),
    }


def _merge_item_window_series(rec: dict, row: dict, *, as_of: str) -> None:
    """Zapisz serie 7/14/30 i linię „Twój typowy post” — osobno od daily."""
    incoming_typ = _as_daily_points(row.get("typical_daily"))
    if incoming_typ:
        rec["typical_daily"] = upsert_daily_points(
            rec.get("typical_daily") if isinstance(rec.get("typical_daily"), dict)
            else {},
            incoming_typ, as_of=as_of)
    by = row.get("series_by_window") or {}
    typ_by = row.get("typical_by_window") or {}
    if isinstance(by, dict) and by:
        rec["series_by_window"] = _upsert_window_map(
            rec.get("series_by_window"), by, as_of=as_of)
    if isinstance(typ_by, dict) and typ_by:
        rec["typical_by_window"] = _upsert_window_map(
            rec.get("typical_by_window"), typ_by, as_of=as_of)


def _upsert_window_map(existing, incoming: dict, *, as_of: str) -> dict:
    """Mapa okna → daily dict; seria z kadru zastępuje stary ślad/OCR."""
    out = dict(existing or {}) if isinstance(existing, dict) else {}
    for key in ("7", "14", "30"):
        pts = _as_daily_points(incoming.get(key) or incoming.get(int(key)))
        if len(pts) < 2:
            continue
        in_src = {p.get("source") for p in pts if p.get("source")}
        # nowy odczyt osi+pikseli — nie dopisuj starych dni z image_trace/OCR
        if in_src & {"chart_pixels", "live_tooltips"}:
            out[key] = upsert_daily_points({}, pts, as_of=as_of)
            continue
        prev = out.get(key) if isinstance(out.get(key), dict) else {}
        out[key] = upsert_daily_points(prev, pts, as_of=as_of)
    return out


def _window_anchor(doc: dict) -> date | None:
    """Kotwica rolling: najnowszy as_of_date z okien, nie „dziś” z hosta."""
    best = None
    for snap in (doc.get("windows") or {}).values():
        if not isinstance(snap, dict):
            continue
        d = _parse_date(snap.get("as_of_date") or snap.get("as_of"))
        if d and (best is None or d > best):
            best = d
    return best


# Źródła dziennej serii — nie mieszamy sumy okna z image-trace.
_DAILY_SOURCES = frozenset((
    "image_trace", "live_tooltips", "chart_pixels", "item_sum", "scrape",
))
_ASOF_SOURCES = frozenset(("as_of_total", "window_total", "live_window"))


def _as_daily_points(daily) -> list[dict]:
    """Lista punktów z dict `{date: rec}` albo z listy scrape'a."""
    if not daily:
        return []
    if isinstance(daily, dict):
        out = []
        for k, v in daily.items():
            if isinstance(v, dict):
                rec = dict(v)
                rec.setdefault("date", v.get("date") or k)
                out.append(rec)
            elif v is not None:
                out.append({"date": k, "views": v})
        return out
    return [p for p in daily if isinstance(p, dict)]


def _merge_content_daily(existing: dict, scrape: dict, content: dict,
                         *, as_of: str) -> dict:
    """Złóż serię per typ: jawna z scrape, potem suma item.daily, na końcu as_of.

    Suma okna (`as_of_total`) nie nadpisuje dziennej serii z wykresu.
    Puste rolki zostają `{}` — bez zera-sinusoidy.
    """
    out = {k: dict((existing or {}).get(k) or {}) for k in CONTENT_KINDS}
    incoming = scrape.get("content_daily") or {}
    for kind in CONTENT_KINDS:
        raw = incoming.get(kind)
        points = _as_daily_points(raw)
        if not points:
            continue
        out[kind] = _merge_type_daily(out[kind], points, as_of=as_of)

    # suma item.daily — tylko gdy pozycje mają prawdziwą serię (≥2 dat)
    derived = _sum_item_dailies(content)
    for kind in CONTENT_KINDS:
        if not derived[kind]:
            continue
        exist_src = _sources_of(out[kind])
        if exist_src & _DAILY_SOURCES:
            continue
        out[kind] = _merge_type_daily(out[kind], derived[kind], as_of=as_of)
    return out


def _merge_type_daily(existing: dict, incoming: list[dict], *, as_of: str) -> dict:
    """Upsert dni typu; suma okna nie psuje image-trace / item_sum."""
    exist_src = _sources_of(existing)
    in_src = {p.get("source") for p in incoming if p.get("source")}
    if exist_src & _DAILY_SOURCES and in_src and in_src <= _ASOF_SOURCES:
        return existing
    return upsert_daily_points(existing, incoming, as_of=as_of)


def _sources_of(daily: dict) -> set:
    return {r.get("source") for r in (daily or {}).values()
            if isinstance(r, dict) and r.get("source")}


def _sum_item_dailies(content: dict) -> dict[str, list[dict]]:
    """Suma wyświetleń pozycji po dacie — tylko gdy jest seria drill-in.

    Wykres „Ten post” jest skumulowany — do sumy typu bierzemy przyrosty,
    żeby rolling nie dodał 0+367+376+379.
    """
    out: dict[str, list[dict]] = {k: [] for k in CONTENT_KINDS}
    for kind in CONTENT_KINDS:
        acc: dict[str, int] = {}
        dates_n = 0
        n_rows = 0
        n_series = 0
        for row in (content or {}).get(kind) or []:
            n_rows += 1
            incs = _item_daily_increments(row)
            if len(incs) >= 2:
                n_series += 1
            dates_n = max(dates_n, len(incs))
            for d, v in incs.items():
                acc[d] = acc.get(d, 0) + v
        # jedna pozycja ze śladem nie udaje sumy całego bloku Posty
        if n_rows > 1 and n_series < n_rows:
            continue
        # jedna data na wszystkich pozycjach to nie seria w czasie
        if dates_n < 2 or not acc:
            continue
        out[kind] = [
            {"date": d, "views": acc[d], "source": "item_sum"}
            for d in sorted(acc)
        ]
    return out


def _item_daily_increments(row: dict) -> dict[str, int]:
    """Punkty pozycji → przyrosty dzienne (skumulowane „Ten post” → diff)."""
    daily = row.get("daily") or {}
    if not isinstance(daily, dict):
        return {}
    keys = sorted(
        k for k, v in daily.items()
        if isinstance(v, dict) and v.get("views") is not None)
    if len(keys) < 2:
        return {}
    vals = [int(daily[k]["views"] or 0) for k in keys]
    mode = (row.get("daily_mode") or "").strip().lower()
    total = _as_int(row.get("views"))
    mono = all(vals[i] <= vals[i + 1] + 1 for i in range(len(vals) - 1))
    looks_cum = mode == "cumulative" or (
        mono and total is not None and vals[-1] >= int(total * 0.9))
    if looks_cum:
        out = {keys[0]: max(0, vals[0])}
        for i in range(1, len(keys)):
            out[keys[i]] = max(0, vals[i] - vals[i - 1])
        return out
    return {k: vals[i] for i, k in enumerate(keys)}


def _derived_content_daily(doc: dict) -> dict:
    """Gdy store nie ma content_daily — jeden punkt z sumy wierszy / views_by_type.

    Nie wymyśla 30 dni. Puste rolki zostają puste.
    """
    out = {k: {} for k in CONTENT_KINDS}
    anchor = _window_anchor(doc)
    as_of_date = (anchor or _today()).isoformat()
    raw_bt = {}
    for snap in (doc.get("windows") or {}).values():
        if isinstance(snap, dict) and snap.get("views_by_type"):
            raw_bt = snap["views_by_type"]
            break
    label_to_kind = {"Posty": "post", "Relacje": "story", "Rolki": "reel"}
    by_type = {}
    for label, kind in label_to_kind.items():
        v = raw_bt.get(label) if isinstance(raw_bt, dict) else None
        if v is None:
            continue
        n = _as_int(v)
        if n is not None:
            by_type[kind] = n
    for kind in CONTENT_KINDS:
        rows = (doc.get("content") or {}).get(kind) or []
        total = 0
        filled = False
        for r in rows:
            if r.get("views") is None:
                continue
            filled = True
            total += int(r.get("views") or 0)
        if not filled:
            if kind == "reel":
                continue
            if kind not in by_type:
                continue
            total = by_type[kind]
        if kind == "reel" and (not filled or total == 0):
            continue
        out[kind] = upsert_daily_points({}, [{
            "date": as_of_date,
            "views": total,
            "source": "as_of_total",
        }])
    return out


def _content_series_meta(content_daily: dict) -> dict:
    """Źródło / liczba punktów — UI nie zgaduje skąd linia."""
    meta = {}
    for kind in CONTENT_KINDS:
        daily = (content_daily or {}).get(kind) or {}
        srcs = sorted(_sources_of(daily))
        meta[kind] = {
            "points_n": len(daily),
            "source": srcs[0] if len(srcs) == 1 else ("mix" if srcs else None),
            "sources": srcs,
        }
    return meta


def _safe_item_id(item_id: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(item_id or "item"))
    return s.strip("._")[:80] or "item"


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(round(float(str(value).replace(" ", "").replace(",", "."))))
    except (TypeError, ValueError):
        return None
