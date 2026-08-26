"""target_scraper — zbieranie profili z otoczenia wybranych kont. ZERO interakcji.

Wchłonął planowany osobno `profile_scraper`: jeden plugin z wyborem źródeł
(`post_likers`, `post_commenters`, `reel_likers`, `reel_commenters`,
`followers`, `following`).

**Brak interakcji jest wymuszony w kodzie** — plugin nie woła like/follow.
Interakcje robi potem `profile_interact` z tej samej listy.

Przebieg JEDNEGO targetu (search TYLKO na starcie targetu):

    search (raz) → profile_other
      → siatka POSTÓW (domyślna): N kafelków ordered
           likers → expand+confirm → harvest → BACK do treści
           commenters → expand+confirm → harvest → BACK do profile_other
      → zakładka ROLKI (ten sam profil, bez nav Clips):
           N rolek — to samo engagement in-place
      → followers / following (jeśli w sources) — pełny ekran, bez expand
    → ensure home_feed  [dopiero po wyczerpaniu targetu]
    → kolejny target → znowu search  |  brak kolejnego → koniec na home
"""
from __future__ import annotations

import random
import time

from v2.plugins import stepcall
from v2.plugins.base import Budget, PluginResult, draw_range
from v2.plugins.registry import register
from v2.plugins.scraper_log import say, target_line, grid_item_line
from v2.plugins.progress_log import announce_progress, sync_plugin_stats
from v2.steps import registry as steps
from v2.steps.lists.harvest_usernames import ALL_USERNAME_IDS
from v2.steps.reels import _sheet

HOME = "home_feed"
PROFILE = "profile_other"

# Lista wprost na profilu — reszta wymaga wejścia w treść z siatki
DIRECT_SOURCES = {"followers": "followers", "following": "following"}

# Źródła engagement z kafelków: (rodzaj listy, content_kind)
POST_SOURCES = {
    "post_likers": ("likers", "post"),
    "post_commenters": ("comments", "post"),
}
REEL_SOURCES = {
    "reel_likers": ("likers", "reel"),
    "reel_commenters": ("comments", "reel"),
}
GRID_SOURCES = {**POST_SOURCES, **REEL_SOURCES}

# Kolejność engagement na otwartej treści
_ENG_ORDER = ("likers", "comments")


@register
class TargetScraper:
    id = "target_scraper"
    entry_screen = HOME
    exit_screen = HOME

    def run(self, ctx, s: dict, budget: Budget) -> PluginResult:
        rng = random.Random()
        stats = {"targets": 0, "lists": 0, "saved": 0, "with_ring": 0,
                 "posts_opened": 0, "reels_opened": 0, "no_tiles": 0,
                 "dupes_skipped": 0}

        targets = _pick_targets(s, rng)
        if not targets:
            ctx.log("target_scraper.no_targets")
            return PluginResult.done(budget, "empty", **stats)

        sources = [x for x in (s.get("sources") or []) if x]
        if not sources:
            ctx.log("target_scraper.no_sources")
            return PluginResult.done(budget, "empty", **stats)

        # Flaga dla operator_say w krokach (expand / open)
        ctx.scratch["target_scraper_active"] = True
        stopped_by = "items"
        try:
            t_n = len(targets)
            for i, target in enumerate(targets):
                reason = budget.stop_reason()
                if reason:
                    stopped_by = reason
                    break
                # Target @nick i/N → SessionStats phase=target
                announce_progress(
                    ctx, say,
                    msg=target_line(target, i + 1, t_n),
                    phase="target", current=i + 1, total=t_n,
                    label="Target", detail=f"@{target}")
                self._one_target(ctx, s, target, sources, budget, rng, stats)
                budget.tick()
                stats["targets"] += 1
                sync_plugin_stats(ctx, stats, items_done=budget.items_done,
                                  max_items=budget.max_items)
                # Wyjście z profilu dopiero po CAŁYM targetcie — HOME / pod search
                # kolejnego. NIGDY mid-target (między kafelkami / zakładkami).
                ctx.log("target_scraper.target_leave", target=target,
                        next_target=(targets[i + 1] if i + 1 < len(targets)
                                     else None))
                ctx.navigator.ensure(HOME, max_steps=4)

            ctx.log("target_scraper.done", stopped_by=stopped_by, **stats)
            return PluginResult.done(budget, stopped_by, **stats)
        finally:
            ctx.scratch.pop("target_scraper_active", None)

    # ------------------------------------------------------------------
    def _one_target(self, ctx, s, target, sources, budget, rng, stats) -> None:
        # Linia „Target @…” już z announce_progress w run() — tu tylko event
        ctx.log("target_scraper.target_start", target=target, sources=sources)
        if not self._go_to_profile(ctx, target):
            return

        saved_before = stats["saved"]
        # Nieznane źródła — log, reszta i tak leci
        for src in sources:
            if (src not in DIRECT_SOURCES and src not in GRID_SOURCES):
                ctx.log("target_scraper.unknown_source", source=src)

        # 1) Posty na GŁÓWNEJ siatce profilu
        post_srcs = [x for x in sources if x in POST_SOURCES]
        if post_srcs and not budget.user_stopped():
            self._from_posts_grid(ctx, s, post_srcs, target, rng, stats, budget)

        # 2) Zakładka Rolki — ten sam profil, bez globalnej nav Clips
        reel_srcs = [x for x in sources if x in REEL_SOURCES]
        if reel_srcs and not budget.user_stopped():
            self._from_reels_tab(ctx, s, reel_srcs, target, rng, stats, budget)

        # 3) Followers / following — też na profilu
        for src in sources:
            if budget.user_stopped():
                break
            if src in DIRECT_SOURCES:
                self._from_profile_list(ctx, s, src, target, rng, stats)
                _back_to_profile(ctx)

        gained = stats["saved"] - saved_before
        say(f"Koniec @{target} — zapisano {gained}")

    def _from_posts_grid(self, ctx, s, sources, target, rng, stats,
                         budget) -> None:
        """Siatka domyślna (Posty) — ordered N kafelków, engagement in-place."""
        posts_cfg = s.get("posts") or {}
        want = draw_range(posts_cfg.get("last_posts"), rng, 3)
        _ = bool(posts_cfg.get("skip_pinned"))  # na razie no-op (brak filtra desc)
        say(f"Siatka postow — {want} kafelkow")
        ctx.log("target_scraper.posts_grid_start", want=want, sources=sources)
        # Czysty kursor siatki postów
        ctx.scratch.pop("grid_used", None)
        self._grid_loop(
            ctx, s, sources, target, rng, stats, budget,
            want=want, content_types=["posts"], expect_kind="post",
            label_one="Post", opened_key="posts_opened")

    def _from_reels_tab(self, ctx, s, sources, target, rng, stats,
                        budget) -> None:
        """Zakładka Rolki na profilu → ordered N rolek + engagement in-place."""
        posts_cfg = s.get("posts") or {}
        # Ten sam last_posts = ile rolek (katalog: „postów/rolek”)
        want = draw_range(posts_cfg.get("last_posts"), rng, 3)
        say(f"Zakladka Rolki — {want} kafelkow")
        if not _ensure_profile_reels_tab(ctx):
            say("Zakladka Rolki — niedostepna, skip")
            ctx.log("target_scraper.reels_tab_missing", target=target)
            return
        ctx.log("target_scraper.reels_tab_ok", want=want, sources=sources)
        # Nowa siatka (rolki) — osobny kursor, nie mieszaj z postami
        ctx.scratch.pop("grid_used", None)
        self._grid_loop(
            ctx, s, sources, target, rng, stats, budget,
            want=want, content_types=["reels"], expect_kind="reel",
            label_one="Rolka", opened_key="reels_opened")

    def _grid_loop(self, ctx, s, sources, target, rng, stats, budget, *,
                   want: int, content_types: list[str], expect_kind: str,
                   label_one: str, opened_key: str) -> None:
        """Wspólna pętla kafelków: open → likers → commenters → BACK do profilu."""
        collect = s.get("collect") or {}
        for i in range(want):
            if budget.user_stopped():
                return
            opened = _step(ctx, "post.open_from_grid", {
                "pool": want + 2,
                "baseline": PROFILE,
                "ordered": True,
                "content_types": content_types,
            })
            if opened.status != "ok":
                reason = (opened.payload or {}).get("reason") or opened.status
                if reason == "no_tiles":
                    stats["no_tiles"] += 1
                # no_matching_kind = na siatce same rolki przy filtrze posts (OK)
                say(f"Siatka {label_one} — skip ({reason})")
                ctx.log("target_scraper.grid_open_fail", label=label_one,
                        reason=reason, content_types=content_types)
                break
            stats[opened_key] = stats.get(opened_key, 0) + 1
            # Preferuj kind z kroku; fallback = expect (posts/reels tab)
            kind = (opened.payload or {}).get("kind") or expect_kind
            # Post/Rolka i/N → SessionStats (phase tile)
            announce_progress(
                ctx, say,
                msg=grid_item_line(label_one, i + 1, want),
                phase="tile", current=i + 1, total=want,
                label=label_one, detail=kind)
            sync_plugin_stats(ctx, stats)
            # Na viewerze z siatki profilu ZAWSZE post.open_* in-place —
            # NIE reels.harvest_* (requires_screen=reels_feed → ensure Clips).
            try:
                for which in _ENG_ORDER:
                    src_key = (f"{expect_kind}_likers" if which == "likers"
                               else f"{expect_kind}_commenters")
                    # Źródła są nazwane post_* / reel_* wg expect_kind zakładki
                    if src_key not in sources:
                        continue
                    limit = draw_range(
                        collect.get("per_post_likers" if which == "likers"
                                    else "per_post_commenters"), rng, 20)
                    self._viewer_engagement(
                        ctx, s, src_key, which, target, rng, stats,
                        limit=limit, content_kind=kind)
            finally:
                # Zawsze BACK do siatki profilu — zero search/home
                more = i + 1 < want
                say("Nastepny kafelek…" if more else f"Koniec siatki {label_one}")
                _back_to_profile(ctx)

    def _from_profile_list(self, ctx, s, src, target, rng, stats) -> None:
        """Obserwujący / obserwowani — natywny pełny ekran (nie half-sheet).

        Bez `expand_sheet_confirmed`: pull na follow_list scrolluje listę
        albo fałszywie blokuje harvest (sheet_top nigdy < 18%H).
        """
        label = "obserwujacych" if src == "followers" else "obserwowanych"
        say(f"Lista {label} — otwieram")
        opened = _step(ctx, "profile.open_followers",
                       {"source": DIRECT_SOURCES[src]})
        if opened.status != "ok":
            say(f"Lista {label} — skip ({opened.status})")
            return
        top = ctx.navigator.identify().top
        if not top:
            ctx.log("target_scraper.list_unidentified", source=src)
            say(f"Lista {label} — nieznany ekran, skip")
            _back_to_profile(ctx)
            return
        try:
            say(f"Lista {label} — zbieram")
            self._harvest(ctx, s, src, target, rng, stats,
                          content_kind=None,
                          limit=draw_range((s.get("collect") or {}).get("per_list"),
                                           rng, 50))
        finally:
            _back_to_profile(ctx)

    def _viewer_engagement(self, ctx, s, src, which, target, rng, stats, *,
                           limit: int, content_kind: str) -> None:
        """Otwórz listę → expand → POTWIERDŹ → dopiero harvest/scroll.

        BACK na retry TYLKO gdy sheet faktycznie wszedł (not_expanded /
        empty_after_expand / rows>0). Przy no_entry/nothing_opened zostajemy
        na viewerze — ślepy BACK zamykał treść (live: back_to_profile.stuck).
        """
        human = "lajkujacych" if which == "likers" else "komentujacych"
        eng = None
        full_screen = False
        confirmed = False
        rows = 0
        for attempt in range(1, 3):
            say(f"Lista {human} — otwieram"
                + (f" (retry {attempt})" if attempt > 1 else ""))
            before = ctx.navigator.identify().top
            ctx.log("target_scraper.viewer_engagement", source=src,
                    content_kind=content_kind, screen_before=before,
                    attempt=attempt)
            eng = _step(ctx, f"post.open_{which}", {})
            pl = eng.payload or {}
            rows = int(pl.get("rows") or 0)
            # TYLKO full_screen z dumpa hierarchy — nie sam expanded/rows
            full_screen = bool(pl.get("full_screen"))
            confirmed = bool(pl.get("expand_confirmed")) and full_screen
            if eng.status == "ok" and rows > 0 and confirmed:
                # Expand bez stdout — szczegóły tylko w JSONL (panel QUIET)
                rb, ra = pl.get("rows_before"), pl.get("rows_after")
                top = pl.get("sheet_top_after")
                pct = pl.get("sheet_top_pct")
                sh, scr = pl.get("sheet_h"), pl.get("screen_h")
                ctx.log("target_scraper.expand_confirmed", source=src,
                        which=which, full_screen=True,
                        rows_before=rb, rows_after=ra,
                        sheet_top=top, sheet_top_pct=pct,
                        sheet_h=sh, screen_h=scr,
                        pulls_done=pl.get("pulls_done"))
                break
            reason = pl.get("reason") or eng.status
            # Sheet na ekranie? → BACK do viewer. Inaczej (no_entry /
            # nothing_opened) zostajemy na treści i retry bez BACK.
            sheet_opened = (
                reason in ("not_expanded", "empty_after_expand")
                or rows > 0
            )
            ctx.log("target_scraper.engagement_retry", source=src,
                    attempt=attempt, reason=reason,
                    expand_confirmed=confirmed, full_screen=full_screen,
                    rows=rows, sheet_opened=sheet_opened)
            if sheet_opened:
                ctx.device.press_back()
                ctx.humanizer.pause("tap")
            if attempt >= 2:
                say(f"Lista {human} — skip ({reason}) — bez scrolla na half-sheet")
                return

        # Zaufaj full_screen z open_* — drugi dump/pull tylko gdy brak flagi
        if not full_screen:
            check = _sheet.dump_sheet_geometry(ctx)
            if not check.get("full_screen"):
                say(f"Lista {human} — half-sheet "
                    f"(top={check.get('sheet_top')}/{check.get('screen_h')}), retry")
                exp = _sheet.expand_sheet_confirmed(ctx, max_pulls=2)
                ctx.log("target_scraper.expand_retry", source=src, which=which,
                        full_screen=exp.get("full_screen"),
                        rows_before=exp.get("rows_before"),
                        rows_after=exp.get("rows_after"),
                        sheet_top_after=exp.get("sheet_top_after"),
                        sheet_top_pct=exp.get("sheet_top_pct"),
                        sheet_h=exp.get("sheet_h"), screen_h=exp.get("screen_h"))
                if not exp.get("full_screen"):
                    say(f"Lista {human} — nadal half-sheet, skip (bez scrolla)")
                    ctx.device.press_back()
                    ctx.humanizer.pause("tap")
                    return
                # Expand OK — bez stdout (operator już widział „Lista … — otwieram")

        say(f"Lista {human} — zbieram")
        try:
            if which == "comments":
                ctx.humanizer.pause("read_comments")
            else:
                ctx.humanizer.pause("between_steps")
            uids = (ALL_USERNAME_IDS if which == "comments"
                    else _sheet.LIKER_USERNAME_IDS)
            # Komentarze: wyklucz autora posta (= from_target) z raw_profiles
            exclude = {target} if which == "comments" and target else None
            self._harvest(ctx, s, src, target, rng, stats,
                          content_kind=content_kind, limit=limit,
                          username_ids=uids, exclude=exclude)
        finally:
            # BACK ×1: sheet → treść (viewer). Do profilu wraca pętla kafelków.
            ctx.device.press_back()
            ctx.humanizer.pause("tap")

    def _harvest(self, ctx, s, src, target, rng, stats, *,
                 content_kind: str | None, limit: int,
                 username_ids: tuple | None = None,
                 exclude: set | None = None) -> None:
        """list.harvest_usernames z metadanymi + dedup."""
        filters = s.get("filters") or {}
        harvest = s.get("harvest") or {}
        if not harvest.get("enabled", True):
            say("Zapis wylaczony (harvest.enabled=false)")
            return
        tag = harvest.get("source_tag") or "auto"
        source = src if tag == "auto" else tag
        extra = {"from_target": target, "content_kind": content_kind}
        params = {
            "limit": limit,
            "max_scrolls": int((s.get("collect") or {}).get("max_scrolls_per_list", 25)),
            "source": source,
            "only_with_story_ring": bool(filters.get("only_with_story_ring")),
            "dedup_days": _dedup_days(s),
            "extra": extra,
        }
        if username_ids:
            params["username_ids"] = list(username_ids)
        if exclude:
            params["exclude"] = list(exclude)
        r = _step(ctx, "list.harvest_usernames", params)
        if r.status == "ok":
            pl = r.payload or {}
            saved = int(pl.get("saved") or 0)
            dupes = int(pl.get("dupes_skipped") or 0)
            stats["lists"] += 1
            stats["saved"] += saved
            stats["with_ring"] += int(pl.get("with_ring") or 0)
            stats["dupes_skipped"] += dupes
            kind_bit = f", {content_kind}" if content_kind else ""
            say(f"Zapisano {saved} → {source}{kind_bit} (skip {dupes} dup)")
        else:
            say(f"Harvest skip {source} ({(r.payload or {}).get('reason') or r.status})")

    def _go_to_profile(self, ctx, target: str) -> bool:
        # Search TYLKO tu — raz na target. Query BEZ @.
        say(f"Szukam {target}…")
        for name, params in (("search.open", {}),
                             ("search.type_query", {"query": target}),
                             ("search.pick_result", {"username": target,
                                                     "prefer_exact": True})):
            if _step(ctx, name, params).status != "ok":
                ctx.log("target_scraper.target_unreachable", target=target, step=name)
                say(f"Profil @{target} — niedostepny ({name})")
                ctx.navigator.ensure(HOME, max_steps=4)
                return False
        top = ctx.navigator.identify().top
        if top != PROFILE:
            ctx.log("target_scraper.not_on_profile", target=target, top=top)
            # Jedyny ensure(PROFILE) na wejściu — nie mid-loop
            if not ctx.navigator.ensure(PROFILE, max_steps=4):
                say(f"Profil @{target} — zly ekran ({top})")
                ctx.navigator.ensure(HOME, max_steps=4)
                return False
        say("Profil OK")
        return True


# ============================================================
def _ensure_profile_reels_tab(ctx) -> bool:
    """Przełącz na zakładkę Rolki NA PROFILU (nie globalna nav Clips).

    Helper z niche_discover — ten sam gest (desc/index ikony).
    """
    try:
        from v2.plugins.impl.niche_discover import _ensure_reels_tab
    except Exception as exc:
        ctx.log("target_scraper.reels_tab_import_fail", error=str(exc)[:120])
        return False
    ok = bool(_ensure_reels_tab(ctx, baseline=PROFILE, attempts=3))
    ctx.log("target_scraper.reels_tab_switch", ok=ok)
    return ok


def _dedup_days(s: dict) -> int | None:
    """harvest.dedup_days albo filters.skip_already_scraped_days; None = forever."""
    harvest = s.get("harvest") or {}
    if "dedup_days" in harvest:
        return harvest.get("dedup_days")
    filters = s.get("filters") or {}
    if "skip_already_scraped_days" in filters:
        return filters.get("skip_already_scraped_days")
    return None


def _pick_targets(s: dict, rng) -> list[str]:
    cfg = s.get("targets") or {}
    lst = [str(t).strip().lstrip("@") for t in (cfg.get("list") or []) if str(t).strip()]
    if not lst:
        return []
    if (cfg.get("order") or "random") == "random":
        lst = list(lst)
        rng.shuffle(lst)
    n = draw_range(cfg.get("per_session"), rng, len(lst))
    return lst[:max(1, n)]


def _back_to_profile(ctx) -> bool:
    """Wróć na siatkę profilu samym BACK — BEZ ensure/home/search.

    Między kafelkami ZOSTAJEMY na profile_other. Max 2× BACK; jeśli już
    jesteśmy na profilu — zero BACK (nadmiarowe cofanie ląduje w search).
    """
    top = ctx.navigator.identify().top
    if top == PROFILE:
        ctx.log("target_scraper.back_to_profile.already", top=top)
        return True
    for i in range(2):
        ctx.log("target_scraper.back_to_profile.press", i=i + 1, from_top=top)
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
        # Oddech zanim identify — po BACK sheet bywa jeszcze w animacji
        time.sleep(_sheet._BREATH_AFTER_BACK_S)
        top = ctx.navigator.identify().top
        if top == PROFILE:
            ctx.log("target_scraper.back_to_profile.ok", presses=i + 1, top=top)
            return True
    ctx.log("target_scraper.back_to_profile.stuck", top=top)
    return top == PROFILE


def _step(ctx, name: str, params: dict):
    return stepcall.call(steps, ctx, name, params, source="target_scraper")
