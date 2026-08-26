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
from v2.profile_filter.core import FilterCriteria, InstagramScraper

# Domyślna sekcja account.json — panel / CLI / tick
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
    """Username'y z raw jeszcze nie rozstrzygnięte w filter state.

    Najnowsze pierwsze; jeden wiersz na nick. Skip/ok/remove w state = pomiń.
    """
    raw = RawProfilesStore(account_id, data_root)
    state = RawFilterStateStore(account_id, data_root)
    done = state.decided_usernames()
    # skip zostawiamy do ponownej próby — nie są w decided
    out: list[dict] = []
    seen: set[str] = set()
    for row in reversed(raw.read_all()):
        u = row.get("username")
        if not u or u in seen or u in done:
            continue
        seen.add(u)
        out.append(row)
        if len(out) >= limit:
            break
    return out


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

    `scrape_fn(username) -> ok|remove|skip` — pod testy bez sieci.
    """
    log = log or (lambda m: print(m))
    fcfg = load_account_filter_cfg(account_id, config_root)
    lim = int(limit if limit is not None else fcfg.get("limit_per_run") or 100)
    proxy_mode = proxy or fcfg.get("proxy") or "losowo"
    criteria = FilterCriteria.from_dict(fcfg)

    candidates = TargetCandidatesStore(account_id, data_root)
    state = RawFilterStateStore(account_id, data_root)
    pending = pending_usernames(account_id, data_root, lim)

    summary = {
        "account": account_id,
        "pending_before": len(pending),
        "limit": lim,
        "proxy": proxy_mode,
        "checked": 0,
        "saved": 0,
        "removed": 0,
        "skipped": 0,
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
        scraper = InstagramScraper(
            proxy_mode=proxy_mode, criteria=criteria, log=log)

    start = time.time()
    for i, row in enumerate(pending, start=1):
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
            # Nie zapisujemy do state — wróci przy następnym runie
            log("Skip…")
            continue

        summary["checked"] += 1
        if dry_run:
            if result == "ok":
                summary["saved"] += 1
                summary["usernames_ok"].append(username)
            else:
                summary["removed"] += 1
                summary["usernames_removed"].append(username)
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
