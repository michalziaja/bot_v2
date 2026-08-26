"""stories.pick_ring_from_list — otwórz relację pierwszej osoby z listy, która ją ma.

Trzy rzeczy ze starego `mass_stories.py`, każda opłacona błędem na żywym koncie:

1. **Ring poznajemy po strukturze wiersza, nie po pikselach** — wiersz
   z aktywną relacją ma dodatkową nakładkę `android.view.View` w wrapperze
   awatara (patrz `steps/reels/_sheet.py`).

2. **Tapiemy w AWATAR, nie w nick.** Nick otwiera profil; relację otwiera
   tylko awatar. I tylko z lewej strony ekranu — po prawej stoi przycisk
   „Obserwuj", więc pomyłka kończy się obserwowaniem obcego konta.

3. **Weryfikujemy, co się otworzyło.** Tap czasem trafia w profil zamiast
   w relację (relacja właśnie wygasła albo ring był fałszywie wykryty) —
   wtedy BACK i próbujemy dalej, zamiast oglądać „segmenty" na profilu.
"""
from __future__ import annotations

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.reels import _sheet
from v2.steps.system._ui_helpers import settle_for_animation

FOLLOW_LIST_IDS = ("com.instagram.android:id/follow_list_username",
                   "com.instagram.android:id/row_user_primary_name")
SCRATCH_RECENT = "ring_recent"


@register
class PickRingFromList:
    name = "stories.pick_ring_from_list"
    requires_screen = None       # lista obserwujących albo lajkujących

    def run(self, ctx, params):
        list_screen = params.get("list_screen") or "follow_list"
        recent_cap = int(params.get("recent_cap", 3))
        username_ids = tuple(params.get("username_ids") or FOLLOW_LIST_IDS)

        recent = ctx.scratch.setdefault(SCRATCH_RECENT, [])
        # Wejście na listę: czekamy na animację, a potem zbieramy wiersze.
        settle_for_animation(ctx, max_wait_s=1.2, fallback_wait_s=0.8)
        rows = _sheet.collect_rows(
            flatten_hierarchy(ctx.device.hierarchy(force=True)), username_ids)

        cands = [r for r in rows
                 if r["ring"] is True and r["avatar"]
                 and r["username"] not in recent]
        if not cands:
            if rows and all(r["ring"] is None for r in rows):
                # Bez tego „brak relacji" nie do odróżnienia od „nie umiemy
                # wykryć relacji na tej wersji IG".
                ctx.log("stories.pick_ring.detection_unconfirmed", rows=len(rows))
            return StepResult.skipped("no_ring_candidates", rows=len(rows))

        target = cands[0]
        recent.append(target["username"])
        del recent[:-recent_cap]          # pamiętamy tylko ostatnich N

        ctx.ht.tap(*target["avatar"], pause_kind="tap")
        ctx.humanizer.pause("between_steps")

        top = ctx.navigator.identify().top
        if top != "story_viewer":
            # Otworzył się profil (albo nic) — wracamy na listę i mówimy
            # wołającemu, żeby spróbował następnej osoby.
            ctx.log("stories.pick_ring.not_a_story", username=target["username"],
                    landed=top)
            if top != list_screen:
                ctx.device.press_back()
                ctx.humanizer.pause("tap")
            if ctx.navigator.identify().top != list_screen:
                return StepResult.failed(f"po tapie w '{target['username']}' "
                                         f"zgubiliśmy listę (jesteśmy: {top})")
            return StepResult.skipped("not_a_story", username=target["username"])

        ctx.log("stories.pick_ring.opened", username=target["username"])
        return StepResult.ok(username=target["username"])
