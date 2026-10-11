"""Bridge: raw_profiles → (HTTP filter) → target_candidates + state.

Nie rusza telefonu / ADB — tylko sieć przez proxy.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from v2.data.stores import (
    RawFilterStateStore,
    RawProfilesStore,
    TargetCandidatesStore,
)
from v2.profile_filter.core import (
    BETWEEN_USERS_SLEEP_S,
    FilterCriteria,
    InstagramScraper,
    PROXY1_CHECK_LIMIT,
    REQUEST_SLEEP_S,
    REQUEST_TIMEOUT_S,
    RETRY_BACKOFF_BASE_S,
    RETRY_BACKOFF_MAX_S,
)

# Domyślna sekcja account.json — panel / CLI / tick
# limit_per_run = „Ilość oczekiwanych targetów” z proxy1.py (max zapisanych)
DEFAULT_PROFILE_FILTER = {
    "enabled": False,
    "times": ["10:30", "18:00"],
    "limit_per_run": 100,
    "proxy": "losowo",
    "followers_min": 60,
    "followers_max": 20000,
    "reject_arabic_bio": True,
    "require_public": True,
    "min_posts": 1,
    # 0 = jak proxy1 (brak pauzy między nickami)
    "sleep_between_s": BETWEEN_USERS_SLEEP_S,
    "sleep_request_s": REQUEST_SLEEP_S,
    "retry_backoff_base_s": RETRY_BACKOFF_BASE_S,
    "retry_backoff_max_s": RETRY_BACKOFF_MAX_S,
    "request_timeout_s": REQUEST_TIMEOUT_S,
}


def merge_profile_filter_cfg(raw: dict | None) -> dict:
    """Defaults + override z account.json."""
    return {**DEFAULT_PROFILE_FILTER, **(raw or {})}


def load_account_filter_cfg(account_id: str, config_root: Path) -> dict:
    path = config_root / "accounts" / f"{account_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"brak konta: {account_id}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return merge_profile_filter_cfg(cfg.get("profile_filter"))


def pending_usernames(account_id: str, data_root: Path,
                      limit: int) -> list[dict]:
    """Pending jak all2.txt w proxy1: od początku pliku, skip na końcu.

    Skip nie jest decided — wraca do kolejki, ale za świeżymi nickami.
    """
    raw = RawProfilesStore(account_id, data_root)
    state = RawFilterStateStore(account_id, data_root)
    done = state.decided_usernames()
    deferred = state.deferred_usernames()
    fresh: list[dict] = []
    delayed: list[dict] = []
    seen: set[str] = set()
    # read_all = kolejność zapisu (najstarsze pierwsze) — jak splitlines() w proxy1
    for row in raw.read_all():
        u = row.get("username")
        if not u or u in seen or u in done:
            continue
        seen.add(u)
        if u in deferred:
            delayed.append(row)
        else:
            fresh.append(row)
    return (fresh + delayed)[: max(0, int(limit))]


def run_filter_for_account(
    account_id: str,
    *,
    data_root: Path,
    config_root: Path,
    limit: int | None = None,
    proxy: str | None = None,
    dry_run: bool = False,
    scrape_fn: Callable[[str], str] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Przepuść pending raw przez filtr → target_candidates.

    Semantyka jak ``proxy1.process_users_txt``:
    ``limit`` / ``limit_per_run`` = max zapisanych (Save), nie liczba skipów.
    Na run max ``PROXY1_CHECK_LIMIT`` sprawdzeń. Skip → ogon kolejki.

    `scrape_fn(username) -> ok|remove|skip` — pod testy bez sieci.
    """
    log = log or (lambda m: print(m))
    fcfg = load_account_filter_cfg(account_id, config_root)
    # Jak proxy1: max_saved = input „Ilość oczekiwanych targetów”
    max_saved = int(limit if limit is not None else fcfg.get("limit_per_run") or 100)
    max_saved = max(1, max_saved)
    check_limit = PROXY1_CHECK_LIMIT
    proxy_mode = proxy or fcfg.get("proxy") or "losowo"
    criteria = FilterCriteria.from_dict(fcfg)
    # 0 = jak proxy1 (testy też wyłączają)
    between_s = max(0.0, float(fcfg.get("sleep_between_s") or 0))
    request_sleep_s = max(0.0, float(fcfg.get("sleep_request_s") or 0))
    retry_backoff_base_s = max(0.1, float(
        fcfg.get("retry_backoff_base_s") or RETRY_BACKOFF_BASE_S))
    retry_backoff_max_s = max(
        retry_backoff_base_s,
        float(fcfg.get("retry_backoff_max_s") or RETRY_BACKOFF_MAX_S))
    request_timeout_s = max(5.0, float(
        fcfg.get("request_timeout_s") or REQUEST_TIMEOUT_S))

    candidates = TargetCandidatesStore(account_id, data_root)
    state = RawFilterStateStore(account_id, data_root)
    pending = pending_usernames(account_id, data_root, check_limit)

    summary = {
        "account": account_id,
        "pending_before": len(pending),
        "limit": max_saved,
        "check_limit": check_limit,
        "proxy": proxy_mode,
        "checked": 0,
        "saved": 0,
        "removed": 0,
        "skipped": 0,
        "sleep_between_s": between_s,
        "sleep_request_s": request_sleep_s,
        "retry_backoff_base_s": retry_backoff_base_s,
        "retry_backoff_max_s": retry_backoff_max_s,
        "request_timeout_s": request_timeout_s,
        "dry_run": dry_run,
        "usernames_ok": [],
        "usernames_removed": [],
        "usernames_skipped": [],
    }

    if not pending:
        log(f"[filter] {account_id}: brak pending raw_profiles")
        return summary

    scraper = None
    if scrape_fn is None:
        # Ten sam konstruktor co proxy1.py (+ log do panelu)
        scraper = InstagramScraper(
            proxy_mode, criteria=criteria, log=log,
            request_sleep_s=request_sleep_s,
            retry_backoff_base_s=retry_backoff_base_s,
            retry_backoff_max_s=retry_backoff_max_s,
            request_timeout_s=request_timeout_s)

    start = time.time()
    for i, row in enumerate(pending, start=1):
        # Opcjonalna pauza — proxy1 jej nie ma (sleep_between_s=0)
        if i > 1 and between_s > 0:
            log(f"[filter] pauza {between_s:g}s przed kolejnym nickiem")
            time.sleep(between_s)
        username = row["username"]
        elapsed = time.strftime("%H:%M:%S", time.gmtime(time.time() - start))
        log(f"{i}. {username} | {elapsed}")

        if scrape_fn is not None:
            result = scrape_fn(username)
        else:
            result = scraper.scrape_user(username)  # type: ignore[union-attr]

        if result == "skip":
            summary["skipped"] += 1
            summary["usernames_skipped"].append(username)
            # Jak proxy1: skip → koniec all2.txt (tu: mark skip)
            if not dry_run:
                state.mark(username, "skip", source=row.get("source"))
            log("Skip...")
            log("----------------------------")
            continue

        summary["checked"] += 1
        if dry_run:
            if result == "ok":
                summary["saved"] += 1
                summary["usernames_ok"].append(username)
            else:
                summary["removed"] += 1
                summary["usernames_removed"].append(username)
            if summary["saved"] >= max_saved:
                log(f"Osiągnięto limit {max_saved} zapisanych użytkowników. "
                    "Zatrzymuję skrypt.")
                break
            continue

        if result == "ok":
            candidates.add(
                username,
                source=row.get("source") or "profile_filter",
                has_ring=bool(row.get("has_ring")),
                from_target=row.get("from_target"),
                content_kind=row.get("content_kind"),
                pre_filter_verdict={
                    "followers_min": criteria.followers_min,
                    "followers_max": criteria.followers_max,
                    "proxy": proxy_mode,
                },
            )
            state.mark(username, "ok", source=row.get("source"))
            summary["saved"] += 1
            summary["usernames_ok"].append(username)
        else:
            state.mark(username, "remove", source=row.get("source"))
            summary["removed"] += 1
            summary["usernames_removed"].append(username)

        # Jak process_users_txt: stop po N zapisanych, nie po N skipach
        if summary["saved"] >= max_saved:
            log(f"Osiągnięto limit {max_saved} zapisanych użytkowników. "
                "Zatrzymuję skrypt.")
            break

    if scraper is not None:
        summary["total_requests"] = scraper.stats.total_requests
    log(
        f"[filter] done checked={summary['checked']} "
        f"saved={summary['saved']} removed={summary['removed']} "
        f"skipped={summary['skipped']}"
    )
    return summary


def write_last_run(account_id: str, data_root: Path, summary: dict,
                   slot: str | None = None) -> Path:
    """Zapisz wynik ostatniego przebiegu + opcjonalnie slot harmonogramu."""
    d = data_root / "accounts" / account_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / "profile_filter_last_run.json"
    payload = {
        **summary,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "slot": slot,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    # Append do historii slotów (dedup schedulera)
    if slot:
        runs = d / "profile_filter_runs.jsonl"
        with runs.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "slot": slot,
                "ts": payload["finished_at"],
                "saved": summary.get("saved"),
                "checked": summary.get("checked"),
                "removed": summary.get("removed"),
            }, ensure_ascii=False) + "\n")
    return path


def read_last_run(account_id: str, data_root: Path) -> dict | None:
    path = data_root / "accounts" / account_id / "profile_filter_last_run.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def filter_status(account_id: str, data_root: Path,
                  config_root: Path) -> dict:
    """Status do panelu: config + liczniki + last run."""
    fcfg = load_account_filter_cfg(account_id, config_root)
    raw = RawProfilesStore(account_id, data_root)
    state = RawFilterStateStore(account_id, data_root)
    cand = TargetCandidatesStore(account_id, data_root)
    decided = state.decided_usernames()
    unique_raw = raw.unique_usernames()
    pending_n = len(unique_raw - decided)
    last = read_last_run(account_id, data_root)
    return {
        "config": fcfg,
        "raw_unique": len(unique_raw),
        "pending": pending_n,
        "candidates": len(cand.read_all()),
        "decided": len(decided),
        "last_run": last,
    }
