"""Szacunek POTENCJALNYCH akcji (lajki / follow / komentarze) dla scenariusza.

Cel: panel Planowanie → Scenariusze („Suma w scenariuszu”) oraz oś czasu
(paski wykorzystania limitów dziennych). To NIE jest gwarancja runtime —
AI, bramki liczników, limity i pech/szansa zawężają wynik w dół.

Zasady uczciwości:
  - budżet `max_items` to zakres min–max jednostek pracy (rolek/profili/…)
  - stała szansa `chance.mode=p` → mnożymy zakres jednostek × p
  - AI / formuła (`score/100`, `ai.enabled`) → min często 0, max = górny
    budżet × górna szansa (albo 1.0 gdy formuła bez stałego p)
  - lajki komentarzy i relacji też wchodzą w `likes` (ten sam limit dzienny)
  - komentarze tekstowe (`daily_comments`) — dziś prawie nigdzie nie ma
    włączonego pisania; zostawiamy 0 z notatką, gdy pojawi się w ustawieniach

Wynik zawsze jako zakres `{min, max}` (zaokrąglony w górę na max, w dół na min).
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from v2.core.limits import classify_action


ACTION_KINDS = ("likes", "follows", "comments")
# classify_action zwraca liczbę pojedynczą; utilization i panel mówią w liczbie mnogiej
_USED_KIND_MAP = {
    "like": "likes",
    "follow": "follows",
    "comment": "comments",
}


def _empty_actions() -> dict:
    return {k: {"min": 0, "max": 0} for k in ACTION_KINDS}


def _as_range(val, default_lo: float = 0, default_hi: float = 0) -> tuple[float, float]:
    """Zakres z `{min,max}`, skalaru albo None."""
    if val is None:
        return float(default_lo), float(default_hi)
    if isinstance(val, dict):
        lo = float(val.get("min", default_lo) or 0)
        hi = float(val.get("max", default_hi if val.get("max") is None else val.get("max")) or 0)
        if hi < lo:
            lo, hi = hi, lo
        return lo, hi
    n = float(val)
    return n, n


def _chance_bounds(chance: Any) -> tuple[float, float, bool]:
    """Zwraca (p_min, p_max, uncertain).

    `uncertain=True` gdy decyzja zależy od AI / formuły wyniku — wtedy
    dolna granica akcji traktujemy ostrożniej (często 0).
    """
    if not isinstance(chance, dict):
        try:
            p = float(chance or 0)
        except (TypeError, ValueError):
            p = 0.0
        p = max(0.0, min(1.0, p))
        return p, p, False

    mode = (chance.get("mode") or "p").lower()
    if mode == "formula":
        # score/100 → teoretycznie 0..1; nie znamy score z góry
        return 0.0, 1.0, True
    try:
        p = float(chance.get("value", 0) or 0)
    except (TypeError, ValueError):
        p = 0.0
    p = max(0.0, min(1.0, p))
    return p, p, False


def _pct_to_p(pct: Any) -> float:
    try:
        return max(0.0, min(1.0, float(pct or 0) / 100.0))
    except (TypeError, ValueError):
        return 0.0


def _add(dst: dict, kind: str, lo: float, hi: float) -> None:
    if hi < 0 and lo < 0:
        return
    lo = max(0.0, lo)
    hi = max(lo, hi)
    slot = dst.setdefault(kind, {"min": 0, "max": 0})
    slot["min"] = float(slot.get("min", 0)) + lo
    slot["max"] = float(slot.get("max", 0)) + hi


def _round_actions(actions: dict) -> dict:
    out = {}
    for k in ACTION_KINDS:
        a = actions.get(k) or {"min": 0, "max": 0}
        lo = max(0, int(math.floor(float(a.get("min", 0)))))
        hi = max(lo, int(math.ceil(float(a.get("max", 0)))))
        out[k] = {"min": lo, "max": hi}
    return out


def _sum_actions(parts: list[dict]) -> dict:
    tot = _empty_actions()
    for p in parts:
        for k in ACTION_KINDS:
            a = (p.get("actions") or {}).get(k) or {}
            tot[k]["min"] += int(a.get("min", 0) or 0)
            tot[k]["max"] += int(a.get("max", 0) or 0)
    return tot


def _items_range(resolved: dict) -> tuple[float, float]:
    return _as_range((resolved.get("budget") or {}).get("max_items"), 0, 0)


def _like_from_decision(s: dict, like_path: tuple[str, ...],
                        items_lo: float, items_hi: float) -> tuple[float, float, list[str]]:
    """Szacunek lajków treści (reel/post) z bloku like_* + opcjonalnego AI.

    like_path np. ("like_reel",) albo ("like_content",) albo ("like",).
    """
    notes: list[str] = []
    node = s
    for key in like_path:
        node = (node or {}).get(key) or {}
    if not node.get("enabled", False):
        return 0.0, 0.0, notes

    ai = s.get("ai") or {}
    ai_on = bool(ai.get("enabled", False))
    p_lo, p_hi, formula = _chance_bounds(node.get("chance"))

    if ai_on or formula:
        # AI może odrzucić prawie wszystko → min 0; max = budżet × górna szansa
        notes.append("lajki zależą od AI / formuły — dolny zakres = 0")
        return 0.0, items_hi * p_hi, notes

    return items_lo * p_lo, items_hi * p_hi, notes


def _comment_likes(s: dict, items_lo: float, items_hi: float) -> tuple[float, float]:
    """Lajki komentarzy pod treścią → limity `likes`."""
    block = ((s.get("comments") or {}).get("like_comments") or {})
    if not block.get("enabled", False):
        return 0.0, 0.0
    c_lo, c_hi = _as_range(block.get("count"), 0, 0)
    extra = _pct_to_p(block.get("extra_chance_percent", 100))
    # Każda treść: count × extra_chance kwalifikujących się komentarzy
    return items_lo * c_lo * extra, items_hi * c_hi * max(extra, 0.01)


def _hot_interact_actions(s: dict, items_lo: float, items_hi: float,
                          profile_settings: dict | None) -> tuple[dict, list[str]]:
    """Hot interact z list lajkujących/komentujących — follow/lajki z profile_interact."""
    notes: list[str] = []
    out = _empty_actions()
    for section in ("likers", "comments"):
        hi = ((s.get(section) or {}).get("hot_interact") or {})
        if not hi.get("enabled", False):
            continue
        c_lo, c_hi = _as_range(hi.get("count"), 0, 0)
        # Ile „trafień” hot interact na zadanie: per jednostka treści
        hit_lo, hit_hi = items_lo * c_lo, items_hi * c_hi
        if hit_hi <= 0:
            continue
        notes.append(f"hot_interact ({section}): szacunek follow/lajków z ustawień profile_interact")
        # Użyj ustawień profile_interact konta (+ overrides hot), uproszczone
        ps = dict(profile_settings or {})
        ov = hi.get("overrides") if isinstance(hi.get("overrides"), dict) else {}
        if ov:
            # płytki merge wystarczy do follow/like_tiles
            for k, v in ov.items():
                if isinstance(v, dict) and isinstance(ps.get(k), dict):
                    ps[k] = {**ps[k], **v}
                else:
                    ps[k] = v
        # skrócona procedura ≈ mniej lajków kafelków; i tak bierzemy górę z like_tiles
        like_tiles = ps.get("like_tiles") or {}
        if like_tiles.get("enabled"):
            t_lo, t_hi = _as_range(like_tiles.get("count"), 0, 0)
            _add(out, "likes", hit_lo * t_lo, hit_hi * t_hi)
        follow = ps.get("follow") or {}
        if follow.get("allow", False):
            fp_lo, fp_hi, unc = _chance_bounds(follow.get("chance"))
            if unc:
                notes.append("follow (hot) przez formułę — min 0")
                _add(out, "follows", 0.0, hit_hi * fp_hi)
            else:
                _add(out, "follows", hit_lo * fp_lo, hit_hi * fp_hi)
        story = ps.get("story") or {}
        if story.get("enabled"):
            watch = story.get("watch") or {}
            p = _pct_to_p(watch.get("like_chance_percent", 0))
            if p > 0:
                seg_lo, seg_hi = _as_range(watch.get("segments"), 1, 1)
                _add(out, "likes", hit_lo * seg_lo * p, hit_hi * seg_hi * p)
    return out, notes


def estimate_plugin_actions(plugin_id: str, resolved: dict, *,
                            profile_settings: dict | None = None) -> dict:
    """Szacunek akcji jednego zadania (już zmergowany `resolved`)."""
    actions = _empty_actions()
    notes: list[str] = []
    uncertain = False
    items_lo, items_hi = _items_range(resolved)
    if items_hi <= 0 and plugin_id not in ("feed_browser",):
        notes.append("brak budżetu max_items — szacunek akcji = 0")
        return {"actions": actions, "notes": notes, "uncertain": False,
                "items": {"min": 0, "max": 0}}

    if plugin_id == "feed_browser":
        notes.append("zero interakcji (lajki wyłączone w kodzie)")

    elif plugin_id == "reels_warmup":
        lo, hi, n = _like_from_decision(resolved, ("like",), items_lo, items_hi)
        notes.extend(n)
        if n:
            uncertain = True
        _add(actions, "likes", lo, hi)

    elif plugin_id in ("reels_niche", "content_search"):
        like_key = ("like_reel",) if plugin_id == "reels_niche" else ("like_content",)
        lo, hi, n = _like_from_decision(resolved, like_key, items_lo, items_hi)
        notes.extend(n)
        if n:
            uncertain = True
        _add(actions, "likes", lo, hi)
        cl_lo, cl_hi = _comment_likes(resolved, items_lo, items_hi)
        _add(actions, "likes", cl_lo, cl_hi)
        hot, hn = _hot_interact_actions(resolved, items_lo, items_hi, profile_settings)
        notes.extend(hn)
        for k in ACTION_KINDS:
            _add(actions, k, hot[k]["min"], hot[k]["max"])

    elif plugin_id == "profile_interact":
        like_tiles = resolved.get("like_tiles") or {}
        if like_tiles.get("enabled"):
            t_lo, t_hi = _as_range(like_tiles.get("count"), 0, 0)
            _add(actions, "likes", items_lo * t_lo, items_hi * t_hi)
        follow = resolved.get("follow") or {}
        if follow.get("allow", False):
            fp_lo, fp_hi, unc = _chance_bounds(follow.get("chance"))
            if unc:
                uncertain = True
                notes.append("follow przez formułę — min 0")
                _add(actions, "follows", 0.0, items_hi * fp_hi)
            else:
                _add(actions, "follows", items_lo * fp_lo, items_hi * fp_hi)
        story = resolved.get("story") or {}
        if story.get("enabled"):
            watch = story.get("watch") or {}
            p = _pct_to_p(watch.get("like_chance_percent", 0))
            if p > 0:
                seg_lo, seg_hi = _as_range(watch.get("segments"), 1, 1)
                _add(actions, "likes", items_lo * seg_lo * p, items_hi * seg_hi * p)

    elif plugin_id == "mass_stories":
        # budżet = targety; per_target.max_ring_opens = osoby z ringiem
        per = resolved.get("per_target") or {}
        ring_lo, ring_hi = _as_range(per.get("max_ring_opens"), 0, 0)
        people_lo, people_hi = items_lo * ring_lo, items_hi * ring_hi
        watch = resolved.get("story_watch") or {}
        p = _pct_to_p(watch.get("like_chance_percent", 0))
        if p > 0:
            seg_lo, seg_hi = _as_range(watch.get("segments"), 1, 1)
            _add(actions, "likes", people_lo * seg_lo * p, people_hi * seg_hi * p)
        else:
            notes.append("like_chance_percent=0 — brak lajków relacji")

    elif plugin_id == "target_scraper":
        notes.append("zero interakcji (tylko zbieranie)")

    elif plugin_id == "niche_discover":
        notes.append("poza scenariuszami — brak szacunku")

    else:
        notes.append(f"brak reguł szacunku dla pluginu '{plugin_id}'")

    return {
        "actions": _round_actions(actions),
        "notes": notes,
        "uncertain": uncertain,
        "items": {"min": int(math.floor(items_lo)),
                  "max": int(math.ceil(items_hi))},
    }


def estimate_actions_from_rows(rows: list[dict], *,
                               profile_settings: dict | None = None) -> dict:
    """Suma akcji po rozwiązanych wierszach `store.resolve_tasks`."""
    per_task: list[dict] = []
    all_notes: list[str] = []
    any_uncertain = False

    for r in rows:
        if not r.get("enabled", True):
            continue
        if not r.get("plugin_enabled_for_account", True):
            all_notes.append(f"{r.get('plugin')}: plugin wyłączony dla konta — pominięty")
            continue
        est = estimate_plugin_actions(
            r["plugin"], r.get("resolved") or {},
            profile_settings=profile_settings,
        )
        if est["uncertain"]:
            any_uncertain = True
        for n in est["notes"]:
            all_notes.append(f"{r.get('plugin')}: {n}")
        per_task.append({
            "id": r.get("id", ""),
            "plugin": r["plugin"],
            "label": r.get("label", r["plugin"]),
            "actions": est["actions"],
            "items": est["items"],
            "uncertain": est["uncertain"],
            "notes": est["notes"],
        })

    totals = _sum_actions(per_task)
    return {
        "actions": totals,
        "per_task": per_task,
        "uncertain": any_uncertain,
        "notes": all_notes,
        "task_count": len(per_task),
    }


def estimate_queue_actions(account: str, queue_name: str | None = None, *,
                           queue_data: dict | None = None) -> dict:
    """Szacunek akcji scenariusza konta (z dysku albo z override edytora)."""
    from v2.plugins import store

    q = queue_data if queue_data is not None else store.read_queue(account, name=queue_name)
    rows = store.resolve_tasks(account, q.get("queue") or [])
    settings = store.read_settings(account)
    profile = settings.get("profile_interact") or {}
    est = estimate_actions_from_rows(rows, profile_settings=profile)
    est["account"] = account
    est["queue_name"] = queue_name
    return est


def count_used_today(account: str, data_root, *, now: datetime | None = None) -> dict:
    """Dzisiejsze lajki / follow / komentarze z `events.jsonl` konta.

    Ta sama `classify_action` co limiter — panel i runtime liczą to samo.
    Brak pliku albo uszkodzone linie = zera, nie wyjątek.
    """
    out = {k: 0 for k in ACTION_KINDS}
    path = Path(data_root) / "accounts" / account / "events.jsonl"
    if not path.exists():
        return out
    now = now or datetime.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                mapped = _USED_KIND_MAP.get(classify_action(ev) or "")
                if not mapped:
                    continue
                try:
                    ts = datetime.strptime(ev["ts"], "%Y-%m-%dT%H:%M:%S")
                except (KeyError, TypeError, ValueError):
                    continue
                if ts >= day_start:
                    out[mapped] += 1
    except OSError:
        return out
    return out


def utilization_vs_limits(actions: dict, limits: dict | None,
                          used_today: dict | None = None) -> dict:
    """Porównaj zakres akcji z limitami dziennymi konta.

    Do decyzji „czy się zmieści” zawsze bierzemy górę zakresu (`max`).
    `used_today` / `remaining` — ile już poszło dziś z events.jsonl
    (żółto gdy max > remaining; `over` zostaje na max > limit).
    """
    lim = limits or {}
    used_map = used_today or {}
    key_map = {
        "likes": "daily_likes",
        "follows": "daily_follows",
        "comments": "daily_comments",
    }
    out = {}
    for kind, lim_key in key_map.items():
        a = (actions or {}).get(kind) or {"min": 0, "max": 0}
        limit = int(lim.get(lim_key) or 0)
        lo = int(a.get("min", 0) or 0)
        hi = int(a.get("max", 0) or 0)
        used = int(used_map.get(kind, 0) or 0)
        remaining = max(0, limit - used) if limit > 0 else None
        if limit > 0:
            pct_min = round(100.0 * lo / limit, 1)
            pct_max = round(100.0 * hi / limit, 1)
        else:
            pct_min = pct_max = None
        out[kind] = {
            "min": lo, "max": hi, "limit": limit,
            "used_today": used,
            "remaining": remaining,
            "pct_min": pct_min, "pct_max": pct_max,
            "over": bool(limit > 0 and hi > limit),
            "over_remaining": bool(remaining is not None and hi > remaining),
        }
    return out
