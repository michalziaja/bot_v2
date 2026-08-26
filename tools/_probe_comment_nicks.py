"""Live: otwórz sheet komentarzy FULL SCREEN → dump węzłów z potencjalnymi nickami."""
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
from v2.steps.registry import load_all
from v2.steps.reels import _sheet
from v2.steps.post import _post


def _step(ctx, name, params=None):
    return stepcall.call(steps, ctx, name, params or {}, source="probe_cmt")


def dump_candidates(flat, h):
    """Wypisz węzły, które mogą być nickami (nie pełny dump)."""
    hits = []
    for e in flat:
        rid = e.get("resource-id") or ""
        short = rid.split("/")[-1] if rid else ""
        text = (e.get("text") or "").strip()
        desc = (e.get("content-desc") or "").strip()
        cls = (e.get("class") or "").split(".")[-1]
        b = e.get("bounds") or ""
        low = (short + " " + text + " " + desc).lower()
        if not any(k in low for k in (
            "user", "name", "comment", "author", "nick",
        )) and not (text and " " not in text and 2 < len(text) < 35):
            # i tak bierz TextView z krótkim tekstem w strefie sheetu
            if "TextView" not in cls and "Button" not in cls:
                continue
            if not text or " " in text or len(text) > 40:
                continue
        y = None
        try:
            y = int(str(b).split("][")[0].strip("[").split(",")[1])
        except Exception:
            pass
        if y is not None and y < 40:
            continue
        hits.append({
            "id": short, "cls": cls, "text": text[:50],
            "desc": desc[:50], "bounds": b, "y": y,
            "clickable": bool(e.get("clickable")),
        })
    # unikalne po (id, text, y)
    seen, out = set(), []
    for h_ in sorted(hits, key=lambda x: (x["y"] or 0, x["id"])):
        key = (h_["id"], h_["text"], h_["y"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h_)
    return out


def main():
    load_all()
    load_plugins()
    ctx = build_context("a50_clone3", speed=2.5)
    top = ctx.navigator.identify().top
    print("start", top)

    # Zamknij aktualny sheet / wróć do profilu→kafelek
    for _ in range(4):
        t = ctx.navigator.identify().top
        if t in ("profile_other", "home_feed", "search_top"):
            break
        ctx.device.press_back()
        time.sleep(0.4)

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

    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    entry = _post.find_comments_entry(flat)
    print("comments_entry", entry)
    if not entry:
        return 2
    ctx.ht.tap(*entry, pause_kind="tap")
    ctx.humanizer.pause("between_steps")
    ctx.device.wait_for_stable_ui(max_wait_s=2.0)
    time.sleep(0.6)

    # Expand confirmed (działający gest)
    exp = _sheet.expand_sheet_confirmed(ctx, max_pulls=3)
    print("expand", {k: exp.get(k) for k in (
        "full_screen", "sheet_top", "sheet_top_pct", "rows_before",
        "rows_after", "handle_y")})

    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    w, h = ctx.account.device["_hardware"]["resolution"]
    print("screen", ctx.navigator.identify().top, "H", h, "nodes", len(flat))

    cands = dump_candidates(flat, h)
    print(f"\n=== CANDIDATES ({len(cands)}) ===")
    for c in cands[:40]:
        print(f"  y={c['y']} id={c['id']!r} cls={c['cls']} "
              f"text={c['text']!r} desc={c['desc']!r} click={c['clickable']}")

    # Co zbiera obecny collect_rows
    rows = _sheet.collect_rows(flat, _sheet.COMMENT_USERNAME_IDS)
    rows2 = _sheet.collect_rows(flat, _sheet.LIKER_USERNAME_IDS + _sheet.COMMENT_USERNAME_IDS)
    print("\ncollect COMMENT_IDS", len(rows), [r["username"] for r in rows[:10]])
    print("collect ALL", len(rows2), [r["username"] for r in rows2[:10]])

    # ID zawierające comment/user w całym flat
    id_set = sorted({(e.get("resource-id") or "").split("/")[-1]
                     for e in flat if e.get("resource-id")})
    interesting = [i for i in id_set if any(
        k in i.lower() for k in ("comment", "user", "name", "author", "row_"))]
    print("\n=== IDS with comment/user/name/row ===")
    for i in interesting:
        print(" ", i)

    out = Path(__file__).with_name("_probe_comment_nicks_out.json")
    out.write_text(json.dumps({
        "expand": exp, "candidates": cands[:60],
        "interesting_ids": interesting,
        "collect_comment": [r["username"] for r in rows],
        "collect_all": [r["username"] for r in rows2],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nWrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
