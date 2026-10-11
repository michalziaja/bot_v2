"""Wlej live 7/14/30 + serie pozycji do store jobsaver — bez seed-wipe.

Dopasowanie wierszy po kind+views (id z captions zostaje). Komentarze PL.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.calibration.recon_pro_dash._trace_item_charts import (  # noqa: E402
    build_item_series,
    parse_overview_dumps,
)
from v2.data.pro_dash_store import ProDashStore  # noqa: E402

DATA = Path("/home/michal/bot_v2/data")
OUT = Path(__file__).resolve().parent


def _match_row(doc: dict, kind: str, views: int) -> dict | None:
    for row in (doc.get("content") or {}).get(kind) or []:
        if row.get("views") == views:
            return row
    return None


def main() -> int:
    ov = parse_overview_dumps()
    series = build_item_series()
    (OUT / "OVERVIEW_WINDOWS.json").write_text(
        json.dumps(ov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "ITEM_SERIES.json").write_text(
        json.dumps(series, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    store = ProDashStore("jobsaver", DATA)
    doc = store.read()
    as_of = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    content = []
    for rec in series.get("items") or []:
        kind = rec.get("kind") or "post"
        views = rec.get("views")
        prev = _match_row(doc, kind, views) or {}
        row = {
            "id": prev.get("id") or rec.get("id"),
            "kind": kind,
            "caption": prev.get("caption"),
            "age": prev.get("age") or "",
            "views": views if views is not None else prev.get("views"),
            "likes": prev.get("likes"),
            "comments": prev.get("comments"),
            "shares": prev.get("shares"),
            "saves": prev.get("saves"),
            "replies": prev.get("replies"),
            "thumb": prev.get("thumb"),
            "daily": rec.get("daily") or [],
            "daily_mode": rec.get("daily_mode") or prev.get("daily_mode"),
            "typical_daily": rec.get("typical_daily") or [],
            "series_by_window": rec.get("series_by_window") or {},
            "typical_by_window": rec.get("typical_by_window") or {},
            "chart_kind": rec.get("chart_kind") or prev.get("chart_kind"),
            "window_gaps": rec.get("window_gaps") or {},
            "ig_windows_identical": rec.get("ig_windows_identical"),
        }
        content.append(row)

    gaps = [
        "live 2026-09-05: Ogólne 7/14/30 z 40_ov_*d; drill-in wszystkich "
        "postów i relacji (40_post_* / 40_story_*); rolki empty",
    ]
    for rec in series.get("items") or []:
        for k, msg in (rec.get("window_gaps") or {}).items():
            gaps.append(f"{rec.get('kind')} {rec.get('views')} {k}: {msg}")

    scrape = {
        "as_of": as_of,
        "source": "live",
        "ig_username": "jobsaver.app",
        "windows": ov.get("windows") or {},
        "content": content,
        "gaps": gaps,
    }
    store.merge_scrape(scrape)
    view = store.public_view()
    print("source", view.get("source"), "updated", view.get("updated_at"))
    for k, w in (view.get("windows") or {}).items():
        print("win", k, {m: w.get(m) for m in
                         ("views", "follows", "interactions", "viewers")})
    for kind in ("post", "story"):
        for r in (view.get("content") or {}).get(kind) or []:
            by = r.get("series_by_window") or {}
            print(kind, r.get("id"), r.get("views"), r.get("chart_kind"),
                  "wins", list(by), "n", {w: len(by.get(w) or {}) for w in by})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
