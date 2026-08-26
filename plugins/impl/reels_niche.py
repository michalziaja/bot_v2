"""reels_niche — główny plugin pozyskiwania.

Pętla nad istniejącymi krokami `reels.*`. Plugin NIE zawiera logiki UI —
wszystkie tapy i dumpy siedzą w krokach. Tutaj jest tylko kolejność, budżet
i decyzje na podstawie ustawień z katalogu.

Kolejność jest celowa i wynika z kosztu:
  1. `reels.watch` — klatki OD RAZU w tle; równolegle bramka (liczniki).
     Gate OK → composite + opis → AI. Gate fail → porzuć klatki.
  2. Po AI: odrzut → swipe, albo zadania (like / harvest / hot) → swipe.

Każdy blok wchodzący w sheet (lajkujący, komentarze) ma `try/finally` z
powrotem na `reels_feed`. Bez tego następny `swipe_next` przewija wnętrze
sheetu zamiast rolki — lekcja L4c z ARCHITEKTURA.md §3.4.
"""
from __future__ import annotations

import random

from v2.plugins import stepcall
from v2.plugins.base import Budget, PluginResult, draw_range, gate_params
from v2.plugins.impl.profile_interact import ProfileInteract, settings_for
from v2.plugins.registry import register
from v2.steps import registry as steps

REELS = "reels_feed"
# Ekran, na który wracamy między kolejnymi „gorącymi" profilami z danego sheetu.
SHEET_SCREEN = {"likers": "reel_likers_list", "comments": "reel_comments_sheet"}
OPEN_PROFILE_STEP = {"likers": "reels.open_liker_profile",
                     "comments": "reels.open_commenter_profile"}
HARVEST_STEP = {"likers": "reels.harvest_likers",
                "comments": "reels.harvest_commenters"}


@register
class ReelsNiche:
    id = "reels_niche"
    entry_screen = "home_feed"
    exit_screen = "home_feed"

    def run(self, ctx, s: dict, budget: Budget) -> PluginResult:
        rng = random.Random()
        stats = {"watched": 0, "gate_skipped": 0, "ai_rejected": 0, "liked": 0,
                 "likers_saved": 0, "commenters_saved": 0, "comments_liked": 0,
                 "repeat_skipped": 0, "hot_visited": 0, "hot_followed": 0,
                 "caption_ok": 0, "caption_missing": 0}

        res = _step(ctx, "reels.open", {})
        if res.status == "failed":
            return PluginResult.failed(f"reels.open: {res.error}", budget, **stats)

        stopped_by = "items"
        last_marker = None          # odcisk OSTATNIO PRZETWORZONEJ rolki
        swipe_failures = 0
        while True:
            reason = budget.stop_reason()
            if reason:
                stopped_by = reason
                break

            ctx.scratch.pop("reel", None)
            ctx.scratch.pop("reel_ai", None)
            ctx.scratch.pop("reel_top_comments", None)
            # Opis MUSI wylecieć razem z resztą: zostawiony, trafiłby do promptu
            # następnej rolki jako jej własny i model oceniałby obraz jednej
            # treści opisem innej.
            ctx.scratch.pop("reel_caption", None)
            ctx.scratch.pop("reel_caption_source", None)
            ctx.scratch.pop("reel_caption_from_watch", None)
            ctx.scratch.pop("reel_caption_slipped", None)

            # Jedna rolka do końca (watch+caption → AI → like…) — swipe
            # dopiero PO odpowiedzi AI / decyzji o odrzuceniu, nigdy w trakcie.
            # Progress X/Y + „Rolka @user” ogłasza _one_reel po odczycie autora.
            outcome, marker, reject = self._one_reel(
                ctx, s, budget, rng, stats, last_marker)
            if marker:
                last_marker = marker
            budget.tick()
            # Live counters + budżet po każdej rolce
            from v2.plugins.progress_log import sync_plugin_stats
            sync_plugin_stats(ctx, stats, items_done=budget.items_done,
                              max_items=budget.max_items)

            # Odrzut / lajk → „Nastepna rolka…” → krótka pauza → swipe.
            if reject:
                from v2.plugins.reel_log import say, reject_line
                say("")
                say(reject_line(reject))
                say("")
            if outcome == "limit_daily":
                stopped_by = "limit_daily"
                break
            if outcome == "feed_lost":
                stopped_by = "error"
                break

            if budget.stop_reason() is None:
                from v2.plugins.reel_log import say, next_reel_line
                # Najpierw komunikat (terminal = zamiar swipe), krótka pauza ze
                # wspólnego profilu humanizera, potem swipe. current = po ticku.
                say(next_reel_line(budget.items_done, budget.max_items))
                ctx.humanizer.pause("before_next_reel")

                nxt = _step(ctx, "reels.swipe_next", {"announce": False})
                if nxt.status == "failed":
                    # Jedno nieudane przewinięcie bywa chwilowe (nakładka jeszcze
                    # się renderuje). Dwa z rzędu znaczą, że feed nie reaguje —
                    # dalsze próby tylko powtarzałyby akcje na tej samej rolce.
                    swipe_failures += 1
                    ctx.log("reels_niche.swipe_failed", error=nxt.error,
                            consecutive=swipe_failures)
                    if swipe_failures >= 2:
                        stopped_by = "error"
                        break
                    ctx.navigator.ensure(REELS, max_steps=3)
                    # Przed kolejnym watch — meta musi być czytelna (marker).
                    _ensure_readable_meta(ctx)
                else:
                    swipe_failures = 0

        ctx.log("reels_niche.done", stopped_by=stopped_by, **stats)
        return PluginResult.done(budget, stopped_by, **stats)

    # ------------------------------------------------------------------
    def _one_reel(self, ctx, s: dict, budget: Budget, rng, stats,
                  last_marker: str | None = None
                  ) -> tuple[str, str | None, str | None]:
        """Jedna rolka. Zwraca (wynik, marker, reject_msg).

        wynik: 'ok' | 'skip' | 'limit_daily' | 'feed_lost'
        reject_msg: czytelny powód odrzucenia (albo None) — pętla pokazuje
        go w terminalu; krótka pauza PRZED swipe_next jest w pętli głównej.
        """
        ai_cfg = s.get("ai") or {}

        # --- 1. klatki + bramka; opis rownolegle z composite (w watch) ---
        # Bez watch_reel — N klatek z odstępem, potem od razu opis/AI.
        # use_frames / read_caption: niezależnie (tylko obraz / tylko opis / oba).
        # Dalej: odrzut → swipe, albo działania (like/harvest) → swipe.
        ai_on = bool(ai_cfg.get("enabled"))
        # use_frames: zbieranie klatek (domyślnie ON — jak wcześniej nawet przy AI off)
        want_frames = bool(ai_cfg.get("use_frames", True))
        want_caption = bool(ai_on and ai_cfg.get("read_caption", True))
        # Przy AI: ostrzeż gdy brak wejść / pokaż tryb tylko-klatki / tylko-opis
        if ai_on:
            from v2.plugins.reel_log import say, ai_mode_line
            mode = ai_mode_line(use_frames=want_frames, read_caption=want_caption)
            if mode:
                say(mode)

        watch = _step(ctx, "reels.watch", {
            "frames": int(ai_cfg.get("frames", 2)),
            "frame_interval_s": float(ai_cfg.get("frame_interval_s", 0.5)),
            "use_frames": want_frames,
            "use_watch_duration": False,
            "watch_reel": s.get("watch_reel"),
            "save_frames": bool(ai_cfg.get("save_frames")),
            "gate": gate_params(s.get("gate") or {}),
            "read_caption": want_caption,
            "open_comments": bool(ai_cfg.get("read_top_comments")),
        })
        stats["watched"] += 1
        reel = ctx.scratch.get("reel") or {}
        marker = reel.get("marker")
        author = reel.get("author") or "?"
        # Uzupełnij detail postępu o autora (Rolka @user i/N)
        try:
            from v2.plugins.progress_log import announce_progress
            from v2.plugins.reel_log import say, reel_progress_line
            cur = int(budget.items_done) + 1
            announce_progress(
                ctx, say,
                msg=reel_progress_line(author, cur, budget.max_items),
                phase="reel", current=cur, total=budget.max_items,
                label="Rolka", detail=f"@{author}" if author else None)
        except Exception:
            pass

        # --- 1b. czy to na pewno NOWA rolka? ---
        # Druga linia obrony za `reels.swipe_next`: nawet gdy krok uznał
        # przewinięcie za udane, nie ruszamy akcji, dopóki odcisk rolki się
        # nie zmieni. Bez tego bot otwierał komentarze tej samej rolki dwa
        # razy pod rząd.
        if marker and last_marker and marker == last_marker:
            ctx.log("reels_niche.same_reel", marker=marker[:70],
                    author=reel.get("author"))
            stats["repeat_skipped"] += 1
            return "skip", marker, f"ta sama rolka co poprzednia (@{author})"

        # Filtr OK / klatki / opis — logi lecą z watch/caption (live), nie tu.
        if watch.status != "ok":
            stats["gate_skipped"] += 1
            pay = watch.payload or {}
            why = pay.get("reason") or watch.status
            gate_why = pay.get("gate_why")
            if why == "ad_detected":
                detail = f"reklama @{author}"
            elif why == "gate_failed":
                detail = f"filtr ({gate_why or 'za malo lajkow/komentarzy'}) @{author}"
            else:
                detail = f"{why} @{author}"
            return "skip", marker, detail

        if _author_blocked(reel, s.get("gate") or {}):
            ctx.log("reels_niche.author_blocked", author=reel.get("author"))
            stats["gate_skipped"] += 1
            return "skip", marker, f"autor na liscie blokad (@{author})"

        # Watch OK, ale meta bez markera (hierarchia w locie) — nie AI / nie like.
        # Soft-skip po gate/blokadach, żeby skip_authors nadal działał.
        if "reel" in ctx.scratch and not marker:
            ctx.log("reels_niche.meta_unreadable", author=reel.get("author"))
            stats["gate_skipped"] += 1
            ctx.navigator.ensure(REELS, max_steps=3)
            _ensure_readable_meta(ctx)
            return "skip", None, "meta nieczytelna — pomijam rolke"

        # Przy opisie zjechalismy na inna rolke — klatki != ekran, AI byloby
        # mieszanka dwoch tresci. Pomijamy ocene; scratch ustawiamy na TO, co
        # jest na ekranie, zeby swipe_next nie zrobil dodatkowego przeskoku.
        if ctx.scratch.get("reel_caption_slipped") or (watch.payload or {}).get("reel_slipped"):
            stats["gate_skipped"] += 1
            try:
                from v2.steps.reels._reel_meta import read_current_reel
                now = read_current_reel(ctx.device)
                if now.get("marker"):
                    ctx.scratch["reel"] = now
                    marker = now.get("marker")
            except Exception:
                pass
            return ("skip", marker,
                    f"przy opisie zjechalismy z @{author} — pomijam AI")

        # Fallback: opis nie byl w watch — capture_reel_caption loguje live.
        if want_caption and not ctx.scratch.get("reel_caption_from_watch"):
            _step(ctx, "reels.read_caption_comments", {
                "open_comments": bool(ai_cfg.get("read_top_comments")),
            })
            if ctx.scratch.get("reel_caption_slipped"):
                stats["gate_skipped"] += 1
                return ("skip", marker,
                        f"przy opisie zjechalismy z @{author} — pomijam AI")

        # --- 2. AI (swipe dopiero PO odpowiedzi) ---
        # Wynik (score/decyzja) drukuje vision.say_ai_result — tu tylko start
        # i decyzja pluginu (ODRZUT); bez drugiego dumpa przy pass.
        score = None
        if ai_cfg.get("enabled"):
            from v2.plugins.reel_log import say, ai_start_line, ai_reject_human
            say(ai_start_line())
            ai_step = _step(ctx, "reels.analyze_frames_ai", {"ai": ai_cfg})
            src = ctx.scratch.get("reel_caption_source")
            if src == "missing":
                stats["caption_missing"] += 1
            elif src:
                stats["caption_ok"] += 1
            ai = ctx.scratch.get("reel_ai") or {}
            score = ai.get("niche_score")
            if ai_step.status == "skipped":
                why = (ai_step.payload or {}).get("reason") or "ai_skipped"
                stats["ai_rejected"] += 1
                return ("skip", marker,
                        ai_reject_human(why=why, author=author))
            verdict = _eval_ai(ai, ai_cfg)
            if not verdict["pass"]:
                ctx.log("reels_niche.ai_reject", **verdict, author=reel.get("author"))
                stats["ai_rejected"] += 1
                return ("skip", marker,
                        ai_reject_human(
                            why=verdict.get("why") or verdict.get("verdict"),
                            score=verdict.get("score"),
                            author=author))

        # --- 4. lajk rolki ---
        like_cfg = s.get("like_reel") or {}
        if like_cfg.get("enabled"):
            lr = _step(ctx, "reels.like", {
                "match": {}, "chance": like_cfg.get("chance") or {},
            })
            if lr.status == "ok":
                stats["liked"] += 1
                from v2.plugins.reel_log import say
                say(f"Polubiono @{author}")
            elif _is_daily_limit(lr):
                return "limit_daily", marker, None

        # --- 5. lajkujący (+ ewentualna interakcja „na gorąco") ---
        likers = s.get("likers") or {}
        harvest = likers.get("harvest") or {}
        hot_likers = likers.get("hot_interact") or {}
        if harvest.get("enabled") and _score_ok(score, likers.get("min_niche_score_to_harvest")):
            outcome = self._sheet_block(
                ctx, s, "likers", rng, stats,
                collect=draw_range(harvest.get("collect"), rng, 10),
                hot=hot_likers)
            if outcome:
                return outcome, marker, None

        # --- 6. komentarze (+ lajki komentarzy, interakcja „na gorąco") ---
        comments = s.get("comments") or {}
        c_harvest = comments.get("harvest") or {}
        c_likes = comments.get("like_comments") or {}
        hot_comments = comments.get("hot_interact") or {}
        if c_harvest.get("enabled") or c_likes.get("enabled") or hot_comments.get("enabled"):
            outcome = self._sheet_block(
                ctx, s, "comments", rng, stats,
                collect=draw_range(c_harvest.get("collect"), rng, 10),
                hot=hot_comments, like_comments=c_likes)
            if outcome:
                return outcome, marker, None

        return "ok", marker, None

    # ------------------------------------------------------------------
    def _sheet_block(self, ctx, s: dict, where: str, rng, stats, *,
                     collect: int, hot: dict, like_comments: dict | None = None):
        """Jedno wejście w sheet: zbieranie → lajki komentarzy → gorące profile.

        Wszystko w JEDNYM otwarciu listy — ponowne otwieranie sheetu na każdą
        czynność to trzy dodatkowe przejścia UI na rolkę i trzy okazje, żeby
        zgubić ekran.

        Zwraca `None` gdy można lecieć dalej, albo powód przerwania rolki.
        `finally` z powrotem na feed jest obowiązkowe: bez niego `swipe_next`
        przewija wnętrze sheetu zamiast rolki (lekcja L4c).
        """
        want_hot = bool(hot.get("enabled"))
        want_likes = bool(like_comments and like_comments.get("enabled"))
        # Lajkowanie komentarzy i wchodzenie na profile wymagają OTWARTEJ listy.
        # Wcześniej krok zbierający zamykał ją w swoim `finally`, więc lajkowanie
        # komentarzy szukało wierszy na feedzie rolek i zawsze kończyło się
        # „no_candidates" — filtr wyglądał na działający, choć nic nie klikał.
        keep_open = want_hot or want_likes
        stop = None
        try:
            r = _step(ctx, HARVEST_STEP[where],
                      {"limit": collect, "keep_sheet_open": keep_open})
            saved = int((r.payload or {}).get("saved") or 0) if r.status == "ok" else 0
            stats["likers_saved" if where == "likers" else "commenters_saved"] += saved

            if keep_open and r.status != "ok":
                # sheet się nie otworzył — nie ma po czym klikać
                ctx.log("reels_niche.sheet_not_open", where=where, status=r.status)
                return None

            if want_likes:
                liked = self._like_comments(ctx, like_comments, rng)
                if liked == "limit_daily":
                    return "limit_daily"
                stats["comments_liked"] += liked

            if want_hot:
                stop = self._hot_interact(ctx, s, where, hot, rng, stats)
        finally:
            # ZAWSZE — także gdy krok rzucił w środku sheetu
            if not _back_on_feed(ctx):
                stop = stop or "feed_lost"
        return stop

    def _hot_interact(self, ctx, s: dict, where: str, hot: dict, rng, stats):
        """Wejdź na N profili z otwartej listy i wykonaj na każdym procedurę P3.

        Ustawienia procedury bierzemy z configu konta (`profile_interact`),
        nadpisane globalnie dla rolek i punktowo dla tego miejsca — dzięki temu
        z gorących interakcji można obserwować rzadziej niż z listy
        pre-filtrowanej, nie duplikując całej sekcji ustawień.
        """
        want = draw_range(hot.get("count"), rng, 1)
        p_settings = settings_for(ctx, s.get("profile_interact_override") or {},
                                  hot.get("overrides") or {})
        short = not hot.get("full_procedure")
        plugin = ProfileInteract()
        sheet_screen = SHEET_SCREEN[where]

        ctx.scratch.pop("hot_used", None)     # nowa rolka = nowa lista
        # Flaga: profile_interact nie aktualizuje raw_profiles przy hot
        ctx.scratch["hot_from_reels"] = True
        try:
            return self._hot_loop(ctx, plugin, p_settings, where, hot, want,
                                  short, sheet_screen, stats)
        finally:
            ctx.scratch.pop("hot_from_reels", None)
            # Baseline wskazuje na sheet tej rolki — zostawiony, kierowałby
            # następne `profile.exit` do listy, której już nie ma.
            ctx.scratch.pop("baseline_screen", None)

    def _hot_loop(self, ctx, plugin, p_settings, where, hot, want, short,
                  sheet_screen, stats):
        from v2.plugins.progress_log import (
            announce_progress, progress_sub, sync_plugin_stats)
        from v2.plugins.reel_log import say, hot_profile_line
        hot_i = 0
        for _ in range(want):
            opened = _step(ctx, OPEN_PROFILE_STEP[where], {
                "prefer_ring":  bool(hot.get("prefer_ring", True)),
                "require_ring": bool(hot.get("require_ring", False)),
            })
            if opened.status != "ok":
                # Nieudany tap w JEDEN wiersz nie kończy tematu — nick trafił
                # już na listę użytych, więc kolejna próba weźmie inny wiersz.
                # Kończymy dopiero, gdy kandydaci się wyczerpali albo zgubiliśmy
                # listę (wtedy krok zwraca `failed`).
                if (opened.payload or {}).get("reason") == "open_failed":
                    continue
                break

            hot_i += 1
            uname = (opened.payload or {}).get("username")
            # Hot @alice 1/3 z listy lajkujacych → progress.sub
            announce_progress(
                ctx, say,
                msg=hot_profile_line(uname, hot_i, want, where=where),
                phase="reel",
                current=int(stats.get("watched") or 0) or hot_i,
                total=None,
                label="Rolka",
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

            # powrót na listę — `profile.exit` wraca do baseline zapisanego
            # przez krok otwierający profil
            _step(ctx, "profile.exit", {})
            if out["stop"] == "limit_daily":
                return "limit_daily"
            if ctx.navigator.identify().top != sheet_screen:
                ctx.log("reels_niche.hot_interact_lost_sheet", where=where)
                break
        return None

    def _like_comments(self, ctx, cfg: dict, rng):
        """Lajkowanie komentarzy. Zwraca liczbę polubionych albo 'limit_daily'."""
        try:
            step_cls = steps.get("reels.like_comment")
        except KeyError:
            ctx.log("reels_niche.like_comment_missing")
            return 0
        want = draw_range(cfg.get("count"), rng, 1)
        liked = 0
        for _ in range(want):
            r = step_cls().run(ctx, {
                "min_comment_likes": int(cfg.get("min_comment_likes", 0)),
                "extra_chance_percent": int(cfg.get("extra_chance_percent", 100)),
            })
            if r.status == "ok":
                liked += 1
            elif _is_daily_limit(r):
                return "limit_daily"
            elif r.status == "skipped" and (r.payload or {}).get("reason") == "no_candidates":
                break
        return liked


# ============================================================
# helpers
# ============================================================
def _step(ctx, name: str, params: dict):
    """Krok z rejestru — wspólna obsługa w `plugins/stepcall.py`."""
    return stepcall.call(steps, ctx, name, params, source="reels_niche")


def _ensure_readable_meta(ctx) -> None:
    """Po failu swipe / pustej meta — dociągnij marker przed kolejnym watch."""
    try:
        from v2.steps.reels._reel_meta import read_current_reel
        from v2.steps.system._ui_helpers import settle_for_animation
        settle_for_animation(ctx, max_wait_s=2.0, fallback_wait_s=1.5)
        ctx.device.wait_for_stable_ui(max_wait_s=2.0)
        meta = read_current_reel(ctx.device)
        if meta.get("marker"):
            ctx.scratch["reel"] = meta
    except Exception:
        pass


def _author_blocked(reel: dict, gate: dict) -> bool:
    author = (reel.get("author") or "").strip().lstrip("@").lower()
    if not author:
        return False
    blocked = {str(a).strip().lstrip("@").lower() for a in (gate.get("skip_authors") or [])}
    return author in blocked


def _eval_ai(ai: dict, cfg: dict) -> dict:
    """Dopasowanie wg trybu z ustawień.

    `niche_match`  — treść musi POZYTYWNIE trafić w niszę (konto o ostro
                     zdefiniowanej tematyce).
    `exclude_only` — nie wymagamy trafienia; odrzucamy tylko to, co model
                     oznaczył jako wykluczone (konto ogólne).
    """
    if not ai:
        return {"pass": False, "why": "no_ai_result"}
    verdict = (ai.get("verdict") or "").lower()
    score = float(ai.get("niche_score") or 0)

    if cfg.get("mode") == "exclude_only":
        if verdict in ("off", "excluded"):
            return {"pass": False, "why": "excluded", "verdict": verdict}
        return {"pass": True, "verdict": verdict, "score": score}

    allow = [str(v).lower() for v in (cfg.get("allow_verdict") or [])]
    if allow and verdict and verdict not in allow:
        return {"pass": False, "why": f"verdict={verdict}", "verdict": verdict}
    min_score = float(cfg.get("min_niche_score", 0))
    if score < min_score:
        return {"pass": False, "why": f"score<{min_score}", "score": score}
    return {"pass": True, "verdict": verdict, "score": score}


def _score_ok(score, threshold) -> bool:
    """Brak progu = zbieramy zawsze. Brak wyniku AI (AI wyłączone) też —
    inaczej wyłączenie AI po cichu wyłączyłoby cały harvest."""
    if not threshold:
        return True
    if score is None:
        return True
    return float(score) >= float(threshold)


def _is_daily_limit(res) -> bool:
    reason = (res.payload or {}).get("reason") or ""
    return "daily" in str(reason)


def _back_on_feed(ctx) -> bool:
    if ctx.navigator.ensure(REELS, max_steps=4):
        return True
    ctx.log("reels_niche.feed_lost")
    return False
