"""Automatyczny tick filtra profili (HTTP) — bez telefonu / ADB.

Co ~interval sprawdza konta z `profile_filter.enabled` i listą `times`
(HH:MM lokalne). Slot odpala się raz na dzień (dedup w
`profile_filter_runs.jsonl`). Globalnie max 1 job naraz.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from v2.profile_filter.pipeline import (
    merge_profile_filter_cfg,
    run_filter_for_account,
    write_last_run,
)

# Okno trafienia w slot (minuty) — tick co 60s → ±1 min wystarczy
SLOT_WINDOW_MIN = 1

# Globalny lock — jeden filtr naraz (oszczędność proxy)
_FILTER_LOCK = threading.Lock()
_running_account: str | None = None


def is_filter_busy() -> bool:
    return _FILTER_LOCK.locked()


def current_filter_account() -> str | None:
    return _running_account


def _slot_key(day: str, hhmm: str) -> str:
    return f"{day}T{hhmm}"


def _normalize_time(t: str) -> str | None:
    """'9:30' / '09:30' → '09:30' albo None."""
    t = (t or "").strip()
    if not t:
        return None
    parts = t.split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def matching_slot(times: list, now: datetime,
                  window_min: int = SLOT_WINDOW_MIN) -> str | None:
    """Zwraca HH:MM jeśli `now` trafia w któreś z `times` (±window_min)."""
    now_min = now.hour * 60 + now.minute
    for raw in times or []:
        hhmm = _normalize_time(str(raw))
        if not hhmm:
            continue
        h, m = map(int, hhmm.split(":"))
        slot_min = h * 60 + m
        if abs(now_min - slot_min) <= window_min:
            return hhmm
    return None


def slot_already_run(account_id: str, data_root: Path, slot: str) -> bool:
    path = data_root / "accounts" / account_id / "profile_filter_runs.jsonl"
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("slot") == slot:
            return True
    return False


def list_filter_accounts(config_root: Path) -> list[tuple[str, dict]]:
    """Konta z włączonym profile_filter."""
    acc_dir = config_root / "accounts"
    if not acc_dir.exists():
        return []
    out = []
    for p in sorted(acc_dir.glob("*.json")):
        if p.name.endswith(".bak") or ".deleted" in p.name:
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        fcfg = merge_profile_filter_cfg(cfg.get("profile_filter"))
        if fcfg.get("enabled"):
            out.append((p.stem, fcfg))
    return out


def tick_once(
    data_root: Path,
    config_root: Path,
    *,
    now: datetime | None = None,
    run_fn: Callable[..., dict] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[dict]:
    """Jedno przejście: odpala due sloty (max 1 konto na tick)."""
    log = log or (lambda m: print(m))
    now = now or datetime.now()
    day = now.date().isoformat()
    actions: list[dict] = []

    if is_filter_busy():
        return [{"result": "busy", "account": current_filter_account()}]

    for account_id, fcfg in list_filter_accounts(config_root):
        hhmm = matching_slot(fcfg.get("times") or [], now)
        if not hhmm:
            continue
        slot = _slot_key(day, hhmm)
        if slot_already_run(account_id, data_root, slot):
            actions.append({
                "account": account_id, "slot": slot, "result": "already_done"})
            continue

        # Max 1 job — bierzemy pierwsze due konto
        actions.append(_run_locked(
            account_id, data_root, config_root,
            slot=slot, fcfg=fcfg, run_fn=run_fn, log=log))
        break

    return actions


def run_now(
    account_id: str,
    data_root: Path,
    config_root: Path,
    *,
    limit: int | None = None,
    proxy: str | None = None,
    run_fn: Callable[..., dict] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Ręczne uruchomienie (panel / CLI) — bez slotu harmonogramu."""
    log = log or (lambda m: print(m))
    if not _FILTER_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "error": f"filtr już działa ({current_filter_account()})",
            "account": account_id,
        }
    global _running_account
    _running_account = account_id
    try:
        fn = run_fn or run_filter_for_account
        summary = fn(
            account_id,
            data_root=data_root,
            config_root=config_root,
            limit=limit,
            proxy=proxy,
            log=log,
        )
        write_last_run(account_id, data_root, summary, slot=None)
        return {"ok": True, **summary}
    finally:
        _running_account = None
        _FILTER_LOCK.release()


def _run_locked(account_id, data_root, config_root, *, slot, fcfg,
                run_fn, log) -> dict:
    if not _FILTER_LOCK.acquire(blocking=False):
        return {"account": account_id, "slot": slot, "result": "busy"}
    global _running_account
    _running_account = account_id
    try:
        log(f"[filter-sched] start {account_id} slot={slot}")
        fn = run_fn or run_filter_for_account
        summary = fn(
            account_id,
            data_root=data_root,
            config_root=config_root,
            limit=int(fcfg.get("limit_per_run") or 100),
            proxy=fcfg.get("proxy") or "losowo",
            log=log,
        )
        write_last_run(account_id, data_root, summary, slot=slot)
        return {
            "account": account_id,
            "slot": slot,
            "result": "started_ok",
            "saved": summary.get("saved"),
            "checked": summary.get("checked"),
        }
    except Exception as e:
        log(f"[filter-sched] error {account_id}: {e}")
        return {
            "account": account_id, "slot": slot,
            "result": "failed", "error": str(e),
        }
    finally:
        _running_account = None
        _FILTER_LOCK.release()


def run_forever(data_root: Path, config_root: Path, interval_s: int = 60):
    """Pętla CLI — analogicznie do scheduler/tick.run_forever."""
    print(f"[filter-sched] start interval={interval_s}s")
    while True:
        try:
            for a in tick_once(data_root, config_root):
                print(f"[filter-sched] {a}")
        except Exception as e:
            print(f"[filter-sched] tick error: {e}")
        time.sleep(interval_s)
