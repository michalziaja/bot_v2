"""search.collect_tag_variants — scrape listy hashtagów z zakładki Znaczniki.

Wymaga aktywnej zakładki Tags (zwykle po `search.switch_results_tab`).
Live (45_search_tags_tab):
  - `row_hashtag_textview_tag_name` → `#fitnessmotivation`
  - `row_hashtag_textview_tag_subtitle` → `174 mln postów` / `922 tys. postów`

Zapisuje scratch `tag_variants` / `tag_variants_query` / `tag_variants_tab`.
Persist do `niche.tag_variants` — `store.write_niche_tag_variants`
(merge + zapis JSON konta) gdy `write_to_account=True`.
"""
from __future__ import annotations

import re
import time
import unicodedata

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register

TAG_NAME_ID = "com.instagram.android:id/row_hashtag_textview_tag_name"
TAG_SUBTITLE_ID = "com.instagram.android:id/row_hashtag_textview_tag_subtitle"
TAG_CONTAINER_ID = "com.instagram.android:id/row_hashtag_container"

# PL fold jak w type_query — dedup bez ogonków
_PL_FOLD = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N",
    "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z",
})

# „174 mln postów”, „23,1 mln”, „922 tys. postów”, „1.2M posts”, „500K posts”
_POST_COUNT_RE = re.compile(
    r"([\d\s]+(?:[.,]\d+)?)\s*"
    r"(mln|milion(?:y|ów)?|tys\.?|tysi[aą]c(?:e|y)?|m|k|b)?\s*"
    r"(?:post|posts|publikacj)",
    re.IGNORECASE,
)


@register
class SearchCollectTagVariants:
    name = "search.collect_tag_variants"
    requires_screen = None

    def run(self, ctx, params):
        max_items = int(params.get("max_items") or params.get("max_variants") or 15)
        max_scrolls = int(params.get("max_scrolls", 2))
        sort_by_posts = bool(params.get("sort_by_post_count", True))
        include_seed = bool(params.get("include_seed", False))
        query = str(
            params.get("query")
            or ctx.scratch.get("tag_variants_query")
            or ctx.scratch.get("last_search_query")
            or ""
        ).strip()
        write_account = bool(params.get("write_to_account", False))

        collected: list[tuple[str, int | None]] = []  # (tag, post_count|None)
        seen: set[str] = set()
        scrolls = 0

        for round_i in range(max_scrolls + 1):
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
            rows = _scrape_tag_rows(flat)
            if not rows and round_i == 0:
                ctx.log("search.collect_tag_variants.empty",
                        query=query[:60], round=round_i)
                return StepResult.failed(
                    "brak wierszy hashtagów — czy zakładka Znaczniki aktywna?",
                    query=query)

            for tag, posts in rows:
                key = _dedup_key(tag)
                if not key or key in seen:
                    continue
                # domyślnie nie wstawiaj samego seeda (#…) do listy wariantów
                if not include_seed and query.lstrip().startswith("#"):
                    if key == _dedup_key(_as_hashtag(query)):
                        continue
                seen.add(key)
                collected.append((tag, posts))

            if len(collected) >= max_items:
                break
            if round_i >= max_scrolls:
                break
            _scroll_list_down(ctx)
            scrolls += 1
            time.sleep(0.45)

        if sort_by_posts and any(p is not None for _, p in collected):
            collected.sort(
                key=lambda t: (t[1] is not None, t[1] or 0), reverse=True)

        # obcięcie limitu po sort
        collected = collected[:max_items]
        hashtags = [t for t, _ in collected]
        post_counts = {
            t: p for t, p in collected if p is not None
        }

        ctx.scratch["tag_variants"] = list(hashtags)
        ctx.scratch["tag_variants_query"] = query
        ctx.scratch["tag_variants_tab"] = "tags"
        if post_counts:
            ctx.scratch["tag_variants_post_counts"] = post_counts

        persisted = False
        persist_note = None
        if write_account and query and hashtags:
            persisted, persist_note = _try_persist_tag_variants(
                ctx, query, hashtags, max_variants=max_items,
                include_seed=include_seed)

        ctx.log("search.collect_tag_variants.ok",
                query=query[:60], count=len(hashtags), scrolls=scrolls,
                persisted=persisted, persist_note=persist_note)
        return StepResult.ok(
            query=query,
            hashtags=hashtags,
            count=len(hashtags),
            scrolled=scrolls,
            post_counts=post_counts,
            persisted=persisted,
            persist_note=persist_note,
        )


def _scrape_tag_rows(flat: list[dict]) -> list[tuple[str, int | None]]:
    """Zwraca [(#tag, post_count|None), ...] w kolejności UI.

    Name i subtitle są osobnymi węzłami DFS — parujemy po kolejności /
    bliskości Y.
    """
    names: list[tuple[str, int, str]] = []  # text, y1, bounds
    subs: list[tuple[str, int]] = []  # text, y1

    for e in flat:
        rid = e.get("resource-id") or ""
        text = (e.get("text") or "").strip()
        if not text:
            continue
        y = _bounds_y1(e.get("bounds"))
        if y is None:
            continue
        if rid == TAG_NAME_ID or (
                rid.endswith("row_hashtag_textview_tag_name") and text.startswith("#")):
            names.append((_as_hashtag(text), y, e.get("bounds") or ""))
        elif rid == TAG_SUBTITLE_ID or rid.endswith(
                "row_hashtag_textview_tag_subtitle"):
            subs.append((text, y))
        elif text.startswith("#") and "hashtag" in rid.lower():
            names.append((_as_hashtag(text), y, e.get("bounds") or ""))

    # fallback: goły TextView z # gdy brak ID (inna wersja IG)
    if not names:
        for e in flat:
            text = (e.get("text") or "").strip()
            if text.startswith("#") and len(text) > 1:
                y = _bounds_y1(e.get("bounds"))
                if y is not None and y > 350:  # poniżej tab bara
                    names.append((_as_hashtag(text), y, e.get("bounds") or ""))

    out: list[tuple[str, int | None]] = []
    used_sub = set()
    for tag, y, _bounds in names:
        posts = None
        best_j = None
        best_dy = 10**9
        for j, (sub_text, sy) in enumerate(subs):
            if j in used_sub:
                continue
            dy = abs(sy - y)
            if dy < best_dy and dy < 120:
                best_dy = dy
                best_j = j
        if best_j is not None:
            used_sub.add(best_j)
            posts = parse_post_count(subs[best_j][0])
        out.append((tag, posts))
    return out


def parse_post_count(raw: str) -> int | None:
    """Parsuj subtitle liczby postów (PL/EN) → int lub None."""
    s = (raw or "").strip().casefold()
    if not s:
        return None
    m = _POST_COUNT_RE.search(s)
    if not m:
        # sama liczba?
        m2 = re.search(r"([\d]+(?:[.,]\d+)?)\s*(mln|tys\.?|m|k)?", s)
        if not m2:
            return None
        num_s, unit = m2.group(1), (m2.group(2) or "")
    else:
        num_s, unit = m.group(1), (m.group(2) or "")

    num_s = num_s.replace(" ", "").replace("\xa0", "")
    # PL: 23,1 → 23.1; EN: 23.1 zostaje
    if "," in num_s and "." not in num_s:
        num_s = num_s.replace(",", ".")
    elif "," in num_s and "." in num_s:
        # 1,234.5 EN — usuń przecinki tysięcy
        num_s = num_s.replace(",", "")
    try:
        val = float(num_s)
    except ValueError:
        return None

    unit = unit.casefold()
    if unit in ("mln", "m") or unit.startswith("milion"):
        val *= 1_000_000
    elif unit in ("tys", "tys.", "k") or unit.startswith("tysi"):
        val *= 1_000
    elif unit == "b":
        val *= 1_000_000_000
    return int(val)


def _as_hashtag(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if not s.startswith("#"):
        s = "#" + s
    # bez spacji w tagu
    return "#" + s.lstrip("#").replace(" ", "")


def _dedup_key(tag: str) -> str:
    """Casefold + fold PL diakrytyków."""
    t = _as_hashtag(tag)
    if not t:
        return ""
    folded = t.translate(_PL_FOLD)
    out = []
    for ch in unicodedata.normalize("NFKD", folded):
        if unicodedata.combining(ch):
            continue
        out.append(ch)
    return "".join(out).casefold()


def _scroll_list_down(ctx) -> None:
    """Swipe w górę po liście Tags (treść w dół)."""
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        w, h = 1080, 2340
    # lista zaczyna się pod tab barem (~350+) — swipe w środku listy
    ctx.ht.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.38),
                 duration_ms=320, pause_kind="tap")


def _bounds_y1(bounds) -> int | None:
    try:
        return int(str(bounds).split(",")[1].split("]")[0])
    except Exception:
        return None


def _try_persist_tag_variants(ctx, query: str, hashtags: list[str],
                              *, max_variants: int,
                              include_seed: bool) -> tuple[bool, str | None]:
    """Persist wariantów do niche.tag_variants przez store.

    Preferuje `write_niche_tag_variants` (merge + zapis JSON);
    fallback: merge_tag_variants + write_niche. Aktualizuje też
    `ctx.account.niche` w pamięci, żeby gałąź B widziała świeżą mapę.
    """
    try:
        from v2.plugins import store
    except Exception as exc:
        return False, f"store_import_failed:{exc}"

    write_tv = getattr(store, "write_niche_tag_variants", None)
    merge_fn = getattr(store, "merge_tag_variants", None)
    write_fn = getattr(store, "write_niche", None)
    if not callable(write_tv) and not callable(merge_fn):
        ctx.log("search.collect_tag_variants.persist_todo",
                reason="brak store.merge/write_niche_tag_variants — tylko scratch",
                query=query[:60], count=len(hashtags))
        return False, "TODO:store.merge_tag_variants"

    account_id = getattr(ctx.account, "account_id", None) or (
        ctx.account.get("account_id") if isinstance(ctx.account, dict) else None)
    if not account_id:
        return False, "brak account_id"

    try:
        if callable(write_tv):
            # atomowy merge + zapis do accounts/<id>.json
            niche = write_tv(
                account_id, query, hashtags,
                max_variants=max_variants,
                include_seed=include_seed,
            )
            note = "write_niche_tag_variants"
        else:
            niche = dict(getattr(ctx.account, "niche", None) or {})
            niche["tag_variants"] = merge_fn(
                niche, query, hashtags,
                max_variants=max_variants,
                include_seed=include_seed,
            )
            if callable(write_fn):
                niche = write_fn(account_id, niche)
            note = "merge_tag_variants"

        # synchronizacja in-memory — AccountConfig.niche jest mutable dict
        acc_niche = getattr(ctx.account, "niche", None)
        if isinstance(acc_niche, dict) and isinstance(niche, dict):
            acc_niche.clear()
            acc_niche.update(niche)
        elif hasattr(ctx.account, "niche"):
            ctx.account.niche = niche
        return True, note
    except Exception as exc:
        ctx.log("search.collect_tag_variants.persist_err",
                error=str(exc)[:160])
        return False, f"persist_err:{exc}"
