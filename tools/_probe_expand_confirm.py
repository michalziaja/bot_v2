"""Live: otwórz listę reakcji → pull-up z UCHWYTU → dump full_screen before/after."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from v2.cli import build_context
from v2.plugins import stepcall
from v2.plugins.registry import load_all as load_plugins
from v2.steps import registry as steps
from v2.steps.registry import load_all
from v2.steps.reels import _sheet


def _step(ctx, name, params=None):
    return stepcall.call(steps, ctx, name, params or {}, source="probe_expand")


def _print_geo(tag, geo):
    print(f"\n=== {tag} ===")
    print(f"  full_screen={geo.get('full_screen')}  top_src={geo.get('top_src')}")
    print(f"  sheet_top={geo.get('sheet_top')} ({geo.get('sheet_top_pct')}%H)")
    print(f"  sheet_h={geo.get('sheet_h')}  screen_h={geo.get('screen_h')}")
    print(f"  handle_y={geo.get('handle_y')}  title_y={geo.get('title_y')}  "
          f"rows={geo.get('rows')}")


def main():
    load_all()
    load_plugins()
    ctx = build_context("a50_clone3", speed=2.5)
    for name in ("system.wake", "system.unlock", "system.ensure_ig_running"):
        _step(ctx, name)
    try:
        ctx.navigator.ensure("home_feed", max_steps=4)
    except Exception:
        pass

    for name, params in (
        ("search.open", {}),
        ("search.type_query", {"query": "basenispa.pl"}),
        ("search.pick_result",
         {"username": "basenispa.pl", "prefer_exact": True}),
    ):
        r = _step(ctx, name, params)
        if r.status != "ok":
            print("FAIL", name, r.status)
            return 1

    opened = _step(ctx, "post.open_from_grid", {
        "ordered": True, "pool": 8, "baseline": "profile_other",
        "content_types": [],
    })
    print("tile", opened.status, opened.payload)
    if opened.status != "ok":
        return 1

    # Tap likers BEZ expand — sami mierzymy gest
    from v2.core.screen_map import flatten_hierarchy
    from v2.steps.post import _post
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    entry = _post.find_likers_entry(flat) or _post.find_comments_entry(flat)
    print("entry", entry)
    if not entry:
        return 2
    ctx.ht.tap(*entry, pause_kind="tap")
    ctx.humanizer.pause("between_steps")
    ctx.device.wait_for_stable_ui(max_wait_s=2.0)
    time.sleep(0.7)

    before = _sheet.dump_sheet_geometry(ctx)
    _print_geo("BEFORE", before)
    y_from = _sheet._sheet_handle_y(ctx, before["screen_h"])
    print(f"\n  pull start y_from={y_from} "
          f"({round(100*y_from/before['screen_h'],1)}%H) → 5%H")

    exp = _sheet.expand_sheet_confirmed(ctx, max_pulls=3)
    after = _sheet.dump_sheet_geometry(ctx)
    _print_geo("AFTER expand_sheet_confirmed", after)
    print("\nRESULT", {k: exp.get(k) for k in (
        "confirmed", "full_screen", "pulls_done", "y_from", "y_to",
        "sheet_top_before", "sheet_top_after", "sheet_top_pct",
        "sheet_h", "screen_h", "rows_before", "rows_after", "top_src")})

    if after.get("full_screen"):
        print("\nOK — lista na CALY ekran (dump full_screen=True)")
        return 0
    print("\nFAIL — nadal half-sheet")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
