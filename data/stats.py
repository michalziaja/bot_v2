"""Statystyki — co bot faktycznie zrobił, policzone z plików, które już powstają.

Nic tu nie zapisujemy i niczego nie dopisujemy do logowania: wszystkie liczby
pochodzą z `events.jsonl`, `raw_profiles.jsonl` i `visited_profiles.jsonl`.

**Akcje liczy `limits.classify_action`** — ta sama funkcja, którą limiter blokuje
działania. To jest cała ochrona przed sytuacją, w której panel pokazuje jedną
liczbę, a bot egzekwuje inną.

Dwie rzeczy, które widok musi powiedzieć wprost, bo inaczej liczby wprowadzą
w błąd:
  - `stats_snapshot` bywa wyłączony dla końca sesji, więc krzywa wzrostu konta
    jest dziurawa — brak danych to nie to samo co brak wzrostu,
  - w logu są dwa rodzaje przebiegów: `queue.*` (pluginy) i starsze `session.*`
    (flow, dziś testy regresyjne kroków). Mieszanie ich zawyżałoby statystykę
    pracy konta, więc flow liczymy osobno.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from v2.core.limits import classify_action

TS_FMT = "%Y-%m-%dT%H:%M:%S"
ACTION_KINDS = ("like", "follow", "comment")

# kind akcji → klucz limitu dziennego w configu konta
_DAILY_LIMIT_KEY = {"like": "daily_likes", "follow": "daily_follows",
                    "comment": "daily_comments", "dm": "daily_dms"}


# ============================================================
# wczytywanie
# ============================================================
def account_dir(account_id: str, data_root) -> Path:
    return Path(data_root) / "accounts" / account_id


def load_events(account_id: str, data_root, since_days: int = 7) -> list[dict]:
    """Zdarzenia z ostatnich N dni. Uszkodzone linie pomijamy w ciszy —
    log jest append-only i przerwany zapis nie może wywrócić statystyk."""
    return _read_jsonl(account_dir(account_id, data_root) / "events.jsonl",
                       since_days)


def load_profiles(account_id: str, data_root, since_days: int = 7) -> list[dict]:
    return _read_jsonl(account_dir(account_id, data_root) / "raw_profiles.jsonl",
                       since_days)


def load_visited(account_id: str, data_root, since_days: int = 7) -> list[dict]:
    return _read_jsonl(account_dir(account_id, data_root) / "visited_profiles.jsonl",
                       since_days)


def _read_jsonl(path: Path, since_days: int) -> list[dict]:
    if not Path(path).exists():
        return []
    cutoff = _day_start(datetime.now()) - timedelta(days=max(since_days - 1, 0))
    out = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            ts = _parse_ts(row.get("ts"))
            if ts is None or ts >= cutoff:
                out.append(row)
    return out


# ============================================================
# agregaty
# ============================================================
def daily_summary(events: list[dict], profiles: list[dict],
                  visited: list[dict], limits: dict | None = None,
                  days: int = 7, today: datetime | None = None) -> dict:
    """Dzień po dniu: akcje, zużycie limitów, zadania, zebrane profile, czas pracy."""
    limits = limits or {}
    today = today or datetime.now()
    dates = [( _day_start(today) - timedelta(days=i)).date().isoformat()
             for i in range(days)][::-1]
    blank = {d: _blank_day(d) for d in dates}

    for e in events:
        day = _day_of(e.get("ts"))
        if day not in blank:
            continue
        row = blank[day]

        kind = classify_action(e)
        if kind:
            row["actions"][kind] = row["actions"].get(kind, 0) + 1

        ekind = e.get("kind")
        payload = e.get("payload") or {}
        if ekind == "queue.task_end":
            status = payload.get("status") or "ok"
            row["tasks"][status] = row["tasks"].get(status, 0) + 1
            row["stopped_by"][payload.get("stopped_by") or "?"] = \
                row["stopped_by"].get(payload.get("stopped_by") or "?", 0) + 1
            row["active_s"] += float(payload.get("duration_s") or 0)
            if status == "failed":
                row["errors"].append({"plugin": payload.get("plugin"),
                                      "error": (payload.get("error") or "")[:200]})
        elif ekind == "queue.start":
            row["sessions"] += 1
        elif ekind == "session.start":
            # przebieg flow (test regresyjny kroków) — liczony osobno
            row["flow_runs"] += 1

    # profile zebrane — dedup po nicku W OBRĘBIE DNIA; ten sam profil zebrany
    # dwa razy tego samego dnia to jedna zdobycz, nie dwie
    per_day_seen: dict = defaultdict(set)
    for p in profiles:
        day = _day_of(p.get("ts"))
        if day not in blank or not p.get("username"):
            continue
        if p["username"] in per_day_seen[day]:
            continue
        per_day_seen[day].add(p["username"])
        blank[day]["profiles_collected"] += 1
        src = p.get("source") or "?"
        blank[day]["profiles_by_source"][src] = \
            blank[day]["profiles_by_source"].get(src, 0) + 1

    for v in visited:
        day = _day_of(v.get("ts"))
        if day in blank:
            blank[day]["profiles_visited"] += 1

    for d in dates:
        blank[d]["active_s"] = round(blank[d]["active_s"], 1)
        blank[d]["limits"] = _limit_usage(blank[d]["actions"], limits)

    days_list = [blank[d] for d in dates]
    return {"days": days_list, "totals": _totals(days_list),
            "today": days_list[-1] if days_list else None}


def plugin_summary(events: list[dict], days: int = 7,
                   today: datetime | None = None) -> list[dict]:
    """Per plugin: uruchomienia, jednostki, mediana czasu, powody zatrzymania, błędy.

    To jest widok odpowiadający na pytanie „co się psuje" — dlatego mediana,
    a nie średnia: jeden przebieg ucięty błędem po 3 sekundach nie ma zaniżać
    obrazu reszty.
    """
    today = today or datetime.now()
    cutoff = _day_start(today) - timedelta(days=max(days - 1, 0))
    acc: dict = {}

    for e in events:
        if e.get("kind") != "queue.task_end":
            continue
        ts = _parse_ts(e.get("ts"))
        if ts is not None and ts < cutoff:
            continue
        p = e.get("payload") or {}
        pid = p.get("plugin") or "?"
        row = acc.setdefault(pid, {"plugin": pid, "runs": 0, "items": 0,
                                   "failed": 0, "durations": [],
                                   "stopped_by": Counter(), "errors": []})
        row["runs"] += 1
        row["items"] += int(p.get("items_done") or 0)
        row["stopped_by"][p.get("stopped_by") or "?"] += 1
        if p.get("duration_s"):
            row["durations"].append(float(p["duration_s"]))
        if (p.get("status") or "") == "failed":
            row["failed"] += 1
            if p.get("error"):
                row["errors"].append(str(p["error"])[:200])

    out = []
    for row in acc.values():
        out.append({"plugin": row["plugin"], "runs": row["runs"],
                    "items": row["items"], "failed": row["failed"],
                    "median_s": _median(row["durations"]),
                    "total_s": round(sum(row["durations"]), 1),
                    "stopped_by": dict(row["stopped_by"]),
                    "errors": row["errors"][:5]})
    return sorted(out, key=lambda r: -r["runs"])


def account_growth(events: list[dict]) -> dict:
    """Odczyty `stats_snapshot` w czasie: posty / obserwujący / obserwowani.

    Zwraca też `enabled_hint` — bo pusta lista znaczy „krok wyłączony", a nie
    „konto nie rośnie", i widok musi umieć to rozróżnić.
    """
    points, skipped = extract_stats_snapshot_points(events)
    return {"points": points, "skipped": skipped,
            "enabled_hint": None if points else
            "brak odczytów — sprawdź toggles.steps.stats_snapshot w configu konta"}


def extract_stats_snapshot_points(
    events: list[dict],
    *,
    session_id: str | None = None,
) -> tuple[list[dict], int]:
    """Wyciągnij punkty followers/following/posts z eventów.

    Źródła (dedup po session_id+ts+wartościach — step i kind=stats_snapshot
    lecą parami z tym samym payloadem):
      - kind ``stats_snapshot`` → payload {posts,followers,following}
      - kind ``step`` + step=system.stats_snapshot + status=ok → payload.payload
    """
    points: list[dict] = []
    skipped = 0
    seen: set[tuple] = set()
    want = str(session_id) if session_id else None
    for e in events:
        if want is not None and str(e.get("session_id") or "") != want:
            continue
        parsed = _parse_stats_snapshot_event(e)
        if parsed is None:
            # policz skipped tylko dla jawnie nieudanych / pominiętych kroków
            p = e.get("payload") or {}
            if (e.get("kind") == "step"
                    and p.get("step") == "system.stats_snapshot"
                    and p.get("status") != "ok"):
                skipped += 1
            continue
        key = (parsed.get("session_id"), parsed.get("ts"),
               parsed.get("posts"), parsed.get("followers"),
               parsed.get("following"))
        if key in seen:
            continue
        seen.add(key)
        points.append(parsed)
    points.sort(key=lambda r: r.get("ts") or "")
    return points, skipped


def _parse_stats_snapshot_event(e: dict) -> dict | None:
    """Jeden event → punkt albo None (nie-snapshot / brak liczników)."""
    kind = e.get("kind")
    p = e.get("payload") or {}
    inner = None
    if kind == "stats_snapshot":
        inner = p
    elif kind == "step" and p.get("step") == "system.stats_snapshot":
        if p.get("status") != "ok":
            return None
        inner = p.get("payload") or {}
    else:
        return None
    if not isinstance(inner, dict):
        return None
    if not any(k in inner for k in ("posts", "followers", "following")):
        return None
    return {
        "ts": e.get("ts"),
        "session_id": e.get("session_id"),
        "posts": inner.get("posts"),
        "followers": inner.get("followers"),
        "following": inner.get("following"),
    }


def summarize_account_stats(points: list[dict]) -> dict:
    """Start / koniec / delta z listy punktów (chronologicznie).

    Jeden punkt (typowo tylko start sesji) → ``end`` i ``delta`` = None.
    Więcej punktów → pierwszy vs ostatni (sesja start+end albo okno 24h).
    """
    if not points:
        return {
            "start": None,
            "end": None,
            "delta": None,
            "points_count": 0,
            "enabled_hint": (
                "brak odczytów — sprawdź toggles.steps.stats_snapshot "
                "w configu konta"
            ),
        }
    start = points[0]
    end = points[-1] if len(points) > 1 else None
    delta = None
    if end is not None:
        delta = {
            "posts": _int_delta(end.get("posts"), start.get("posts")),
            "followers": _int_delta(end.get("followers"), start.get("followers")),
            "following": _int_delta(end.get("following"), start.get("following")),
        }
    return {
        "start": start,
        "end": end,
        "delta": delta,
        "points_count": len(points),
        "enabled_hint": None,
    }


def load_account_stats(
    account_id: str,
    data_root,
    *,
    session_id: str | None = None,
    since_days: int | None = 14,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict:
    """Odczyt followers/following z events.jsonl pod panel / CLI.

    Filtry (łączone AND gdy podane):
      - ``session_id`` — tylko ta sesja
      - ``since_days`` — jak load_events (None = cały plik)
      - ``window_start`` / ``window_end`` — kotwica ts ∈ [start, end]
    """
    path = account_dir(account_id, data_root) / "events.jsonl"
    # Tylko eventy snapshotów — nie ładujemy wszystkich stepów z okna
    events = _read_stats_snapshot_events(
        path,
        since_days=since_days,
        session_id=session_id,
        window_start=window_start,
        window_end=window_end,
    )
    points, skipped = extract_stats_snapshot_points(events, session_id=session_id)
    out = summarize_account_stats(points)
    out["skipped"] = skipped
    return out


def full_report(account_id: str, data_root, days: int = 7,
                limits: dict | None = None) -> dict:
    """Komplet w jednym wywołaniu — używane przez CLI i panel, żeby oba
    pokazywały dokładnie to samo."""
    events = load_events(account_id, data_root, days)
    return {
        "account": account_id,
        "days": days,
        "generated_at": datetime.now().strftime(TS_FMT),
        "daily": daily_summary(events,
                               load_profiles(account_id, data_root, days),
                               load_visited(account_id, data_root, days),
                               limits, days),
        "plugins": plugin_summary(events, days),
        "growth": account_growth(events),
    }


# ============================================================
# helpers
# ============================================================
def _int_delta(later, earlier) -> int | None:
    """Różnica intów; None gdy którykolwiek odczyt brakuje."""
    if later is None or earlier is None:
        return None
    try:
        return int(later) - int(earlier)
    except (TypeError, ValueError):
        return None


def _read_stats_snapshot_events(
    path: Path,
    *,
    since_days: int | None = None,
    session_id: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[dict]:
    """Stream events.jsonl → tylko stats_snapshot (+ step system.stats_snapshot).

    Unika wciągania wszystkich stepów sesji (profile.visit itd.) przy agregacji.
    """
    if not Path(path).exists():
        return []
    cutoff = None
    if since_days is not None:
        cutoff = _day_start(datetime.now()) - timedelta(days=max(since_days - 1, 0))
    want_sid = str(session_id) if session_id else None
    out = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Szybki prefiltr bez pełnego parse — większość linii odpada
            if "stats_snapshot" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            kind = row.get("kind")
            if kind == "stats_snapshot":
                pass
            elif kind == "step":
                p = row.get("payload") or {}
                if p.get("step") != "system.stats_snapshot":
                    continue
            else:
                continue
            if want_sid is not None and str(row.get("session_id") or "") != want_sid:
                continue
            ts = _parse_ts(row.get("ts"))
            if cutoff is not None and (ts is None or ts < cutoff):
                continue
            if window_start is not None and (ts is None or ts < window_start):
                continue
            if window_end is not None and (ts is None or ts > window_end):
                continue
            out.append(row)
    return out


def _blank_day(date: str) -> dict:
    return {"date": date,
            "actions": {k: 0 for k in ACTION_KINDS},
            "limits": {},
            "tasks": {}, "stopped_by": {}, "errors": [],
            "profiles_collected": 0, "profiles_by_source": {},
            "profiles_visited": 0,
            "active_s": 0.0, "sessions": 0, "flow_runs": 0}


def _limit_usage(actions: dict, limits: dict) -> dict:
    out = {}
    for kind, used in actions.items():
        cap = int((limits or {}).get(_DAILY_LIMIT_KEY.get(kind, ""), 0) or 0)
        out[kind] = {"used": used, "cap": cap,
                     "pct": round(100 * used / cap) if cap else None}
    return out


def _totals(days: list[dict]) -> dict:
    tot = {"actions": {k: 0 for k in ACTION_KINDS}, "tasks": {},
           "stopped_by": {}, "profiles_collected": 0, "profiles_visited": 0,
           "active_s": 0.0, "sessions": 0, "flow_runs": 0}
    for d in days:
        for k, v in d["actions"].items():
            tot["actions"][k] = tot["actions"].get(k, 0) + v
        for key in ("tasks", "stopped_by"):
            for k, v in d[key].items():
                tot[key][k] = tot[key].get(k, 0) + v
        for key in ("profiles_collected", "profiles_visited", "sessions",
                    "flow_runs"):
            tot[key] += d[key]
        tot["active_s"] += d["active_s"]
    tot["active_s"] = round(tot["active_s"], 1)
    return tot


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    mid = len(v) // 2
    return round(v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2, 1)


def _parse_ts(ts) -> datetime | None:
    try:
        return datetime.strptime(str(ts), TS_FMT)
    except (TypeError, ValueError):
        return None


def _day_of(ts) -> str | None:
    d = _parse_ts(ts)
    return d.date().isoformat() if d else None


def _day_start(t: datetime) -> datetime:
    return t.replace(hour=0, minute=0, second=0, microsecond=0)
