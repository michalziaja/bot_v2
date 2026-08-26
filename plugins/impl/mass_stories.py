"""mass_stories — oglądanie relacji obserwujących wybrane konta-targety.

Port `instagram_bot/actions/mass_stories.py` (614 linii klasy) na warstwę
pluginu. Cała logika UI siedzi w krokach; tutaj jest kolejność, budżet
i decyzja, kiedy przejść do następnego targetu.

Przebieg dla jednego targetu:

    wyszukiwarka → profil targetu → lista obserwujących
    → pętla: znajdź osobę z aktywną relacją → obejrzyj segmenty → zamknij
             → gdy brak kandydatów: przewiń listę → powtórz
    → wyjście na home

Jednostką budżetu jest TARGET, nie relacja — `max_ring_opens` ogranicza pracę
w obrębie jednego targetu.

Czego pilnujemy (lekcje ze starego bota, każda opłacona błędem na koncie):
kontrolny pół-scroll przed pierwszym dumpem, wykrywanie dna listy po
powtórzonym zestawie wierszy, tap w awatar (nie w nick), weryfikacja że
otworzyła się relacja, `recent` żeby nie wracać do tych samych osób,
i `finally` zamykające podgląd relacji.
"""
from __future__ import annotations

import random

from v2.plugins import stepcall
from v2.plugins.base import Budget, PluginResult, draw_range
from v2.plugins.registry import register
from v2.steps import registry as steps

HOME = "home_feed"
LIST_SCREEN = "follow_list"


@register
class MassStories:
    id = "mass_stories"
    entry_screen = HOME
    exit_screen = HOME

    def run(self, ctx, s: dict, budget: Budget) -> PluginResult:
        rng = random.Random()
        stats = {"targets": 0, "profiles": 0, "segments": 0,
                 "not_a_story": 0, "lists_exhausted": 0}

        targets = _pick_targets(s, rng)
        if not targets:
            ctx.log("mass_stories.no_targets")
            return PluginResult.done(budget, "empty", **stats)

        stopped_by = "items"
        for target in targets:
            reason = budget.stop_reason()
            if reason:
                stopped_by = reason
                break

            done = self._one_target(ctx, s, target, budget, rng, stats)
            budget.tick()
            stats["targets"] += 1
            if done == "user":
                stopped_by = "user"
                break

        ctx.navigator.ensure(HOME, max_steps=4)
        ctx.log("mass_stories.done", stopped_by=stopped_by, **stats)
        return PluginResult.done(budget, stopped_by, **stats)

    # ------------------------------------------------------------------
    def _one_target(self, ctx, s: dict, target: str, budget, rng, stats):
        ctx.log("mass_stories.target_start", target=target)
        per = s.get("per_target") or {}
        watch = dict((s.get("story_watch") or {}))
        max_opens = draw_range(per.get("max_ring_opens"), rng, 6)
        max_scrolls = int(per.get("max_scrolls", 15))
        recent_cap = int(per.get("recent_cap", 3))

        # nowy target = nowa lista: czyścimy pamięć podręczną poprzedniej
        for key in ("ring_recent", "list_snapshot", "list_stalls"):
            ctx.scratch.pop(key, None)

        if not self._go_to_followers(ctx, s, target):
            return "next"

        opened = 0
        scrolls = 0
        first_look = True
        try:
            while opened < max_opens and scrolls <= max_scrolls:
                if budget.user_stopped():
                    return "user"

                pick = _step(ctx, "stories.pick_ring_from_list", {
                    "list_screen": LIST_SCREEN, "recent_cap": recent_cap})

                if pick.status == "failed":
                    break                       # zgubiliśmy listę
                if pick.status == "skipped":
                    if (pick.payload or {}).get("reason") == "not_a_story":
                        stats["not_a_story"] += 1
                        continue                # tamten wiersz jest już w `recent`
                    # brak kandydatów na ekranie → przewiń i szukaj dalej
                    scroll = _step(ctx, "list.scroll_until_new", {
                        "username_ids": _sheet_ids(),
                        "control_half_scroll": first_look,
                        "max_scrolls": 3})
                    first_look = False
                    scrolls += 1
                    if scroll.status != "ok":
                        stats["lists_exhausted"] += 1
                        break                   # dno listy
                    continue

                seg = _step(ctx, "stories.watch_segments", watch)
                if seg.status == "ok":
                    stats["profiles"] += 1
                    stats["segments"] += int((seg.payload or {}).get("watched") or 0)
                opened += 1

                # Wracamy na listę po KAŻDEJ relacji — swipe „dalej" wiódłby
                # do relacji kolejnej osoby z tacki IG, a nie z naszej listy.
                _step(ctx, "stories.close", {"target": LIST_SCREEN})
        finally:
            # ZAWSZE: podgląd relacji zamknięty, telefon na stronie głównej
            _step(ctx, "stories.close", {})
            ctx.navigator.ensure(HOME, max_steps=4)
        return "next"

    def _go_to_followers(self, ctx, s: dict, target: str) -> bool:
        for name, params in (
            ("search.open", {}),
            ("search.type_query", {"query": f"@{target}"}),
            ("search.pick_result", {"username": target, "prefer_exact": True}),
            ("profile.open_followers", {"source": (s.get("targets") or {}).get("source",
                                                                              "followers")}),
        ):
            r = _step(ctx, name, params)
            if r.status != "ok":
                ctx.log("mass_stories.target_unreachable", target=target,
                        step=name, status=r.status)
                ctx.navigator.ensure(HOME, max_steps=4)
                return False
        return True


# ============================================================
def _pick_targets(s: dict, rng) -> list[str]:
    cfg = s.get("targets") or {}
    lst = [str(t).strip().lstrip("@") for t in (cfg.get("list") or []) if str(t).strip()]
    if not lst:
        return []
    if (cfg.get("order") or "random") == "random":
        lst = list(lst)
        rng.shuffle(lst)
    return lst


def _sheet_ids():
    from v2.steps.stories.pick_ring_from_list import FOLLOW_LIST_IDS
    return list(FOLLOW_LIST_IDS)


def _step(ctx, name: str, params: dict):
    return stepcall.call(steps, ctx, name, params, source="mass_stories")
