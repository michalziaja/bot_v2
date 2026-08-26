"""Live A/B: jak najlepiej rozwinąć opis rolki, żeby był zawsze widoczny.

Uruchomienie (z katalogu nadrzędnego `bot/`):
  python -m v2.tools.probe_reel_caption --account=a50_clone2 --user=apply4me.app

Dla każdej metody: świeże otwarcie 1. rolki → gest → screen + dump opisu → BACK.
Wyniki + JPG w data/accounts/<id>/frames/<session>/caption_probe/
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v2.cli import build_context
from v2.core.screen_map import flatten_hierarchy
from v2.plugins.impl import niche_discover as nd
from v2.steps._expand_more import expand_more_if_present
from v2.steps.registry import load_all
from v2.steps.reels.read_caption_comments import CAPTION_IDS, PANEL_MARKERS


def _panel_open(flat: list[dict]) -> bool:
    for n in flat:
        rid = (n.get("resource-id") or "").lower()
        if any(m in rid for m in PANEL_MARKERS):
            return True
    return False


def _caption_from_ids(flat: list[dict]) -> str:
    best = ""
    for n in flat:
        if n.get("resource-id") in CAPTION_IDS:
            t = (n.get("text") or "").strip()
            if len(t) > len(best):
                best = t
    return best


def _measure(ctx) -> dict:
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    by_id = _caption_from_ids(flat)
    dump = nd._dump_caption_text(ctx)
    return {
        "panel": _panel_open(flat),
        "chars_id": len(by_id),
        "chars_dump": len(dump or ""),
        "preview": (by_id or dump or "")[:120],
        "truncated": nd._bio_looks_truncated(by_id or dump),
    }


def _shot(ctx, out_dir: Path, name: str) -> str | None:
    path = out_dir / f"{name}.jpg"
    try:
        path.write_bytes(ctx.device.screenshot())
        return str(path)
    except Exception as exc:
        print(f"  [shot fail] {exc}")
        return None


def _open_first_reel(ctx, baseline: str) -> bool:
    ctx.scratch["grid_used"] = set()
    nd._return_to_profile_main(ctx, baseline)
    if not nd._ensure_reels_tab(ctx, baseline=baseline):
        print("BRAK zakładki Rolki")
        return False
    time.sleep(0.8)
    nd._reveal_profile_grid(ctx)
    time.sleep(0.4)
    opened = nd._step(ctx, "post.open_from_grid", {
        "pool": 8, "baseline": baseline,
        "content_types": ["reels"], "ordered": True,
    })
    if opened.status != "ok":
        print(f"  open_from_grid: {opened.status} {opened.error or opened.payload}")
        return False
    if not nd._ensure_media_viewer(ctx, want_kinds=["reel"]):
        print("  viewer check FAIL")
        return False
    return True


def _method_noop(ctx):
    pass


def _method_expand_more(ctx):
    expand_more_if_present(ctx)
    time.sleep(0.45)


def _method_swipe_mid(ctx):
    w, h = ctx.account.device["_hardware"]["resolution"]
    ctx.ht.swipe(w // 2, int(h * 0.85), w // 2, int(h * 0.50),
                 duration_ms=280, pause_kind="tap")
    time.sleep(0.7)


def _method_swipe_deep(ctx):
    w, h = ctx.account.device["_hardware"]["resolution"]
    ctx.ht.swipe(w // 2, int(h * 0.88), w // 2, int(h * 0.32),
                 duration_ms=350, pause_kind="tap")
    time.sleep(0.9)


def _method_swipe_then_more(ctx):
    _method_swipe_mid(ctx)
    expand_more_if_present(ctx)
    time.sleep(0.4)
    if nd._bio_looks_truncated(nd._dump_caption_text(ctx)):
        expand_more_if_present(ctx)
        time.sleep(0.35)


def _method_more_then_swipe(ctx):
    expand_more_if_present(ctx)
    time.sleep(0.4)
    _method_swipe_mid(ctx)


def _method_tap_caption_id(ctx):
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    for n in flat:
        if n.get("resource-id") in CAPTION_IDS:
            from v2.steps._expand_more import _center
            c = _center(n.get("bounds"))
            if c:
                ctx.ht.tap(*c, pause_kind="tap")
                time.sleep(0.7)
                return
    # fallback: strefa opisu lewy-dół
    w, h = ctx.account.device["_hardware"]["resolution"]
    ctx.ht.tap(int(w * 0.18), int(h * 0.82), pause_kind="tap")
    time.sleep(0.7)


def _method_tap_bottom_left(ctx):
    w, h = ctx.account.device["_hardware"]["resolution"]
    ctx.ht.tap(int(w * 0.15), int(h * 0.80), pause_kind="tap")
    time.sleep(0.7)


def _method_double_swipe_soft(ctx):
    """Dwa krótkie swipe'e — mniej ryzyka przewinięcia na następną rolkę."""
    w, h = ctx.account.device["_hardware"]["resolution"]
    for _ in range(2):
        ctx.ht.swipe(w // 2, int(h * 0.82), w // 2, int(h * 0.62),
                     duration_ms=220, pause_kind="tap")
        time.sleep(0.45)
    expand_more_if_present(ctx)
    time.sleep(0.35)


METHODS = [
    ("01_baseline", _method_noop),
    ("02_expand_more", _method_expand_more),
    ("03_swipe_mid", _method_swipe_mid),
    ("04_swipe_deep", _method_swipe_deep),
    ("05_swipe_then_more", _method_swipe_then_more),
    ("06_more_then_swipe", _method_more_then_swipe),
    ("07_tap_caption_id", _method_tap_caption_id),
    ("08_tap_bottom_left", _method_tap_bottom_left),
    ("09_double_swipe_soft", _method_double_swipe_soft),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="a50_clone2")
    ap.add_argument("--user", default="apply4me.app")
    ap.add_argument("--speed", type=float, default=3.0)
    args = ap.parse_args()

    load_all()
    ctx = build_context(args.account, speed=args.speed)
    out_dir = (Path(__file__).resolve().parents[1] / "data" / "accounts"
               / args.account / "frames" / ctx.session_id / "caption_probe")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"session={ctx.session_id}")
    print(f"out={out_dir}")

    # wake / zamknij shade / IG na foreground
    try:
        ctx.device.wake()
    except Exception:
        pass
    # NotificationShade blokuje UI — collapse statusbara + HOME + relaunch
    import subprocess
    serial = (ctx.account.device.get("_hardware") or {}).get("adb_id") or ""
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "cmd", "statusbar", "collapse"],
            check=False, timeout=5,
            capture_output=True,
        )
    except Exception:
        pass
    try:
        ctx.device.press_home()
        time.sleep(0.5)
    except Exception:
        pass
    try:
        ctx.device.launch_app(ctx.ig_package)
        time.sleep(2.5)
    except Exception as exc:
        print(f"launch_app: {exc}")
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "cmd", "statusbar", "collapse"],
            check=False, timeout=5,
            capture_output=True,
        )
    except Exception:
        pass
    if not ctx.navigator.ensure("home_feed", max_steps=8):
        print(f"ensure home_feed FAIL top={ctx.navigator.identify().top}")
        return 1
    print(f"na home_feed OK")

    plug = nd.NicheDiscover()
    ok = plug._open_competitor(ctx, args.user)
    if not ok:
        print(f"nie otwarto profilu @{args.user}")
        return 1
    baseline = ctx.navigator.identify().top
    print(f"profil OK baseline={baseline}")

    results = []
    for name, fn in METHODS:
        print(f"\n=== {name} ===")
        if not _open_first_reel(ctx, baseline):
            results.append({"method": name, "ok": False, "error": "open_failed"})
            continue
        _shot(ctx, out_dir, f"{name}_before")
        before = _measure(ctx)
        try:
            fn(ctx)
        except Exception as exc:
            print(f"  EXC: {exc}")
            results.append({"method": name, "ok": False, "error": str(exc)})
            nd._return_to_profile_main(ctx, baseline)
            continue
        time.sleep(0.35)
        after = _measure(ctx)
        shot = _shot(ctx, out_dir, f"{name}_after")
        row = {
            "method": name,
            "ok": True,
            "before": before,
            "after": after,
            "shot": shot,
            "gain_id": after["chars_id"] - before["chars_id"],
            "gain_dump": after["chars_dump"] - before["chars_dump"],
            "score": (
                after["chars_id"] * 2
                + after["chars_dump"]
                + (80 if after["panel"] else 0)
                + (0 if after["truncated"] else 40)
            ),
        }
        results.append(row)
        print(f"  panel={after['panel']} id={after['chars_id']} "
              f"dump={after['chars_dump']} trunc={after['truncated']} "
              f"score={row['score']}")
        print(f"  preview={after['preview']!r}")
        nd._return_to_profile_main(ctx, baseline)
        time.sleep(0.4)

    ranking = sorted(
        [r for r in results if r.get("ok")],
        key=lambda r: r.get("score", 0),
        reverse=True,
    )
    summary = {"user": args.user, "ranking": ranking, "all": results}
    (out_dir / "results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== RANKING =====")
    for i, r in enumerate(ranking, 1):
        a = r["after"]
        print(f"{i}. {r['method']:22s} score={r['score']:4d} "
              f"id={a['chars_id']:4d} dump={a['chars_dump']:4d} "
              f"panel={a['panel']} trunc={a['truncated']}")
    if ranking:
        print(f"\nBEST: {ranking[0]['method']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
