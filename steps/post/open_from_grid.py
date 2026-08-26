"""post.open_from_grid — otwórz jedną treść z siatki (wyniki wyszukiwania albo profil).

Domyślnie losujemy z PIERWSZYCH N kafelków (`pool`) — na górze siedzą treści
najbardziej dopasowane do frazy. Przy `ordered=True` bierzemy kolejny wolny
kafelek od góry (najnowszy → dalej), bez losowania — discovery niszy.

Otwarcie: lekko dłuższy `firm_tap` + weryfikacja „wyszliśmy z profilu".
Gdy nadal siatka — ponów TEN SAM kafelek (nie idź dalej).
"""
from __future__ import annotations

import random
import time

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.post import _post
from v2.steps.registry import register
from v2.steps.system._ui_helpers import settle_for_animation

SCRATCH_USED = "grid_used"
_KIND_ALIASES = {"reels": "reel", "posts": "post", "photos": "post"}


@register
class OpenFromGrid:
    name = "post.open_from_grid"
    requires_screen = None

    def run(self, ctx, params):
        rng = random.Random()
        pool = int(params.get("pool", 4))
        ordered = bool(params.get("ordered"))
        baseline = params.get("baseline") or ctx.navigator.identify().top
        # dłuższy tap (~140ms) — krótkie bywały „mis-tap" i zostawały na profilu
        hold_ms = int(params.get("hold_ms", 140))
        open_retries = max(1, int(params.get("open_retries", 2)))

        # Przed zbieraniem kafelków wystarczy, że animacje feedu się skończą.
        settle_for_animation(ctx, max_wait_s=1.5, fallback_wait_s=1.0)
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        want = [_KIND_ALIASES.get(k, k) for k in (params.get("content_types") or []) if k]
        # zakładka Rolki: pozwól na fallback kwadratowych komórek
        tiles = _post.collect_tiles(
            flat, allow_square_fallback=("reel" in want))
        if not tiles:
            # diagnostyka: co widać zamiast kafelków (zakładka Rolki bywa inna)
            sample = []
            for e in flat[:80]:
                rid = (e.get("resource-id") or "")
                desc = (e.get("content-desc") or "")[:40]
                if rid or desc:
                    sample.append(f"{rid.split('/')[-1]}|{desc}")
                if len(sample) >= 12:
                    break
            ctx.log("post.open_from_grid.no_tiles", baseline=baseline,
                    sample=sample, n_nodes=len(flat))
            return StepResult.skipped("no_tiles")

        used = ctx.scratch.setdefault(SCRATCH_USED, set())
        # ordered: cała widoczna siatka w kolejności czytania; inaczej pool
        window = tiles if ordered else tiles[:pool]
        cands = [t for t in window if t["center"] not in used]

        # Typ treści widać już w opisie kafelka, więc gdy szukamy tylko rolek
        # albo tylko postów, odsiewamy PRZED kliknięciem — zamiast wchodzić
        # i wychodzić z czegoś, czego i tak nie chcemy.
        # Katalog mówi „reels"/„posts" (nazwy pól w panelu), a kafelek zwraca
        # „reel"/„post" — bez ujednolicenia filtr nie trafiałby nigdy i plugin
        # odrzucałby całą siatkę.
        if want:
            typed = [t for t in cands if t["kind"] in want]
            if typed:
                cands = typed
            elif "reel" in want:
                # zakładka Rolki: kafelki często bez „Autor rolki" w desc → kind=None
                unknown = [t for t in cands if not t.get("kind")]
                if unknown:
                    for t in unknown:
                        t["kind"] = "reel"
                    cands = unknown
                else:
                    return StepResult.skipped("no_matching_kind", tiles=len(tiles),
                                              want=want)
            else:
                # NIE bierzemy „obcego" rodzaju (np. rolki gdy chcemy posty)
                return StepResult.skipped("no_matching_kind", tiles=len(tiles),
                                          want=want)

        if not cands:
            return StepResult.skipped("pool_exhausted", tiles=len(tiles))

        # ordered → pierwszy wolny (najnowszy na górze); inaczej los z puli
        target = cands[0] if ordered else rng.choice(cands)
        cx, cy = target["center"]
        ctx.scratch["baseline_screen"] = baseline

        # Retry TEGO SAMEGO kafelka, dopóki nie wyjdziemy z profilu
        opened_ok = False
        landed = None
        flat2 = []
        for attempt in range(1, open_retries + 1):
            ctx.ht.firm_tap(cx, cy, hold_ms=hold_ms, pause_kind="tap")
            ctx.humanizer.pause("between_steps")
            # daj IG czas na transition — zbyt szybki dump widzi jeszcze siatkę
            settle_for_animation(ctx, max_wait_s=2.0, fallback_wait_s=1.2)
            time.sleep(0.25)

            state = ctx.navigator.identify()
            landed = state.top
            flat2 = flatten_hierarchy(ctx.device.hierarchy(force=True))
            still_grid = _post.still_on_profile_grid(flat2)
            # Navigator bywa mylący (viewer → „home_feed"); pewniejsze: hierarchia
            opened_ok = _post.media_viewer_open(flat2, want or None)
            left_profile = (not still_grid) and opened_ok

            ctx.log("post.open_from_grid.open_check",
                    attempt=attempt, baseline=baseline, landed=landed,
                    still_grid=still_grid, opened_ok=opened_ok,
                    left_profile=left_profile, hold_ms=hold_ms)

            if left_profile and not state.overlay:
                break

            # nadal na profilu — nie oznaczaj used; powtórz TEN SAM kafelek
            ctx.log("post.open_from_grid.retry_same_tile",
                    attempt=attempt, max_retries=open_retries,
                    reason=("still_on_grid" if still_grid
                            else "viewer_not_confirmed"))
            if attempt < open_retries:
                time.sleep(0.35)

        if not opened_ok or _post.still_on_profile_grid(flat2):
            ctx.log("post.open_from_grid.nothing_opened", baseline=baseline,
                    landed=landed,
                    still_grid=_post.still_on_profile_grid(flat2),
                    is_post=_post.is_post_viewer(flat2),
                    is_reel=_post.is_reel(flat2),
                    attempts=open_retries)
            # po wyczerpaniu prób — oznacz, żeby nie kręcić w nieskończoność
            used.add(target["center"])
            _recover_to(ctx, baseline)
            return StepResult.skipped("nothing_opened", landed=landed)

        used.add(target["center"])

        # Typ z opisu kafelka jest pewniejszy niż zgadywanie po otwarciu;
        # sprawdzenie drzewa zostaje jako awaryjne.
        kind = target["kind"]
        if not kind:
            kind = "reel" if _post.is_reel(flat2) else "post"
        ctx.scratch["content_kind"] = kind
        # `content_kind`, nie `kind` — pierwszy parametr `ctx.log` też nazywa
        # się `kind` i kolizja wysypuje krok
        ctx.log("post.open_from_grid.done", content_kind=kind, landed=landed,
                ordered=ordered, desc=target["desc"][:60],
                hold_ms=hold_ms)
        return StepResult.ok(kind=kind, landed=landed, ordered=ordered)


def _recover_to(ctx, baseline: str | None) -> None:
    """Wyjdź BACK-iem z tego, co się otworzyło, i wróć na siatkę."""
    for _ in range(2):
        state = ctx.navigator.identify()
        if state.top == baseline and not state.overlay:
            return
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
