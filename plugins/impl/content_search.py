"""content_search — wyszukiwanie po hashtagu, słowie kluczowym albo lokalizacji.

Jeden plugin z trybem w ustawieniach (`source`), nie trzy osobne: po wpisaniu
frazy przebieg jest identyczny, różni się tylko zakładka wyników.

    fraza → wyniki → kafelek z pierwszych N → treść (rolka albo post)
    → analiza (gate/like/harvest/hot)
    → [opcjonalnie scrolls_in_viewer×: swipe → analiza]
    → BACK do search_results

**Analiza AI domyślnie WYŁĄCZONA** (Decyzja o lajku = Tylko %) — fraza sama
celuje w niszę. Przy Tylko AI / AI potem % runtime woła `reels.watch` z
`use_frames` / `read_caption` / komentarzami wg settings, potem
`reels.analyze_frames_ai` (tylko rolki; posty bez Vision).

**Wyjście z treści idzie przez BACK**, nie tap w dolną nawigację — rolka
i post otwarte z siatki jej nie mają (ta sama lekcja co L4c).
Między scrollami w viewerze **nigdy** `ensure(home)` / clips — tylko swipe
i na końcu BACK do wyników.
"""
from __future__ import annotations

import random
import time

from v2.plugins import stepcall
from v2.plugins.base import Budget, PluginResult, draw_range, gate_params
from v2.plugins.content_search_log import (
    say, no_phrases_line, phrase_start_line, searching_line,
    results_ok_line, results_fail_line, search_open_fail_line,
    open_tile_line, open_tile_fail_line, post_opened_line, reel_skip_line,
    like_ok_line, like_skip_line, harvest_line, back_results_line,
    results_lost_line, done_line, search_obstacle_line,
    hot_start_line, hot_profile_line, hot_no_candidates_line,
    hot_open_fail_line, hot_done_line, hot_lost_sheet_line,
    viewer_scroll_line, viewer_lost_line,
)
from v2.plugins.progress_log import (
    announce_progress, progress_sub, sync_plugin_stats,
)
from v2.plugins.impl.profile_interact import ProfileInteract, settings_for
from v2.plugins.registry import register
from v2.steps import registry as steps

HOME = "home_feed"
RESULTS = "search_results"

# Ekrany „nad” siatką wyników (# / keyword): BACK musi zejść do RESULTS,
# a nie zatrzymać się na powiązanej rolce w feedzie.
_ABOVE_SEARCH_RESULTS = frozenset({
    "reels_feed",
    "reel_comments_sheet",
    "reel_likers_list",
    "profile_other",
    "profile_self",
    "story_viewer",
    "share_sheet",
    "profile_options_sheet",
})

# Sheet / overlay nad treścią — przed swipe trzeba zejść BACK-iem
# (prawdziwy sheet; false positive `reel_comments_sheet` → flaga).
_VIEWER_SHEETS = frozenset({
    "reel_comments_sheet",
    "reel_likers_list",
    "share_sheet",
    "profile_options_sheet",
    "story_viewer",
})


@register
class ContentSearch:
    id = "content_search"
    entry_screen = HOME
    exit_screen = HOME

    def run(self, ctx, s: dict, budget: Budget) -> PluginResult:
        rng = random.Random()
        stats = {"phrases": 0, "items": 0, "reels": 0, "posts": 0, "liked": 0,
                 "likers_saved": 0, "commenters_saved": 0,
                 "hot_visited": 0, "hot_followed": 0, "no_tiles": 0,
                 # gałąź B — osobne liczniki (nie zużywają limitu fraz A)
                 "tag_variants_phrases": 0, "tag_variants_collected": 0}

        # Ujednolić UI Znaczniki (ile zbierać / ile otworzyć) + legacy mode=grid
        s = normalize_tag_variants_settings(s)
        phrases = _pick_phrases(s, rng)
        tvb = s.get("tag_variants_budget") or {}
        # sesja bez fraz A jest OK, gdy włączona sama gałąź B (mapa / live)
        if not phrases and not tvb.get("enabled"):
            ctx.log("content_search.no_phrases")
            # Operator: pusta lista fraz — nie ma czego szukać
            say(no_phrases_line())
            return PluginResult.done(budget, "empty", **stats)

        # Flaga dla kroków reels.*: NIE return_to_reels/ensure(clips)
        ctx.scratch["content_search_active"] = True
        ctx.scratch["baseline_screen"] = RESULTS
        # bufor live collect + już użyte znaczniki (dedup A↔B)
        ctx.scratch.setdefault("session_tag_variants", [])
        ctx.scratch.setdefault("tag_variants_used", [])
        try:
            stopped_by = "items"
            # Indeks frazy do logów X/Y + SessionStats (1-based)
            p_n = len(phrases)
            # --- Gałąź A: frazy (keywords / hashtags z phrases.*) ---
            for p_i, phrase in enumerate(phrases, start=1):
                reason = budget.stop_reason()
                if reason:
                    stopped_by = reason
                    break
                # Przekaż indeks do _search → „Szukam #x… 1/2”
                ctx.scratch["_cs_phrase_i"] = p_i
                ctx.scratch["_cs_phrase_n"] = p_n
                out = self._one_phrase(ctx, s, phrase, budget, rng, stats)
                stats["phrases"] += 1
                sync_plugin_stats(ctx, stats, items_done=budget.items_done,
                                  max_items=budget.max_items)
                if out in ("limit_daily", "user"):
                    stopped_by = out
                    break

            # --- Gałąź B: znaczniki / warianty (osobny budżet) ---
            if (stopped_by == "items"
                    and not budget.stop_reason()
                    and tvb.get("enabled")):
                tags = _pick_tag_variant_seeds(ctx, s, rng)
                t_n = len(tags)
                for t_i, tag in enumerate(tags, start=1):
                    reason = budget.stop_reason()
                    if reason:
                        stopped_by = reason
                        break
                    ctx.scratch["_cs_phrase_i"] = t_i
                    ctx.scratch["_cs_phrase_n"] = t_n
                    out = self._one_tag_variant(
                        ctx, s, tag, budget, rng, stats)
                    stats["tag_variants_phrases"] += 1
                    sync_plugin_stats(ctx, stats, items_done=budget.items_done,
                                      max_items=budget.max_items)
                    if out in ("limit_daily", "user"):
                        stopped_by = out
                        break
        finally:
            # Wyczyść flagi sesji — ten sam ctx w kolejce; baseline_screen
            # zostawiony na search_results psuje profile.exit (live regresja).
            ctx.scratch.pop("content_search_active", None)
            ctx.scratch.pop("baseline_screen", None)
            ctx.scratch.pop("session_tag_variants", None)
            ctx.scratch.pop("tag_variants_used", None)
            ctx.scratch.pop("_cs_phrase_i", None)
            ctx.scratch.pop("_cs_phrase_n", None)

        ctx.navigator.ensure(HOME, max_steps=4)
        ctx.log("content_search.done", stopped_by=stopped_by, **stats)
        # Podsumowanie sesji (stdout) — JSONL ma to samo w content_search.done
        say(done_line(stopped_by=stopped_by, **stats))
        return PluginResult.done(budget, stopped_by, **stats)

    # ------------------------------------------------------------------
    def _one_phrase(self, ctx, s: dict, phrase: str, budget, rng, stats):
        """Gałąź A: fraza → harvest głównych wyników → (opcjonalnie Tags collect).

        Harvest powiązanych # robi gałąź B (`tag_variants_budget`) po całej A.
        Collect Tags pomijamy przy source=from_map (tylko mapa) gdy B jest włączone.
        """
        per = s.get("per_phrase") or {}
        want = draw_range(per.get("items"), rng, 3)
        # Ile dodatkowych swipe'ów w viewerze po otwarciu kafelka (0 = jak kiedyś)
        scrolls = max(0, draw_range(per.get("scrolls_in_viewer"), rng, 0))
        # Przy scrollach > 0 drugi kafelek ≈ treść już widziana w swipe
        if scrolls > 0 and want > 1:
            want = 1
        pool = int(per.get("grid_pool_size", 4))
        source = (s.get("source") or "hashtag").strip()
        # Query jak w search.type_query — ta sama forma co na telefonie
        query = _query_for(source, phrase)
        say(phrase_start_line(query, source=source))

        ctx.scratch.pop("grid_used", None)      # nowa fraza = nowa siatka
        if not self._search(ctx, s, phrase):
            return "next"

        # 1) Najpierw harvest z głównych wyników (nie z zakładki Znaczniki)
        out = self._harvest_grid(
            ctx, s, phrase, budget, rng, stats,
            want=want, scrolls=scrolls, pool=pool, source=source)
        # limit / budżet / user — nie idź w Tags w tej frazie
        if out in ("limit_daily", "budget", "user"):
            return out

        # 2) Opcjonalnie: zakładka Znaczniki → collect (harvest # = gałąź B)
        disc = s.get("discover_tag_variants") or {}
        if not disc.get("enabled"):
            return out

        tvb = s.get("tag_variants_budget") or {}
        tag_src = str(tvb.get("source") or "live_collect").strip().lower()
        # from_map + otwieranie # → nie marnuj czasu na Tags w tej sesji
        if tvb.get("enabled") and tag_src == "from_map":
            return out

        self._discover_tag_variants(ctx, s, query, stats)
        return out

    def _one_tag_variant(self, ctx, s: dict, tag: str, budget, rng, stats):
        """Gałąź B: jeden znacznik/wariant → search # → harvest (osobne limity)."""
        tvb = s.get("tag_variants_budget") or {}
        per = s.get("per_phrase") or {}
        want = max(1, int(tvb.get("tiles_per_phrase", 2)))
        # 0 jest poprawnym limitem — nie używaj `or` (0 → falsy)
        scrolls = max(0, int(tvb.get("scrolls", 2)))
        pool = int(per.get("grid_pool_size", 4))
        phrase = _query_for("hashtag", tag)
        say(phrase_start_line(phrase, source="hashtag"))
        ctx.scratch.pop("grid_used", None)
        _mark_tag_used(ctx, phrase)
        if not self._search(ctx, {**s, "source": "hashtag"}, phrase):
            return "next"
        return self._harvest_grid(
            ctx, s, phrase, budget, rng, stats,
            want=want, scrolls=scrolls, pool=pool, source="hashtag")

    def _discover_tag_variants(self, ctx, s: dict, query: str,
                               stats: dict) -> list[str]:
        """Switch Tags → collect → scratch/persist; soft-fail bez crashu sesji."""
        disc = s.get("discover_tag_variants") or {}
        switched = _step(ctx, "search.switch_results_tab", {"tab": "tags"})
        if switched.status != "ok":
            ctx.log("content_search.tags_tab_skip",
                    query=query[:60],
                    error=getattr(switched, "error", None))
            return []

        collected = _step(ctx, "search.collect_tag_variants", {
            "query": query,
            "max_items": int(disc.get("max_variants") or 15),
            "max_scrolls": int(disc.get("max_scrolls") or 2),
            "sort_by_post_count": bool(disc.get("sort_by_post_count", True)),
            "include_seed": bool(disc.get("include_seed", False)),
            "write_to_account": bool(disc.get("write_to_account", True)),
        })
        if collected.status != "ok":
            ctx.log("content_search.collect_tag_variants_skip",
                    query=query[:60],
                    error=getattr(collected, "error", None))
            return []

        pl = collected.payload or {}
        hashtags = [
            _query_for("hashtag", h)
            for h in (pl.get("hashtags") or [])
            if str(h).strip()
        ]
        hashtags = _dedupe_phrases(hashtags)
        stats["tag_variants_collected"] = (
            int(stats.get("tag_variants_collected") or 0) + len(hashtags))

        # bufor sesji pod gałąź B (source=live_collect|both)
        sess = list(ctx.scratch.get("session_tag_variants") or [])
        ctx.scratch["session_tag_variants"] = _dedupe_phrases(sess + hashtags)

        # opcjonalna promocja do flat list (default off)
        if hashtags and disc.get("promote_to_discovered"):
            _promote_variants_to_discovered(ctx, hashtags)
        if hashtags and disc.get("sync_content_search"):
            _sync_variants_to_phrases(ctx, hashtags)

        ctx.log("content_search.tag_variants_ok",
                query=query[:60], count=len(hashtags),
                persisted=bool(pl.get("persisted")))
        return hashtags

    def _harvest_grid(self, ctx, s: dict, phrase: str, budget, rng, stats,
                      *, want: int, scrolls: int, pool: int, source: str):
        """Wspólna pętla kafelków (gałąź A i B) — po udanym search na RESULTS."""
        query = _query_for(source, phrase)
        tile_n = 0  # 1-based w tej frazie (dla operatora)
        for i in range(want):
            if budget.stop_reason():
                return "budget"

            tile_n += 1
            opened = _step(ctx, "post.open_from_grid",
                           {"pool": pool, "baseline": RESULTS,
                            "content_types": s.get("content_types") or []})
            if opened.status != "ok":
                reason = (opened.payload or {}).get("reason")
                say(open_tile_fail_line(reason))
                if reason == "no_tiles":
                    stats["no_tiles"] += 1
                break

            kind = (opened.payload or {}).get("kind") or "post"
            # Po otwarciu — kafelek N/want + typ (rolka/post) → SessionStats
            announce_progress(
                ctx, say,
                msg=open_tile_line(tile_n, kind=kind, total=want),
                phase="tile", current=tile_n, total=want,
                label="Kafelek", detail=kind)
            back_ok = True
            out = "ok"
            try:
                # 1 + scrolls: analiza → (swipe → analiza)×scrolls → BACK do RESULTS
                # (jeden raz na końcu kafelka — NIE między scrollami)
                views = 1 + scrolls
                for view_i in range(views):
                    if budget.stop_reason():
                        out = "budget"
                        break
                    out = self._one_item(ctx, s, kind, rng, stats)
                    budget.tick()
                    stats["items"] += 1
                    stats[("reels" if kind == "reel" else "posts")] += 1
                    sync_plugin_stats(ctx, stats, items_done=budget.items_done,
                                      max_items=budget.max_items)
                    if out == "limit_daily":
                        break
                    # Zostały scrolle? → sheet→treść, swipe, identify
                    if view_i >= scrolls:
                        break
                    # Zamknij sheet do viewera (nie do RESULTS) przed swipe
                    _dismiss_sheet_stack(ctx)
                    if not _still_in_content(ctx, kind):
                        top = ctx.navigator.identify().top
                        ctx.log("content_search.viewer_lost",
                                phase="before_scroll",
                                scroll=view_i + 1, of=scrolls,
                                content_kind=kind, top=top)
                        say(viewer_lost_line(phase="before_scroll"))
                        break
                    say(viewer_scroll_line(view_i + 1, scrolls))
                    ctx.log("content_search.viewer_scroll",
                            n=view_i + 1, of=scrolls, content_kind=kind)
                    if not _swipe_in_viewer(ctx, kind):
                        ctx.log("content_search.swipe_failed",
                                n=view_i + 1, of=scrolls, content_kind=kind)
                        break
                    if not _still_in_content(ctx, kind):
                        top = ctx.navigator.identify().top
                        ctx.log("content_search.viewer_lost",
                                phase="after_scroll",
                                scroll=view_i + 1, of=scrolls,
                                content_kind=kind, top=top)
                        say(viewer_lost_line(phase="after_scroll"))
                        break
                    # Po swipe typ może się zmienić (Top miesza rolki/posty)
                    kind = _detect_content_kind(ctx, kind)
            finally:
                # Dopiero tu: sheet → treść → BACK do wyników (raz na kafelek)
                _dismiss_sheet_stack(ctx)
                back_ok = _back_to(ctx, RESULTS)
                ctx.scratch.pop("content_search_sheet", None)
                if back_ok:
                    # Przywróć baseline pod kolejne open_from_grid / hot
                    ctx.scratch["baseline_screen"] = RESULTS
            if out == "limit_daily":
                return "limit_daily"
            if out == "budget":
                return "budget"
            if not back_ok:
                # Nie wróciliśmy na wyniki (np. home_feed po back_failed).
                # Nie zakładaj search — następna fraza zrobi identify+open.
                lost_st = ctx.navigator.identify()
                ctx.log("content_search.results_lost", phrase=phrase,
                        top=lost_st.top,
                        overlay=getattr(lost_st, "overlay", None),
                        pkg=getattr(lost_st, "package", None))
                say(results_lost_line(query))
                break
            # Jeszcze są kafelki w pętli? (kolejny obrót albo koniec frazy)
            more = (i + 1) < want and not budget.stop_reason()
            say(back_results_line(more=more))
        return "next"

    def _one_item(self, ctx, s: dict, kind: str, rng, stats):
        gate = s.get("gate") or {}
        ai_cfg = s.get("ai") or {}
        # AI tylko na rolkach — posty z siatki: gate/Vision pomijamy
        ai_on = bool(ai_cfg.get("enabled")) and kind == "reel"

        if kind == "reel":
            # Tylko % → bez klatek (jak dotychczas). AI on → flagi z settings.
            want_frames = bool(ai_on and ai_cfg.get("use_frames", True))
            want_caption = bool(ai_on and ai_cfg.get("read_caption", True))
            if ai_on:
                from v2.plugins.reel_log import ai_mode_line
                mode = ai_mode_line(use_frames=want_frames,
                                    read_caption=want_caption)
                if mode:
                    say(mode)

            watch = _step(ctx, "reels.watch", {
                "frames": int(ai_cfg.get("frames", 2)) if want_frames else 0,
                "frame_interval_s": float(ai_cfg.get("frame_interval_s", 0.5)),
                "use_frames": want_frames,
                "use_watch_duration": False,
                "save_frames": bool(ai_cfg.get("save_frames")) if want_frames else False,
                "gate": gate_params(gate),
                "read_caption": want_caption,
                "open_comments": bool(ai_on and ai_cfg.get("read_top_comments")),
                "log_prefix": "content_search",
                # skip_until nie dotyczy content_search (swipe w pętli viewer)
                "skip_until": None,
            })
            if watch.status != "ok":
                # Szczegóły odrzutu już z watch (Filtr nie OK / Reklama…)
                say(reel_skip_line())
                return "skip"

            # Przy opisie zjechalismy na inna rolke — nie mieszaj AI
            if (ctx.scratch.get("reel_caption_slipped")
                    or (watch.payload or {}).get("reel_slipped")):
                say(reel_skip_line("zjechanie przy opisie"))
                return "skip"

            # Fallback: opis nie byl w watch
            if want_caption and not ctx.scratch.get("reel_caption_from_watch"):
                _step(ctx, "reels.read_caption_comments", {
                    "open_comments": bool(ai_cfg.get("read_top_comments")),
                })
                if ctx.scratch.get("reel_caption_slipped"):
                    say(reel_skip_line("zjechanie przy opisie"))
                    return "skip"

            if ai_on:
                from v2.plugins.reel_log import ai_start_line, ai_reject_human
                from v2.plugins.impl.reels_niche import _eval_ai
                say(ai_start_line())
                ai_step = _step(ctx, "reels.analyze_frames_ai", {"ai": ai_cfg})
                ai = ctx.scratch.get("reel_ai") or {}
                if ai_step.status == "skipped":
                    why = (ai_step.payload or {}).get("reason") or "ai_skipped"
                    say(ai_reject_human(why=why))
                    return "skip"
                verdict = _eval_ai(ai, ai_cfg)
                if not verdict["pass"]:
                    say(ai_reject_human(
                        why=verdict.get("why") or verdict.get("verdict"),
                        score=verdict.get("score")))
                    return "skip"
        else:
            # Post: brak reels.watch / AI — krótka linia zamiast ciszy
            say(post_opened_line())

        like_cfg = s.get("like_content") or {}
        if like_cfg.get("enabled"):
            step = "reels.like" if kind == "reel" else "post.like"
            lr = _step(ctx, step, {"match": {}, "chance": like_cfg.get("chance") or {}})
            if lr.status == "ok":
                stats["liked"] += 1
                # post.like → operator_say „Polubiono”; reels.like — tu (bez autora w kroku)
                if kind == "reel":
                    say(like_ok_line(kind=kind))
            else:
                reason = (lr.payload or {}).get("reason")
                # Szansa chybiona — QUIET (szum); limity / brak przycisku — linia PL
                if str(reason or "") == "chance_missed":
                    pass
                else:
                    say(like_skip_line(reason))
                if "daily" in str(reason or ""):
                    return "limit_daily"

        for where, save_key in (("likers", "likers_saved"),
                                ("comments", "commenters_saved")):
            cfg = s.get(where) or {}
            out = self._engagement(ctx, s, kind, where, cfg, rng, stats, save_key)
            if out == "limit_daily":
                return "limit_daily"
            # Live 13:57: po hot zgubiony sheet → home; NIE wołaj harvest
            # commenters z requires_screen=reels_feed. Wróć do RESULTS / next.
            if out == "lost_sheet":
                return "ok"
        return "ok"

    def _engagement(self, ctx, s, kind, where, cfg, rng, stats, save_key):
        harvest = cfg.get("harvest") or {}
        hot = cfg.get("hot_interact") or {}
        if not (harvest.get("enabled") or hot.get("enabled")):
            return "ok"

        if kind == "reel":
            # Po utracie sheeta / home — nie otwieraj harvest na złym ekranie
            top = ctx.navigator.identify().top
            if top in ("home_feed", "search_top", "search_input_active", RESULTS):
                # home_feed bywa false positive viewera — daj szansę hierarchii
                if top == HOME and _still_in_content(ctx, kind):
                    pass  # viewer OK, harvest wolno
                else:
                    ctx.log("content_search.skip_harvest_wrong_screen",
                            where=where, top=top)
                    say(f"Harvest {('lajkujacych' if where == 'likers' else 'komentujacych')} — pomijam (ekran {top})")
                    return "lost_sheet"

            open_step = ("reels.harvest_likers" if where == "likers"
                         else "reels.harvest_commenters")
            keep_open = bool(hot.get("enabled"))
            stop = "ok"
            try:
                r = _step(ctx, open_step, {
                    "limit": draw_range(harvest.get("collect"), rng, 10),
                    "keep_sheet_open": keep_open})
                pl = r.payload or {}
                saved = int(pl.get("saved") or 0)
                dupes = int(pl.get("dupes_skipped") or 0)
                stats[save_key] += saved
                # Flaga: otworzyliśmy prawdziwy sheet — `_dismiss` może BACK
                if r.status == "ok":
                    ctx.scratch["content_search_sheet"] = True
                # Ludzki zapis: Zapisano N (D dup) — raw *.done tylko w JSONL
                if harvest.get("enabled") and r.status in ("ok", "skipped"):
                    say(harvest_line(where, saved, dupes=dupes))
                elif harvest.get("enabled") and r.status == "failed":
                    say(f"Harvest {('lajkujacych' if where == 'likers' else 'komentujacych')} — blad ekranu")
                if keep_open and r.status == "ok":
                    stop = self._hot(ctx, s, where, hot, rng, stats,
                                     sheet=("reel_likers_list" if where == "likers"
                                            else "reel_comments_sheet"))
            finally:
                # Sheet → treść (viewer), NIE do search_results (scrolle jeszcze trwają)
                _dismiss_sheet_stack(ctx)
                # Baseline: sheet zamknięty; RESULTS dopiero po pętli scrolli
                ctx.scratch["baseline_screen"] = RESULTS
            return stop

        # post otwarty z siatki
        opened = _step(ctx, f"post.open_{where}", {})
        if opened.status != "ok":
            return "ok"
        # Otworzyliśmy listę nad postem — wolno BACK-iem zejść z sheetu
        ctx.scratch["content_search_sheet"] = True
        try:
            r = _step(ctx, "list.harvest_usernames", {
                "limit": draw_range(harvest.get("collect"), rng, 10),
                "source": harvest.get("source_tag") or f"post_{where}"})
            pl = r.payload or {}
            saved = int(pl.get("saved") or 0)
            dupes = int(pl.get("dupes_skipped") or 0)
            stats[save_key] += saved
            # Jak scraper: Zapisano N (dup) zamiast list.harvest_usernames.done
            if harvest.get("enabled"):
                say(harvest_line(where, saved, dupes=dupes))
            if hot.get("enabled"):
                return self._hot(ctx, s, where, hot, rng, stats,
                                 sheet=ctx.navigator.identify().top)
        finally:
            # Wróć do viewera posta — NIE do RESULTS (pętla scrolli jeszcze trwa)
            _dismiss_sheet_stack(ctx)
            ctx.scratch["baseline_screen"] = RESULTS
        return "ok"

    def _hot(self, ctx, s, where, hot, rng, stats, *, sheet):
        """Interakcja „na gorąco" — jak reels_niche, ale powrót pod siatkę #.

        `open_liker_profile` ustawia baseline_screen=sheet (dla profile.exit).
        Po pętli MUSIMY przywrócić search_results — inaczej kolejny krok
        z requires_screen=reels_feed woła ensure → clips_tab (live 13:30).

        Po utracie sheeta: NIE kontynuuj hot / commenters — zejdź do treści
        (viewer), nie do RESULTS (scrolle jeszcze mogą trwać; live scrolls).
        """
        want = draw_range(hot.get("count"), rng, 1)
        p_settings = settings_for(ctx, hot.get("overrides") or {})
        short = not hot.get("full_procedure")
        plugin = ProfileInteract()
        open_step = ("reels.open_liker_profile" if where == "likers"
                     else "reels.open_commenter_profile")

        # Siatka # — nie gubimy jej gdy open_liker nadpisze baseline sheetem
        grid_baseline = RESULTS
        visited_before = stats.get("hot_visited", 0)
        followed_before = stats.get("hot_followed", 0)
        lost = False

        ctx.scratch.pop("hot_used", None)
        # Jak reels_niche: nie aktualizuj raw_profiles przy hot
        ctx.scratch["hot_from_reels"] = True
        say(hot_start_line(where, want))
        hot_i = 0
        try:
            for _ in range(want):
                opened = _step(ctx, open_step, {
                    "prefer_ring": bool(hot.get("prefer_ring", True)),
                    "require_ring": bool(hot.get("require_ring", False))})
                if opened.status != "ok":
                    reason = (opened.payload or {}).get("reason")
                    if reason == "open_failed":
                        # Live 14:06/14:23: why/landed + otwarto-i-zgubiono
                        pl = opened.payload or {}
                        say(hot_open_fail_line(
                            pl.get("username"),
                            why=pl.get("why"), landed=pl.get("landed"),
                            saw_profile=bool(pl.get("saw_profile"))))
                        continue
                    # no_candidates / no_rows → koniec hot, nie recover do clips
                    if reason == "no_candidates":
                        say(hot_no_candidates_line())
                    ctx.log("content_search.hot_end", where=where, reason=reason)
                    break
                hot_i += 1
                uname = (opened.payload or {}).get("username")
                # Hot @nick i/N z listy → progress.sub
                announce_progress(
                    ctx, say,
                    msg=hot_profile_line(uname, hot_i, want, where=where),
                    phase="tile",
                    current=int(stats.get("items") or 0) or hot_i,
                    total=None,
                    label="Kafelek",
                    detail=f"@{uname}" if uname else None,
                    sub=progress_sub(
                        phase="hot_" + where, current=hot_i, total=want,
                        label=("lista lajkujacych" if where == "likers"
                               else "lista komentujacych"),
                        detail=f"@{uname}" if uname else None))
                out = plugin.run_one(ctx, p_settings, short=short)
                stats["hot_visited"] += 1
                if out["followed"]:
                    stats["hot_followed"] += 1
                sync_plugin_stats(ctx, stats)
                # profile.exit → sheet (baseline ustawiony przez open_liker)
                _step(ctx, "profile.exit", {})
                if out["stop"] == "limit_daily":
                    return "limit_daily"
                top = ctx.navigator.identify().top
                if top != sheet:
                    # Sheet utracony — zejdź do treści (viewer), NIE do RESULTS
                    # (scrolle w viewerze jeszcze mogą trwać; RESULTS = koniec kafelka)
                    ctx.log("content_search.hot_lost_sheet", where=where, top=top)
                    say(hot_lost_sheet_line(where, top=top))
                    lost = True
                    _dismiss_sheet_stack(ctx)
                    break
        finally:
            ctx.scratch.pop("hot_from_reels", None)
            # Kluczowe: baseline z powrotem na siatkę # (nie likers/comments)
            ctx.scratch["baseline_screen"] = grid_baseline
            say(hot_done_line(
                visited=stats["hot_visited"] - visited_before,
                followed=stats["hot_followed"] - followed_before))
        return "lost_sheet" if lost else "ok"

    def _search(self, ctx, s: dict, phrase: str) -> bool:
        """Wpisz frazę i ZATWIERDŹ ją.

        Samo wpisanie zostawia listę sugestii (konta), a nie siatkę treści —
        na żywym przebiegu kończyło się to `no_tiles` przy każdej frazie.
        Pole trzeba też wyczyścić, bo pamięta poprzednie zapytanie.

        Po `back_failed` (home_feed) NIE zakładamy że jesteśmy w search —
        zawsze `search.open`, a po failu type_query: identify → ewentualnie
        open od nowa → jeden retry, inaczej skip frazy (bez probe w próżnię).
        """
        query = _query_for(s.get("source") or "hashtag", phrase)
        # X/Y frazy z run() (scratch) — ten sam current/total do JSON
        p_i = ctx.scratch.get("_cs_phrase_i")
        p_n = ctx.scratch.get("_cs_phrase_n")
        announce_progress(
            ctx, say,
            msg=searching_line(query, p_i, p_n),
            phase="phrase", current=int(p_i or 1),
            total=int(p_n) if p_n is not None else None,
            label="Szukam", detail=query)

        # Gdzie jesteśmy PRZED open — po utracie wyników bywa home_feed
        pre = ctx.navigator.identify()
        ctx.log("content_search.search_screen", phase="before_open",
                phrase=phrase, top=pre.top,
                overlay=getattr(pre, "overlay", None),
                pkg=getattr(pre, "package", None))

        if _step(ctx, "search.open", {}).status == "failed":
            say(search_open_fail_line())
            ctx.navigator.ensure(HOME, max_steps=4)
            return False

        typed = _step(ctx, "search.type_query",
                      {"query": query, "clear_first": True, "submit": True})
        if typed.status == "failed" or not (typed.payload or {}).get("submitted"):
            # Fail type → identify (ekran!), potem jedna decyzja: reopen / skip
            st = ctx.navigator.identify()
            pl = typed.payload or {}
            ov = getattr(st, "overlay", None) or pl.get("overlay")
            pkg = getattr(st, "package", None) or pl.get("pkg")
            ctx.log("content_search.type_query_failed",
                    phrase=phrase, error=typed.error,
                    top=st.top, overlay=ov, pkg=pkg,
                    off_search=bool(pl.get("off_search")),
                    field_top=pl.get("top"))
            # Settings / obca app — jeden komunikat (nie dump retry w panelu)
            if _is_search_ui_obstacle(ov, pkg):
                say(search_obstacle_line(overlay=ov, pkg=pkg))
            # Poza search albo type padł na złym UX — open od nowa + 1 retry
            if st.top not in ("search_input_active", "search_results", "search_top") \
                    or pl.get("off_search"):
                ctx.log("content_search.reopen_after_type_fail",
                        phrase=phrase, from_top=st.top)
                if _step(ctx, "search.open", {}).status != "failed":
                    typed = _step(ctx, "search.type_query",
                                  {"query": query, "clear_first": True,
                                   "submit": True})
            if typed.status == "failed" or not (typed.payload or {}).get("submitted"):
                st2 = ctx.navigator.identify()
                ov2 = getattr(st2, "overlay", None)
                pkg2 = getattr(st2, "package", None)
                ctx.log("content_search.no_results_grid", phrase=phrase,
                        payload=typed.payload,
                        top=st2.top, overlay=ov2, pkg=pkg2)
                # Jeśli przeszkoda już opisana wyżej — nie dubluj „brak siatki”
                if not _is_search_ui_obstacle(ov, pkg) and not _is_search_ui_obstacle(ov2, pkg2):
                    say(results_fail_line(query))
                elif _is_search_ui_obstacle(ov2, pkg2) and not _is_search_ui_obstacle(ov, pkg):
                    say(search_obstacle_line(overlay=ov2, pkg=pkg2))
                ctx.navigator.ensure(HOME, max_steps=4)
                return False
        say(results_ok_line())
        # Baseline siatki # — zanim open_liker nadpisze sheetem
        ctx.scratch["baseline_screen"] = RESULTS
        return True


# ============================================================
def _is_search_ui_obstacle(overlay: str | None, pkg: str | None) -> bool:
    """Settings / obca app nad IG — zamiast raw type_query.retry na panelu."""
    blob = f"{overlay or ''} {pkg or ''}".lower()
    return ("foreign_app" in blob) or ("settings" in blob)


def _still_in_content(ctx, kind: str) -> bool:
    """Czy nadal jesteśmy w viewerze treści (nie siatka #, nie sheet).

    Po hot/sheet `_dismiss_sheet_stack` powinien zejść na treść; jeśli identify
    mówi RESULTS — pętla scrolli się kończy (BACK do wyników z `_one_phrase`).

    Live: viewer rolki z # bywa mylony z `home_feed` / `reel_comments_sheet`
    (false positive po open_from_grid). RESULTS nigdy ≠ treść.
    """
    st = ctx.navigator.identify()
    top = st.top
    # Wyniki wyszukiwania / search UI — zawsze poza treścią
    if top in (RESULTS, "search_top", "search_input_active"):
        return False
    if st.overlay:
        return False
    if top == "reels_feed":
        return True

    # Sheet: prawdziwy (flaga z harvest) → nie treść.
    # `reel_comments_sheet` BEZ flagi = false positive viewera z # → treść.
    if top in _VIEWER_SHEETS:
        if (top == "reel_comments_sheet"
                and not ctx.scratch.get("content_search_sheet")):
            return True
        return False

    # home_feed / inne: false positive viewera z siatki # — hierarchia
    if _hierarchy_says_content(ctx, kind):
        return True
    if top == HOME:
        return False
    # Awaryjnie: jesteśmy „nad” wynikami, ale nie na znanym sheetcie
    return top in _ABOVE_SEARCH_RESULTS and top not in _VIEWER_SHEETS


def _hierarchy_says_content(ctx, kind: str) -> bool:
    """Hierarchia: rolka / viewer posta — niezależnie od mylącego identify.top."""
    try:
        from v2.core.screen_map import flatten_hierarchy
        from v2.steps.post import _post
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if kind == "reel" and _post.is_reel(flat):
            return True
        return _post.media_viewer_open(flat)
    except Exception:
        return False


def _detect_content_kind(ctx, fallback: str) -> str:
    """Po swipe w viewerze Top może podać rolkę albo post — odśwież kind."""
    try:
        from v2.core.screen_map import flatten_hierarchy
        from v2.steps.post import _post
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if _post.is_reel(flat):
            return "reel"
        if _post.is_post_viewer(flat):
            return "post"
    except Exception:
        pass
    return fallback or "post"


def _swipe_in_viewer(ctx, kind: str) -> bool:
    """Kolejna treść w viewerze — bez wychodzenia na search_results.

    Rolka: `reels.swipe_next` (marker + potwierdzenie).
    Post: ten sam gest pionowy w viewerze „Posty"/explore — bez markera rolek.
    """
    if kind == "reel":
        r = _step(ctx, "reels.swipe_next", {"announce": False})
        return r.status == "ok"
    return _swipe_post_viewer(ctx)


def _swipe_post_viewer(ctx) -> bool:
    """Pionowy swipe w viewerze posta (feed z #) — bez ensure / bez home."""
    ht = getattr(ctx, "ht", None)
    if ht is None:
        ctx.log("content_search.post_swipe_no_ht")
        return False
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        w, h = 1080, 2340
    # Jak reels.swipe_next: pierwszy, najłagodniejszy gest
    ht.swipe(w // 2, int(h * 0.65), w // 2, int(h * 0.30),
             duration_ms=300, pause_kind="swipe")
    try:
        ctx.device.wait_for_stable_ui(max_wait_s=3.0)
    except Exception:
        pass
    ctx.humanizer.pause("tap")
    return True


def _query_for(source: str, phrase: str) -> str:
    """Hashtag musi mieć `#`, słowo kluczowe i lokalizacja nie."""
    p = phrase.strip()
    if source == "hashtag":
        return p if p.startswith("#") else f"#{p}"
    return p.lstrip("#")


def _phrase_bucket(source: str) -> str:
    """Która lista w phrases.* odpowiada typowi źródła."""
    if source == "hashtag":
        return "hashtags"
    if source == "keyword":
        return "keywords"
    return "locations"


def normalize_tag_variants_settings(s: dict) -> dict:
    """Ujednolicenie sekcji Znaczniki (UI) + kompatybilność ze starym JSON.

    Nowe UI:
      - discover.enabled = „Używaj znaczników”
      - discover.max_variants = ile # zbierać
      - budget.max_phrases = ile # otworzyć (0 = tylko collect)
      - budget.tiles/scrolls/source = harvest znaczników

    Legacy:
      - mode=grid bez budget → budget.enabled + max_phrases≥1, mode=collect
      - max_phrases>0 ⇒ budget.enabled=True (nie trzeba osobnego switcha)
      - tiles=1 gdy scrolls>0 (unikaj duplikatów w viewerze)
    """
    out = dict(s or {})
    disc = dict(out.get("discover_tag_variants") or {})
    tvb = dict(out.get("tag_variants_budget") or {})
    per = dict(out.get("per_phrase") or {})

    mode = str(disc.get("mode") or "collect").strip().lower()
    try:
        max_open = max(0, int(tvb.get("max_phrases", 0) or 0))
    except (TypeError, ValueError):
        max_open = 0

    # Stary mode=grid = „otwórz 1 znacznik po collect” → gałąź B
    if mode == "grid" and not tvb.get("enabled") and max_open <= 0:
        max_open = 1
        tvb["max_phrases"] = 1
        # limity jak w starej ścieżce A (per_phrase), jeśli B nie miało własnych
        if "tiles_per_phrase" not in tvb:
            items = per.get("items")
            if isinstance(items, dict):
                try:
                    tvb["tiles_per_phrase"] = max(
                        1, int(items.get("max") or items.get("min") or 1))
                except (TypeError, ValueError):
                    tvb["tiles_per_phrase"] = 1
            elif items is not None:
                try:
                    tvb["tiles_per_phrase"] = max(1, int(items))
                except (TypeError, ValueError):
                    tvb["tiles_per_phrase"] = 1
        if "scrolls" not in tvb:
            sc = per.get("scrolls_in_viewer")
            if isinstance(sc, dict):
                try:
                    tvb["scrolls"] = max(
                        0, int(sc.get("max") or sc.get("min") or 0))
                except (TypeError, ValueError):
                    tvb["scrolls"] = 0
            elif sc is not None:
                try:
                    tvb["scrolls"] = max(0, int(sc))
                except (TypeError, ValueError):
                    tvb["scrolls"] = 0
        # grid legacy zbierał live w tej samej sesji
        if not tvb.get("source"):
            tvb["source"] = "live_collect"

    tvb["max_phrases"] = max_open
    # 0 otwarć = wyłącz harvest B; >0 = włącz (switch legacy zbędny)
    tvb["enabled"] = bool(max_open > 0)
    # Harvest # tylko w gałęzi B — mode=grid w A dublowałby otwarcia
    disc["mode"] = "collect"

    # Anty-duplikacja: scrolle w viewerze ⇒ jeden kafelek na znacznik
    try:
        tag_scrolls = max(0, int(tvb.get("scrolls", 0) or 0))
    except (TypeError, ValueError):
        tag_scrolls = 0
    try:
        tag_tiles = max(1, int(tvb.get("tiles_per_phrase", 1) or 1))
    except (TypeError, ValueError):
        tag_tiles = 1
    if tag_scrolls > 0 and tag_tiles > 1:
        tag_tiles = 1
    tvb["scrolls"] = tag_scrolls
    tvb["tiles_per_phrase"] = tag_tiles

    if not tvb.get("source"):
        tvb["source"] = "live_collect"

    # Znaczniki wyłączone → nie otwieraj gałęzi B nawet przy starym enabled
    if not disc.get("enabled"):
        tvb["enabled"] = False

    out["discover_tag_variants"] = disc
    out["tag_variants_budget"] = tvb
    out["per_phrase"] = per
    return out


def _migrate_legacy_phrase_list(phrases: dict) -> dict:
    """Stara płaska `list` → hashtags / keywords (raz), zachowaj list dla kompat."""
    ph = dict(phrases or {})
    legacy = [str(p).strip() for p in (ph.get("list") or []) if str(p).strip()]
    tags = [str(p).strip() for p in (ph.get("hashtags") or []) if str(p).strip()]
    kws = [str(p).strip() for p in (ph.get("keywords") or []) if str(p).strip()]
    locs = [str(p).strip() for p in (ph.get("locations") or []) if str(p).strip()]
    if legacy and not tags and not kws and not locs:
        for p in legacy:
            if p.startswith("#"):
                tags.append(p if p.startswith("#") else f"#{p}")
            else:
                kws.append(p.lstrip("#"))
        ph["hashtags"] = _dedupe_phrases(tags)
        ph["keywords"] = _dedupe_phrases(kws)
    else:
        ph["hashtags"] = _dedupe_phrases(tags)
        ph["keywords"] = _dedupe_phrases(kws)
        ph["locations"] = _dedupe_phrases(locs)
    return ph


def _dedupe_phrases(items: list[str]) -> list[str]:
    out, seen = [], set()
    for x in items or []:
        s = str(x).strip()
        if not s:
            continue
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _pick_phrases(s: dict, rng) -> list[str]:
    """Frazy z listy dopasowanej do source (hashtag / keyword / location)."""
    cfg = _migrate_legacy_phrase_list(s.get("phrases") or {})
    source = (s.get("source") or "hashtag").strip()
    bucket = _phrase_bucket(source)
    lst = list(cfg.get(bucket) or [])
    # awaryjnie: stara płaska list, przefiltrowana po kontekście
    if not lst:
        legacy = [str(p).strip() for p in (cfg.get("list") or []) if str(p).strip()]
        if source == "hashtag":
            tagged = [p for p in legacy if p.startswith("#")]
            lst = tagged or [f"#{p.lstrip('#')}" for p in legacy]
        elif source == "keyword":
            plain = [p.lstrip("#") for p in legacy if not p.startswith("#")]
            lst = plain or [p.lstrip("#") for p in legacy]
        else:
            lst = legacy
    if source == "hashtag":
        lst = [_query_for("hashtag", p) for p in lst]
    else:
        lst = [p.lstrip("#") for p in lst]
    lst = _dedupe_phrases([p for p in lst if p])
    if not lst:
        return []
    if (cfg.get("order") or "random") == "random":
        lst = list(lst)
        rng.shuffle(lst)
    n = draw_range(cfg.get("per_session"), rng, 1)
    return lst[:max(1, n)]


def _pick_tag_variant_seeds(ctx, s: dict, rng) -> list[str]:
    """Gałąź B: znaczniki z mapy / live collect / both — bez już użytych."""
    tvb = s.get("tag_variants_budget") or {}
    source = str(tvb.get("source") or "from_map").strip().lower()
    max_n = max(0, int(tvb.get("max_phrases", 2)))
    if max_n <= 0:
        return []

    candidates: list[str] = []
    if source in ("from_map", "both"):
        candidates.extend(_variants_from_niche_map(ctx))
    if source in ("live_collect", "both"):
        candidates.extend(ctx.scratch.get("session_tag_variants") or [])

    # normalizuj do #…, odetnij użyte w tej sesji (mode=grid / wcześniejsze B)
    used = {
        str(x).casefold()
        for x in (ctx.scratch.get("tag_variants_used") or [])
        if str(x).strip()
    }
    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        tag = _query_for("hashtag", str(raw).strip())
        if not tag or len(tag) < 2:
            continue
        key = tag.casefold()
        if key in seen or key in used:
            continue
        seen.add(key)
        out.append(tag)

    if (s.get("phrases") or {}).get("order") == "sequential":
        pass  # zachowaj kolejność mapy / collect
    else:
        rng.shuffle(out)
    return out[:max_n]


def _variants_from_niche_map(ctx) -> list[str]:
    """Spłaszcz wartości niche.tag_variants → lista # (kolejność mapy)."""
    niche = getattr(getattr(ctx, "account", None), "niche", None) or {}
    mapping = niche.get("tag_variants") if isinstance(niche, dict) else None
    if not isinstance(mapping, dict):
        return []
    out: list[str] = []
    for _expr, vals in mapping.items():
        for v in vals or []:
            s = str(v).strip()
            if s:
                out.append(s if s.startswith("#") else f"#{s}")
    return out


def _first_unused_tag(ctx, variants: list[str]) -> str | None:
    """Pierwszy wariant niezużyty w sesji (mode=grid)."""
    used = {
        str(x).casefold()
        for x in (ctx.scratch.get("tag_variants_used") or [])
        if str(x).strip()
    }
    for raw in variants or []:
        tag = _query_for("hashtag", str(raw).strip())
        if tag and tag.casefold() not in used:
            return tag
    return None


def _mark_tag_used(ctx, tag: str) -> None:
    """Oznacz znacznik jako zużyty (dedup między mode=grid a gałęzią B)."""
    t = _query_for("hashtag", str(tag or "").strip())
    if not t:
        return
    used = list(ctx.scratch.get("tag_variants_used") or [])
    if t.casefold() in {str(x).casefold() for x in used}:
        return
    used.append(t)
    ctx.scratch["tag_variants_used"] = used


def _promote_variants_to_discovered(ctx, hashtags: list[str]) -> None:
    """Opcjonalnie: append wariantów do niche.hashtags_discovered."""
    try:
        from v2.plugins import store
    except Exception:
        return
    account_id = getattr(getattr(ctx, "account", None), "account_id", None)
    if not account_id or not hashtags:
        return
    try:
        niche = dict(getattr(ctx.account, "niche", None) or {})
        merged = store.apply_discovered_phrases(
            niche, [], hashtags, mode="append")
        niche.update(merged)
        # zachowaj mapę tag_variants przy zapisie
        if "tag_variants" in (getattr(ctx.account, "niche", None) or {}):
            niche["tag_variants"] = (ctx.account.niche or {}).get("tag_variants")
        saved = store.write_niche(account_id, niche)
        if isinstance(getattr(ctx.account, "niche", None), dict):
            ctx.account.niche.clear()
            ctx.account.niche.update(saved)
    except Exception as exc:
        ctx.log("content_search.promote_variants_err", error=str(exc)[:160])


def _sync_variants_to_phrases(ctx, hashtags: list[str]) -> None:
    """Opcjonalnie: append wariantów do content_search.phrases.hashtags."""
    account_id = getattr(getattr(ctx, "account", None), "account_id", None)
    if not account_id or not hashtags:
        return
    try:
        from v2.plugins import store
        settings = store.read_settings(account_id)
        cs = dict(settings.get("content_search") or {})
        phrases = _migrate_legacy_phrase_list(cs.get("phrases") or {})
        phrases["hashtags"] = _dedupe_phrases(
            list(phrases.get("hashtags") or []) + list(hashtags))[:40]
        phrases["list"] = _dedupe_phrases(
            list(phrases.get("hashtags") or [])
            + list(phrases.get("keywords") or [])
            + list(phrases.get("locations") or []))[:40]
        cs["phrases"] = phrases
        store.write_settings(account_id, {"content_search": cs})
    except Exception as exc:
        ctx.log("content_search.sync_variants_err", error=str(exc)[:160])


def _dismiss_sheet_stack(ctx, *, max_backs: int = 6) -> None:
    """Zejdź z sheetu/profilu BACK-iem do treści — bez ensure(reels)/clips.

    Cel: viewer (reels_feed / post), NIE search_results.
    `reel_comments_sheet` bywa false positive na viewerze z # (live: open
    ląduje jako comments, a BACK zamykał całą rolkę → RESULTS przed scrollem).
    BACK z comments tylko gdy sami otworzyliśmy sheet (`content_search_sheet`).
    """
    # Jednoznaczne overlaye — zawsze wolno zamknąć
    unambiguous = frozenset({
        "reel_likers_list",
        "profile_other",
        "profile_self",
        "story_viewer",
        "share_sheet",
        "profile_options_sheet",
    })
    for _ in range(max_backs):
        state = ctx.navigator.identify()
        top = state.top
        # Już poza sheetem / na wynikach — nie kop głębiej
        if top in (RESULTS, HOME, "search_top", "search_input_active",
                   "reels_feed"):
            ctx.scratch.pop("content_search_sheet", None)
            return
        should = False
        if state.overlay or top in unambiguous:
            should = True
        elif top == "reel_comments_sheet":
            # Tylko prawdziwy sheet z harvest/open — nie false positive viewera
            should = bool(ctx.scratch.get("content_search_sheet"))
        if not should:
            return
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
        time.sleep(0.12)
        nxt_st = ctx.navigator.identify()
        nxt = nxt_st.top
        # Overshoot: sheet był płytszy niż myśleliśmy → już RESULTS/home
        if nxt in (RESULTS, HOME, "search_top", "search_input_active"):
            ctx.log("content_search.dismiss_overshoot",
                    from_top=top, to=nxt)
            ctx.scratch.pop("content_search_sheet", None)
            return
        # Wylądowaliśmy na treści / poza sheetem
        if (nxt not in unambiguous and nxt != "reel_comments_sheet"
                and not nxt_st.overlay):
            ctx.scratch.pop("content_search_sheet", None)
            return


def _back_to(ctx, target: str | None, *, max_backs: int = 8) -> bool:
    """Powrót BACK-iem — ekrany otwarte z siatki nie mają dolnej nawigacji.

    Zwraca, czy faktycznie jesteśmy na docelowym ekranie. `ensure` bywa tu
    bezradny: na `search_results` nie da się „dojechać" kotwicą, bo to stan
    po wpisaniu frazy, a nie zakładka.

    Live bug: rolka z # otwiera się w kontekście `reels_feed` (powiązane);
    2–3× BACK lądowało na *kolejnej* rolce zamiast na siatce wyników.
    Dlatego schodzimy dłużej, dopóki top ∈ stosu nad wynikami.
    """
    if not target:
        return True
    last_top = None
    for i in range(max_backs):
        state = ctx.navigator.identify()
        last_top = state.top
        if state.top == target and not state.overlay:
            return True
        # Overlay klawiatury / system — też BACK
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
        # Oddech — zanim kolejny identify (UI w animacji BACK)
        time.sleep(0.12)
        # Po zejściu ze stosu treści: jeśli nadal nie RESULTS, daj szansę
        # kolejnym BACK (nie przerywaj wcześnie na reels_feed).
        nxt = ctx.navigator.identify().top
        last_top = nxt
        if nxt == target:
            return True
        # Utknięcie poza stosem (np. home) — dalsze BACK tylko pogarsza
        if (nxt not in _ABOVE_SEARCH_RESULTS
                and nxt != target
                and nxt is not None
                and i >= 2):
            ctx.log("content_search.back_unexpected",
                    target=target, top=nxt, backs=i + 1)
            break
    final = ctx.navigator.identify()
    ok = final.top == target
    if not ok:
        # Po stracie wyników (często home_feed) — pełny kontekst ekranu
        ctx.log("content_search.back_failed",
                target=target, top=final.top,
                overlay=getattr(final, "overlay", None),
                pkg=getattr(final, "package", None),
                last_top=last_top, max_backs=max_backs)
    return ok


def _step(ctx, name: str, params: dict):
    return stepcall.call(steps, ctx, name, params, source="content_search")
