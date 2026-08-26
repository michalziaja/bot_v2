"""stories.watch_segments — obejrzyj serię segmentów relacji jednej osoby.

Krok NIE otwiera relacji ani jej nie zamyka — dostaje otwarty `story_viewer`
i zostawia go otwartym. Nawigacja należy do wołającego, bo różni wołający
wchodzą tu z różnych miejsc (tacka na stronie głównej, lista obserwujących,
profil) i wracają w różne miejsca.

Port `instagram_bot/actions/story_watch.py`. Cztery rzeczy, których nie wolno
zgubić, bo każda kosztowała realny błąd na koncie:

  1. przed KAŻDYM gestem sprawdzamy, czy nadal jesteśmy w podglądzie — relacja
     kończy się sama i tap trafia wtedy w ekran pod spodem,
  2. reklama → przejście swipe'm w bok, nie tapem (tap otwiera stronę
     reklamodawcy),
  3. otwarta klawiatura odpowiedzi → BACK, zanim cokolwiek dotkniemy,
  4. tap „dalej" w wąskim pasku przy prawej krawędzi, z dala od nagłówka
     i pola odpowiedzi.
"""
from __future__ import annotations

import random
import time

from v2.plugins.base import draw_range
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.stories import _viewer


@register
class WatchSegments:
    name = "stories.watch_segments"
    requires_screen = None      # wołający już nas tu wprowadził

    def run(self, ctx, params):
        rng = random.Random()
        want = draw_range(params.get("segments"), rng, 2)
        # Czas oglądania relacji to biznesowy parametr pluginu.
        # watch_story = nowy klucz; segment_view_s = backward compat.
        view_s = (params.get("watch_story")
                  or params.get("segment_view_s")
                  or {"min": 3, "max": 6})
        skip_sponsored = bool(params.get("skip_sponsored", True))
        like_chance = int(params.get("like_chance_percent", 0))

        if not _viewer.in_viewer(ctx):
            return StepResult.skipped("not_in_story_viewer")

        watched = 0
        sponsored_seen = 0
        liked = 0
        advanced = False        # czy sami przeszliśmy do następnego użytkownika
        for i in range(want):
            ctx.humanizer.sleep(rng.uniform(float(view_s.get("min", 3)),
                                            float(view_s.get("max", 6))))

            flat = _viewer.dismiss_reply_keyboard(ctx, _viewer.dump(ctx))
            if not _viewer.in_viewer(ctx):
                ctx.log("stories.closed_early", segment=i + 1, of=want)
                break

            marker = _viewer.sponsored_marker(flat)
            sponsored = marker is not None
            watched += 1
            if sponsored:
                sponsored_seen += 1
                # zapisujemy CO trafiło — inaczej nie odróżnimy realnej reklamy
                # od fałszywego trafienia w słowo kluczowe
                ctx.log("stories.sponsored_detected", segment=i + 1, marker=marker)

            if like_chance and not sponsored and rng.randint(1, 100) <= like_chance:
                if _like_segment(ctx, rng):
                    liked += 1

            if i >= want - 1:
                break

            flat = _viewer.dismiss_reply_keyboard(ctx, _viewer.dump(ctx))
            if not _viewer.in_viewer(ctx):
                # Ten sam powód co wyżej, tylko wykryty po lajku/odczycie —
                # też musi trafić do logu, inaczej seria „urywa się" bez śladu.
                ctx.log("stories.closed_early", segment=i + 1, of=want,
                        after="segment_read")
                break

            if sponsored and skip_sponsored:
                # Swipe w bok pomija CAŁĄ reklamę i ląduje u następnego
                # użytkownika. Wołający musi o tym wiedzieć, bo jego własne
                # przejście „dalej" przeskoczyłoby wtedy jedną osobę.
                ctx.log("stories.sponsored_swipe", segment=i + 1)
                _viewer.swipe_next_user(ctx, rng)
                advanced = True
                break
            _viewer.tap_next_segment(ctx, rng)

        ctx.log("stories.watch_segments.done", watched=watched, planned=want,
                sponsored=sponsored_seen, liked=liked, advanced=advanced)
        return StepResult.ok(watched=watched, planned=want,
                             sponsored=sponsored_seen, liked=liked,
                             advanced_to_next_user=advanced)


def _like_segment(ctx, rng) -> bool:
    """Double-tap w środek. Limit sprawdzany tuż przed, jak przy każdym lajku."""
    from v2.core import limits
    lim = limits.check(ctx, "like")
    if not lim.allowed:
        ctx.log("stories.like_limit", **lim.__dict__)
        return False
    w, h = _viewer.resolution(ctx)
    ctx.ht.tap(w // 2, h // 2, pause_kind="tap")
    time.sleep(0.08)            # techniczny odstęp double-tapu, nie pauza ludzka
    ctx.ht.tap(w // 2, h // 2, pause_kind="tap")
    limits.note_action(ctx, "like")
    ctx.log("stories.segment_liked")
    return True
