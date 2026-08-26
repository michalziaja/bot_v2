"""Live statystyki sesji — `session_stats.json` (nie mylić z read-only `stats.py`).

Zapis atomowy (tmp + replace). Aktualizowany przy `set_progress` / `bump` /
`set_counters`. Snapshot końcowy: `sessions/<session_id>.json`.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable


def _iso_now() -> str:
    """Timestamp lokalny bez strefy — spójny z events.jsonl / queue_state."""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _account_dir(data_root: Path, account_id: str) -> Path:
    return Path(data_root) / "accounts" / account_id


class SessionStats:
    """Stan postępu sesji kolejki — counters + progress X/Y + by_plugin."""

    def __init__(self, account_id: str, data_root):
        self.account_id = str(account_id)
        self.data_root = Path(data_root)
        self._state: dict[str, Any] = {}
        # Snapshot counters sprzed bieżącego zadania (żeby set_counters
        # sumował lokalne stats pluginu z poprzednimi zadaniami)
        self._task_base: dict[str, int] = {}
        self._plugin_base: dict[str, int] = {}
        self._current_plugin: str | None = None
        # Ostatni progress (dedup eventów session.progress)
        self._last_progress_sig: tuple | None = None
        # Hook z cli: lambda prog: ctx.log("session.progress", **prog)
        self._on_progress: Callable[[dict], None] | None = None

    # ------------------------------------------------------------------
    # ścieżki
    # ------------------------------------------------------------------
    @property
    def live_path(self) -> Path:
        return _account_dir(self.data_root, self.account_id) / "session_stats.json"

    def snapshot_path(self, session_id: str | None = None) -> Path:
        sid = session_id or self._state.get("session_id") or "unknown"
        return (_account_dir(self.data_root, self.account_id)
                / "sessions" / f"{sid}.json")

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def begin(self, *, session_id: str, queue_name: str | None = None) -> None:
        """Reset stanu na start kolejki (JobRunner.run)."""
        now = _iso_now()
        self._state = {
            "session_id": session_id,
            "account_id": self.account_id,
            "started_at": now,
            "updated_at": now,
            "queue": {
                "name": queue_name,
                "task_i": 0,
                "task_n": 0,
            },
            "plugin": None,
            "task_id": None,
            "budget": {"items_done": 0, "max_items": None},
            "progress": None,
            "counters": {},
            "by_plugin": {},
            # Czas trwania kolejki / zadań — pod ETA w panelu
            "timing": {
                "total_s": 0.0,
                "tasks": [],
                "by_plugin": {},
                "rates": {},
            },
            "summary": None,
        }
        self._task_base = {}
        self._plugin_base = {}
        self._current_plugin = None
        self._last_progress_sig = None
        self._task_t0 = None  # monotonic start bieżącego zadania (opcjonalnie)
        self.flush()

    def set_task(self, *, plugin: str, task_id: str,
                 task_i: int, task_n: int,
                 budget: dict | None = None) -> None:
        """Nowe zadanie kolejki — task_i/n + budżet; reset progress."""
        if not self._state:
            # begin() nie wywołany (testy / flow bez kolejki) — zainicjuj minimalnie
            self.begin(session_id="unknown", queue_name=None)
        self._current_plugin = plugin
        # Baza sesji / pluginu przed tym zadaniem
        self._task_base = {
            k: int(v or 0) for k, v in (self._state.get("counters") or {}).items()
        }
        bp = (self._state.get("by_plugin") or {}).get(plugin) or {}
        self._plugin_base = {k: int(v or 0) for k, v in bp.items()}

        q = self._state.setdefault("queue", {})
        q["task_i"] = int(task_i)
        q["task_n"] = int(task_n)
        self._state["plugin"] = plugin
        self._state["task_id"] = task_id
        b = budget or {}
        self._state["budget"] = {
            "items_done": int(b.get("items_done") or 0),
            "max_items": b.get("max_items"),
        }
        self._state["progress"] = None
        self._last_progress_sig = None
        self._task_t0 = time.monotonic()
        self.flush()

    def record_task_timing(
            self, *, duration_s: float,
            items_done: int = 0,
            stats: dict | None = None,
            status: str | None = None,
            stopped_by: str | None = None) -> None:
        """Dopisz czas zakończonego zadania kolejki + przelicz rates.

        Wołane z JobRunner po `plugin.run` (duration z monotonic).
        """
        if not self._state:
            return
        plugin = self._current_plugin or self._state.get("plugin") or "?"
        task_id = self._state.get("task_id")
        dur = max(0.0, float(duration_s or 0))
        stats = stats or {}
        likes = _count_likes(stats)
        try:
            follows = int(stats.get("followed") or 0)
        except (TypeError, ValueError):
            follows = 0
        try:
            items = int(items_done or 0)
        except (TypeError, ValueError):
            items = 0
        if items <= 0:
            for k in ("items", "visited", "watched", "reels", "posts"):
                try:
                    items = int(stats.get(k) or 0)
                except (TypeError, ValueError):
                    items = 0
                if items > 0:
                    break
        row = {
            "plugin": plugin,
            "task_id": task_id,
            "duration_s": round(dur, 1),
            "items_done": items,
            "liked": likes,
            "followed": follows,
            "status": status,
            "stopped_by": stopped_by,
        }
        timing = self._state.setdefault("timing", {
            "total_s": 0.0, "tasks": [], "by_plugin": {}, "rates": {},
        })
        timing.setdefault("tasks", []).append(row)
        timing["total_s"] = round(
            float(timing.get("total_s") or 0) + dur, 1)
        bp = timing.setdefault("by_plugin", {}).setdefault(plugin, {
            "duration_s": 0.0, "items_done": 0, "liked": 0, "followed": 0,
            "tasks": 0,
        })
        bp["duration_s"] = round(float(bp.get("duration_s") or 0) + dur, 1)
        bp["items_done"] = int(bp.get("items_done") or 0) + items
        bp["liked"] = int(bp.get("liked") or 0) + likes
        bp["followed"] = int(bp.get("followed") or 0) + follows
        bp["tasks"] = int(bp.get("tasks") or 0) + 1
        timing["rates"] = compute_session_rates(
            timing, self._state.get("counters") or {})
        self._task_t0 = None
        self.flush()

    def set_progress(self, *, phase: str, current: int,
                     total: int | None = None, label: str | None = None,
                     detail: str | None = None,
                     sub: dict | None = None) -> None:
        """Bieżący krok operatora (to samo co linia X/Y w logu)."""
        if not self._state:
            return
        prog: dict[str, Any] = {
            "phase": phase,
            "label": label or phase,
            "current": int(current),
            "total": int(total) if total is not None else None,
        }
        if detail is not None:
            prog["detail"] = detail
        if sub is not None:
            prog["sub"] = sub
        self._state["progress"] = prog
        # Event tylko gdy zmienia się faza / current (nie przy każdym bump)
        sig = (phase, int(current),
               int(total) if total is not None else None,
               (sub or {}).get("phase"), (sub or {}).get("current"))
        emit = sig != self._last_progress_sig
        self._last_progress_sig = sig
        self.flush(emit_event=emit)

    def bump(self, key: str, n: int = 1) -> None:
        """+n do licznika sesji i by_plugin[bieżący plugin]."""
        if not self._state or not key:
            return
        n = int(n)
        counters = self._state.setdefault("counters", {})
        counters[key] = int(counters.get(key) or 0) + n
        # Baza zadania też rośnie — set_counters potem nie nadpisze bumpów
        self._task_base[key] = int(self._task_base.get(key) or 0) + n
        plugin = self._current_plugin or self._state.get("plugin")
        if plugin:
            bp = self._state.setdefault("by_plugin", {}).setdefault(plugin, {})
            bp[key] = int(bp.get(key) or 0) + n
            self._plugin_base[key] = int(self._plugin_base.get(key) or 0) + n
        self.flush()

    def set_counters(self, stats: dict | None) -> None:
        """Zsynchronizuj lokalne `stats` pluginu → counters + by_plugin.

        Lokalne stats = liczniki BIEŻĄCEGO zadania; sesja = baza + local.
        """
        if not self._state or not stats:
            return
        counters = self._state.setdefault("counters", {})
        plugin = self._current_plugin or self._state.get("plugin")
        bp: dict[str, int] = {}
        if plugin:
            bp = dict(self._plugin_base)
        for k, v in stats.items():
            if v is None:
                continue
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            counters[k] = int(self._task_base.get(k) or 0) + iv
            if plugin:
                bp[k] = int(self._plugin_base.get(k) or 0) + iv
        if plugin:
            self._state.setdefault("by_plugin", {})[plugin] = bp
        self.flush()

    def sync_budget(self, items_done: int | None,
                    max_items: int | None = None) -> None:
        """Aktualizuj budget.items_done / max_items (po ticku / końcu zadania)."""
        if not self._state:
            return
        b = self._state.setdefault("budget", {})
        if items_done is not None:
            b["items_done"] = int(items_done)
        if max_items is not None or "max_items" not in b:
            b["max_items"] = max_items
        self.flush()

    def snapshot_end(self, *, summary: dict | None = None) -> Path | None:
        """Finalny dump + kopia do sessions/<session_id>.json."""
        if not self._state:
            return None
        now = _iso_now()
        self._state["updated_at"] = now
        self._state["ended_at"] = now
        if summary is not None:
            self._state["summary"] = summary
        # Domknij rates (wall-clock vs suma zadań — preferuj sumę tasków)
        timing = self._state.setdefault("timing", {
            "total_s": 0.0, "tasks": [], "by_plugin": {}, "rates": {},
        })
        wall = _wall_duration_s(
            self._state.get("started_at"), self._state.get("ended_at"))
        if wall is not None:
            timing["wall_s"] = round(wall, 1)
            # Gdy brak task timings (awaria) — użyj wall
            if not timing.get("tasks") and float(timing.get("total_s") or 0) <= 0:
                timing["total_s"] = round(wall, 1)
        timing["rates"] = compute_session_rates(
            timing, self._state.get("counters") or {})
        self.flush()
        # Archiwum append-only (jeden plik per session_id)
        snap = self.snapshot_path()
        snap.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(snap, self._state)
        return snap

    def as_dict(self) -> dict:
        """Kopia stanu (testy / panel)."""
        return json.loads(json.dumps(self._state, ensure_ascii=False))

    def flush(self, *, emit_event: bool = False) -> None:
        """Zapisz live JSON; opcjonalnie wywołaj hook progress."""
        if not self._state:
            return
        self._state["updated_at"] = _iso_now()
        path = self.live_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, self._state)
        if emit_event and self._on_progress and self._state.get("progress"):
            try:
                self._on_progress(dict(self._state["progress"]))
            except Exception:
                # Event nie może wywalić pętli pluginu
                pass


def _atomic_write(path: Path, data: dict) -> None:
    """tmp + os.replace — bez częściowego JSON przy crashu."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _count_likes(stats: dict) -> int:
    """Suma lajków z różnych kluczy pluginów (rolki / kafelki / …)."""
    n = 0
    for k in ("liked", "tiles_liked", "likes", "comment_likes"):
        try:
            n += int(stats.get(k) or 0)
        except (TypeError, ValueError):
            pass
    return n


def _wall_duration_s(started_at: str | None, ended_at: str | None) -> float | None:
    """Różnica ISO lokalnych timestampów (bez strefy) → sekundy."""
    if not started_at or not ended_at:
        return None
    try:
        t0 = time.strptime(str(started_at)[:19], "%Y-%m-%dT%H:%M:%S")
        t1 = time.strptime(str(ended_at)[:19], "%Y-%m-%dT%H:%M:%S")
        return max(0.0, time.mktime(t1) - time.mktime(t0))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def compute_session_rates(timing: dict, counters: dict | None = None) -> dict:
    """sec_per_* z czasu zadań / wall oraz liczników sesji.

    Preferuje sumę `timing.tasks`; counters uzupełniają gdy w taskach 0.
    """
    counters = counters or {}
    total_s = float(timing.get("total_s") or 0)
    if total_s <= 0:
        total_s = float(timing.get("wall_s") or 0)

    liked = 0
    followed = 0
    items = 0
    for t in timing.get("tasks") or []:
        liked += int(t.get("liked") or 0)
        followed += int(t.get("followed") or 0)
        items += int(t.get("items_done") or 0)
    # Fallback na counters sesji (gdy task rows niepełne)
    if liked <= 0:
        liked = _count_likes(counters)
    if followed <= 0:
        try:
            followed = int(counters.get("followed") or 0)
        except (TypeError, ValueError):
            followed = 0
    if items <= 0:
        for k in ("items", "visited", "watched", "reels", "posts"):
            try:
                items += int(counters.get(k) or 0)
            except (TypeError, ValueError):
                pass

    def _rate(n: int) -> float | None:
        if total_s <= 0 or n <= 0:
            return None
        return round(total_s / n, 2)

    return {
        "sec_per_like": _rate(liked),
        "sec_per_follow": _rate(followed),
        "sec_per_item": _rate(items),
        "likes": liked,
        "follows": followed,
        "items": items,
        "total_s": round(total_s, 1) if total_s else 0.0,
    }
