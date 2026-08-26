"""profile.visit — pełna wizyta profilu.

Zakłada że jesteśmy na profile_other (wołacz doprowadził tapem z search/likers).
Etapy: header → sanity → gate (opcjonalne) → story (opcjonalne) → grid AI →
match → like tiles (opcjonalne) → follow decision → follow execute → save →
skip_until profile.exit.

Params z YAML flow-a:
  sanity:  {abort_if_private, abort_if_already_following}          — zawsze
  gate:    {is_private_ok, min_posts, min_followers, max_followers,
           min_ratio, bio_exclusions}                              — opcjonalne
  story:   {enabled: bool}                                          — opcjonalne
  match:   {mode, min_niche_tiles, min_niche_ratio, min_valid_tiles,
            max_excluded_ratio, pick_strategy, tiles_count,
            scrolls_to_grid}                                        — opcjonalne
  like_tiles: {enabled, max}                                        — opcjonalne
  follow:  {allow: bool, min_scoring: float, chance: {p|formula}}   — opcjonalne
"""
from __future__ import annotations
import random
from pathlib import Path
from v2.core import limits
from v2.data.stores import VisitedProfilesStore
from v2.plugins.profile_log import (
    say, sanity_reject_line, gate_fail_line,
    story_watching_line, story_none_line, story_fail_line,
    grid_capture_line, grid_ai_wait_line, grid_capture_fail_line,
    say_grid_ai_result, match_fail_line, match_ok_line, tiles_like_line,
    follow_ok_line, follow_skip_line, follow_limit_line, left_profile_line,
)
from v2.steps.reels._sheet import operator_say
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.profile._header import read_profile_header, eval_gate, eval_sanity
from v2.steps.system._ui_helpers import settle_for_animation


SKIP_TARGET = "profile.exit"


@register
class ProfileVisit:
    name = "profile.visit"
    requires_screen = "profile_other"

    def run(self, ctx, params):
        sanity   = params.get("sanity") or {}
        gate     = params.get("gate") or {}
        story    = params.get("story") or {}
        match    = params.get("match") or {}
        tilecfg  = params.get("like_tiles") or {}
        followc  = params.get("follow") or {}

        # Ślady po poprzednim profilu — zostawione, zapisałyby się w bazie jako
        # dowód dla TEGO profilu (np. gdy tu analizy siatki w ogóle nie ma).
        for k in ("profile_grid_tiles", "profile_grid_image",
                  "profile_grid_scrolls", "profile_grid_estimated"):
            ctx.scratch.pop(k, None)

        # --- 1. header dump ---
        header = read_profile_header(ctx.device)
        if not header.get("username"):
            # Krok, który nas tu wprowadził, wie w kogo kliknął. Bez tego wpis
            # w bazie odwiedzonych zapisywał się jako „unknown" i dedup
            # przestawał działać — a to znaczy wracanie na te same profile.
            fallback = ctx.scratch.get("expected_username")
            if fallback:
                header["username"] = fallback
                ctx.log("profile.visit.username_from_caller", username=fallback)
        ctx.scratch["profile_header"] = header
        ctx.log("profile.visit.header", **{k: v for k, v in header.items() if k != "bio"})

        # --- 2. sanity check (zawsze) ---
        sres = eval_sanity(header, sanity)
        if not sres["pass"]:
            ctx.log("profile.visit.sanity_abort", why=sres["why"], username=header.get("username"))
            say(sanity_reject_line(sres["why"], header.get("username")))
            # Dedup visited musi działać także przy odrzuceniu — inaczej nick
            # wraca z raw w kolejnym przebiegu.
            _save_visited(ctx, header, {}, action="skip",
                          reason=f"sanity:{sres['why']}", tiles_liked=0)
            return StepResult.skipped(sres["why"],
                                      username=header.get("username"),
                                      skip_until=SKIP_TARGET)

        # --- 3. gate (opcjonalne — brak sekcji = brak sprawdzania) ---
        if gate:
            gres = eval_gate(header, gate)
            if not gres["pass"]:
                ctx.log("profile.visit.gate_fail", why=gres["why"], username=header.get("username"))
                say(gate_fail_line(gres["why"], header.get("username")))
                _save_visited(ctx, header, {}, action="skip",
                              reason=f"gate:{gres['why']}", tiles_liked=0)
                return StepResult.skipped("gate_failed",
                                          gate_why=gres["why"],
                                          username=header.get("username"),
                                          skip_until=SKIP_TARGET)

        # --- 4. story watch (opcjonalne) ---
        story_watched = False
        if story.get("enabled"):
            if header.get("has_story_ring"):
                say(story_watching_line())
                story_watched = _watch_story(ctx, story)
                if not story_watched:
                    say(story_fail_line())
                ctx.scratch["story_watched"] = story_watched
            else:
                say(story_none_line())

        # Po story (sukces/fail/brak) musimy nadal siedzieć na profile_other.
        # ensure() po wyjściu na likers/search wspina się BACK-iem poza profil
        # (live: likers→comments→search_results→search_top) i psuje wizytę.
        if not _still_on_profile(ctx, where="after_story"):
            _save_visited(ctx, header, {}, action="skip",
                          reason="left_profile:after_story",
                          story_watched=story_watched, tiles_liked=0)
            return StepResult.skipped("left_profile",
                                      username=header.get("username"),
                                      skip_until=SKIP_TARGET)

        # Przed drogim zrzutem siatki + Vision — ponów sanity prywatnego.
        # Live @ghescrime: pierwszy dump mógł mieć inny wording / empty-state
        # doładowany później; bez tego idziemy w estimate_grid + AI.
        if match and sanity.get("abort_if_private", True):
            fresh = read_profile_header(ctx.device)
            if fresh.get("is_private"):
                header["is_private"] = True
                ctx.scratch["profile_header"] = header
                ctx.log("profile.visit.sanity_abort", why="became_private",
                        username=header.get("username"), when="pre_grid")
                say(sanity_reject_line("became_private", header.get("username")))
                _save_visited(ctx, header, {}, action="skip",
                              reason="sanity:became_private",
                              story_watched=story_watched, tiles_liked=0)
                return StepResult.skipped("became_private",
                                          username=header.get("username"),
                                          skip_until=SKIP_TARGET)

        # --- 5. grid AI (opcjonalne — jeśli match / grid_ai.enabled) ---
        ai_verdict = {}
        if match:
            ai_verdict = _analyze_grid(ctx, match)
            ctx.scratch["profile_ai"] = ai_verdict
            # Wyszliśmy z profilu — nie udawaj match_fail/no_ai_tiles na search_top
            if ai_verdict.get("_error") == "not_on_profile":
                _save_visited(ctx, header, ai_verdict, action="skip",
                              reason="left_profile:grid",
                              story_watched=story_watched, tiles_liked=0)
                return StepResult.skipped("left_profile",
                                          username=header.get("username"),
                                          skip_until=SKIP_TARGET)
            # Zawsze wypisz wynik AI (liczby + Kafelek N — niche: reason).
            # Vision.run też woła say_grid_ai_result — flaga `_grid_ai_said`
            # blokuje duplikat; early-error bez Vision (capture/nisza) tu dopiero.
            say_grid_ai_result(ai_verdict)
            mres = _eval_match(ai_verdict, match)
            if not mres["pass"]:
                say(match_fail_line(
                    mres.get("why") or "match",
                    n_niche=mres.get("n_niche"),
                    n_excluded=mres.get("n_excluded")))
                _save_visited(ctx, header, ai_verdict, action="skip",
                              reason=f"match:{mres['why']}",
                              story_watched=story_watched, tiles_liked=0)
                ctx.log("profile.visit.match_fail", **mres)
                return StepResult.skipped("match_failed",
                                          username=header.get("username"),
                                          skip_until=SKIP_TARGET)
            # Pass — krótko (szczegóły kafelków już ze say_grid_ai_result)
            say(match_ok_line(
                n_niche=mres.get("n_niche"),
                n_excluded=mres.get("n_excluded")))
        elif tilecfg.get("enabled"):
            # Tylko % — zrzut siatki bez Vision; losowe kafelki + chance
            ai_verdict = _capture_grid_for_likes(ctx, tilecfg)
            ctx.scratch["profile_ai"] = ai_verdict

        # --- 6. like tiles (opcjonalne) ---
        tiles_liked = 0
        if tilecfg.get("enabled") and (
                ai_verdict.get("tiles")
                or ctx.scratch.get("profile_grid_tiles")):
            tiles_liked = _like_grid_tiles(ctx, ai_verdict, tilecfg, match)
        ctx.scratch["tiles_liked"] = tiles_liked

        # Live 13:56: lajk kafelka + ensure(profile_other) wspiął się przez
        # search_results → search_top. Bez profilu follow i exit są bez sensu.
        # Komunikat PL już z _still_on_profile (left_profile_line) — bez dubla
        # „Follow — nie (poza profilem)".
        if not _still_on_profile(ctx, where="after_like_tiles"):
            scoring, _parts = _score_profile(
                header, ai_verdict, story_watched, tiles_liked,
                story_stage=bool(story.get("enabled")),
                tiles_stage=bool(tilecfg.get("enabled")))
            _save_visited(ctx, header, ai_verdict,
                          action=("like_only" if tiles_liked else "skip"),
                          reason="left_profile:after_like_tiles",
                          scoring=scoring, tiles_liked=tiles_liked,
                          story_watched=story_watched, followed=False)
            return StepResult.ok(username=header.get("username"),
                                 action="skip", followed=False,
                                 tiles_liked=tiles_liked,
                                 story_watched=story_watched,
                                 scoring=round(scoring, 3),
                                 left_profile=True,
                                 skip_until=SKIP_TARGET)

        # Analiza siatki zjeżdża w dół profilu, a przycisk „Obserwuj" siedzi
        # w nagłówku. Bez powrotu na górę decyzja follow kończyłaby się
        # „follow_no_button" na każdym profilu, na którym siatkę oglądaliśmy.
        _restore_header_view(ctx)

        # --- 7. follow decision ---
        scoring, score_parts = _score_profile(
            header, ai_verdict, story_watched, tiles_liked,
            story_stage=bool(story.get("enabled")),
            tiles_stage=bool(tilecfg.get("enabled")))
        action, skip_why = _decide_action(scoring, followc)
        ctx.scratch["profile_decision"] = {"scoring": scoring, "action": action,
                                           "skip_why": skip_why}
        ctx.log("profile.visit.decision", scoring=round(scoring, 3), action=action,
                username=header.get("username"),
                skip_why=skip_why,
                score_parts={k: round(v, 3) for k, v in score_parts.items()})

        # --- 8. follow execute (jeśli decision=='follow') ---
        followed = False
        follow_skip: str | None = None
        if action == "follow" and followc.get("allow", True):
            lim = limits.check(ctx, "follow")
            if not lim.allowed:
                ctx.log("profile.visit.follow_limit", **lim.__dict__)
                say(follow_limit_line(lim.reason))
                # daily exceeded → kończymy sesję; hourly → tylko ten profil
                follow_skip = "session.end" if lim.scope == "daily" else SKIP_TARGET
                _save_visited(ctx, header, ai_verdict, action="skip",
                              reason=lim.reason, story_watched=story_watched,
                              tiles_liked=tiles_liked, scoring=scoring)
                return StepResult.skipped(lim.reason,
                                          username=header.get("username"),
                                          skip_until=follow_skip)
            followed, follow_why = _execute_follow(ctx)
            if followed:
                limits.note_action(ctx, "follow")
                say(follow_ok_line(scoring=scoring))
            else:
                # action=follow ale followed=false — zawsze powiedz DLACZEGO
                # (brak przycisku / nie potwierdzono), nie „Skip follow — score”
                say(follow_skip_line(scoring=scoring,
                                     reason=follow_why or "nie potwierdzono"))
        elif action == "follow":
            # allow=false — scoring mówił follow, ustawienia blokują
            say(follow_skip_line(scoring=scoring, reason="allow=false"))
        else:
            # skip: poniżej progu / szansa — reason z _decide_action
            say(follow_skip_line(scoring=scoring, reason=skip_why or action))
        ctx.scratch["followed"] = followed

        # --- 9. save do visited_profiles.jsonl ---
        _save_visited(ctx, header, ai_verdict,
                      action=action if followed else ("like_only" if tiles_liked else "skip"),
                      scoring=scoring, tiles_liked=tiles_liked,
                      story_watched=story_watched, followed=followed)

        return StepResult.ok(username=header.get("username"),
                             action=action, followed=followed,
                             tiles_liked=tiles_liked, story_watched=story_watched,
                             scoring=round(scoring, 3),
                             skip_until=SKIP_TARGET)


# ============================================================
# helpers
# ============================================================
def _still_on_profile(ctx, *, where: str) -> bool:
    """True gdy top == profile_other. Bez ensure — tylko diagnostyka.

    JSONL: raw top/where. Stdout/panel: left_profile_line (prefix z kontekstu).
    """
    top = ctx.navigator.identify().top
    if top == "profile_other":
        return True
    ctx.log("profile.visit.left_profile", top=top, where=where)
    # Prefix: content_search (hot) / profile_interact / reels_niche
    operator_say(ctx, left_profile_line(where, top=top))
    return False


def _soft_return_to_profile(ctx, *, max_backs: int = 2) -> bool:
    """Wróć na profile_other samym BACK-iem — bez navigator.ensure.

    Ścieżka content_search / hot-from-likers: stos to
    search → reel → likers → profile. `ensure(profile_other)` widzi sheet/
    search_results w BACK_TO_EXIT i wspina się *poza* profil aż do search_top
    (live 13:36: likers→comments→search_results→search_top).
    Tutaj: max 2× BACK tylko gdy nadal story_viewer; inaczej abort.
    """
    for attempt in range(max_backs):
        top = ctx.navigator.identify().top
        if top == "profile_other":
            return True
        if top != "story_viewer":
            # Już poza viewerem i profilem — kolejny BACK zniszczyłby wizytę
            ctx.log("profile.visit.story_soft_return_abort",
                    top=top, attempt=attempt)
            return False
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
    top = ctx.navigator.identify().top
    ok = top == "profile_other"
    if not ok:
        ctx.log("profile.visit.story_soft_return_abort",
                top=top, attempt=max_backs)
    return ok


def _watch_story(ctx, story_cfg: dict | None = None) -> bool:
    """Tap w ring awatara → story_viewer → oglądaj → BACK na profil.

    NIE używamy `navigator.ensure("story_viewer")` gdy tap nie otworzył
    relacji: `profile_other` jest w BACK_TO_EXIT_SCREENS, więc ensure robił
    BACK i *wypychał z profilu* na sheet/rolkę rodzica — potem grid AI
    scrollował już poza profilem (live: story_no_viewer → reel_comments_sheet).

    Po udanym oglądaniu też NIE wołamy ensure(profile_other) — wystarczy
    1–2× BACK ze story_viewer; ensure wspina się przez likers/search.
    """
    from v2.core.screen_map import flatten_hierarchy
    from v2.steps.profile._header import story_ring_tap_xy

    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    xy = story_ring_tap_xy(flat)
    if not xy:
        # Awaryjnie: lewy-górny awatar (nie 20% H — to było *pod* ringiem)
        w, h = ctx.account.device["_hardware"]["resolution"]
        xy = (max(80, w // 7), max(280, int(h * 0.14)))
        ctx.log("profile.visit.story_tap_fallback", xy=xy)
    else:
        ctx.log("profile.visit.story_tap_ring", xy=xy)

    opened = False
    landed = None
    for attempt in range(1, 3):
        ctx.ht.tap(xy[0], xy[1], pause_kind="tap")
        # Story to przejście między ekranami; czekamy na animację, a potem
        # identify() potwierdzi czy jesteśmy na viewerze.
        settle_for_animation(ctx, max_wait_s=1.5, fallback_wait_s=1.0)
        landed = ctx.navigator.identify().top
        if landed == "story_viewer":
            opened = True
            break
        ctx.log("profile.visit.story_tap_miss", attempt=attempt, landed=landed)

    if not opened:
        # Już na profilu → zostajemy (zero BACK / ensure), idziemy do siatki.
        # Tap otworzył coś innego → jeden BACK, potem wołający sprawdzi top.
        if landed and landed not in ("profile_other", "profile_self"):
            ctx.device.press_back()
            ctx.humanizer.pause("tap")
        ctx.log("profile.visit.story_no_viewer", landed=landed)
        return False

    # Czas oglądania relacji to biznesowy parametr pluginu (watch_story).
    watch_cfg = (story_cfg or {}).get("watch_story") or {"min": 3, "max": 8}
    duration = ctx.humanizer._rng.uniform(float(watch_cfg.get("min", 3)),
                                          float(watch_cfg.get("max", 8)))
    ctx.humanizer.sleep(duration, kind="watch_story")
    # Soft return: BACK ze story_viewer (ew. drugi, jeśli viewer jeszcze stoi).
    # Bez ensure — patrz docstring `_soft_return_to_profile`.
    _soft_return_to_profile(ctx, max_backs=2)
    return True


def _capture_grid_for_likes(ctx, tilecfg: dict | None = None) -> dict:
    """Zrzut siatki bez Vision — tryb „Tylko %” (losowe kafelki + chance).

    Wypełnia scratch `profile_grid_tiles` jak `_analyze_grid`, a `tiles` w
    werdykcie to synteza (wszystkie widoczne = kandydaci do lajku).
    """
    from v2.steps.profile import _grid
    import time as _t

    tilecfg = tilecfg or {}
    top = ctx.navigator.identify().top
    if top != "profile_other":
        ctx.log("profile.visit.grid_not_on_profile", top=top, mode="chance_only")
        return {"tiles": {}, "_error": "not_on_profile"}

    frames_dir = ctx.events.file_path.parent / "frames" / ctx.session_id
    frames_dir.mkdir(parents=True, exist_ok=True)
    header = ctx.scratch.get("profile_header") or {}
    tiles_count = int(tilecfg.get("tiles_count") or 9)
    scrolls = int(tilecfg.get("scrolls_to_grid") or 1)

    say(grid_capture_line())
    shot = _grid.capture_grid(
        ctx, frames_dir / f"profile_grid_{int(_t.time()*1000)}.jpg",
        tiles_count=tiles_count,
        scrolls=scrolls,
        max_extra_scrolls=int(tilecfg.get("max_extra_scrolls", 3)),
        allow_estimate=bool(header.get("posts")))
    ctx.scratch["profile_grid_tiles"] = shot["tiles"]
    ctx.scratch["profile_grid_image"] = shot["path"]
    ctx.scratch["profile_grid_scrolls"] = shot["scrolls"]
    ctx.scratch["profile_grid_estimated"] = shot["estimated"]

    if not shot["tiles"]:
        say(grid_capture_fail_line())
        return {"tiles": {}, "_error": "grid_capture_failed"}

    # 1-based jak w odpowiedzi AI — każdy widoczny kafelek to kandydat
    synthetic = {
        i: {"category": "neutral", "should_like": True}
        for i in range(1, len(shot["tiles"]) + 1)
    }
    ctx.log("profile.visit.grid_chance_only", tiles=len(synthetic))
    return {"tiles": synthetic, "_mode": "chance_only"}


def _analyze_grid(ctx, opts: dict | None = None) -> dict:
    """Zrzut siatki postów z ponumerowanymi kafelkami + AI Vision `profile_grid`.

    Numerację rysujemy sami po realnych bounds kafelków (`_grid.capture_grid`),
    więc numer w odpowiedzi modelu wskazuje dokładnie ten kafelek, którego
    współrzędne mamy w `ctx.scratch['profile_grid_tiles']`.

    Gdy kafelków nie da się znaleźć, NIE wysyłamy surowego zrzutu na ślepo —
    model odesłałby kategorie dla kafelków, których numeracji nikt nie zna,
    i decyzja o polubieniu trafiałaby w przypadkowy post. Zwracamy pusty wynik,
    a `_eval_match` odrzuci profil z powodem `no_ai_tiles`.

    Podczas oczekiwania na Vision **zostajemy na profilu** — zero scrolli /
    nawigacji aż wróci wynik (dalej sync `vision.run`).
    """
    from v2.ai.vision import AiVision
    from v2.plugins.store import niche_is_accepted
    from v2.steps.profile import _grid
    import time as _t

    opts = opts or {}

    # Discovery zostawiło accepted=false — skip Vision bez failu kolejki
    if not niche_is_accepted(ctx.account):
        ctx.log("niche_not_accepted", where="profile.visit.grid")
        return {"tiles": {}, "_error": "niche_not_accepted"}

    # Siatka TYLKO na profile_other. NIE ensure — po likers/search BACK wspina
    # się poza profil (live: grid_not_on_profile top=search_top + no_ai_tiles).
    top = ctx.navigator.identify().top
    if top != "profile_other":
        ctx.log("profile.visit.grid_not_on_profile", top=top)
        return {"tiles": {}, "_error": "not_on_profile"}

    frames_dir = ctx.events.file_path.parent / "frames" / ctx.session_id
    frames_dir.mkdir(parents=True, exist_ok=True)

    header = ctx.scratch.get("profile_header") or {}
    username = header.get("username") or ""
    # Port starego grid_screenshot: tiles_count=9/12, max_scroll_for_grid=1
    tiles_count = int(opts.get("tiles_count", 9))
    scrolls = int(opts.get("scrolls_to_grid", 1))

    # Operator: zrzut → czekam na AI (bez dumpowania JSON)
    say(grid_capture_line())
    shot = _grid.capture_grid(
        ctx, frames_dir / f"profile_grid_{int(_t.time()*1000)}.jpg",
        tiles_count=tiles_count,
        scrolls=scrolls,
        # Stary bot: while attempts < 3 gdy brakuje kafelków po wstępnym scrollu
        max_extra_scrolls=int(opts.get("max_extra_scrolls", 3)),
        # Zgadywana siatka ma sens tylko na profilu, który JAKIEŚ posty ma.
        # Na pustym (albo prywatnym) narysowałaby ramki na komunikacie
        # „Brak postów", a model kategoryzowałby szum.
        allow_estimate=bool(header.get("posts")))
    ctx.scratch["profile_grid_tiles"] = shot["tiles"]
    ctx.scratch["profile_grid_image"] = shot["path"]
    ctx.scratch["profile_grid_scrolls"] = shot["scrolls"]
    ctx.scratch["profile_grid_estimated"] = shot["estimated"]

    if not shot["path"]:
        ctx.log("profile.visit.grid_capture_failed", username=username,
                tiles=len(shot["tiles"]))
        say(grid_capture_fail_line())
        return {"tiles": {}, "_error": "grid_capture_failed"}

    # Pełny brief niszy + wykluczenia POSTÓW (grid_ai.exclusions | niche)
    from v2.ai.niche_inputs import effective_exclusions, niche_prompt_fields
    niche = ctx.account.niche or {}
    niche_fields = niche_prompt_fields(
        niche, exclusions=effective_exclusions(niche, opts))

    # Czekamy na profilu na wynik Vision — nie scrolluj / nie wychodź w trakcie
    ctx.log("profile.visit.grid_ai_wait", username=username,
            tiles=len(shot["tiles"]), scrolls=shot["scrolls"])
    say(grid_ai_wait_line())
    vision = AiVision(account=ctx.account.raw)
    verdict = vision.run("profile_grid", inputs={
        **niche_fields,
        "username":    username,
        "bio":         header.get("bio") or "(brak)",
        "liczba_kafelkow": len(shot["tiles"]),
        "frames":      [shot["path"]],
    # `opts` = sekcja `grid_ai` / `match` — strictness, exclusions, prompt_override
    }, opts=opts)

    after = ctx.navigator.identify().top
    if after != "profile_other":
        # UI nie powinno samo zjechać — log, BEZ ensure (to by zeszło z profilu)
        ctx.log("profile.visit.grid_ai_left_profile", top=after)
    return verdict


def _restore_header_view(ctx) -> None:
    """Wróć na górę profilu tyloma przewinięciami, iloma zjechaliśmy do siatki.

    Współrzędne kafelków w `ctx.scratch['profile_grid_tiles']` przestają po tym
    obowiązywać — dlatego wołamy to DOPIERO po etapie lajkowania kafelków.
    Swipe odwrotny do `scroll_profile_toward_grid` (ten sam dystans co wstęp).
    """
    from v2.steps.profile import _grid
    n = int(ctx.scratch.pop("profile_grid_scrolls", 0) or 0)
    if n <= 0:
        return
    for _ in range(n):
        _grid.scroll_profile_toward_header(ctx)
    # Po scrollu wystarczy stabilizacja animacji; header jest wysoko,
    # nie potrzebujemy czekać na konkretny element.
    settle_for_animation(ctx, max_wait_s=1.2, fallback_wait_s=0.8)
    ctx.log("profile.visit.grid_scroll_back", scrolls=n)


def _eval_match(ai_verdict: dict, match: dict) -> dict:
    """Czy profil kwalifikuje się na podstawie kategoryzacji kafelków siatki.

    Dwa tryby (`mode`), bo konta dzielą się na dwa typy:

    - `niche_match` — konto o ostro zdefiniowanej niszy. Wymagamy POZYTYWNEGO
      dopasowania: min_niche_tiles / min_niche_ratio.
    - `exclude_only` — konto ogólne. Nie wymagamy trafienia w niszę; wystarczy,
      że po odrzuceniu wykluczeń zostaje min_valid_tiles kafelków do pracy.

    Pojedyncze wykluczenie NIGDY nie dyskwalifikuje profilu — wyrzuca tylko ten
    kafelek z puli do polubienia (patrz `_like_grid_tiles`). Profil odpada
    dopiero gdy wykluczenia zajmują więcej niż `max_excluded_ratio` siatki.
    """
    tiles = ai_verdict.get("tiles") or {}
    if not tiles:
        return {"pass": False, "why": "no_ai_tiles"}
    cats = [t.get("category") for t in tiles.values()]
    n_niche    = sum(1 for c in cats if c == "niche")
    n_excluded = sum(1 for c in cats if c == "excluded")
    n_total    = len(cats)
    n_valid    = n_total - n_excluded
    base = {"n_niche": n_niche, "n_excluded": n_excluded, "n_total": n_total}

    max_excl = match.get("max_excluded_ratio")
    if max_excl is not None and n_total > 0:
        excl_ratio = n_excluded / n_total
        if excl_ratio > max_excl:
            return {"pass": False, "why": f"excluded_ratio>{max_excl}",
                    "excluded_ratio": round(excl_ratio, 2), **base}

    if match.get("mode") == "exclude_only":
        min_valid = match.get("min_valid_tiles", 1)
        if n_valid < min_valid:
            return {"pass": False, "why": f"valid_tiles<{min_valid}",
                    "n_valid": n_valid, **base}
        return {"pass": True, "n_valid": n_valid, **base}

    # niche_match (domyślny)
    if "min_niche_tiles" in match and n_niche < match["min_niche_tiles"]:
        return {"pass": False, "why": f"niche_tiles<{match['min_niche_tiles']}", **base}
    if "min_niche_ratio" in match and n_total > 0:
        ratio = n_niche / n_total
        if ratio < match["min_niche_ratio"]:
            return {"pass": False, "why": f"ratio<{match['min_niche_ratio']}",
                    "ratio": round(ratio, 2), **base}
    return {"pass": True, **base}


def _pick_like_candidates(tiles: dict, match: dict | None = None) -> list:
    """Które numery kafelków wolno lajkować — czysta logika pod testy.

    - excluded nigdy
    - exclude_only: wszystkie niewykluczone
    - niche_match: should_like; przy strictness=loose i pustej puli → fallback
      na wszystkie niewykluczone
    """
    match = match or {}
    exclude_only = match.get("mode") == "exclude_only"
    loose = (not exclude_only
             and (match.get("strictness") or "balanced") == "loose")

    candidates = [num for num, t in tiles.items()
                  if t.get("category") != "excluded"
                  and (exclude_only or t.get("should_like"))]

    if loose and not candidates:
        candidates = [num for num, t in tiles.items()
                      if t.get("category") != "excluded"]

    if match.get("pick_strategy") == "random":
        # losowanie zostawiamy wołającemu (ma RNG / side-effect log)
        return candidates
    candidates = sorted(
        candidates,
        key=lambda n: ((tiles[n].get("category") != "niche"), str(n)))
    return candidates


def _like_grid_tiles(ctx, ai_verdict: dict, cfg: dict, match: dict | None = None) -> int:
    """Lajkuj kafelki do limitu. Każdy lajk sprawdza limits + opcjonalny chance.

    Wykluczone kafelki są odrzucane ZAWSZE — to jedyne miejsce, gdzie
    wykluczenie faktycznie działa (nie dyskwalifikuje profilu, tylko wypada
    z puli). W trybie `exclude_only` kandydatem jest każdy niewykluczony
    kafelek; w `niche_match` tylko te, które AI oznaczyło `should_like`.

    Przy `niche_match` + `strictness=loose`: gdy brak kandydatów should_like,
    dołączamy wszystkie niewykluczone (neutral fallback).

    Bez AI (`_mode=chance_only` / pusty match): wszystkie widoczne + losowanie.

    Procedura per kafelek: firm_tap bounds → chance → post.like → back / ensure siatka.
    `try/finally` gwarantuje, że nie zostajemy w viewerze/sheecie.
    """
    import time
    from v2.core.chance import roll as chance_roll
    from v2.core.screen_map import flatten_hierarchy
    from v2.plugins import stepcall
    from v2.steps import registry as steps
    from v2.steps.post import _post

    match = match or {}
    max_tiles = _tile_count(cfg)
    tiles = ai_verdict.get("tiles") or {}
    chance_only = (ai_verdict.get("_mode") == "chance_only") or (not match)
    # Bez AI: losuj z syntezy / numerów gridu
    if chance_only and not tiles:
        grid_n = len(ctx.scratch.get("profile_grid_tiles") or [])
        tiles = {i: {"category": "neutral", "should_like": True}
                 for i in range(1, grid_n + 1)}
    pick_match = (dict(match) if match else {"mode": "exclude_only",
                                             "pick_strategy": "random"})
    if chance_only:
        pick_match = {"mode": "exclude_only", "pick_strategy": "random"}

    candidates = _pick_like_candidates(tiles, pick_match)

    # Log gdy loose fallback faktycznie dołożył neutrale (brak should_like)
    if ((pick_match.get("strictness") or "balanced") == "loose"
            and pick_match.get("mode") != "exclude_only"
            and candidates
            and not any((tiles.get(n) or {}).get("should_like") for n in tiles)):
        ctx.log("profile.visit.like_loose_fallback", n_candidates=len(candidates))

    if pick_match.get("pick_strategy") == "random" or chance_only:
        random.shuffle(candidates)
    else:
        candidates = sorted(
            candidates,
            key=lambda n: ((tiles[n].get("category") != "niche"), str(n)))
    # bierzemy zapas kandydatów — chance może odrzucić część
    pool = candidates[: max(max_tiles * 3, max_tiles)]

    # Bounds 1:1 z numeracją AI (indeks 0 = kafelek „1")
    grid_tiles = ctx.scratch.get("profile_grid_tiles") or []
    hold_ms = int(cfg.get("hold_ms", 140))
    open_retries = max(1, int(cfg.get("open_retries", 2)))
    chance_cfg = cfg.get("chance") or {"mode": "p", "value": 1.0}
    # score z AI content (jeśli kiedyś będzie) — na siatce zwykle brak; 100=pewny
    chance_ctx = {"score": float(ai_verdict.get("niche_score") or 100)}
    rng = random.Random()

    liked = 0
    tried = []
    for num in pool:
        if liked >= max_tiles:
            break
        lim = limits.check(ctx, "like")
        if not lim.allowed:
            ctx.log("profile.visit.like_limit", **lim.__dict__)
            break

        # Szansa przed otwarciem — nie marnuj tapów przy miss
        ch = chance_roll(chance_cfg, chance_ctx, rng)
        if not ch.get("pass"):
            ctx.log("profile.visit.like_chance_miss", tile=num, **ch)
            continue

        box = _tile_bounds(grid_tiles, num)
        if not box:
            ctx.log("profile.visit.like_tile_no_bounds", tile=num,
                    n_grid=len(grid_tiles))
            continue
        tried.append(num)
        cx = (box[0] + box[2]) // 2
        cy = (box[1] + box[3]) // 2

        opened = False
        try:
            for attempt in range(1, open_retries + 1):
                # firm_tap jak w post.open_from_grid — krótki tap bywa mis-tapem
                if hasattr(ctx.ht, "firm_tap"):
                    ctx.ht.firm_tap(cx, cy, hold_ms=hold_ms, pause_kind="tap")
                else:
                    ctx.ht.tap(cx, cy, pause_kind="tap")
                ctx.humanizer.pause("between_steps")
                # Czekamy na animację otwarcia viewer/posta; weryfikacja
                # robi późniejszy hierarchy + media_viewer_open.
                settle_for_animation(ctx, max_wait_s=2.0, fallback_wait_s=1.2)
                time.sleep(0.25)
                flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
                opened = _post.media_viewer_open(flat)
                ctx.log("profile.visit.like_tile_open_check",
                        tile=num, attempt=attempt, opened=opened)
                if opened:
                    break
                time.sleep(0.35)

            if not opened:
                ctx.log("profile.visit.like_tile_open_failed", tile=num)
                continue

            # post.like — source=profile_interact: cichy OK + prefix jak visit
            like_res = stepcall.call(
                steps, ctx, "post.like", {}, source="profile_interact")
            if like_res.status == "ok":
                liked += 1
                ctx.log("profile.visit.like_tile_ok", tile=num)
            else:
                ctx.log("profile.visit.like_tile_skipped",
                        tile=num, reason=(like_res.payload or {}).get("reason"),
                        status=like_res.status)
        finally:
            # Nigdy nie zostawiać viewera — kolejny kafelek / follow wymaga siatki
            _recover_profile_grid(ctx)

    # Jedna linia podsumowania zamiast spamu per kafelek
    say(tiles_like_line(tried, liked))
    return liked


def _tile_bounds(grid_tiles: list, num) -> tuple[int, int, int, int] | None:
    """Mapuj numer AI (1-based, czasem str) → bounds (x1,y1,x2,y2)."""
    try:
        idx = int(num) - 1
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= len(grid_tiles):
        return None
    box = grid_tiles[idx]
    if isinstance(box, (list, tuple)) and len(box) >= 4:
        return int(box[0]), int(box[1]), int(box[2]), int(box[3])
    if isinstance(box, dict):
        b = box.get("bounds") or box.get("box")
        if isinstance(b, (list, tuple)) and len(b) >= 4:
            return int(b[0]), int(b[1]), int(b[2]), int(b[3])
    return None


def _recover_profile_grid(ctx) -> None:
    """Wyjdź z viewera posta/rolki i wróć na `profile_other` (siatka).

    Live 13:56 (simona_bogacz / content_search hot): po lajku 3× BACK
    przeszło profil → comments/search, a potem `ensure(profile_other)`
    (profile_other w BACK_TO_EXIT, bez tab-kotwicy) wspiął się
    search_results → search_top i zniszczył stos #. Tu: BACK tylko gdy
    nadal widać viewer/story; inaczej abort — bez ensure.
    """
    from v2.core.screen_map import flatten_hierarchy
    from v2.steps.post import _post

    for _ in range(3):
        state = ctx.navigator.identify()
        if state.top == "profile_other" and not getattr(state, "overlay", None):
            return
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        # Tylko viewer relacji / posta / rolki — nie sheet rodzica ani search
        still_viewer = (
            state.top == "story_viewer"
            or _post.media_viewer_open(flat)
        )
        if not still_viewer:
            ctx.log("profile.visit.like_recover_abort", top=state.top)
            return
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
    top = ctx.navigator.identify().top
    if top != "profile_other":
        # Świadomie BEZ ensure — patrz docstring
        ctx.log("profile.visit.like_recover_missed", top=top)


def _tile_count(cfg: dict) -> int:
    """Ile kafelków polubić. Katalog pluginów podaje zakres {min,max}
    (losowany), starsze flow YAML — pojedyncze `max`."""
    cnt = cfg.get("count")
    if isinstance(cnt, dict):
        return random.randint(int(cnt.get("min", 0)), int(cnt.get("max", 0)))
    return int(cfg.get("max", 3))


SCORE_WEIGHTS = {"niche_ratio": 0.50, "story": 0.10,
                 "tiles": 0.05, "follower_ratio": 0.15}


def _score_profile(header: dict, ai: dict, story_watched: bool, tiles_liked: int,
                   *, story_stage=True, tiles_stage=True) -> tuple[float, dict]:
    """Ważona ocena 0.0–1.0 **znormalizowana do składników, które dało się zbadać**.

    Wcześniej wagi sumowały się do stałej, niezależnie od tego, ile etapów
    faktycznie się wykonało. W skróconej procedurze („na gorąco") nie ma ani
    analizy siatki, ani relacji, ani lajkowania kafelków — czyli 0.65 z 0.85
    wagi było niedostępne z definicji i wynik nie mógł przekroczyć ~0.2.
    Przy domyślnym progu 0.5 znaczyło to, że follow z gorącej interakcji
    **nigdy nie padnie** — bot chodziłby po profilach bez żadnego skutku.
    Wyszło na żywym przebiegu E4: scoring 0.0–0.015 na każdym profilu.

    Normalizacja sprawia, że próg znaczy to samo w obu trybach: „tyle procent
    tego, co dało się ocenić". Zwracamy też rozbicie, żeby z logu było widać,
    z czego wynik powstał.
    """
    parts: dict = {}
    tiles = ai.get("tiles") or {}
    if tiles:
        cats = [t.get("category") for t in tiles.values()]
        parts["niche_ratio"] = sum(1 for c in cats if c == "niche") / max(len(cats), 1)
    if story_stage:
        parts["story"] = 1.0 if story_watched else 0.0
    if tiles_stage:
        parts["tiles"] = min(tiles_liked, 3) / 3.0

    ff = header.get("followers") or 0
    fg = header.get("following") or 0
    if fg > 0:
        parts["follower_ratio"] = min(ff / fg, 10.0) / 10.0

    total_w = sum(SCORE_WEIGHTS[k] for k in parts)
    if not total_w:
        return 0.0, parts
    score = sum(SCORE_WEIGHTS[k] * v for k, v in parts.items()) / total_w
    return max(0.0, min(1.0, score)), parts


def _decide_action(scoring: float, cfg: dict) -> tuple[str, str | None]:
    """Zwraca `(action, skip_why)` — skip_why tylko gdy action=='skip'."""
    from v2.core.chance import roll as chance_roll
    min_follow = float(cfg.get("min_scoring", 0.50))
    if scoring < min_follow:
        return "skip", "ponizej progu"
    # dodatkowa warstwa losowa — człowiek nie obserwuje każdego profilu,
    # który mu się spodobał
    chance = cfg.get("chance") or {}
    if chance and not chance_roll(chance, {"scoring": scoring})["pass"]:
        return "skip", "szansa"
    return "follow", None


def _execute_follow(ctx) -> tuple[bool, str | None]:
    """Tap w Follow. Zwraca `(ok, why)` — why gdy nie udało się."""
    from v2.core.screen_map import flatten_hierarchy, ids_from_flat, dominant_package
    # Poza profilem nie szukaj przycisku — uniknij mylących tapów / ciszy w logu
    flat = flatten_hierarchy(ctx.device.hierarchy())
    ids = ids_from_flat(flat)
    pkg = dominant_package(flat)
    top = ctx.screen_map.identify(ids, pkg).top
    if top not in ("profile_other", "profile_self"):
        ctx.log("profile.visit.follow_off_profile", top=top)
        return False, "poza profilem"
    btn = _find_follow_button(flat)
    if not btn:
        ctx.log("profile.visit.follow_no_button")
        return False, "brak przycisku"
    c = _center(btn.get("bounds") or "")
    if not c:
        ctx.log("profile.visit.follow_bad_bounds")
        return False, "zly bounds"
    ctx.ht.tap(*c, pause_kind="tap")
    ctx.humanizer.pause("between_steps")
    # weryfikacja: jeden nowy dump po tapie
    flat2 = flatten_hierarchy(ctx.device.hierarchy())
    btn2 = _find_follow_button(flat2)
    if btn2:
        blob = ((btn2.get("text") or "") + " " + (btn2.get("content-desc") or "")).lower()
        if "obserwujesz" in blob or "following" in blob:
            return True, None
    ctx.log("profile.visit.follow_not_confirmed")
    return False, "nie potwierdzono"


def _find_follow_button(flat):
    for n in flat:
        rid = (n.get("resource-id") or "").lower()
        if "profile_header_follow_button" in rid or "profile_header_user_action_follow_button" in rid:
            return n
    return None


def _save_visited(ctx, header, ai_verdict, **kw):
    store = VisitedProfilesStore(ctx.account.account_id,
                                 ctx.events.file_path.parent.parent.parent)
    store.add(username=header.get("username") or "unknown",
              source=ctx.scratch.get("baseline_screen") or "unknown",
              header={k: header.get(k) for k in
                      ("posts", "followers", "following", "is_private", "verified", "has_story_ring")},
              ai_tiles_count=len((ai_verdict or {}).get("tiles") or {}),
              # Ścieżka do zrzutu siatki — bez niej „dlaczego ten profil
              # przeszedł/odpadł" da się sprawdzić tylko z pamięci.
              grid_image=ctx.scratch.get("profile_grid_image"),
              grid_estimated=ctx.scratch.get("profile_grid_estimated", False),
              **kw)


def _center(bounds: str):
    try:
        a, b = bounds.split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None
