"""Wywołanie kroku z poziomu pluginu — jedno miejsce dla wszystkich pluginów.

Robi to, co FlowRunner robi wokół kroku, a czego plugin nie może pominąć:

  - **routing po `requires_screen`**
  - **zamianę wyjątku na `failed`**
  - log `step` w tym samym formacie co z FlowRunnera

Komunikaty ludzkie dla rolek ida z `reel_log` / pluginu — tu nie dumpujemy
`-> reels.watch` / `ok reels.watch` przy kazdym kroku (szum). Przy FAIL
nadal piszemy na stdout (`reels_niche` / `reels_warmup` przez reel_log;
`content_search` ma wlasny prefix — nie mylic z petla rolek).
To samo dla `target_scraper` + `scraper_log`, `profile_interact` + `profile_log`
oraz `content_search` + `content_search_log`.
"""
from __future__ import annotations

# Kroki rolek: status OK/SKIP milczy na stdout (plugin mowi ludzkim jezykiem).
_QUIET_OK = {
    "reels.watch", "reels.read_caption_comments", "reels.analyze_frames_ai",
    "reels.swipe_next", "reels.like", "reels.open",
    # Hot z listy — ludzki status z content_search_log / scraper_log
    "reels.open_liker_profile", "reels.open_commenter_profile",
}

# Nawigacja scrapera — status ludzki z scraper_log, nie spam „ok: …”
_QUIET_OK_TARGET = {
    "search.open", "search.type_query", "search.pick_result",
    "search.switch_results_tab", "search.collect_tag_variants",
    "profile.open_followers",
    "post.open_from_grid", "post.open_likers", "post.open_comments",
    "list.harvest_usernames",
    "reels.harvest_likers", "reels.harvest_commenters",
}

# profile_interact: search + wizyta — ludzkie linie z profile_log / visit
_QUIET_OK_PROFILE = {
    "search.open", "search.type_query", "search.pick_result",
    "profile.visit", "profile.pick_from_store", "post.like",
}

# content_search: search + kafelki + harvest + hot — ludzkie linie z content_search_log
# (reels.watch i tak w _QUIET_OK; tu gasimy spam „ok: search.open” / step na stdout)
_QUIET_OK_CONTENT = {
    "search.open", "search.type_query",
    "search.switch_results_tab", "search.collect_tag_variants",
    "post.open_from_grid", "post.like", "post.open_likers", "post.open_comments",
    "list.harvest_usernames",
    "reels.like", "reels.harvest_likers", "reels.harvest_commenters",
    "reels.open_liker_profile", "reels.open_commenter_profile",
    "profile.exit", "profile.visit",
}

# Te kroki mają własny komunikat fail/skip w content_search_log —
# nie powtarzaj „failed: search.open — …” obok „Szukaj — nie otwarto”.
_QUIET_ALL_CONTENT = {
    "search.open", "search.type_query", "post.open_from_grid",
    # Hot: fail/skip → hot_open_fail_line / hot_no_candidates (bez step na stdout)
    "reels.open_liker_profile", "reels.open_commenter_profile",
}


# Ekrany stosu # / search: viewer rolki bywa mylony; sheet/profil z hot
# nadpisuje baseline_screen. NIGDY ensure(reels_feed) → clips_tab.
# Live ~13:30: po hot baseline=reel_likers_list → ensure clips = katastrofa.
_CONTENT_SEARCH_NO_CLIPS = frozenset({
    "reels_feed",
    "home_feed",  # false positive viewera z siatki # — NIE ensure(clips)
    "reel_comments_sheet",
    "reel_likers_list",
    "profile_other",
    "profile_self",
    "story_viewer",
    "share_sheet",
    "profile_options_sheet",
    "search_results",
    "search_top",
    "search_input_active",
})


def call(registry, ctx, name: str, params: dict, source: str = "plugin"):
    from v2.runner.step import StepResult
    try:
        step = registry.get(name)()
    except KeyError as e:
        return StepResult.failed(str(e))

    req = getattr(step, "requires_screen", None)
    if req:
        top = ctx.navigator.identify().top
        if top != req:
            # content_search: NIGDY ensure(reels_feed)/clips_tab — rolka z #
            # nie jest main feedem. Live 13:03 (viewer=sheet) + 13:30
            # (baseline nadpisany na likers po hot). Niezależnie od baseline.
            if source == "content_search" and req == "reels_feed":
                if top in _CONTENT_SEARCH_NO_CLIPS:
                    # Viewer / sheet / profil / wyniki — NIE ensure, NIE BACK
                    pass
                else:
                    # Separator w kind: panel sklejał kind+step bez spacji
                    # → „skip_clips_ensurereels.harvest_commenters" (live 13:57)
                    ctx.log("content_search.skip_clips_ensure",
                            step=name, top=top, want=req)
                    _emit(source, name,
                          f"Blad: zly ekran (chcialem {req}, jest {top})")
                    return StepResult.failed(
                        f"wymagany ekran {req}, jestesmy na {top}")
            elif not ctx.navigator.ensure(req):
                ctx.log(f"{source}.wrong_screen", step=name, want=req, got=top)
                _emit(source, name,
                      f"Blad: zly ekran (chcialem {req}, jest {top})")
                return StepResult.failed(
                    f"wymagany ekran {req}, jestesmy na {top}")

    # Pauza między krokami atomowymi — jednolita z FlowRunnerem.
    # Zakres pochodzi z centralnego profilu humanizera (kind `between_steps`).
    hum = getattr(ctx, "humanizer", None)
    if hum:
        hum.pause("between_steps", why=f"stepcall:{name}")

    try:
        res = step.run(ctx, params)
    except Exception as e:
        import traceback
        res = StepResult.failed(f"{e}\n{traceback.format_exc()}")

    payload = res.payload or {}
    # Event zawsze (statystyki) — stdout tylko przy problemach / nie-cichych
    quiet_ok = res.status in ("ok", "skipped")
    # profile.visit woła post.like ze source=profile_interact (cichy OK)
    profile_src = source in ("profile_interact", "profile.visit")
    quiet = (
        (name in _QUIET_OK and quiet_ok)
        or (source == "target_scraper" and name in _QUIET_OK_TARGET and quiet_ok)
        or (profile_src and name in _QUIET_OK_PROFILE and quiet_ok)
        # content_search: reels.* + search/kafelek/harvest — ludzkie linie z logu
        or (source == "content_search" and name in _QUIET_OK and quiet_ok)
        or (source == "content_search" and name in _QUIET_OK_CONTENT and quiet_ok)
        # fail/skip już opisany po ludzku w pluginie (bez dubli stepcall)
        or (source == "content_search" and name in _QUIET_ALL_CONTENT)
    )
    if not quiet:
        reason = payload.get("reason")
        rline = f" ({reason})" if reason else ""
        errline = f" — {res.error}" if res.error else ""
        _emit(source, name, f"{res.status}: {name}{rline}{errline}")

    # source w payloadzie — panel QUIET_ALL tylko dla content_search (nie reels_niche)
    ctx.log("step", step=name, status=res.status, payload=payload,
            error=res.error, source=source)
    return res


def _emit(source: str, name: str, msg: str) -> None:
    """Stdout z prefixem pluginu (reels_niche / reels_warmup / target_scraper / …)."""
    # Tylko prawdziwy reels_niche — nie każdy krok reels.* (content_search też je woła)
    if source == "reels_niche":
        from v2.plugins.reel_log import say
        say(msg)
        return
    # Warmup: ten sam reel_log, ale tag reels_warmup (nie default niche)
    if source == "reels_warmup":
        from v2.plugins.reel_log import say
        say(msg, prefix="reels_warmup")
        return
    if source == "target_scraper":
        from v2.plugins.scraper_log import say
        say(msg)
        return
    # profile.visit / profile_interact — ten sam prefix panelu
    if source in ("profile_interact", "profile.visit") or name.startswith("profile."):
        from v2.plugins.profile_log import say
        say(msg)
        return
    if source == "content_search":
        # FAIL / nie-ciche kroki — ten sam prefix co content_search_log
        from v2.plugins.content_search_log import say
        say(msg)
        return
    print(f"  {msg}", flush=True)
