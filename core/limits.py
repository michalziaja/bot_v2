"""Enforcement dziennych/godzinowych limitów z configu konta.

Liczy eventy z `events.jsonl` — źródło prawdy o wykonanych akcjach. Cache
w scratch żeby nie parsować pliku przy każdym follow-u.

Używane przez: profile.follow_execute (kind='follow'), profile.visit
przy like_grid_tiles (kind='like'), przyszły profile.comment (kind='comment').
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class LimitCheck:
    allowed: bool
    kind:    str
    scope:   str        # 'daily' | 'hourly' | 'none'
    current: int
    limit:   int
    remaining: int

    @property
    def reason(self) -> str:
        if self.allowed: return "ok"
        return f"{self.kind}_{self.scope}_exceeded ({self.current}/{self.limit})"


# --- akcje które liczymy vs klucze w configu ---
_KIND_TO_DAILY = {
    "like":     ("daily_likes",     "likes"),
    "follow":   ("daily_follows",   "follows"),
    "comment":  ("daily_comments",  "comments"),
    "dm":       ("daily_dms",       "dms"),
}


def check(ctx, kind: str) -> LimitCheck:
    """Sprawdź czy jest budget na daną akcję.
    Zwraca LimitCheck z decyzją + aktualnym zużyciem + limitem."""
    if kind not in _KIND_TO_DAILY:
        return LimitCheck(True, kind, "none", 0, 0, 0)

    daily_key, hourly_key = _KIND_TO_DAILY[kind]
    limits = ctx.account.limits or {}
    daily_limit  = limits.get(daily_key, 0)
    hourly_limit = (limits.get("per_hour_caps") or {}).get(hourly_key, 0)

    if daily_limit == 0 and hourly_limit == 0:
        return LimitCheck(True, kind, "none", 0, 0, 0)

    counts = _count_actions(ctx, kind)
    daily_cur  = counts["daily"]
    hourly_cur = counts["hourly"]

    if daily_limit > 0 and daily_cur >= daily_limit:
        return LimitCheck(False, kind, "daily", daily_cur, daily_limit, 0)
    if hourly_limit > 0 and hourly_cur >= hourly_limit:
        return LimitCheck(False, kind, "hourly", hourly_cur, hourly_limit, 0)

    remaining = min(
        daily_limit - daily_cur if daily_limit else 10**9,
        hourly_limit - hourly_cur if hourly_limit else 10**9,
    )
    return LimitCheck(True, kind, "daily" if daily_limit else "hourly",
                      daily_cur, daily_limit or hourly_limit, remaining)


def note_action(ctx, kind: str) -> None:
    """Wywołane po udanej akcji — invalidate cache żeby kolejne check() liczyły od nowa."""
    ctx.scratch.pop("_limits_cache", None)


# ============================================================
def _count_actions(ctx, kind: str) -> dict:
    """Zwraca {'daily': N, 'hourly': N} dla danego kind z events.jsonl.
    Cache w scratch."""
    cache = ctx.scratch.get("_limits_cache")
    if cache and (time.monotonic() - cache["ts"]) < 60:
        return cache["counts"].get(kind, {"daily": 0, "hourly": 0})

    path = Path(ctx.events.file_path)
    if not path.exists():
        return {"daily": 0, "hourly": 0}

    now = datetime.now()
    day_start  = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_start = now.replace(minute=0, second=0, microsecond=0)

    counts: dict = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:    e = json.loads(line)
            except: continue
            k = classify_action(e)
            if not k: continue
            try:    ts = datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%S")
            except: continue
            if ts < day_start: continue
            slot = counts.setdefault(k, {"daily": 0, "hourly": 0})
            slot["daily"] += 1
            if ts >= hour_start: slot["hourly"] += 1

    ctx.scratch["_limits_cache"] = {"ts": time.monotonic(), "counts": counts}
    return counts.get(kind, {"daily": 0, "hourly": 0})


def classify_action(event: dict) -> str | None:
    """Który 'kind' akcji reprezentuje zdarzenie — albo `None`, gdy żaden.

    **Publiczna i JEDYNA definicja tego, co jest akcją.** Statystyki liczą akcje
    tą samą funkcją, którą limiter je blokuje; inaczej panel pokazywałby inną
    liczbę niż ta egzekwowana i przy pierwszej rozbieżności nie wiadomo byłoby,
    której wierzyć.

    Uwaga na kroki, które wykonują akcję „przy okazji": `profile.visit` sam
    obserwuje profil, a `reels.like_comment` sam lajkuje. Zliczamy je po
    payloadzie, bo inaczej te akcje nie wchodziłyby do limitu dziennego —
    bot pilnowałby budżetu, którego część wydaje poza ewidencją.
    """
    kind = event.get("kind")
    payload = event.get("payload") or {}
    if kind == "step":
        if payload.get("status") != "ok":
            return None
        step = payload.get("step") or ""
        inner = payload.get("payload") or {}
        if step in ("reels.like", "reels.like_comment"):  return "like"
        if step == "profile.follow_execute":              return "follow"
        if step == "profile.like_grid_tile":              return "like"
        if step == "profile.visit":
            return "follow" if inner.get("followed") else None
        if step in ("profile.comment", "reels.comment"):  return "comment"
    return None
