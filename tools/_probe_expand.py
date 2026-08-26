"""Live: open likers → rows before/after expand; to samo comments; lokalny BACK."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from v2.cli import build_context
from v2.core.screen_map import flatten_hierarchy
from v2.plugins import stepcall
from v2.plugins.registry import load_all as load_plugins
from v2.plugins.scraper_log import say
from v2.steps import registry as steps
from v2.steps.lists.harvest_usernames import ALL_USERNAME_IDS
from v2.steps.registry import load_all
from v2.steps.reels import _sheet


def _step(ctx, name, params=None):
    return stepcall.call(steps, ctx, name, params or {}, source="probe")


def _nrows(ctx):
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    return len(_sheet.collect_rows(flat, ALL_USERNAME_IDS))


def main():
    load_all()
    load_plugins()
    ctx = build_context("a50_clone3", speed=2.5)
    for name in ("system.wake", "system.unlock", "system.ensure_ig_running"):
        _step(ctx, name)
    for name, params in (
        ("search.open", {}),
        ("search.type_query", {"query": "basenispa.pl"}),
        ("search.pick_result", {"username": "basenispa.pl", "prefer_exact": True}),
    ):
        if _step(ctx, name, params).status != "ok":
            return 1

    opened = _step(ctx, "post.open_from_grid", {
        "ordered": True, "pool": 6, "baseline": "profile_other",
        "content_types": [],
    })
    print("tile", opened.status, opened.payload)
    if opened.status != "ok":
        return 1
    kind = (opened.payload or {}).get("kind")

    # --- LIKERS: ręczny tap-path jak open, potem porównanie expand ---
    # Używamy kroku post.open_likers (ma expand + log rows_before/after)
    lik = _step(ctx, "post.open_likers", {})
    print("LIKERS", lik.status, lik.payload)
    if lik.status == "ok":
        pl = lik.payload or {}
        print("  rows_before", pl.get("rows_before"),
              "rows_after", pl.get("rows_after"),
              "y_from", pl.get("y_from"), "pulls", pl.get("pulls"))
        har = _step(ctx, "list.harvest_usernames", {
            "limit": 6, "max_scrolls": 2, "source": f"{kind}_likers",
            "dedup_days": 0,
            "extra": {"from_target": "basenispa.pl", "content_kind": kind},
        })
        print("  harvest_likers", har.status, har.payload)
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
        time.sleep(0.5)

    # --- COMMENTS ---
    com = _step(ctx, "post.open_comments", {})
    print("COMMENTS", com.status, com.payload)
    if com.status == "ok":
        pl = com.payload or {}
        print("  rows_before", pl.get("rows_before"),
              "rows_after", pl.get("rows_after"),
              "y_from", pl.get("y_from"))
        # dodatkowy expand + pomiar (plugin i tak expandował)
        before = _nrows(ctx)
        info = _sheet.expand_sheet(ctx, pulls=1)
        after = _nrows(ctx)
        print("  extra_expand", info, "rows", before, "->", after)
        har = _step(ctx, "list.harvest_usernames", {
            "limit": 5, "max_scrolls": 2,
            "source": f"{kind}_commenters", "dedup_days": 0,
            "username_ids": list(_sheet.COMMENT_USERNAME_IDS),
            "extra": {"from_target": "basenispa.pl", "content_kind": kind},
        })
        print("  harvest_comments", har.status, har.payload)
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
    else:
        # lokalny retry BACK×1
        say("comments fail — BACK×1 retry")
        ctx.device.press_back()
        time.sleep(0.4)
        com2 = _step(ctx, "post.open_comments", {})
        print("COMMENTS_RETRY", com2.status, com2.payload)

    print("top_end", ctx.navigator.identify().top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
