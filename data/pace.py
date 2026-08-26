"""Historyczne tempo akcji (sec/like, sec/follow) z snapshotów sesji.

Służy do ETA: „X lajków ≈ Y minut” na podstawie ostatnich kolejek konta.
Gdy brak próbek — ostrożne defaulty (nie zaniżają czasu).
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from v2.data.session_stats import compute_session_rates

# Fallback gdy konto nie ma jeszcze timingów w snapshotach (sec / akcja)
DEFAULT_SEC_PER_LIKE = 55.0
DEFAULT_SEC_PER_FOLLOW = 90.0
DEFAULT_SEC_PER_ITEM = 40.0


def sessions_dir(data_root: Path, account_id: str) -> Path:
    return Path(data_root) / "accounts" / str(account_id) / "sessions"


def load_recent_session_rates(
        account_id: str, data_root,
        *, last_n: int = 30) -> list[dict]:
    """Lista `rates` ze snapshotów (najnowsze pierwsze), tylko z sensownym total_s."""
    root = sessions_dir(Path(data_root), account_id)
    if not root.is_dir():
        return []
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    out: list[dict] = []
    for path in files[: max(1, int(last_n))]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        timing = data.get("timing") or {}
        rates = timing.get("rates")
        if not isinstance(rates, dict) or not rates:
            # Stare snapshoty bez timing — spróbuj z wall + counters
            rates = compute_session_rates(
                {"total_s": 0, "wall_s": _infer_wall(data), "tasks": []},
                data.get("counters") or {})
        total = float(rates.get("total_s") or timing.get("total_s") or 0)
        if total < 30:  # za krótka sesja = szum
            continue
        if not any(rates.get(k) for k in (
                "sec_per_like", "sec_per_follow", "sec_per_item")):
            continue
        rates = dict(rates)
        rates["_session_id"] = data.get("session_id") or path.stem
        out.append(rates)
    return out


def _infer_wall(data: dict) -> float:
    from v2.data.session_stats import _wall_duration_s
    return float(_wall_duration_s(
        data.get("started_at"), data.get("ended_at")) or 0)


def _median(vals: list[float]) -> float | None:
    clean = [float(v) for v in vals if v is not None and float(v) > 0]
    if not clean:
        return None
    return float(statistics.median(clean))


def account_pace(account_id: str, data_root, *, last_n: int = 30) -> dict[str, Any]:
    """Zagregowane tempo konta + źródło (sessions | defaults)."""
    samples = load_recent_session_rates(account_id, data_root, last_n=last_n)
    like_r = _median([s.get("sec_per_like") for s in samples
                      if s.get("sec_per_like")])
    follow_r = _median([s.get("sec_per_follow") for s in samples
                        if s.get("sec_per_follow")])
    item_r = _median([s.get("sec_per_item") for s in samples
                      if s.get("sec_per_item")])
    used_defaults = not samples
    return {
        "sec_per_like": like_r if like_r is not None else DEFAULT_SEC_PER_LIKE,
        "sec_per_follow": (follow_r if follow_r is not None
                           else DEFAULT_SEC_PER_FOLLOW),
        "sec_per_item": item_r if item_r is not None else DEFAULT_SEC_PER_ITEM,
        "samples": len(samples),
        "source": "defaults" if used_defaults else "sessions",
        "defaults_used": {
            "like": like_r is None,
            "follow": follow_r is None,
            "item": item_r is None,
        },
    }


def estimate_actions_eta(
        actions: dict | None, pace: dict | None = None) -> dict[str, Any]:
    """Z zakresów akcji (min/max likes/follows) → ETA w sekundach.

    Bierze max(czas z lajków, czas z follow) jako dolne ograniczenie równoległości
    nie zakładamy — akcje są sekwencyjne w sesji, więc **sumujemy** like+follow.
    """
    actions = actions or {}
    pace = pace or {
        "sec_per_like": DEFAULT_SEC_PER_LIKE,
        "sec_per_follow": DEFAULT_SEC_PER_FOLLOW,
    }
    spl = float(pace.get("sec_per_like") or DEFAULT_SEC_PER_LIKE)
    spf = float(pace.get("sec_per_follow") or DEFAULT_SEC_PER_FOLLOW)

    def _rng(kind: str) -> tuple[float, float]:
        a = actions.get(kind) or {}
        try:
            lo = float(a.get("min") or 0)
            hi = float(a.get("max") or 0)
        except (TypeError, ValueError):
            return 0.0, 0.0
        if hi < lo:
            lo, hi = hi, lo
        return max(0.0, lo), max(0.0, hi)

    like_lo, like_hi = _rng("likes")
    fol_lo, fol_hi = _rng("follows")
    # Komentarze tekstowe — rzadkie; pomijamy w ETA (brak rzetelnego tempa)

    total_lo = like_lo * spl + fol_lo * spf
    total_hi = like_hi * spl + fol_hi * spf
    return {
        "total_s": {"min": int(total_lo), "max": int(max(total_lo, total_hi))},
        "by_kind": {
            "likes": {
                "min_s": int(like_lo * spl),
                "max_s": int(like_hi * spl),
                "sec_per": spl,
            },
            "follows": {
                "min_s": int(fol_lo * spf),
                "max_s": int(fol_hi * spf),
                "sec_per": spf,
            },
        },
        "pace": {
            "sec_per_like": spl,
            "sec_per_follow": spf,
            "samples": pace.get("samples"),
            "source": pace.get("source"),
            "defaults_used": pace.get("defaults_used"),
        },
    }
