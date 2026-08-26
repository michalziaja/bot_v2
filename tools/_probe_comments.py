"""Probe: viewer z siatki → dump UI komentarzy → spróbuj open_comments."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from v2.cli import build_context
from v2.core.screen_map import flatten_hierarchy
from v2.plugins import stepcall
from v2.plugins.registry import load_all as load_plugins
from v2.steps import registry as steps
from v2.steps.post import _post
from v2.steps.registry import load_all
from v2.steps.reels import _sheet


def _step(ctx, name, params=None):
    return stepcall.call(steps, ctx, name, params or {}, source="probe")


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
        r = _step(ctx, name, params)
        if r.status != "ok":
            print("fail", name, r.status)
            return 1

    r = _step(ctx, "post.open_from_grid", {
        "ordered": True, "pool": 6, "baseline": "profile_other",
        "content_types": [],
    })
    print("opened", r.status, r.payload)

    # Najpierw likers i BACK — jak w prawdziwym flow
    lik = _step(ctx, "post.open_likers", {})
    print("likers", lik.status, lik.payload)
    if lik.status == "ok":
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
        time.sleep(0.5)

    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    print("top", ctx.navigator.identify().top)
    print("find_comments_entry", _post.find_comments_entry(flat))

    hits = []
    for e in flat:
        rid = (e.get("resource-id") or "")
        text = (e.get("text") or "")
        desc = (e.get("content-desc") or "")
        blob = (rid + " " + text + " " + desc).lower()
        if any(k in blob for k in ("koment", "comment", "row_feed_button")):
            hits.append({
                "rid": rid.split("/")[-1],
                "click": e.get("clickable"),
                "text": text[:80],
                "desc": desc[:80],
                "bounds": e.get("bounds"),
            })
    # wszystkie clickable z liczbą
    nums = []
    for e in flat:
        if not e.get("clickable"):
            continue
        t = (e.get("text") or "").strip()
        if t and any(ch.isdigit() for ch in t):
            nums.append({"text": t, "bounds": e.get("bounds"),
                         "rid": (e.get("resource-id") or "").split("/")[-1]})
    print("COMMENTISH", json.dumps(hits, ensure_ascii=False, indent=2))
    print("NUM_CLICKABLE", json.dumps(nums, ensure_ascii=False, indent=2))

    eng = _step(ctx, "post.open_comments", {})
    print("open_comments", eng.status, eng.payload)
    if eng.status == "ok":
        _sheet.expand_sheet(ctx)
        after = flatten_hierarchy(ctx.device.hierarchy(force=True))
        rows = _sheet.collect_rows(after, _sheet.COMMENT_USERNAME_IDS)
        rows2 = _sheet.collect_rows(after, _sheet.LIKER_USERNAME_IDS)
        print("comment_rows", len(rows), "liker_id_rows", len(rows2),
              "top", ctx.navigator.identify().top)
        # sample rids
        sample = []
        for e in after:
            rid = (e.get("resource-id") or "")
            if "user" in rid.lower() or "comment" in rid.lower():
                sample.append({
                    "rid": rid.split("/")[-1],
                    "text": (e.get("text") or "")[:40],
                    "click": e.get("clickable"),
                })
            if len(sample) >= 20:
                break
        print("SAMPLE", json.dumps(sample, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
