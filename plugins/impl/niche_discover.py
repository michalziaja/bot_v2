"""niche_discover — one-shot discovery niszy z treści IG.

  Pipeline (dwie fazy):
  A. ZBIERANIE (telefon): own → competitors (search → profil → posty/rolki).
     Bio tekst + frames + opisy → JSON per konto — BEZ wywołań AI.
  B. AI (po zebraniu wszystkiego):
     1. niche_discover_posts   — zbiorczy composite ~4 postów + captiony
     2. niche_discover_reels   — każda rolka osobno (1 call = 1 rolka)
     3. niche_discover_account — finalize jednego konta (bio tekst + partials)
     4. niche_discover_merge   — gdy ≥2 profile: wspólny zakres (own ważniejsze)

Uruchamiane z panelu (Ustawienia → Nisza), nie ze scenariuszy.
Zapis: account.niche z accepted=false — operator zatwierdza zanim AI engagement.

Odczyt opisu rolki (`_read_reel_caption_guarded`) — TYLKO TAP (bez swipe).
  ZAWSZE najpierw expand (tap caption / „…więcej”), dopiero potem dump do sample.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from v2.ai.vision import AiVision, accumulate_usage, empty_usage_totals
from v2.core.composite import (
    build_composite_grid, stitch_vertical_crops, fit_jpeg_box,
)
from v2.core.screen_map import flatten_hierarchy
from v2.plugins import stepcall, store
from v2.plugins.base import Budget, PluginResult
from v2.plugins.registry import register
from v2.steps import registry as steps
from v2.steps.profile._grid import parse_bounds
from v2.steps.profile._header import read_profile_header
from v2.steps.reels.read_caption_comments import CAPTION_IDS
from v2.steps.system._ui_helpers import settle_for_animation


HOME = "home_feed"
PROFILE_SELF = "profile_self"
PROFILE_OTHER = "profile_other"
LIST_LIMIT = 20
# Poprzednio (przed pudełkiem 1280×1920): scrcpy max_size=720, thumb_w=320,
# zapis 1:1 ze źródła. Teraz ×1.25 — sufit fit = dłuższy bok jak nowy scrcpy,
# wysokość pod proporcje phone (~1080×2340) żeby kolaż 2×2 się mieścił.
AI_FRAME_MAX_W = 900   # 720 * 1.25
AI_FRAME_MAX_H = 1950  # round(900 * 2340 / 1080)
COMPOSITE_THUMB_W = 400  # 320 * 1.25
_HASHTAG_RE = re.compile(r"#([\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9_]+)", re.UNICODE)


def _collect_count(collect: dict, kind: str, default: int = 4) -> int:
    """Ile postów/rolek — stała liczba (bez losowania min/max).

    Akceptuje też stary format range `{"min":N,"max":M}` z zapisanych kont
    (bierze max, żeby nie ściąć limitu po migracji panelu).
    """
    raw = (collect.get(kind) or {}).get("count", default)
    if isinstance(raw, dict):
        try:
            return max(0, int(raw.get("max") or raw.get("min") or default))
        except (TypeError, ValueError):
            return default
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default


@register
class NicheDiscover:
    id = "niche_discover"
    entry_screen = HOME
    exit_screen = HOME

    def run(self, ctx, s: dict, budget: Budget) -> PluginResult:
        stats = {
            "sources": 0, "posts": 0, "reels": 0,
            "ai_posts": 0, "ai_reels": 0, "ai_account": 0, "ai_merge": 0,
            "saved": False, "stub": False,
            "collected_packs": 0,
            # suma tokenów z całego przebiegu (posts+reels+account+merge)
            "ai_usage": empty_usage_totals(),
        }
        seed = (s.get("seed") or "").strip()
        sources_cfg = s.get("sources") or {}
        ai_cfg = dict(s.get("ai") or {})
        write_cfg = s.get("write") or {}
        existing = dict(ctx.account.niche or {})
        collect = s.get("collect") or {}
        # te same limity dla own i konkurencji — bez losowania
        posts_n = _collect_count(collect, "posts", 4)
        reels_n = _collect_count(collect, "reels", 4)

        stopped_by = "items"
        packs: list[dict] = []

        want_own = bool(sources_cfg.get("own_profile", True))
        want_comp = bool(sources_cfg.get("competitors"))
        comp = s.get("competitors") or {}
        # cała lista — bez limitu „max profili"
        comp_names = _normalize_usernames(comp.get("list") or [])
        if want_comp and not comp_names:
            ctx.log("niche_discover.competitors_list_empty",
                    hint="uzupełnij competitors.list (np. juraspa.pl)")

        if not want_own and not (want_comp and comp_names):
            ctx.log("niche_discover.no_sources",
                    own_profile=want_own, competitors=want_comp,
                    list_len=len(comp_names))
            ctx.navigator.ensure(HOME, max_steps=4)
            return PluginResult.done(budget, "empty", **stats)

        # ========== FAZA A: zbieranie frames + opisów → JSON (bez AI) ==========
        ctx.log("niche_discover.phase_collect_start",
                own=want_own, competitors=len(comp_names) if want_comp else 0)

        if want_own:
            ctx.log("niche_discover.source_start", source_kind="own", phase="collect")
            out = self._collect_one_source(
                ctx, s, budget, stats,
                source_kind="own",
                source_account="own",
                username="",
                posts_n=posts_n,
                reels_n=reels_n,
                baseline_screen=PROFILE_SELF,
                ensure_profile=lambda: self._ensure_own_profile(ctx),
            )
            if out.get("pack"):
                packs.append(out["pack"])
            if out.get("stop"):
                stopped_by = out["stop"]

        if stopped_by == "items" and want_comp:
            for user in comp_names:
                if budget.user_stopped():
                    stopped_by = "user"
                    break
                # wyjdź z profilu → search → kolejne konto
                ctx.log("niche_discover.source_start",
                        source_kind="competitor", user=user, phase="collect")
                ctx.navigator.ensure(HOME, max_steps=4)
                if not self._open_competitor(ctx, user):
                    ctx.log("niche_discover.competitor_failed", user=user)
                    continue
                out = self._collect_one_source(
                    ctx, s, budget, stats,
                    source_kind="competitor",
                    source_account=f"@{user}",
                    username=user,
                    posts_n=posts_n,
                    reels_n=reels_n,
                    baseline_screen=PROFILE_OTHER,
                    ensure_profile=lambda: self._still_on_profile(ctx),
                )
                if out.get("pack"):
                    packs.append(out["pack"])
                if out.get("stop"):
                    stopped_by = out["stop"]
                    break

        # telefon wolny — wróć na home przed serią zapytań AI
        ctx.navigator.ensure(HOME, max_steps=4)
        _write_collect_index(ctx, packs)
        stats["collected_packs"] = len(packs)
        ctx.log("niche_discover.phase_collect_done",
                packs=len(packs), stopped_by=stopped_by,
                posts=stats["posts"], reels=stats["reels"])

        # ---- brak zebranych danych ----
        if not packs:
            if seed or (existing.get("description") or "").strip():
                final = self._finalize_text_only(ctx, s, seed, existing, stats)
                if final:
                    self._save(ctx, s, final, existing, write_cfg, stats)
                    ctx.navigator.ensure(HOME, max_steps=4)
                    _log_usage_summary(ctx, stats)
                    return PluginResult.done(budget, "seed_only", **stats)
            ctx.log("niche_discover.empty")
            return PluginResult.done(budget, "empty", **stats)

        # ========== FAZA B: AI per profil, potem merge ==========
        ctx.log("niche_discover.phase_ai_start", packs=len(packs))
        account_results: list[dict] = []
        for pack in packs:
            if budget.user_stopped():
                stopped_by = "user"
                break
            ex = existing if pack.get("source_kind") == "own" else {}
            result = self._analyze_pack(
                ctx, s, pack, seed=seed, existing=ex, stats=stats)
            if result:
                account_results.append(result)

        if not account_results:
            ctx.log("niche_discover.ai_empty_after_collect", packs=len(packs))
            return PluginResult.done(budget, stopped_by or "empty", **stats)

        if len(account_results) == 1:
            final = account_results[0]
        else:
            final = self._run_merge(ctx, account_results, seed, ai_cfg, stats)
            stats["ai_merge"] += 1

        self._save(ctx, s, final, existing, write_cfg, stats)
        ctx.navigator.ensure(HOME, max_steps=4)
        _log_usage_summary(ctx, stats)
        ctx.log("niche_discover.done", stopped_by=stopped_by,
                accounts=len(account_results), packs=len(packs),
                **_stats_for_log(stats))
        return PluginResult.done(budget, stopped_by, **stats)

    # ------------------------------------------------------------------
    def _collect_one_source(self, ctx, s, budget, stats, *,
                            source_kind, source_account, username,
                            posts_n, reels_n, baseline_screen,
                            ensure_profile) -> dict:
        """Faza A: bio tekst + posty/rolki → JSON. Bez wywołań AI."""
        if not ensure_profile():
            ctx.log("niche_discover.profile_failed", source=source_account)
            return {}

        # bio/nazwa jako tekst (MetaComposeView / resource-id) — bez zrzutu profilu
        profile_meta = _read_profile_bio_full(ctx)
        if not username:
            username = (profile_meta.get("username")
                        or profile_meta.get("full_name") or "")
        bio_text = (profile_meta.get("bio") or "").strip()
        full_name = (profile_meta.get("full_name") or "").strip()
        stats["sources"] += 1
        ctx.log("niche_discover.profile_bio", source=source_account,
                username=username or "",
                full_name=full_name[:40],
                bio_chars=len(bio_text),
                bio_source=profile_meta.get("bio_source") or "",
                bio_preview=bio_text[:80])
        _reveal_profile_grid(ctx)

        collect = s.get("collect") or {}
        # min 1 klatka (wcześniej wymuszano ≥4); max z panelu (12)
        reel_frames = max(1, int(collect.get("reel_frames") or 4))
        reel_interval = max(1.0, float(collect.get("reel_frame_interval_s") or 1.0))

        # odciski treści z postów — rolki z tą samą treścią pomijamy (dedup)
        seen_post_fps: set[str] = set()
        ctx.scratch["niche_post_fps"] = seen_post_fps
        posts = self._collect_kind(
            ctx, budget, stats,
            kind="post", content_types=["posts"],
            want=int(posts_n),
            baseline=baseline_screen,
            source_kind=source_kind, source_account=source_account,
            reel_frames=reel_frames, reel_interval_s=reel_interval,
            seen_post_fps=seen_post_fps,
        )
        stop = None
        if budget.user_stopped():
            stop = "user"
            reels: list[dict] = []
        else:
            reels = self._collect_kind(
                ctx, budget, stats,
                kind="reel", content_types=["reels"],
                want=int(reels_n),
                baseline=baseline_screen,
                source_kind=source_kind, source_account=source_account,
                reel_frames=reel_frames, reel_interval_s=reel_interval,
                seen_post_fps=seen_post_fps,
            )
            if budget.user_stopped():
                stop = "user"

        if not posts and not reels and not bio_text:
            ctx.log("niche_discover.collect_empty", source=source_account)
            return {"stop": stop} if stop else {}

        pack = {
            "source_kind": source_kind,
            "source_account": source_account,
            "username": username or "",
            "full_name": full_name,
            "bio": bio_text,
            "bio_source": profile_meta.get("bio_source") or "",
            # legacy: wcześniej zrzut; zostawiamy puste pole dla starych JSON-ów
            "profile_image": "",
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "posts": _samples_for_json(posts),
            "reels": _samples_for_json(reels),
        }
        json_path = _save_collect_pack(ctx, pack)
        pack["json_path"] = json_path or ""
        ctx.log("niche_discover.collect_saved",
                source=source_account, path=json_path or "",
                posts=len(posts), reels=len(reels), bio_chars=len(bio_text))
        return {"pack": pack, "stop": stop}

    def _analyze_pack(self, ctx, s, pack: dict, *, seed, existing,
                      stats) -> dict | None:
        """Faza B: Vision na już zebranym packu (posts → reels → account)."""
        ai_cfg = dict(s.get("ai") or {})
        source_kind = pack.get("source_kind") or "own"
        source_account = pack.get("source_account") or "own"
        username = pack.get("username") or ""
        bio_text = _format_profile_bio_for_ai(pack)
        posts = list(pack.get("posts") or [])
        reels = list(pack.get("reels") or [])

        ctx.log("niche_discover.analyze_pack",
                source=source_account, posts=len(posts), reels=len(reels),
                bio_chars=len(pack.get("bio") or ""))

        posts_partial = self._analyze_samples(
            ctx, posts, mode="niche_discover_posts",
            bio=bio_text, seed=seed, ai_cfg=ai_cfg,
            source_kind=source_kind, source_account=source_account,
            username=username, stats=stats, stat_key="ai_posts",
        ) if posts else {
            "themes": [], "audience_signals": [], "keywords": [],
            "hashtags": [], "notes": [],
            "source_kind": source_kind, "source_account": source_account,
        }
        reels_partial = self._analyze_samples(
            ctx, reels, mode="niche_discover_reels",
            bio=bio_text, seed=seed, ai_cfg=ai_cfg,
            source_kind=source_kind, source_account=source_account,
            username=username, stats=stats, stat_key="ai_reels",
        ) if reels else {
            "themes": [], "audience_signals": [], "keywords": [],
            "hashtags": [], "notes": [],
            "source_kind": source_kind, "source_account": source_account,
        }

        return self._finalize_account(
            ctx, posts_partial, reels_partial, bio_text, seed, existing,
            source_kind, source_account, username, ai_cfg, stats,
            observed_hashtags=extract_hashtags_from_texts(
                [sm.get("caption") for sm in posts + reels]),
        )

    # ------------------------------------------------------------------
    def _collect_kind(self, ctx, budget, stats, *,
                      kind, content_types, want, baseline,
                      source_kind, source_account,
                      reel_frames: int = 4, reel_interval_s: float = 1.0,
                      seen_post_fps: set | None = None) -> list[dict]:
        """Zbieranie treści — zawsze opis + zrzut, od najnowszej po kolei.

        Posty (siatka): od 1. kafelka, bez filtra post/film — każdy kafelek
        to screen mediów + opis. Rolki: tylko zakładka Rolki + rozwinięty opis.
        `seen_post_fps` — odciski treści z postów; przy rolkach dedup tej samej treści.
        """
        if want <= 0:
            return []
        if kind == "post" or content_types == ["posts"]:
            # zakładka siatki — WSZYSTKIE kafelki (zdjęcie, karuzela, film)
            # Start zawsze od 1. kafelka (góra-lewa); scroll tylko gdy siatki nie widać.
            _return_to_profile_main(ctx, baseline)
            # tabs poza kadrem (np. po długim bio) — pull w górę, nie swipe w dół
            if not _dump_profile_tabs(ctx):
                _scroll_profile_to_tabs(ctx)
            if _select_profile_grid_tab(ctx, "posts"):
                ctx.log("niche_discover.posts_tab_ok", source=source_account)
                time.sleep(0.45)
            # po wejściu w Posty: bez scrolla „na zapas” gdy grid już w hierarchy
            _reveal_profile_grid(ctx)
            return self._collect_from_grid(
                ctx, budget, stats,
                kind="post", content_types=[],  # brak filtra kind
                want=want, baseline=baseline, source_kind=source_kind,
                source_account=source_account,
                reel_frames=reel_frames, reel_interval_s=reel_interval_s,
                grid_mode="posts",
                seen_post_fps=seen_post_fps,
            )
        return self._collect_from_grid(
            ctx, budget, stats,
            kind=kind, content_types=content_types, want=want,
            baseline=baseline, source_kind=source_kind,
            source_account=source_account,
            reel_frames=reel_frames, reel_interval_s=reel_interval_s,
            grid_mode="reels",
            seen_post_fps=seen_post_fps,
        )

    def _collect_from_grid(self, ctx, budget, stats, *,
                           kind, content_types, want, baseline,
                           source_kind, source_account,
                           reel_frames: int = 4,
                           reel_interval_s: float = 1.0,
                           grid_mode: str = "posts",
                           seen_post_fps: set | None = None) -> list[dict]:
        """Kafelki z siatki po kolei (najnowszy → dalej) → BACK na profil.

        Każda treść: open_from_grid → weryfikacja viewera → screen/opis → BACK.
        Gdy profil ma mniej treści niż `want` — zbierz tyle ile jest i zakończ
        (bez błędu, bez nieskończonego przewijania).
        """
        samples: list[dict] = []
        # lokalny zbiór albo wspólny z fazy postów (dedup rolek + postów)
        post_fps = seen_post_fps if seen_post_fps is not None else set()
        # czysta lista — open_from_grid + ordered=True bierze 1. kafelek od góry-lewej
        ctx.scratch["grid_used"] = set()
        ctx.log("niche_discover.grid_start", grid_mode=grid_mode,
                ordered=True, from_tile=1, want=want)
        frames_dir = _frames_dir(ctx)
        # ile kolejnych nieudanych prób (brak kafelka / false open) zanim odpuścimy
        empty_streak = 0
        # po scrollu centers kafelków się zmieniają — te same treści wracają
        # jako „nowe”; dup_streak łapie to fingerprintem i kończy zbieranie
        dup_streak = 0
        max_empty = 2
        max_dup = 3
        posts_tab = grid_mode == "posts"

        # Rolki TYLKO z zakładki Rolki (ikona play na profilu) — nie z Widoku siatki
        if not posts_tab and (kind == "reel" or content_types == ["reels"]):
            if _ensure_reels_tab(ctx, baseline=baseline):
                ctx.log("niche_discover.reels_tab_ok", source=source_account)
                # siatka rolek: bez scrolla w dół — od razu 1. kafelek
                # Po tapie w zakładkę Rolki czekamy na animację przejścia;
                # wystarczy stabilizacja klatki, a nie pełna stabilność hierarchii.
                try:
                    settle_for_animation(ctx, max_wait_s=1.5,
                                         fallback_wait_s=1.0)
                except Exception:
                    pass
                time.sleep(0.55)
            else:
                ctx.log("niche_discover.reels_tab_missing",
                        source=source_account,
                        hint="brak zakładki Rolki — pomijam zbieranie rolek")
                return samples

        while len(samples) < want:
            if budget.user_stopped():
                break
            opened = _step(ctx, "post.open_from_grid", {
                "pool": 12,
                "baseline": baseline,
                "content_types": content_types,
                "ordered": True,
                "hold_ms": 150,
                "open_retries": 2,
            })
            if opened.status != "ok":
                reason = (opened.payload or {}).get("reason") or opened.error
                # jeden scroll w dół — może są kolejne kafelki poniżej
                if reason in ("pool_exhausted", "no_matching_kind",
                              "nothing_opened", "no_tiles"):
                    _scroll_profile_grid(ctx)
                    # NIE czyść grid_used — inaczej otworzymy te same posty od nowa
                    opened = _step(ctx, "post.open_from_grid", {
                        "pool": 12, "baseline": baseline,
                        "content_types": content_types, "ordered": True,
                        "hold_ms": 150, "open_retries": 2,
                    })
                    reason = ((opened.payload or {}).get("reason")
                              if opened.status != "ok"
                              else None)
                if opened.status != "ok":
                    empty_streak += 1
                    ctx.log("niche_discover.grid_exhausted",
                            content_kind=kind, got=len(samples), want=want,
                            reason=reason, empty_streak=empty_streak)
                    # gdy coś już zebraliśmy — po 1× wyczerpaniu kończymy
                    # (nie kręć w kółko po tych samych 6 z 8)
                    limit = 1 if samples else max_empty
                    if empty_streak >= limit:
                        break
                    continue

            # siatka postów: film też otwiera viewer — akceptuj post i reel
            want_k = ["post", "reel"] if posts_tab else ["reel"]
            if not _ensure_media_viewer(ctx, want_kinds=want_k):
                # nie wyszliśmy z profilu mimo „ok" — recover i spróbuj dalej
                ctx.log("niche_discover.grid_open_false_positive",
                        content_kind=kind)
                _return_to_profile_main(ctx, baseline)
                if posts_tab:
                    _select_profile_grid_tab(ctx, "posts")
                else:
                    _ensure_reels_tab(ctx, baseline=baseline)
                empty_streak += 1
                if empty_streak >= max_empty:
                    break
                continue

            empty_streak = 0
            got_kind = (opened.payload or {}).get("kind") or kind
            caption = ""
            image_path = None
            skip_as_dup = False
            try:
                if posts_tab:
                    # zawsze screen mediów + opis — bez rozróżniania post/film
                    image_path = _capture_post_media(
                        ctx,
                        frames_dir /
                        f"niche_{source_kind}_post_{len(samples)}.jpg")
                    caption, _ = _read_post_caption_full(ctx)
                    caption = sanitize_content_caption(caption)
                    sample_kind = "post"
                    # odcisk: caption albo hash cropa (puste opisy też dedup)
                    fp = (content_fingerprint(
                        caption=caption, author=source_account or "")
                          or media_file_fingerprint(image_path))
                    if fingerprint_matches(fp, post_fps):
                        ctx.log(
                            "niche_discover.post_skip_duplicate",
                            fp_preview=(fp or "")[:80],
                            got=len(samples), want=want,
                        )
                        skip_as_dup = True
                    elif fp:
                        post_fps.add(fp)
                else:
                    # PRZED drogim multi-frame: czy ta treść była już jako post?
                    fp, fp_preview = _light_reel_content_fp(
                        ctx, fallback_author=source_account or "")
                    if fingerprint_matches(fp, post_fps):
                        ctx.log(
                            "niche_discover.reel_skip_already_as_post",
                            fp_preview=(fp_preview or "")[:80],
                            tile_kind=got_kind,
                        )
                        skip_as_dup = True
                        sample_kind = "reel"
                    else:
                        image_path = _capture_reel_multi(
                            ctx,
                            frames_dir /
                            f"niche_{source_kind}_reel_{len(samples)}.jpg",
                            n_frames=reel_frames,
                            interval_s=reel_interval_s,
                        )
                        caption = sanitize_content_caption(
                            _read_caption_best_effort(ctx, "reel") or "")
                        sample_kind = "reel"
                        # dopisz odcisk rolki (kolejne dupy po scrollu)
                        rfp = (fp
                               or content_fingerprint(
                                   caption=caption,
                                   author=source_account or "")
                               or media_file_fingerprint(image_path))
                        if rfp:
                            post_fps.add(rfp)
            finally:
                _return_to_profile_main(ctx, baseline)
                if posts_tab:
                    _select_profile_grid_tab(ctx, "posts")
                else:
                    _ensure_reels_tab(ctx, baseline=baseline)
            if skip_as_dup:
                # po scrollu te same treści wracają z innymi center — kończymy
                dup_streak += 1
                if dup_streak >= max_dup:
                    ctx.log("niche_discover.grid_dup_stop",
                            content_kind=kind, got=len(samples), want=want,
                            dup_streak=dup_streak)
                    break
                continue
            dup_streak = 0
            samples.append({
                "index": len(samples) + 1,
                "kind": sample_kind,
                "caption": caption,
                "image_path": image_path,
                "source_kind": source_kind,
                "source_account": source_account,
                "tile_kind": got_kind,  # diagnostyka: co było na kafelku
            })
            stats["posts" if sample_kind == "post" else "reels"] += 1
            # bez tick kolejka pokazuje items=0 mimo zapisanego discovery
            try:
                budget.tick()
            except Exception:
                pass
            ctx.log("niche_discover.grid_sampled", content_kind=sample_kind,
                    tile_kind=got_kind, n=len(samples), want=want,
                    has_image=bool(image_path),
                    caption_chars=len(caption or ""),
                    items_done=getattr(budget, "items_done", None))

        if len(samples) < want:
            # mniej treści na profilu niż w ustawieniach — OK, resztę pomijamy
            ctx.log("niche_discover.grid_shortfall",
                    content_kind=kind, want=want, got=len(samples),
                    skipped=want - len(samples), source=source_account)
        return samples

    # ------------------------------------------------------------------
    def _analyze_samples(self, ctx, samples, *, mode, bio, seed, ai_cfg,
                         source_kind, source_account, username,
                         stats, stat_key) -> dict:
        """Chunki → Vision; scal partials lokalnie.

        Posty: zbiorczy composite do `batch_size` (domyślnie 4) + opisy 1..N.
        Rolki: każda osobno (batch=1) — jeden kolaż klatek + jeden opis na call.
        """
        if not samples:
            return {
                "themes": [], "audience_signals": [], "keywords": [],
                "hashtags": [], "exclusions": [], "notes": [],
                "source_kind": source_kind, "source_account": source_account,
            }
        # rolki zawsze 1:1; posty zbiorczo (panel: ai.batch_size, default 4)
        batch = _analysis_batch_size(mode, ai_cfg)
        partials = []
        vision = AiVision(account=ctx.account.raw, ctx=ctx)
        frames_dir = _frames_dir(ctx)
        for chunk_i, chunk in enumerate(_chunks(samples, batch)):
            captions = _captions_numbered(chunk)
            frames = [p for p in (_composite_or_frames(chunk, frames_dir, chunk_i)
                                  or []) if p]
            try:
                result = vision.run(mode, inputs={
                    "bio": bio or "(brak)",
                    "seed": seed or "(brak)",
                    "captions_numbered": captions or "(brak)",
                    "source_kind": source_kind,
                    "source_account": source_account,
                    "username": username or "",
                    "frames": frames,
                    "precision": ai_cfg.get("strictness") or "balanced",
                    "observed_hashtags": (
                        ", ".join(extract_hashtags_from_texts(
                            [sm.get("caption") for sm in chunk]))
                        or "(brak # w opisach tego batcha)"
                    ),
                    **_list_count_inputs(ai_cfg),
                }, opts=ai_cfg)
            except Exception as exc:
                # SSL / sieć nie mogą wyrzucić zebranego captionu
                ctx.log("niche_discover.ai_failed", mode=mode, error=str(exc)[:200])
                stats["ai_error"] = str(exc)[:120]
                result = {
                    "themes": [], "audience_signals": [], "keywords": [],
                    "hashtags": [], "exclusions": [],
                    "notes": [f"ai_error:{type(exc).__name__}"],
                    "_stub": True,
                }
            if result.get("_stub"):
                stats["stub"] = True
            # zbierz tokeny z tego batcha (posts/reels)
            _record_ai_usage(stats, result, mode=mode)
            partials.append(result)
            stats[stat_key] += 1
            ctx.log("niche_discover.ai_partial", mode=mode,
                    source_kind=source_kind, chunk=chunk_i,
                    n=len(chunk), batch=batch, stub=bool(result.get("_stub")))
        return _merge_partials(partials, source_kind, source_account)

    def _finalize_account(self, ctx, posts_partial, reels_partial, bio_text,
                          seed, existing, source_kind, source_account, username,
                          ai_cfg, stats, observed_hashtags: list | None = None
                          ) -> dict:
        vision = AiVision(account=ctx.account.raw, ctx=ctx)
        # Bez description/audience — nowa analiza ZAWSZE nadpisuje opis;
        # keywords/hashtags tylko jako kontekst (nie „zachowaj poprzedni”).
        existing_pub = {
            k: existing.get(k) for k in ("keywords", "hashtags")
            if existing.get(k) not in (None, "", [])
        }
        obs = list(observed_hashtags or [])
        bio_in = (bio_text or "").strip() or "(brak bio / nazwy w dumpu profilu)"
        try:
            # bez zrzutu profilu — bio idzie jako tekst
            result = vision.run("niche_discover_account", inputs={
                "bio": bio_in,
                "seed": seed or "(brak)",
                "existing_niche_json": json.dumps(existing_pub, ensure_ascii=False)
                                       if existing_pub else "{}",
                "posts_partial_json": json.dumps(
                    _strip_meta(posts_partial), ensure_ascii=False),
                "reels_partial_json": json.dumps(
                    _strip_meta(reels_partial), ensure_ascii=False),
                "source_kind": source_kind,
                "source_account": source_account,
                "username": username or "",
                "precision": ai_cfg.get("strictness") or "balanced",
                "frames": [],
                "observed_hashtags": (
                    ", ".join(obs) if obs else "(brak # w zebranych opisach)"
                ),
                **_list_count_inputs(ai_cfg),
            }, opts=ai_cfg)
        except Exception as exc:
            ctx.log("niche_discover.ai_account_failed", error=str(exc)[:200])
            stats["ai_error"] = str(exc)[:120]
            result = {
                "description": "", "audience": "",
                "keywords": [], "hashtags": [], "exclusions": [],
                "notes": [f"ai_error:{type(exc).__name__}"],
                "_stub": True,
            }
        if result.get("_stub"):
            stats["stub"] = True
        # observed NAJPIERW — żeby przeżyły limit w normalize_niche
        if obs:
            result["hashtags"] = list(obs) + list(result.get("hashtags") or [])
        _record_ai_usage(stats, result, mode="niche_discover_account")
        stats["ai_account"] += 1
        result["source_kind"] = source_kind
        result["source_account"] = source_account
        result["username"] = username or result.get("username") or ""
        ctx.log("niche_discover.ai_account", source_kind=source_kind,
                source_account=source_account, stub=bool(result.get("_stub")),
                bio_chars=len(bio_in), observed_hashtags=len(obs))
        return result

    def _run_merge(self, ctx, account_results, seed, ai_cfg, stats) -> dict:
        vision = AiVision(account=ctx.account.raw, ctx=ctx)
        # own na początku — model widzi priorytet kolejności + source_kind
        ordered = sorted(
            account_results,
            key=lambda r: 0 if r.get("source_kind") == "own" else 1,
        )
        payload = [{
            "source_kind": r.get("source_kind"),
            "source_account": r.get("source_account"),
            "username": r.get("username") or "",
            "audience": r.get("audience") or "",
            "description": r.get("description") or "",
            "keywords": r.get("keywords") or [],
            "hashtags": r.get("hashtags") or [],
            # bez exclusions — discovery ich nie generuje
            "reasons": r.get("reasons") or [],
        } for r in ordered]
        result = vision.run("niche_discover_merge", inputs={
            "seed": seed or "(brak)",
            "account_results_json": json.dumps(payload, ensure_ascii=False),
            "precision": ai_cfg.get("strictness") or "balanced",
            **_list_count_inputs(ai_cfg),
        }, opts=ai_cfg)
        if result.get("_stub"):
            stats["stub"] = True
        _record_ai_usage(stats, result, mode="niche_discover_merge")
        # po max_tokens / złym JSON — nie zwracaj pustki (UI pokazałby same tokeny)
        if result.get("error") or not (result.get("description") or "").strip():
            fallback = next(
                (r for r in ordered if (r.get("description") or "").strip()),
                ordered[0] if ordered else {},
            )
            ctx.log("niche_discover.ai_merge_fallback",
                    reason=result.get("error") or "empty_description",
                    repaired=bool(result.get("_json_repaired")),
                    fallback_source=fallback.get("source_account") or "")
            usage = result.get("_usage")
            result = dict(fallback)
            if usage:
                result["_usage"] = usage
            result["_merge_fallback"] = True
        elif result.get("_json_repaired"):
            ctx.log("niche_discover.ai_merge_repaired", n=len(payload))
        ctx.log("niche_discover.ai_merge", n=len(payload),
                stub=bool(result.get("_stub")),
                repaired=bool(result.get("_json_repaired")),
                fallback=bool(result.get("_merge_fallback")))
        return result

    def _finalize_text_only(self, ctx, s, seed, existing, stats) -> dict | None:
        ai_cfg = dict(s.get("ai") or {})
        return self._finalize_account(
            ctx, {}, {}, "(brak bio — tryb text-only)", seed, existing,
            "own", "own", "", ai_cfg, stats,
        )

    def _save(self, ctx, s, final, existing, write_cfg, stats) -> None:
        ai_cfg = dict(s.get("ai") or {})
        # append = dopisz discovered; replace = nadpisz tylko discovered (manual OK)
        merge_mode = str(
            write_cfg.get("discovered_merge") or "append"
        ).strip().lower()
        if merge_mode not in ("append", "replace"):
            merge_mode = "append"
        # description/audience z analizy; keywords/hashtags → discovered (+ merge)
        # exclusions zawsze z existing; limity = dokładne liczby z panelu
        niche = normalize_niche(
            final, existing=existing,
            keywords_limit=_exact_list_count(ai_cfg, "keywords_count"),
            hashtags_limit=_exact_list_count(ai_cfg, "hashtags_count"),
            discovered_merge=merge_mode,
        )
        # obrona: pusty wynik AI (parse fail) nie może wyzerować dobrej niszy
        content_empty = (
            not (niche.get("description") or "").strip()
            and not (niche.get("keywords") or [])
            and not (niche.get("hashtags") or [])
        )
        if content_empty and (
            (existing.get("description") or "").strip()
            or (existing.get("keywords") or [])
            or (existing.get("hashtags") or [])
        ):
            ctx.log("niche_discover.save_keep_existing",
                    reason="empty_ai_result",
                    kept_description=(existing.get("description") or "")[:60])
            niche["description"] = str(existing.get("description") or "").strip()
            niche["audience"] = str(existing.get("audience") or "").strip()
            # zachowaj źródła + scaloną listę z existing (legacy → ensure)
            src = store.ensure_niche_phrase_sources(existing)
            niche.update(src)
            niche["keywords"] = store.merge_niche_phrases(
                src["keywords_manual"], src["keywords_discovered"])
            niche["hashtags"] = store.merge_niche_phrases(
                src["hashtags_manual"], src["hashtags_discovered"],
                force_hash=True)
        niche["accepted"] = False
        niche["discovered_at"] = datetime.now().isoformat(timespec="seconds")
        # tokeny z całego przebiegu — panel Nisza pokazuje po odświeżeniu configu
        usage = _finalize_usage(stats)
        niche["last_ai_usage"] = usage
        niche["last_ai_model"] = usage.get("model") or ""
        niche["last_ai_tokens_in"] = int(usage.get("tokens_in") or 0)
        niche["last_ai_tokens_out"] = int(usage.get("tokens_out") or 0)
        store.write_niche(ctx.account.account_id, niche)
        stats["saved"] = True
        ctx.log("niche_discover.saved",
                description=(niche.get("description") or "")[:80],
                keywords=len(niche.get("keywords") or []),
                hashtags=len(niche.get("hashtags") or []),
                keywords_manual=len(niche.get("keywords_manual") or []),
                keywords_discovered=len(niche.get("keywords_discovered") or []),
                hashtags_manual=len(niche.get("hashtags_manual") or []),
                hashtags_discovered=len(niche.get("hashtags_discovered") or []),
                discovered_merge=merge_mode,
                want_keywords=_exact_list_count(ai_cfg, "keywords_count"),
                want_hashtags=_exact_list_count(ai_cfg, "hashtags_count"),
                model=niche["last_ai_model"],
                tokens_in=niche["last_ai_tokens_in"],
                tokens_out=niche["last_ai_tokens_out"])
        if write_cfg.get("sync_content_search_phrases"):
            # append-only do content_search: scalona lista (manual + discovered)
            _sync_phrases(
                ctx.account.account_id,
                niche.get("keywords") or [],
                niche.get("hashtags") or [],
            )

    def _ensure_own_profile(self, ctx) -> bool:
        """Wejdź na własny profil (profile_self) — ścieżka jak w stats_snapshot."""
        ok = ctx.navigator.ensure(PROFILE_SELF, max_steps=5)
        if not ok:
            ctx.log("niche_discover.own_profile_failed",
                    top=ctx.navigator.identify().top)
        return ok

    def _still_on_profile(self, ctx) -> bool:
        top = ctx.navigator.identify().top
        if top in (PROFILE_OTHER, PROFILE_SELF):
            return True
        # po capturze/gridzie czasem spada nawigacja — dociągnij
        return ctx.navigator.ensure(PROFILE_OTHER, max_steps=3)

    def _open_competitor(self, ctx, username: str) -> bool:
        """Wyszukaj konkurencję istniejącymi krokami search.* (jak profile_interact).

        Query BEZ @ (sam nick). submit=False → lista sugestii, potem pick_result
        wybiera dokładny nick i robi ensure(profile_other).

        Po type_query bywa dialog IME — lista userów pojawia się z opóźnieniem,
        więc czekamy i retry'ujemy pick_result zamiast od razu kończyć empty.
        """
        user = username.lstrip("@").strip()
        # Bez @ — na tym IG lista Konta lepiej trafia po samym nicku;
        # @ w polu bywa no-op / psuje sugestie.
        queries = [user]
        for qi, query in enumerate(queries):
            if qi > 0:
                ctx.navigator.ensure(HOME, max_steps=4)
                ctx.log("niche_discover.competitor_retry_query",
                        user=user, query=query)
            res = _step(ctx, "search.open", {})
            if res.status == "failed":
                ctx.log("niche_discover.competitor_step_failed",
                        user=user, step="search.open", error=res.error)
                continue
            typed = _step(ctx, "search.type_query", {
                "query": query,
                "clear_first": True,
                "submit": False,
            })
            if typed.status == "failed":
                ctx.log("niche_discover.competitor_step_failed",
                        user=user, step="search.type_query", error=typed.error)
                continue
            ctx.log("niche_discover.competitor_step_ok",
                    user=user, step="search.type_query",
                    **(typed.payload or {}))
            # daj IG czas na dociągnięcie sugestii po ewentualnym dialogu IME
            try:
                ctx.device.wait_for_stable_ui(max_wait_s=2.0)
            except Exception:
                pass
            time.sleep(1.2)
            picked = None
            for attempt in range(1, 4):
                picked = _step(ctx, "search.pick_result", {
                    "username": user,
                    "prefer_exact": True,
                })
                if picked.status == "ok":
                    break
                ctx.log("niche_discover.competitor_pick_retry",
                        user=user, attempt=attempt, error=picked.error)
                time.sleep(1.0)
            if picked is None or picked.status == "failed":
                ctx.log("niche_discover.competitor_step_failed",
                        user=user, step="search.pick_result",
                        error=(picked.error if picked else "no_attempt"))
                continue
            ctx.log("niche_discover.competitor_step_ok",
                    user=user, step="search.pick_result",
                    **(picked.payload or {}))
            top = ctx.navigator.identify().top
            if top in (PROFILE_OTHER, PROFILE_SELF):
                return True
            ctx.log("niche_discover.competitor_not_on_profile",
                    user=user, top=top)
        ctx.navigator.ensure(HOME, max_steps=4)
        return False


# ============================================================
# Normalizacja / helpers (eksportowane do testów)
# ============================================================
def _record_ai_usage(stats: dict, result: dict | None, *, mode: str) -> None:
    """Dopisz usage z jednego Vision.call do stats['ai_usage']."""
    if "ai_usage" not in stats or not isinstance(stats.get("ai_usage"), dict):
        stats["ai_usage"] = empty_usage_totals()
    accumulate_usage(stats["ai_usage"], result, mode=mode)


def _finalize_usage(stats: dict) -> dict:
    """Uporządkuj ai_usage przed zapisem / logiem (kopia)."""
    raw = stats.get("ai_usage") if isinstance(stats.get("ai_usage"), dict) \
        else empty_usage_totals()
    models = list(raw.get("models") or [])
    model = raw.get("model") or ""
    if not model and models:
        model = models[0] if len(models) == 1 else "+".join(models)
    out = {
        "model": model,
        "models": models,
        "tokens_in": int(raw.get("tokens_in") or 0),
        "tokens_out": int(raw.get("tokens_out") or 0),
        "calls": int(raw.get("calls") or 0),
        "usage_missing": bool(raw.get("usage_missing")),
        "by_mode": dict(raw.get("by_mode") or {}),
        "by_model": dict(raw.get("by_model") or {}),
        "cost_usd": float(raw.get("cost_usd") or 0),
        "cost_unknown": bool(raw.get("cost_unknown")),
    }
    stats["ai_usage"] = out
    # płaskie klucze w stats PluginResult — wygodne w logu / panelu events
    stats["tokens_in"] = out["tokens_in"]
    stats["tokens_out"] = out["tokens_out"]
    stats["ai_model"] = out["model"]
    return out


def _log_usage_summary(ctx, stats: dict) -> None:
    """Jedna linia w logu + stdout po analizie."""
    usage = _finalize_usage(stats)
    ctx.log("niche_discover.ai_usage", **usage)
    print(
        f"\n===== AI USAGE (niche_discover) =====\n"
        f"model: {usage.get('model') or '(brak)'}\n"
        f"tokens_in:  {usage.get('tokens_in', 0)}\n"
        f"tokens_out: {usage.get('tokens_out', 0)}\n"
        f"calls:      {usage.get('calls', 0)}\n"
        f"cost_usd:   {usage.get('cost_usd', 0)}\n"
        f"===== END AI USAGE =====\n",
        flush=True,
    )


def _stats_for_log(stats: dict) -> dict:
    """Stats do ctx.log — bez zagnieżdżonego by_mode jeśli za duże; zostaw sumy."""
    out = dict(stats)
    usage = out.get("ai_usage")
    if isinstance(usage, dict):
        # pełny breakdown zostaje w stats PluginResult; w done wystarczy skrót
        out["ai_usage"] = {
            "model": usage.get("model"),
            "tokens_in": usage.get("tokens_in"),
            "tokens_out": usage.get("tokens_out"),
            "calls": usage.get("calls"),
            "cost_usd": usage.get("cost_usd"),
            "cost_unknown": usage.get("cost_unknown"),
            "usage_missing": usage.get("usage_missing"),
            "by_mode": usage.get("by_mode") or {},
            "by_model": usage.get("by_model") or {},
        }
    return out


def _analysis_batch_size(mode: str, ai_cfg: dict | None) -> int:
    """Ile sample'i na jedno wywołanie Vision.

    Posty: zbiorczy composite (`ai.batch_size`, default 4).
    Rolki: zawsze 1 (każda osobno).
    """
    if mode == "niche_discover_reels":
        return 1
    return max(1, int((ai_cfg or {}).get("batch_size") or 4))


def _exact_list_count(ai_cfg: dict | None, key: str, *, default: int = 8) -> int:
    """Dokładna liczba keywords/hashtags z panelu (1..30)."""
    try:
        n = int((ai_cfg or {}).get(key) or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(30, n))


def _list_count_inputs(ai_cfg: dict | None) -> dict:
    """Placeholdery do promptów Vision — dokładne liczby list."""
    kw = _exact_list_count(ai_cfg, "keywords_count")
    ht = _exact_list_count(ai_cfg, "hashtags_count")
    return {
        "keywords_count": str(kw),
        "hashtags_count": str(ht),
        # hint wstrzykiwany do YAML — egzekwuje krótkie keywords + exact counts
        "list_counts_hint": (
            f"- Zwróć DOKŁADNIE {kw} keywords oraz DOKŁADNIE {ht} hashtags.\n"
            f"- KEYWORDS: krótkie frazy 1–3 słowa (lepiej 1 określenie), BEZ # "
            f"(np. \"laminacja rzęs\", \"strategia reels\", \"stylizacja outfitów\"). "
            f"Jeden temat = jeden keyword — NIE sklejaj dwóch tematów przez "
            f"\"i\" / \"and\" / \"&\". "
            f"ŹLE: \"makeup tutorial i poradniki\", \"fashion inspiration i styling tips\". "
            f"Hashtagi to tagi IG Z # (np. #jobsearch, #aitools) — zwykle bez spacji.\n"
            f"- KEYWORDS != HASHTAGS: NIE kopiuj keywordu jako hashtagu przez doklejenie # "
            f"(zakaz: keyword \"jobsearch\" + hashtag \"#jobsearch\" jako ta sama treść).\n"
            f"- Najpierw wyciągnij realne # z opisów treści; potem DOPEŁNIJ brakujące "
            f"hashtagi i keywords, które pasują do niszy, ale nie wystąpiły w materiale."
        ),
    }


def extract_hashtags_from_texts(texts) -> list[str]:
    """Unikalne # z captionów (kolejność pierwszego wystąpienia)."""
    out, seen = [], set()
    for raw in texts or []:
        for m in _HASHTAG_RE.finditer(str(raw or "")):
            tag = "#" + m.group(1)
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(tag)
    return out


def _keyword_mirrors_hashtag(keyword: str, hash_stems: set[str]) -> bool:
    """True gdy keyword to tylko forma hashtagu (ze spacjami / bez #)."""
    stem = re.sub(r"[\s_\-]+", "", (keyword or "").lstrip("#")).casefold()
    return bool(stem) and stem in hash_stems


# Łączniki sklejające dwa tematy w jednym keywordzie (rozdzielamy na osobne)
_KEYWORD_JOIN_RE = re.compile(r"\s+(?:i|and|&)\s+", re.IGNORECASE)
# Max słów w jednym keywordzie po normalizacji
_MAX_KEYWORD_WORDS = 3


def _expand_short_keywords(items) -> list[str]:
    """Rozdziel keywords sklejone „i/and/&” i przytnij do max 3 słów.

    Zachowuje UTF-8 / polskie znaki; nie rusza hashtagów (tylko keywords).
    """
    out = []
    for x in items or []:
        s = str(x).strip().lstrip("#").strip()
        if not s:
            continue
        # jeden temat = jeden keyword — rozdziel przy łącznikach
        for part in _KEYWORD_JOIN_RE.split(s):
            part = part.strip(" ,;-/")
            if not part:
                continue
            words = part.split()
            if len(words) > _MAX_KEYWORD_WORDS:
                # za długie → pierwsze 3 słowa (bez psucia polskich znaków)
                part = " ".join(words[:_MAX_KEYWORD_WORDS])
            out.append(part)
    return out


def normalize_niche(raw: dict, *, existing: dict | None = None,
                    limit: int = LIST_LIMIT,
                    keywords_limit: int | None = None,
                    hashtags_limit: int | None = None,
                    discovered_merge: str = "replace") -> dict:
    """Trim, # na hashtagach, dedup, limity; keywords ≠ lustrzane hashtagi.

    description/audience — zawsze z nowej analizy (`raw`).
    Nowe keywords/hashtags z AI → `*_discovered` (append|replace);
    `*_manual` bez zmian. Scalone `keywords`/`hashtags` = manual + discovered.
    Domyślny `discovered_merge=replace` zachowuje stare zachowanie testów
    (płaska lista = wynik AI); `_save` podaje tryb z panelu (domyślnie append).
    Wykluczenia NIE pochodzą z AI — zawsze zostają ręczne z `existing`.
    Keywords: krótkie 1–3 słowa; sklejone „i/and/&” rozdzielane na osobne.
    """
    existing = existing or {}
    desc = str(raw.get("description") or "").strip()
    audience = str(raw.get("audience") or "").strip()
    kw_lim = keywords_limit if keywords_limit is not None else limit
    ht_lim = hashtags_limit if hashtags_limit is not None else limit

    # keywords: zdejmij #, rozdziel sklejone tematy, przytnij długość
    raw_kw = _expand_short_keywords(raw.get("keywords") or [])
    # hashtagi: wymuś #
    raw_ht = []
    for x in raw.get("hashtags") or []:
        s = str(x).strip()
        if not s:
            continue
        raw_ht.append("#" + s.lstrip("#"))

    # najpierw znormalizuj wynik AI (limity + lustro), potem wgraj do źródeł
    ai_hashtags = _dedup_strs(raw_ht, limit=ht_lim)
    hash_stems = {
        re.sub(r"[\s_\-]+", "", h.lstrip("#")).casefold() for h in ai_hashtags
    }
    filtered_kw = [
        k for k in raw_kw if not _keyword_mirrors_hashtag(k, hash_stems)
    ]
    ai_keywords = _dedup_strs(filtered_kw, limit=kw_lim)

    phrases = store.apply_discovered_phrases(
        existing, ai_keywords, ai_hashtags, mode=discovered_merge,
    )

    exclusions = _dedup_strs(existing.get("exclusions"), limit=limit)
    return {
        "description": desc,
        "audience": audience,
        "keywords": phrases["keywords"],
        "hashtags": phrases["hashtags"],
        "keywords_manual": phrases["keywords_manual"],
        "keywords_discovered": phrases["keywords_discovered"],
        "hashtags_manual": phrases["hashtags_manual"],
        "hashtags_discovered": phrases["hashtags_discovered"],
        "exclusions": exclusions,
    }


def _dedup_strs(items, *, limit: int) -> list[str]:
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
        if len(out) >= limit:
            break
    return out


def _captions_numbered(samples: list[dict]) -> str:
    """Opisy 1..N — tekst do promptu Vision (obok siatki zdjęć)."""
    lines = []
    for i, sm in enumerate(samples, 1):
        cap = (sm.get("caption") or "").strip() or "(brak opisu)"
        lines.append(f"{i}. {cap}")
    return "\n\n".join(lines)


# Minimalna długość znormalizowanego captionu, by uznać fingerprint za wiarygodny
_FP_CAP_MIN = 12
# Prefiks captionu w odcisku (post bywa dłuższy niż lekki dump rolki)
_FP_CAP_LEN = 96


def _norm_fp_caption(text: str) -> str:
    """Normalizacja captionu pod stabilny fingerprint post↔reel."""
    t = (text or "").strip().casefold()
    t = re.sub(r"\s+", " ", t)
    # skróty UI IG na końcu opisu
    t = re.sub(r"\s*[.…]+?\s*(więcej|more)\s*$", "", t, flags=re.I)
    return t.strip()


def content_fingerprint(*, caption: str = "", author: str = "",
                        likes=None) -> str | None:
    """Stabilny odcisk treści między viewerem posta a rolki.

    Główny sygnał: znormalizowany początek captionu. Bez sensownego captionu
    — słabszy fallback author+likes (tylko gdy caption pusty/krótki).
    `None` = za mało sygnału; nie wolno wtedy skipować (unikamy false positive).
    """
    cap = _norm_fp_caption(caption)[:_FP_CAP_LEN]
    au = _norm_fp_caption(author).lstrip("@")
    if len(cap) >= _FP_CAP_MIN:
        return f"cap:{cap}"
    if au and likes is not None:
        return f"au:{au}|lk:{likes}|cap:{cap}"
    return None


def fingerprint_matches(candidate: str | None, seen: set[str] | None) -> bool:
    """True gdy candidate to ta sama treść co któryś odcisk z postów.

    Dopuszcza prefix-match na `cap:…` — lekki dump rolki często ma krótszy
    fragment niż pełny caption z viewera posta.
    """
    if not candidate or not seen:
        return False
    if candidate in seen:
        return True
    if not candidate.startswith("cap:"):
        return False
    cbody = candidate[4:]
    for s in seen:
        if not s.startswith("cap:"):
            continue
        sbody = s[4:]
        n = min(len(cbody), len(sbody))
        if n >= _FP_CAP_MIN and cbody[:n] == sbody[:n]:
            return True
    return False


def _light_reel_content_fp(ctx, *, fallback_author: str = ""
                           ) -> tuple[str | None, str]:
    """Lekki fingerprint rolki BEZ multi-frame i bez pełnego guarded caption.

    Celowo BEZ expandu — tylko do dedupu / porównania treści.
    Finalny caption w samples idzie wyłącznie przez `_read_reel_caption_guarded`.

    Kolejność: meta (_reel_meta) → dump CAPTION_IDS → stan panelu.
    Zwraca (fingerprint|None, krótki preview do logu).
    """
    from v2.steps.reels import _reel_meta

    meta: dict = {}
    try:
        meta = _reel_meta.read_current_reel(ctx.device) or {}
    except Exception:
        meta = {}
    caption = (meta.get("caption") or "").strip()
    author = (meta.get("author") or fallback_author or "").strip()
    likes = meta.get("likes")

    if not caption:
        try:
            caption = _dump_caption_text(ctx, strict=True) or ""
        except Exception:
            caption = ""
    if not caption:
        try:
            st = _reel_caption_state(ctx, expected_author=author or None)
            caption = (st.get("caption") or "").strip()
            if not author:
                author = (st.get("author") or "").strip()
        except Exception:
            pass

    fp = content_fingerprint(caption=caption, author=author, likes=likes)
    preview = (fp or f"empty:au={author[:24]}:lk={likes}")[:80]
    return fp, preview


def _merge_partials(partials: list[dict], source_kind: str,
                    source_account: str) -> dict:
    if not partials:
        return {"themes": [], "audience_signals": [], "keywords": [],
                "hashtags": [], "notes": [],
                "source_kind": source_kind, "source_account": source_account}
    if len(partials) == 1:
        p = dict(partials[0])
        p["source_kind"] = source_kind
        p["source_account"] = source_account
        p.pop("exclusions", None)
        return p
    out = {
        "themes": [], "audience_signals": [], "keywords": [],
        "hashtags": [], "notes": [],
        "source_kind": source_kind, "source_account": source_account,
    }
    for p in partials:
        for k in ("themes", "audience_signals", "keywords",
                  "hashtags", "notes"):
            out[k].extend(p.get(k) or [])
    for k in ("themes", "audience_signals", "keywords",
              "hashtags", "notes"):
        out[k] = _dedup_strs(out[k], limit=LIST_LIMIT)
    return out


def _strip_meta(d: dict) -> dict:
    return {k: v for k, v in (d or {}).items()
            if not str(k).startswith("_")}


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _frames_dir(ctx) -> Path:
    d = ctx.events.file_path.parent / "frames" / ctx.session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _collect_dir(ctx) -> Path:
    """Katalog JSON zebranymi packami (faza A) — frames/<session>/collect/."""
    d = _frames_dir(ctx) / "collect"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _collect_slug(source_kind: str, source_account: str, username: str) -> str:
    """Bezpieczna nazwa pliku: own.json / competitor_apply4me_app.json."""
    if source_kind == "own":
        return "own"
    raw = (username or source_account or "user").lstrip("@").strip()
    slug = re.sub(r"[^\w.\-]+", "_", raw, flags=re.UNICODE).strip("._") or "user"
    return f"competitor_{slug[:60]}"


def _samples_for_json(samples: list[dict]) -> list[dict]:
    """Uprość sample do JSON (ścieżki + caption; bez obiektów runtime)."""
    out = []
    for sm in samples or []:
        out.append({
            "index": sm.get("index"),
            "kind": sm.get("kind"),
            "caption": sm.get("caption") or "",
            "image_path": sm.get("image_path") or "",
            "source_kind": sm.get("source_kind"),
            "source_account": sm.get("source_account"),
            "tile_kind": sm.get("tile_kind"),
        })
    return out


def _save_collect_pack(ctx, pack: dict) -> str | None:
    """Zapisz pack jednego konta od razu po zebraniu (faza A)."""
    try:
        slug = _collect_slug(
            pack.get("source_kind") or "",
            pack.get("source_account") or "",
            pack.get("username") or "",
        )
        path = _collect_dir(ctx) / f"{slug}.json"
        # bez json_path w pliku (unika cykli / śmieci)
        payload = {k: v for k, v in pack.items() if k != "json_path"}
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)
    except Exception as exc:
        ctx.log("niche_discover.collect_save_failed", error=str(exc)[:120])
        return None


def _write_collect_index(ctx, packs: list[dict]) -> None:
    """Indeks wszystkich zebranych kont w sesji (po fazie A)."""
    try:
        path = _collect_dir(ctx) / "index.json"
        rows = []
        for p in packs or []:
            rows.append({
                "source_kind": p.get("source_kind"),
                "source_account": p.get("source_account"),
                "username": p.get("username"),
                "json_path": p.get("json_path"),
                "posts": len(p.get("posts") or []),
                "reels": len(p.get("reels") or []),
                "profile_image": p.get("profile_image") or "",
                "bio": (p.get("bio") or "")[:120],
                "bio_chars": len(p.get("bio") or ""),
                "collected_at": p.get("collected_at"),
            })
        path.write_text(
            json.dumps({"packs": rows, "n": len(rows)},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ctx.log("niche_discover.collect_index", path=str(path), n=len(rows))
    except Exception as exc:
        ctx.log("niche_discover.collect_index_failed", error=str(exc)[:120])


def _format_profile_bio_for_ai(pack: dict) -> str:
    """Tekst bio/nazwy do promptów Vision (bez obrazu profilu)."""
    lines = []
    un = (pack.get("username") or "").strip()
    fn = (pack.get("full_name") or "").strip()
    bio = (pack.get("bio") or "").strip()
    if un:
        lines.append(f"username: @{un.lstrip('@')}")
    if fn:
        lines.append(f"nazwa: {fn}")
    if bio:
        lines.append(f"bio:\n{bio}")
    return "\n".join(lines) if lines else "(brak bio / nazwy w dumpu profilu)"


def _read_profile_bio_full(ctx) -> dict:
    """Dump nazwy + bio z hierarchii — BEZ zrzutu ekranu.

    Kolejność:
    1) dociągnij nagłówek do góry
    2) odczyt baseline (chars_before)
    3) jeśli widać „więcej"/ucięte bio → tap expand (steps/_expand_more),
       poczekaj; powtórz gdy nadal `_bio_looks_truncated`
    4) gdy przycisk siedzi w samym TextView bio — tap w tekst
    5) dopiero potem zapis profile_bio.txt / zwrot do JSON
    """
    from v2.steps._expand_more import expand_more_if_present

    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
        ctx.ht.swipe(w // 2, int(h * 0.35), w // 2, int(h * 0.70),
                     duration_ms=280, pause_kind="tap")
        time.sleep(0.55)
    except Exception:
        pass

    # baseline przed expandem — do logu chars_before / chars_after
    header = read_profile_header(ctx.device)
    bio_before = (header.get("bio") or "").strip()
    chars_before = len(bio_before)
    expanded = False

    # max 3 próby: przycisk „więcej" ALBO tap w ucięty tekst bio (Compose)
    for _ in range(3):
        bio_now = (header.get("bio") or "").strip()
        tapped = expand_more_if_present(ctx)
        if not tapped and _bio_looks_truncated(bio_now):
            # „więcej" bywa wbudowane w TextView bio bez osobnego Button
            tapped = _tap_profile_bio_text(ctx)
        if not tapped:
            break
        expanded = True
        time.sleep(0.45)
        header = read_profile_header(ctx.device)
        bio_now = (header.get("bio") or "").strip()
        if not _bio_looks_truncated(bio_now):
            break

    bio = (header.get("bio") or "").strip()
    ctx.log(
        "niche_discover.profile_bio_expand",
        expanded=expanded,
        chars_before=chars_before,
        chars_after=len(bio),
        bio_source=header.get("bio_source") or "",
        still_truncated=_bio_looks_truncated(bio),
    )

    # sidecar tekstowy obok frames (debug / audyt) — dopiero po expand
    try:
        path = _frames_dir(ctx) / "profile_bio.txt"
        blob = _format_profile_bio_for_ai({
            "username": header.get("username") or "",
            "full_name": header.get("full_name") or "",
            "bio": bio,
        })
        path.write_text(blob, encoding="utf-8")
        ctx.log("niche_discover.profile_bio_txt", path=str(path),
                chars=len(bio), source=header.get("bio_source") or "")
    except Exception as exc:
        ctx.log("niche_discover.profile_bio_txt_failed", error=str(exc)[:120])

    return header


def _tap_profile_bio_text(ctx) -> bool:
    """Tap w TextView bio gdy expand „więcej” siedzi w samym tekście.

    Szuka `profile_header_bio_text` albo uciętego tekstu w górnej części
    ekranu (MetaComposeView). Nie rusza UFI / „obejrzyj więcej rolek”.
    """
    from v2.steps._expand_more import MORE_BLOCKLIST, UFI_MORE_RID_MARKERS, _center

    try:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    except Exception:
        return False

    # próg Y — bio siedzi w nagłówku, nie w feedzie / UFI na dole
    max_y = 1400
    try:
        max_y = int(ctx.account.device["_hardware"]["resolution"][1] * 0.62)
    except Exception:
        pass

    bio_rid = []
    truncated_upper = []
    for n in flat:
        rid = (n.get("resource-id") or "").lower()
        if any(m in rid for m in UFI_MORE_RID_MARKERS):
            continue
        text = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()
        blob = f"{text} {desc}".casefold()
        if any(b in blob for b in MORE_BLOCKLIST):
            continue
        if not text:
            continue
        if "profile_header_bio" in rid:
            bio_rid.append(n)
            continue
        if not _bio_looks_truncated(text):
            continue
        # Compose: ucięte bio zwykle w górnej części profilu
        c = _center(n.get("bounds"))
        if c and c[1] < max_y:
            truncated_upper.append(n)

    target = bio_rid[0] if bio_rid else (
        max(truncated_upper, key=lambda n: len(n.get("text") or ""))
        if truncated_upper else None
    )
    if not target:
        return False
    c = _center(target.get("bounds"))
    if not c:
        return False
    ctx.ht.tap(*c, pause_kind="tap")
    ctx.log(
        "niche_discover.profile_bio_tap_text",
        preview=(target.get("text") or "")[:60],
        rid=(target.get("resource-id") or "")[:60],
    )
    return True


def _profile_grid_visible(ctx) -> bool:
    """True gdy w hierarchy widać zakładki profilu + ≥1 kafelek z bounds.

    Sygnał „siatka już w kadrze” — wtedy nie robimy scrolla przed 1. otwarciem.
    """
    from v2.steps.post import _post

    try:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    except Exception:
        return False
    # tabs (Siatka / Rolki / …) muszą być w drzewie
    if not _list_profile_tab_icons(flat):
        return False
    # choć jeden kafelek z niepustymi bounds = da się tapnąć od góry
    for t in _post.collect_tiles(flat):
        b = parse_bounds(t.get("bounds") or "")
        if not b:
            continue
        x1, y1, x2, y2 = b
        if x2 > x1 and y2 > y1:
            return True
    return False


def _reveal_profile_grid(ctx) -> None:
    """Po dumpie bio: zjedź w dół TYLKO gdy siatki/kafelków jeszcze nie widać.

    Gdy tabs + kafelki już w kadrze — zero swipe (zaczynamy od 1. kafelka).
    """
    if _profile_grid_visible(ctx):
        ctx.log("niche_discover.grid_already_visible", scroll=False)
        return
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        return
    # swipe w górę = treść w dół → odkrywa kafelki pod bio/headerem
    ctx.ht.swipe(w // 2, int(h * 0.78), w // 2, int(h * 0.42),
                 duration_ms=320, pause_kind="tap")
    time.sleep(0.5)
    ctx.log("niche_discover.grid_revealed", scroll=True)


def _ensure_media_viewer(ctx, want_kinds: list[str] | None = None) -> bool:
    """True tylko gdy faktycznie jesteśmy w viewerze posta/rolki (nie na siatce)."""
    from v2.steps.post import _post
    try:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    except Exception:
        return False
    ok = _post.media_viewer_open(flat, want_kinds)
    ctx.log("niche_discover.viewer_check", ok=ok,
            want=want_kinds or [],
            still_grid=_post.still_on_profile_grid(flat),
            is_post=_post.is_post_viewer(flat),
            is_reel=_post.is_reel(flat))
    return ok


def _bgr_to_png_bytes(frame) -> bytes | None:
    """BGR ndarray → PNG w RAM (do crop/fit bez zapisu pośredniego)."""
    try:
        import cv2
        ok, buf = cv2.imencode(".png", frame)
        if not ok:
            return None
        return buf.tobytes()
    except Exception:
        return None


def _scale_crop_to_image(
        crop: tuple[int, int, int, int],
        device_wh: tuple[int, int],
        image_wh: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Bounds z hierarchii (rozdzielczość telefonu) → piksele kadru scrcpy."""
    dw, dh = device_wh
    iw, ih = image_wh
    if dw < 1 or dh < 1 or iw < 1 or ih < 1:
        return crop
    # scrcpy skaluje proporcjonalnie — sx≈sy, liczymy osobno dla pewności
    sx, sy = iw / dw, ih / dh
    x1, y1, x2, y2 = crop
    return (
        int(round(x1 * sx)), int(round(y1 * sy)),
        int(round(x2 * sx)), int(round(y2 * sy)),
    )


def _capture_screen_png(ctx) -> tuple[bytes | None, str]:
    """Pełny kadr jako PNG. Preferuje żywy scrcpy; fallback: screenshot HTTP.

    Zwraca `(bytes|None, source)` — source to `'scrcpy'` albo `'screenshot'`.
    """
    # 1) żywy stream H.264 / mjpeg z FrameBuffer
    try:
        fb = getattr(ctx, "fb", None)
        if fb is not None and fb.is_live():
            frame = fb.get_frame()
            if frame is not None:
                raw = _bgr_to_png_bytes(frame)
                if raw:
                    ctx.log(
                        "niche_discover.frame_source",
                        source="scrcpy",
                        h=int(frame.shape[0]),
                        w=int(frame.shape[1]),
                    )
                    return raw, "scrcpy"
    except Exception as exc:
        ctx.log("niche_discover.scrcpy_frame_failed", error=str(exc)[:120])

    # 2) fallback HTTP uiautodev — tylko gdy scrcpy nie żyje
    try:
        png = ctx.device.screenshot()
        ctx.log("niche_discover.frame_source", source="screenshot")
        return png, "screenshot"
    except Exception as exc:
        ctx.log("niche_discover.screenshot_failed", error=str(exc)[:120])
        return None, "screenshot"


def _screenshot_to(ctx, path: Path, *, fit_ai: bool = True) -> str | None:
    """Zrzut → JPEG (scrcpy preferowane). Fit do pudełka AI bez upscale."""
    try:
        data, source = _capture_screen_png(ctx)
        if not data:
            return None
        path = Path(path)
        if fit_ai:
            # max_upscale=1.0 — źródło już +25% przez scrcpy 900; nie dubluj
            built = fit_jpeg_box(
                data, path, max_w=AI_FRAME_MAX_W, max_h=AI_FRAME_MAX_H,
                max_upscale=1.0)
            if built:
                ctx.log("niche_discover.screenshot_saved",
                        source=source, path=built)
                return built
        # bez fit: zapisz JPEG (PNG z scrcpy nie może iść jako „.jpg" surowy)
        built = fit_jpeg_box(
            data, path, max_w=10_000, max_h=10_000, max_upscale=1.0)
        if built:
            ctx.log("niche_discover.screenshot_saved",
                    source=source, path=built)
            return built
        return None
    except Exception as exc:
        ctx.log("niche_discover.screenshot_failed", error=str(exc))
        return None


def _finalize_saved_frame(path: Path | None) -> str | None:
    """Przeskaluj już zapisany JPEG do pudełka AI (in-place, bez upscale)."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return str(path)
    built = fit_jpeg_box(
        p, p, max_w=AI_FRAME_MAX_W, max_h=AI_FRAME_MAX_H, max_upscale=1.0)
    return built or str(path)


def _grab_frame_bgr(ctx):
    """Jedna klatka BGR: żywy scrcpy, inaczej świeży screenshot HTTP."""
    try:
        fb = getattr(ctx, "fb", None)
        if fb is not None and fb.is_live():
            frame = fb.get_frame()
            if frame is not None:
                ctx.log(
                    "niche_discover.frame_source",
                    source="scrcpy",
                    h=int(frame.shape[0]),
                    w=int(frame.shape[1]),
                )
                return frame
        import cv2
        import numpy as np
        png = ctx.device.screenshot()
        frame = cv2.imdecode(
            np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is not None:
            ctx.log(
                "niche_discover.frame_source",
                source="screenshot",
                h=int(frame.shape[0]),
                w=int(frame.shape[1]),
            )
        return frame
    except Exception as exc:
        ctx.log("niche_discover.frame_grab_failed", error=str(exc)[:120])
        return None


def _capture_reel_multi(ctx, out_path: Path, *, n_frames: int = 4,
                        interval_s: float = 1.0) -> str | None:
    """N klatek z rolki (N≥1, odstęp ≥1s) → jeden ponumerowany kolaż.

    Preferuje scrcpy (`_grab_frame_bgr`); bez streamu pada na screenshot HTTP.
    1 klatka = 1 kafelek; 2+ w siatce 2-kolumnowej (pad tylko do pełnego rzędu).
    """
    # bez wymuszania ≥4 — user może wybrać 1–N w panelu
    n = max(1, int(n_frames))
    gap = max(1.0, float(interval_s))
    out_path = Path(out_path)
    frames = []
    for i in range(n):
        if i > 0:
            time.sleep(gap)
        frame = _grab_frame_bgr(ctx)
        if frame is not None:
            frames.append(frame)
        ctx.log("niche_discover.reel_frame", i=i + 1, of=n,
                ok=frame is not None)
    if not frames:
        ctx.log("niche_discover.reel_frames_empty")
        return _screenshot_to(ctx, out_path)
    # thumb 400 (=320×1.25) → kolaż ~800 szer. przy 2 kolumnach;
    # 1 klatka: cols dobierze build_composite_grid (bez pustego kafelka)
    built = build_composite_grid(
        frames, out_path, cols=2, thumb_w=COMPOSITE_THUMB_W)
    ctx.log("niche_discover.reel_composite",
            frames=len(frames), path=built or str(out_path),
            ok=bool(built))
    if built:
        return _finalize_saved_frame(Path(built))
    return _screenshot_to(ctx, out_path)


def _composite_or_frames(samples: list[dict], frames_dir: Path,
                         chunk_i: int) -> list[str]:
    """Zbuduj composite z JPEG-ów sample; fallback: lista pojedynczych ścieżek.

    Jeden sample (np. już gotowy kolaż N klatek rolki) zwracamy bez
    przebudowy — inaczej `cols=2` dokładałby czarny kafelek.
    """
    paths = [sm["image_path"] for sm in samples if sm.get("image_path")]
    if not paths:
        return []
    if len(paths) == 1:
        return paths
    arrays = []
    try:
        import cv2
        import numpy as np
        for p in paths:
            data = Path(p).read_bytes()
            arr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8),
                               cv2.IMREAD_COLOR)
            if arr is not None:
                arrays.append(arr)
    except Exception:
        return paths
    if not arrays:
        return paths
    out = frames_dir / f"niche_composite_{chunk_i}_{int(time.time()*1000)}.jpg"
    built = build_composite_grid(
        arrays, out, cols=2, thumb_w=COMPOSITE_THUMB_W)
    if built:
        return [_finalize_saved_frame(Path(built)) or built]
    return paths


def _nudge_ai_caption_view(ctx) -> None:
    """Lekki scroll pod długi opis — pasek reakcji + sporo tekstu (jak panel 2)."""
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        return
    # ~22% ekranu — mniej niż pełna strona, żeby nie zgubić engagement baru
    ctx.ht.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.50),
                 duration_ms=300, pause_kind="tap")
    time.sleep(0.4)


# Landmarki feed/viewer posta — granice media / opis / kolejny post
_MEDIA_RID_HINTS = ("media_group", "carousel_media_group", "media_content")
_HEADER_RID_HINTS = ("row_feed_profile_header",)
_BUTTONS_RID_HINTS = ("row_feed_view_group_buttons",)
_CAPTION_END_TEXT = {
    "mniej", "less", "wyświetl tłumaczenie", "see translation",
    "see original", "zobacz oryginał",
}
# Kolejność ikon na profilu IG: 0=siatka, 1=Rolki, 2=reposty/…, 3=oznaczone
_GRID_TAB_ALIASES = {
    "posts": ("widok siatki", "grid view", "posts"),
    "reels": ("rolki", "reels", "video", "filmy"),
    "tagged": ("zdjęcia, na których jesteś", "photos of you", "tagged"),
}
_GRID_TAB_INDEX = {"posts": 0, "reels": 1, "tagged": 3}


def _rid_endswith(rid: str, hints: tuple[str, ...]) -> bool:
    rid = rid or ""
    return any(rid.endswith(h) or h in rid for h in hints)


def _find_post_regions(flat: list[dict], screen_h: int) -> dict:
    """Granice bieżącego posta z drzewa UI.

    `end_y` = y, na którym kończy się rozwinięty opis (tuż nad kolejnym postem).
    Dzięki temu crop nie łapie nagłówka / mediów następnej treści.
    """
    headers, medias, buttons, captions = [], [], [], []
    caption_end_marks = []
    for n in flat:
        b = parse_bounds(n.get("bounds") or "")
        if not b:
            continue
        rid = n.get("resource-id") or ""
        if _rid_endswith(rid, _HEADER_RID_HINTS):
            headers.append(b)
        elif _rid_endswith(rid, _MEDIA_RID_HINTS):
            medias.append(b)
        elif _rid_endswith(rid, _BUTTONS_RID_HINTS):
            buttons.append(b)
        elif rid in CAPTION_IDS or rid.endswith("caption_text_view"):
            captions.append(b)
        text = (n.get("text") or "").strip().casefold()
        desc = (n.get("content-desc") or "").strip().casefold()
        if text in _CAPTION_END_TEXT or desc in _CAPTION_END_TEXT:
            caption_end_marks.append(b)

    headers.sort(key=lambda t: t[1])
    medias.sort(key=lambda t: t[1])
    buttons.sort(key=lambda t: t[1])

    # Kolejny post: drugi nagłówek ALBO jedyny nagłówek poniżej mediów/reakcji
    # (w viewerze „Posty" bieżący post często NIE ma osobnego row_feed_profile_header
    # — nick siedzi w captionie; pierwszy widoczny header = start następnej treści).
    next_header_y = None
    if len(headers) >= 2:
        next_header_y = headers[1][1]
    elif len(headers) == 1:
        ref_y = None
        if buttons:
            ref_y = buttons[0][3]
        elif medias:
            ref_y = medias[0][3]
        if ref_y is not None and headers[0][1] >= ref_y - 24:
            next_header_y = headers[0][1]
    # czasem kolejny post widać jako drugie media (header zagnieżdżony)
    if next_header_y is None and len(medias) >= 2:
        if medias[1][1] > medias[0][3] - 40:
            next_header_y = medias[1][1]

    def _before_next(b):
        return next_header_y is None or b[1] < next_header_y - 8

    media = None
    for m in medias:
        if not _before_next(m):
            continue
        if media is None or (m[3] - m[1]) > (media[3] - media[1]):
            media = m

    btn = next((b for b in buttons if _before_next(b)), None)

    cap_bottom = None
    for c in captions:
        if _before_next(c):
            cap_bottom = max(cap_bottom or 0, c[3])
    for m in caption_end_marks:
        if _before_next(m):
            cap_bottom = max(cap_bottom or 0, m[3])

    if next_header_y is not None:
        end_y = max(0, next_header_y - 6)
    elif cap_bottom is not None:
        end_y = min(screen_h, cap_bottom + 12)
    else:
        end_y = screen_h

    return {
        "media": media,
        "buttons": btn,
        "caption_bottom": cap_bottom,
        "end_y": end_y,
        "next_header_y": next_header_y,
    }


def _scroll_profile_to_tabs(ctx) -> None:
    """Dociągnij górę profilu — pasek zakładek (siatka/Rolki) musi być w hierarchy."""
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        return
    # swipe w dół = treść w górę / pull — tabs wracają do kadru po scrollu siatki
    ctx.ht.swipe(w // 2, int(h * 0.32), w // 2, int(h * 0.72),
                 duration_ms=300, pause_kind="tap")
    time.sleep(0.45)


def _dump_profile_tabs(ctx) -> list[dict]:
    """Surowy dump ikon zakładek (z `selected`) — źródło prawdy po ADB/uiauto."""
    try:
        tree = ctx.device.hierarchy(force=True)
    except Exception:
        return []
    out = []

    def walk(n):
        p = n.get("properties") or {}
        rid = p.get("resource-id") or ""
        if rid.endswith("profile_tab_icon_view"):
            out.append({
                "resource-id": rid,
                "content-desc": p.get("content-desc") or "",
                "bounds": p.get("bounds") or "",
                "selected": (p.get("selected") or "").lower() == "true",
                "clickable": (p.get("clickable") or "").lower() == "true",
            })
        for c in n.get("children") or []:
            walk(c)

    walk(tree)
    out.sort(key=lambda n: (parse_bounds(n.get("bounds") or "") or (0, 0, 0, 0))[0])
    return out


def _list_profile_tab_icons(flat: list[dict]) -> list[dict]:
    """Ikony `profile_tab_icon_view` posortowane od lewej (x)."""
    tabs = []
    for n in flat:
        rid = n.get("resource-id") or ""
        if not rid.endswith("profile_tab_icon_view"):
            continue
        b = parse_bounds(n.get("bounds") or "")
        if not b:
            continue
        tabs.append(n)
    tabs.sort(key=lambda n: parse_bounds(n.get("bounds") or "")[0])
    return tabs


def _return_to_profile_main(ctx, baseline: str | None = None) -> bool:
    """Wróć na GŁÓWNĄ stronę badanego profilu (pasek Siatka/Rolki widoczny).

    ADB: z vieweru „Posty” 1× BACK → tabs OK; 2× BACK → Szukaj (tabs znikają).
    Dlatego NIE używamy ślepego `_back_to` — stop gdy widać `profile_tab_icon_view`.
    """
    for backs in range(4):
        tabs = _dump_profile_tabs(ctx)
        if tabs:
            ctx.log("niche_discover.profile_main_ok",
                    n_tabs=len(tabs), backs=backs,
                    descs=[(t.get("content-desc") or "")[:20] for t in tabs])
            return True
        ctx.device.press_back()
        try:
            ctx.humanizer.pause("tap")
        except Exception:
            time.sleep(0.45)
        time.sleep(0.35)

    # wypadliśmy poza profil (np. search) — spróbuj ensure baseline
    if baseline:
        try:
            ctx.navigator.ensure(baseline, max_steps=4)
            time.sleep(0.6)
        except Exception as exc:
            ctx.log("niche_discover.profile_main_ensure_fail",
                    error=str(exc)[:120])
        tabs = _dump_profile_tabs(ctx)
        if tabs:
            ctx.log("niche_discover.profile_main_ok",
                    n_tabs=len(tabs), backs="ensure",
                    descs=[(t.get("content-desc") or "")[:20] for t in tabs])
            return True
    ctx.log("niche_discover.profile_main_missing", baseline=baseline)
    return False


def _reels_tab_is_selected(ctx) -> bool:
    """True gdy aktywna zakładka to Rolki (selected=true albo jedyna trafiona)."""
    aliases = _GRID_TAB_ALIASES.get("reels") or ("rolki", "reels")
    for t in _dump_profile_tabs(ctx):
        desc = (t.get("content-desc") or "").strip().casefold()
        if any(a in desc for a in aliases) and t.get("selected"):
            return True
    return False


def _select_profile_grid_tab(ctx, tab: str) -> bool:
    """Tap w zakładkę profilu: posts / reels / tagged.

    1) content-desc (Rolki / Reels / …)
    2) fallback: indeks ikony (Rolki = 2. od lewej) — potwierdzone ADB na A50
    """
    aliases = _GRID_TAB_ALIASES.get(tab) or (tab,)
    from v2.steps.post._post import center as _tile_center
    tabs = _dump_profile_tabs(ctx)
    if not tabs:
        try:
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
            tabs = _list_profile_tab_icons(flat)
        except Exception as exc:
            ctx.log("niche_discover.grid_tab_dump_failed", error=str(exc)[:120])
            return False
    # 1) po opisie dostępności
    for n in tabs:
        desc = (n.get("content-desc") or "").strip().casefold()
        if not any(a in desc for a in aliases):
            continue
        c = _tile_center(n.get("bounds"))
        if not c:
            continue
        ctx.ht.tap(*c, pause_kind="tap")
        time.sleep(0.75)
        ctx.log("niche_discover.grid_tab_tapped", tab=tab, desc=desc[:40],
                how="desc", xy=c)
        return True
    # 2) po pozycji: 0=siatka, 1=Rolki, …
    idx = _GRID_TAB_INDEX.get(tab)
    if idx is not None and len(tabs) > idx:
        n = tabs[idx]
        c = _tile_center(n.get("bounds"))
        if c:
            desc = (n.get("content-desc") or "").strip().casefold()
            ctx.ht.tap(*c, pause_kind="tap")
            time.sleep(0.75)
            ctx.log("niche_discover.grid_tab_tapped", tab=tab, desc=desc[:40],
                    how="index", index=idx, n_tabs=len(tabs), xy=c)
            return True
    ctx.log("niche_discover.grid_tab_not_found", tab=tab,
            n_tabs=len(tabs),
            descs=[(t.get("content-desc") or "")[:30] for t in tabs])
    return False


def _ensure_reels_tab(ctx, *, baseline: str | None = None,
                      attempts: int = 3) -> bool:
    """Wejście w ikonę Rolki na głównej stronie profilu (ADB: desc=Rolki, idx=1).

    Kolejność: powrót na profil z tabami → (opcjonalnie scroll) → tap Rolki
    → weryfikacja selected=true.
    """
    if not _return_to_profile_main(ctx, baseline):
        return False
    if _reels_tab_is_selected(ctx):
        ctx.log("niche_discover.reels_tab_already", source="selected")
        return True
    for attempt in range(max(1, attempts)):
        # gdy tabs są, nie ciągnij pull-to-refresh bez potrzeby
        tabs = _dump_profile_tabs(ctx)
        if not tabs:
            _scroll_profile_to_tabs(ctx)
        if not _select_profile_grid_tab(ctx, "reels"):
            ctx.log("niche_discover.reels_tab_retry", attempt=attempt + 1)
            _scroll_profile_to_tabs(ctx)
            continue
        if _reels_tab_is_selected(ctx):
            return True
        # czasem selected opóźnione — jeszcze raz sprawdź po krótkiej pauzie
        time.sleep(0.4)
        if _reels_tab_is_selected(ctx):
            return True
        ctx.log("niche_discover.reels_tab_not_selected", attempt=attempt + 1)
    return _reels_tab_is_selected(ctx)


def _capture_post_media(ctx, path: Path | None) -> str | None:
    """Wycinek samego `media_group` — bez opisu, paska UI i kolejnego posta.

    Kadr z scrcpy (fallback: screenshot HTTP). Bounds z hierarchii skalujemy
    do rozmiaru kadru. Na dysk tylko crop JPEG ≤900×1950.
    """
    if path is None:
        return None
    path = Path(path)
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        w, h = 1080, 2340
    try:
        png, source = _capture_screen_png(ctx)
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    except Exception as exc:
        ctx.log("niche_discover.post_media_shot_failed", error=str(exc)[:120])
        return _screenshot_to(ctx, path)
    if not png:
        return _screenshot_to(ctx, path)

    # wymiary kadru (scrcpy ≠ natywne 1080×2340) — do skalowania cropa
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(png)) as im:
            iw, ih = im.size
    except Exception:
        iw, ih = w, h

    reg = _find_post_regions(flat, h)
    media = reg.get("media")
    if media and (media[3] - media[1]) >= 80:
        crop_dev = (media[0], media[1], media[2], media[3])
    else:
        # fallback: środkowy pas pod action barem „Posty"
        crop_dev = (0, int(h * 0.10), w, int(h * 0.58))
    crop = _scale_crop_to_image(crop_dev, (w, h), (iw, ih))
    built = stitch_vertical_crops([png], [crop], path)
    ctx.log("niche_discover.post_media_crop",
            crop=crop, crop_device=crop_dev, source=source,
            frame_wh=[iw, ih], next=reg.get("next_header_y"),
            ok=bool(built))
    if built:
        return _finalize_saved_frame(Path(built))
    # awaria cropa — zapisz skalowany full-frame (bez osobnego „oryginału")
    return _screenshot_to(ctx, path)


def _read_post_caption_full(ctx, *, ai_capture_path: Path | None = None
                            ) -> tuple[str, str | None]:
    """Pełny opis posta jako tekst (dump hierarchii) — BEZ screenów diagnostycznych.

    `ai_capture_path` zachowane dla kompatybilności — jeśli podane, robi też
    crop mediów (rzadka ścieżka). Preferuj osobne `_capture_post_media`.
    """
    from v2.steps._expand_more import expand_more_if_present

    image_path = None
    if ai_capture_path is not None:
        image_path = _capture_post_media(ctx, ai_capture_path)

    _nudge_toward_caption(ctx)
    if expand_more_if_present(ctx):
        time.sleep(0.4)
    if expand_more_if_present(ctx):
        time.sleep(0.35)

    expanded = True
    merged = ""
    prev = ""
    for page in range(4):
        part = _dump_caption_text(ctx) or ""
        ctx.log("niche_discover.caption_dump", page=page, chars=len(part),
                preview=part[:80], expanded=bool(expanded))
        if part:
            merged = merge_caption_parts(merged, part) if merged else part

        still_cut = (
            _bio_looks_truncated(part)
            or _caption_taller_than_screen(ctx)
            or _caption_looks_incomplete(merged)
        )
        if not still_cut:
            break
        if page > 0 and part and part == prev:
            break
        prev = part
        expand_more_if_present(ctx)
        _nudge_caption_page(ctx)

    ctx.log("niche_discover.caption_merged", chars=len(merged),
            preview=(merged or "")[:100])
    # Insights własnego profilu / UFI nie są opisem
    return sanitize_content_caption(merged) or "", image_path


def _caption_looks_incomplete(text: str | None) -> bool:
    """Heurystyka: dump ucięty w połowie (… / przyimek na końcu)."""
    if not text or len(text) < 120:
        return False
    t = text.rstrip()
    if t.endswith(("…", "...")):
        return True
    # typowe ucięcie viewportu: kończy się krótkim słowem funkcyjnym
    last = (t.split()[-1] if t.split() else "").strip(".,!?#\"'…")
    return last.casefold() in {
        "na", "w", "z", "i", "do", "od", "po", "że", "bo", "the", "a", "an", "to", "and",
    }


def _scroll_profile_grid(ctx) -> None:
    """Przewiń siatkę profilu w dół — szukamy postów poniżej rolek."""
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        return
    ctx.ht.swipe(w // 2, int(h * 0.75), w // 2, int(h * 0.35),
                 duration_ms=350, pause_kind="tap")
    time.sleep(0.55)


def _caption_taller_than_screen(ctx) -> bool:
    """True gdy węzeł opisu wystaje poniżej dolnej części ekranu / jest bardzo wysoki."""
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    except Exception:
        return False
    for n in flat:
        if n.get("resource-id") not in CAPTION_IDS:
            continue
        b = parse_bounds(n.get("bounds") or "")
        if not b:
            continue
        x1, y1, x2, y2 = b
        # opis sięga prawie do dołu albo wyższy niż ~45% ekranu
        if y2 >= int(h * 0.88) or (y2 - y1) >= int(h * 0.45):
            return True
    return False


def _nudge_toward_caption(ctx) -> None:
    """Lekko zjedź w dół — media u góry, opis pod spodem ma wejść w kadr."""
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        return
    # ~27% ekranu — wystarczy, by widać było „więcej" pod paskiem reakcji
    ctx.ht.swipe(w // 2, int(h * 0.78), w // 2, int(h * 0.48),
                 duration_ms=320, pause_kind="tap")
    time.sleep(0.45)


def _nudge_caption_page(ctx) -> None:
    """Kolejny kawałek długiego opisu — wyraźniejszy scroll niż sam nudge."""
    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        return
    # ~40% ekranu — drugi dump ma pokazać dalszy ciąg, nie ten sam fragment
    ctx.ht.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.28),
                 duration_ms=320, pause_kind="tap")
    time.sleep(0.45)


def merge_caption_parts(a: str, b: str) -> str:
    """Scal dwa dumpy opisu — wspólny overlap suffix/prefix, bez duplikatu."""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a:
        return b
    if not b:
        return a
    if b in a:
        return a
    if a in b:
        return b
    # overlap od najdłuższego; min 4 znaki (krótkie styki typu „środek”)
    max_ov = min(len(a), len(b), 160)
    for n in range(max_ov, 3, -1):
        if a[-n:] == b[:n]:
            return a + b[n:]
    # miękki overlap: ostatnie ~24 znaki a gdzieś w b
    needle = a[-24:] if len(a) > 24 else a
    if len(needle) >= 8:
        idx = b.find(needle)
        if idx >= 0:
            return a + b[idx + len(needle):]
        idx = b.find(needle[-12:])
        if idx >= 0:
            return a + b[idx + 12:]
    return a.rstrip() + "\n" + b.lstrip()


# ID UFI / liczników — NIGDY nie traktuj jako opis (bug: fallback brał like_count)
_CAPTION_BLOCKED_RID = (
    "like_count", "comment_count", "clips_ufi", "ufi_more",
    "like_button", "comment_button", "save_button", "share_button",
    "clips_author", "clips_media_component",
)
# Prefiksy tekstu UFI (PL/EN) — odrzucaj nawet gdy wpadną do CAPTION_IDS
_UFI_COUNT_PREFIXES = (
    "liczba polubień", "liczba komentarzy",
    "number of likes", "number of comments",
    "wyświetl polubienia", "wyświetl komentarze",
    "view likes", "view comments",
)
# Własny profil: pod mediów Insights zamiast opisu
_INSIGHTS_MARKERS = (
    "wyświetl statystyki",
    "view insights",
    "view stats",
    "see insights",
    "zobacz statystyki",
)


def _is_ufi_count_text(text: str | None) -> bool:
    """True gdy tekst to licznik lajków/komentarzy, nie opis."""
    t = (text or "").strip().casefold()
    if not t:
        return False
    return any(t.startswith(p) or p in t[:48] for p in _UFI_COUNT_PREFIXES)


def _is_insights_stats_text(text: str | None) -> bool:
    """True gdy to Insights własnego profilu (np. „7,6 tys. · Wyświetl statystyki”)."""
    t = (text or "").strip().casefold()
    if not t:
        return False
    if any(m in t for m in _INSIGHTS_MARKERS):
        return True
    # skróty typu „12 tys. · …” bez pełnego „statystyki” — rzadkie, ale ostrożnie:
    # tylko gdy jest „tys”/„k” i kropka środkowa + wyświetl
    if "·" in t and ("statystyk" in t or "insights" in t):
        return True
    return False


def sanitize_content_caption(text: str | None) -> str:
    """Odrzuć Insights / UFI — zostaw pusty string zamiast śmieciowego „opisu”."""
    t = (text or "").strip()
    if not t:
        return ""
    if _is_ufi_count_text(t) or _is_insights_stats_text(t):
        return ""
    return t


def media_file_fingerprint(path: str | Path | None) -> str | None:
    """Odcisk cropa mediów (gdy caption pusty) — chroni przed podwójnym zbieraniem."""
    if not path:
        return None
    try:
        import hashlib
        p = Path(path)
        if not p.is_file():
            return None
        data = p.read_bytes()[:65536]
        if len(data) < 64:
            return None
        return "img:" + hashlib.sha1(data).hexdigest()[:16]
    except Exception:
        return None


def _rid_blocked_for_caption(rid: str | None) -> bool:
    r = (rid or "").lower()
    return any(b in r for b in _CAPTION_BLOCKED_RID)


def _caption_text_ok(text: str | None, *, min_len: int = 8) -> bool:
    """Czy kandydat nadaje się na opis (nie śmieci UI / UFI / Insights / zegar)."""
    import re
    t = (text or "").strip()
    if len(t) < min_len:
        return False
    if re.match(r"^\d{1,2}:\d{2}$", t):
        return False
    if _is_ufi_count_text(t) or _is_insights_stats_text(t):
        return False
    return True


def pick_caption_from_flat(flat: list[dict], *, strict: bool = False) -> str:
    """Wybierz opis z hierarchii — najpierw CAPTION_IDS, bez UFI/like_count.

    `strict=True` (rolki): wyłącznie CAPTION_IDS — bez fallbacku na dowolny TextView
    (inaczej dump łapał „Liczba polubień…" z paska UFI). Dla rolek Compose
    użyj też `extract_reel_caption_from_tree` (osobno).
    """
    parts = []
    for n in flat:
        if n.get("resource-id") not in CAPTION_IDS:
            continue
        text = (n.get("text") or "").strip()
        if not text:
            text = (n.get("content-desc") or "").strip()
        if _caption_text_ok(text):
            parts.append(text)
    if parts:
        return max(parts, key=len)
    if strict:
        return ""
    best = ""
    skip_ids = ("tab_", "action_bar", "navigation", "button", "icon", "status")
    for n in flat:
        rid = n.get("resource-id") or ""
        if _rid_blocked_for_caption(rid):
            continue
        if any(s in rid for s in skip_ids):
            continue
        text = (n.get("text") or "").strip()
        if _caption_text_ok(text) and len(text) > len(best):
            best = text
    return best


def extract_reel_caption_from_tree(root: dict) -> str:
    """Opis rolki z ViewGroup pod RecyclerView (viewer bez caption_text_view).

    Żywe ścieżki (uiautomator):
      …/RecyclerView/ViewGroup[2]/ViewGroup[2|3]
    Tekst bywa na samym ViewGroup (text/content-desc) albo w dzieciach TextView.
    """
    if not isinstance(root, dict):
        return ""
    screen_h, screen_w = _estimate_screen_wh(root)
    # (len, text, prefer_score)
    cands: list[tuple[int, str, int]] = []

    def _walk(node: dict, under_rv: bool) -> None:
        p = node.get("properties") or {}
        cls = (p.get("class") or "").casefold()
        rid = (p.get("resource-id") or "")
        is_rv = "recyclerview" in cls
        now_rv = under_rv or is_rv

        if now_rv and not is_rv:
            if not _rid_blocked_for_caption(rid):
                for raw in ((p.get("text") or "").strip(),
                            (p.get("content-desc") or "").strip()):
                    if not _caption_text_ok(raw):
                        continue
                    box = parse_bounds(p.get("bounds") or "")
                    score = len(raw)
                    if box:
                        x1, y1, x2, y2 = box
                        # prawa kolumna UFI
                        if x1 > int(screen_w * 0.72):
                            continue
                        # opis — dolna część kadru (nie top bar)
                        if y2 < int(screen_h * 0.32):
                            continue
                        # preferuj lewą/środkową strefę opisu
                        if x2 < int(screen_w * 0.85) and y1 > int(screen_h * 0.45):
                            score += 50
                    cands.append((score, raw, score))

        for c in node.get("children") or []:
            _walk(c, now_rv)

    _walk(root, False)
    if not cands:
        return ""
    cands.sort(key=lambda t: t[0], reverse=True)
    return cands[0][1]


def _estimate_screen_wh(root: dict) -> tuple[int, int]:
    """Max (height, width) z bounds drzewa."""
    best_h, best_w = 0, 0

    def _walk(node: dict) -> None:
        nonlocal best_h, best_w
        p = node.get("properties") or {}
        box = parse_bounds(p.get("bounds") or "")
        if box:
            best_w = max(best_w, int(box[2]))
            best_h = max(best_h, int(box[3]))
        for c in node.get("children") or []:
            _walk(c)

    _walk(root)
    return (best_h if best_h >= 800 else 2340,
            best_w if best_w >= 400 else 1080)


def _read_caption_best_effort(ctx, kind: str) -> str:
    """Odczyt opisu — posty: pełny flow viewer; rolki: guardowany tap+dump."""
    if kind == "post":
        caption, _ = _read_post_caption_full(ctx)
        return caption
    if kind == "reel":
        return _read_reel_caption_guarded(ctx)
    return _dump_caption_text(ctx) or ""


# Sam `comment_composer` siedzi na UFI rolki — NIE znaczy „panel opisu otwarty".
# Inaczej zawsze skipujemy swipe i kończymy na comments_sheet_no_caption.
_CAPTION_PANEL_EXPANDED = (
    "bottom_sheet_container",
    "layout_comment_thread",
    "row_comment_container",
    "comment_thread",
)


def caption_panel_expanded(flat: list[dict]) -> bool:
    """True tylko przy faktycznie rozwiniętym panelu opisu/komentarzy."""
    for n in flat:
        rid = (n.get("resource-id") or "").lower()
        if any(m in rid for m in _CAPTION_PANEL_EXPANDED):
            return True
    return False


def _reel_caption_state(ctx, *, expected_author: str | None = None) -> dict:
    """Snapshot po geście: viewer / siatka / panel / autor / opis (IDs lub RV)."""
    from v2.steps.post import _post
    from v2.steps.reels import _reel_meta

    try:
        raw = ctx.device.hierarchy(force=True)
        flat = flatten_hierarchy(raw) if not isinstance(raw, list) else raw
        tree = raw if isinstance(raw, dict) else None
    except Exception:
        return {
            "ok": False, "on_viewer": False, "on_grid": False,
            "panel": False, "author": None, "caption": "",
            "chars": 0, "reel_changed": False, "reason": "hierarchy_fail",
        }
    on_viewer = _post.media_viewer_open(flat, ["reel"])
    on_grid = _post.still_on_profile_grid(flat)
    author = None
    for n in flat:
        if n.get("resource-id") == _reel_meta.IDS["author"]:
            author = (n.get("text") or "").strip() or None
            break
    panel = caption_panel_expanded(flat)
    caption = pick_caption_from_flat(flat, strict=True)
    if not caption and tree is not None:
        caption = extract_reel_caption_from_tree(tree)
    reel_changed = bool(
        expected_author and author and author != expected_author)
    return {
        "ok": bool(on_viewer and not on_grid and not reel_changed),
        "on_viewer": on_viewer,
        "on_grid": on_grid,
        "panel": panel,
        "author": author,
        "caption": caption,
        "chars": len(caption or ""),
        "reel_changed": reel_changed,
        "reason": None,
    }


def _close_reel_caption_panel(ctx, *, expected_author: str | None = None) -> dict:
    """BACK zamyka panel opisu — nie wychodź na siatkę / inną rolkę."""
    st = _reel_caption_state(ctx, expected_author=expected_author)
    if not st.get("panel"):
        return st
    try:
        ctx.device.press_back()
        time.sleep(0.4)
    except Exception as exc:
        ctx.log("niche_discover.reel_caption.back_fail", error=str(exc)[:80])
        return st
    st2 = _reel_caption_state(ctx, expected_author=expected_author)
    ctx.log("niche_discover.reel_caption.after_back",
            on_viewer=st2.get("on_viewer"), on_grid=st2.get("on_grid"),
            panel=st2.get("panel"), reel_changed=st2.get("reel_changed"))
    return st2


def find_reel_caption_tap_target(flat: list[dict], *, w: int, h: int,
                                  tree: dict | None = None) -> dict | None:
    """Znajdź współrzędne do TAPU w opis (bez swipe).

    Kolejność:
      1. CAPTION_IDS z bounds (nawet pusty kontener — tap rozwija panel)
      2. ViewGroup z tekstem pod RecyclerView (Compose viewer)
      3. klikalny tekst z „…więcej" / „more" (nie UFI)
      4. fallback: strefa dolnego opisu
    """
    from v2.steps._expand_more import MORE_IN_TEXT, UFI_MORE_RID_MARKERS, _center
    from v2.steps.post._post import center as bounds_center

    # 1) caption component / text view
    for rid in CAPTION_IDS:
        for n in flat:
            if n.get("resource-id") != rid:
                continue
            c = bounds_center(n.get("bounds")) or _center(n.get("bounds"))
            if c:
                return {"x": c[0], "y": c[1], "source": rid.split("/")[-1],
                        "preview": ((n.get("text") or n.get("content-desc")
                                     or "")[:60])}

    # 2) ViewGroup pod RecyclerView z widocznym tekstem opisu
    if tree is not None:
        tgt = _reel_caption_compose_tap_target(tree, w=w, h=h)
        if tgt:
            return tgt
    # flat-only: długi tekst w dolnej lewej strefie (bez resource-id)
    best = None
    best_len = 0
    for n in flat:
        rid = n.get("resource-id") or ""
        if rid or _rid_blocked_for_caption(rid):
            continue
        text = (n.get("text") or n.get("content-desc") or "").strip()
        if not _caption_text_ok(text) or len(text) <= best_len:
            continue
        c = bounds_center(n.get("bounds")) or _center(n.get("bounds"))
        if not c or c[0] > int(w * 0.72) or c[1] < int(h * 0.40):
            continue
        best = {"x": c[0], "y": c[1], "source": "rv_viewgroup_flat",
                "preview": text[:60]}
        best_len = len(text)
    if best:
        return best

    # 3) „…więcej" w tekście (nie clips_ufi_more)
    for n in flat:
        rid = (n.get("resource-id") or "").lower()
        if any(m in rid for m in UFI_MORE_RID_MARKERS):
            continue
        text = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()
        blob = f"{text} {desc}"
        if not any(m in blob for m in MORE_IN_TEXT):
            if text.casefold() not in {"więcej", "more", "see more"} \
                    and desc.casefold() not in {"więcej", "more", "see more"}:
                continue
        if n.get("clickable") is False and not text:
            continue
        c = bounds_center(n.get("bounds")) or _center(n.get("bounds"))
        if c:
            if c[0] > int(w * 0.82):
                continue
            return {"x": c[0], "y": c[1], "source": "more_in_text",
                    "preview": blob[:60]}

    # 4) fallback strefa — stary bot: x 280–659, y 1950–1965 @ 1080×2340
    fx = int(w * 0.42)
    fy = int(h * 0.835)
    return {"x": fx, "y": fy, "source": "fallback_zone", "preview": ""}


def _reel_caption_compose_tap_target(tree: dict, *, w: int, h: int) -> dict | None:
    """Środek ViewGroup z najdłuższym opisem pod RecyclerView."""
    from v2.steps._expand_more import _center
    from v2.steps.post._post import center as bounds_center

    best = None
    best_score = 0

    def _walk(node: dict, under_rv: bool) -> None:
        nonlocal best, best_score
        p = node.get("properties") or {}
        cls = (p.get("class") or "").casefold()
        is_rv = "recyclerview" in cls
        now_rv = under_rv or is_rv
        if now_rv and not is_rv:
            text = ((p.get("text") or "").strip()
                    or (p.get("content-desc") or "").strip())
            if _caption_text_ok(text) and len(text) > best_score:
                c = bounds_center(p.get("bounds")) or _center(p.get("bounds"))
                if c and c[0] <= int(w * 0.72) and c[1] >= int(h * 0.40):
                    best = {"x": c[0], "y": c[1],
                            "source": "rv_viewgroup", "preview": text[:60]}
                    best_score = len(text)
        for c in node.get("children") or []:
            _walk(c, now_rv)

    _walk(tree, False)
    return best


def _more_marker_on_screen(ctx) -> bool:
    """True gdy na hierarchy widać „więcej"/„more" (nie UFI) — do logów / decyzji."""
    from v2.steps._expand_more import _find_more_target
    try:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    except Exception:
        return False
    return _find_more_target(flat) is not None


def _read_reel_caption_guarded(ctx) -> str:
    """Odczyt opisu rolki — TYLKO TAP (bez swipe); ZAWSZE expand przed zapisem.

    Procedura:
      1. potwierdź viewer
      2. seed z hierarchy (already_visible) — NIE finalny (Compose bywa teaserem)
      3. ZAWSZE: tap w caption + expand_more_if_present (nawet przy already_have)
      4. dump CAPTION_IDS / extract_reel_caption_from_tree, merge parts
      5. truncated → drugi expand; BACK zamyka panel gdy otworzyliśmy go tapem

    Wyjątek od dalszego expandu: po tapie/expand nadal ten sam pełny tekst
    bez markerów truncacji — wtedy uznajemy caption za finalny.
    """
    from v2.steps._expand_more import expand_more_if_present
    from v2.steps.reels import _reel_meta

    # --- 1. viewer ---
    if not _ensure_media_viewer(ctx, want_kinds=["reel"]):
        ctx.log("niche_discover.reel_caption.fail", reason="not_on_viewer")
        return ""

    try:
        meta0 = _reel_meta.read_current_reel(ctx.device)
    except Exception:
        meta0 = {}
    expected_author = meta0.get("author")

    try:
        w, h = ctx.account.device["_hardware"]["resolution"]
    except Exception:
        ctx.log("niche_discover.reel_caption.fail", reason="no_resolution")
        return ""

    def _fail(reason: str, *, close_panel: bool = False) -> str:
        ctx.log("niche_discover.reel_caption.fail", reason=reason,
                author=expected_author)
        if close_panel:
            _close_reel_caption_panel(ctx, expected_author=expected_author)
        return ""

    st0 = _reel_caption_state(ctx, expected_author=expected_author)
    chars_before = int(st0.get("chars") or 0)
    more_before = _more_marker_on_screen(ctx)
    ctx.log("niche_discover.reel_caption.enter",
            author=expected_author, chars=chars_before,
            panel=st0.get("panel"), on_viewer=st0.get("on_viewer"),
            more_on_screen=more_before,
            looks_truncated=_bio_looks_truncated(st0.get("caption")))

    # seed z viewera — może być teaserem („udostępnia notatkę:") bez „…więcej"
    text = ""
    seed = ""
    already_have = (chars_before > 0
                    and not _is_ufi_count_text(st0.get("caption")))
    if already_have:
        seed = (st0.get("caption") or "").strip()
        text = seed
        ctx.log("niche_discover.reel_caption.already_visible",
                chars=len(seed),
                looks_truncated=_bio_looks_truncated(seed),
                more_on_screen=more_before)

    # ZAWSZE expand przed zapisem do sample — seed (already_have) NIE jest finalny
    # (Compose często pokazuje teaser bez „…więcej", np. „udostępnia notatkę:")
    panel_opened_by_us = False
    did_tap = False
    did_expand = False

    # --- 2. ZAWSZE: znajdź kandydata + TAP (bez swipe) — nie pomijaj przy seed ---
    try:
        raw = ctx.device.hierarchy(force=True)
        flat = flatten_hierarchy(raw) if not isinstance(raw, list) else raw
        tree = raw if isinstance(raw, dict) else None
    except Exception:
        if not seed:
            return _fail("hierarchy_before_tap")
        raw, flat, tree = None, [], None

    target = (find_reel_caption_tap_target(flat, w=w, h=h, tree=tree)
              if flat or tree is not None else None)
    if not target and not seed:
        return _fail("no_caption_tap_target")
    if target:
        ctx.log("niche_discover.reel_caption.tap_target",
                x=target["x"], y=target["y"], source=target["source"],
                preview=(target.get("preview") or "")[:60])
        ctx.ht.tap(target["x"], target["y"], pause_kind="tap")
        time.sleep(0.7)
        did_tap = True

        st = _reel_caption_state(ctx, expected_author=expected_author)
        ctx.log("niche_discover.reel_caption.after_tap",
                on_viewer=st["on_viewer"], on_grid=st["on_grid"],
                panel=st["panel"], chars=st["chars"],
                reel_changed=st["reel_changed"])
        if st["on_grid"] or not st["on_viewer"]:
            return _fail("left_viewer_after_tap")
        if st["reel_changed"]:
            return _fail("reel_changed_after_tap")
        if st["panel"]:
            panel_opened_by_us = True
        if st["chars"] > 0 and not _is_ufi_count_text(st["caption"]):
            text = merge_caption_parts(text or "", st["caption"])
    else:
        ctx.log("niche_discover.reel_caption.tap_skipped",
                reason="no_target_but_seed", chars=len(seed))

    # --- 3. expand „…więcej" w opisie (nie UFI) ---
    if expand_more_if_present(ctx):
        did_expand = True
        time.sleep(0.45)
        st = _reel_caption_state(ctx, expected_author=expected_author)
        ctx.log("niche_discover.reel_caption.after_expand",
                on_viewer=st["on_viewer"], on_grid=st["on_grid"],
                panel=st["panel"], chars=st["chars"],
                reel_changed=st["reel_changed"])
        if st["on_grid"] or not st["on_viewer"]:
            return _fail("left_viewer_after_expand",
                         close_panel=panel_opened_by_us)
        if st["reel_changed"]:
            return _fail("reel_changed_after_expand",
                         close_panel=panel_opened_by_us)
        if st["panel"]:
            panel_opened_by_us = True
        if st["chars"] > 0 and not _is_ufi_count_text(st["caption"]):
            text = merge_caption_parts(text or "", st["caption"])

    # --- 4. dump CAPTION_IDS / RecyclerView ViewGroup (dopiero PO expandzie) ---
    dump = _dump_caption_text(ctx, strict=True)
    if dump and not _is_ufi_count_text(dump):
        text = merge_caption_parts(text or "", dump) if text else dump
    if text and _is_ufi_count_text(text):
        ctx.log("niche_discover.reel_caption.rejected_ufi", preview=text[:80])
        text = ""

    if not text:
        st = _reel_caption_state(ctx, expected_author=expected_author)
        if st["chars"] > 0 and not _is_ufi_count_text(st["caption"]):
            text = st["caption"]
        else:
            if panel_opened_by_us:
                _close_reel_caption_panel(ctx, expected_author=expected_author)
            return _fail("no_caption_after_tap")

    # --- 5. truncated → drugi expand (bez swipe/scroll poza panelem) ---
    # pełny tekst bez markerów = koniec; inaczej jeszcze raz „więcej"
    if _bio_looks_truncated(text) or _more_marker_on_screen(ctx):
        if expand_more_if_present(ctx):
            did_expand = True
            time.sleep(0.4)
        st = _reel_caption_state(ctx, expected_author=expected_author)
        ctx.log("niche_discover.reel_caption.after_reexpand",
                chars=st["chars"], panel=st["panel"], ok=st["ok"])
        if st["ok"] and st["chars"] > 0:
            text = merge_caption_parts(text or "", st["caption"])
            dump2 = _dump_caption_text(ctx, strict=True)
            if dump2 and not _is_ufi_count_text(dump2):
                text = merge_caption_parts(text or "", dump2)
        elif not st["ok"]:
            if panel_opened_by_us:
                _close_reel_caption_panel(ctx, expected_author=expected_author)
            ctx.log("niche_discover.reel_caption.truncated_abort",
                    kept_chars=len(text or ""))
            return text or ""

    if panel_opened_by_us:
        _close_reel_caption_panel(ctx, expected_author=expected_author)

    if text and _is_ufi_count_text(text):
        return _fail("ufi_after_all")

    chars_after = len(text or "")
    ctx.log("niche_discover.reel_caption.expand_required",
            chars_before=chars_before, chars_after=chars_after,
            did_tap=did_tap, did_expand=did_expand,
            still_truncated=_bio_looks_truncated(text),
            grew=chars_after > chars_before)
    ctx.log("niche_discover.reel_caption.ok", chars=chars_after,
            preview=(text or "")[:100], author=expected_author,
            panel_opened_by_us=panel_opened_by_us,
            did_tap=did_tap, did_expand=did_expand)
    return text or ""


def _dump_caption_text(ctx, *, strict: bool = False) -> str:
    """Dump opisu z hierarchy. `strict` = CAPTION_IDS, potem RV/ViewGroup."""
    try:
        raw = ctx.device.hierarchy(force=True)
    except Exception:
        return ""
    if isinstance(raw, list):
        return pick_caption_from_flat(raw, strict=strict)
    flat = flatten_hierarchy(raw)
    text = pick_caption_from_flat(flat, strict=strict)
    if text:
        return text
    # Compose viewer: tekst w ViewGroup pod RecyclerView (bez caption_text_view)
    return extract_reel_caption_from_tree(raw)


def _bio_looks_truncated(text: str | None) -> bool:
    """Czy tekst wygląda na ucięty (ma znacznik …więcej / kończy się wielokropkiem)."""
    if not text:
        return False
    t = text.strip().casefold()
    markers = ("… więcej", "…więcej", "... więcej", "...więcej",
               "… more", "…more", "... more", "...more")
    if any(m in t for m in markers):
        return True
    return t.endswith("…") or t.endswith("...")


def _back_to(ctx, target: str | None) -> bool:
    if not target:
        return True
    for _ in range(3):
        if ctx.navigator.identify().top == target:
            return True
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
    if ctx.navigator.identify().top != target:
        ctx.navigator.ensure(target, max_steps=3)
    return ctx.navigator.identify().top == target


def _sync_phrases(account_id: str, keywords: list[str],
                  hashtags: list[str] | None = None) -> None:
    """Dopisz keywords → phrases.keywords, hashtags → phrases.hashtags.

    Append-only per bucket; pomija duplikaty (case-insensitive).
    """
    from v2.plugins.impl.content_search import (
        _dedupe_phrases, _migrate_legacy_phrase_list,
    )

    kws = [str(x).strip().lstrip("#") for x in (keywords or []) if str(x).strip()]
    tags = []
    for x in hashtags or []:
        s = str(x).strip()
        if not s:
            continue
        tags.append(s if s.startswith("#") else f"#{s}")
    if not kws and not tags:
        return
    try:
        settings = store.read_settings(account_id)
        cs = settings.get("content_search") or {}
        phrases = _migrate_legacy_phrase_list(cs.get("phrases") or {})
        if kws:
            phrases["keywords"] = _dedupe_phrases(
                list(phrases.get("keywords") or []) + kws)[:40]
        if tags:
            phrases["hashtags"] = _dedupe_phrases(
                list(phrases.get("hashtags") or []) + tags)[:40]
        # legacy list = unia (kompat starych tooli)
        phrases["list"] = _dedupe_phrases(
            list(phrases.get("hashtags") or [])
            + list(phrases.get("keywords") or [])
            + list(phrases.get("locations") or []))[:40]
        cs["phrases"] = phrases
        store.write_settings(account_id, {"content_search": cs})
    except Exception:
        pass


def _normalize_usernames(raw) -> list[str]:
    """Lista nicków bez @; zachowaj kropki (juraspa.pl jest poprawnym nickiem IG)."""
    out, seen = [], set()
    for u in raw or []:
        s = str(u).strip().lstrip("@").strip()
        if not s:
            continue
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _step(ctx, name: str, params: dict):
    return stepcall.call(steps, ctx, name, params, source="niche_discover")
