"""feed_browser — przeglądanie strony głównej i relacji z tacki.

Plugin bez ryzyka: buduje „ciepło" konta i naturalną historię aktywności,
niczego nie lajkując i nikogo nie obserwując.

**Brak interakcji jest wymuszony w kodzie, nie w ustawieniach.** Nie ma pola
w schemacie, którym dałoby się to włączyć, a lajkowanie relacji jest tu na
sztywno wyzerowane — nawet gdyby ktoś dopisał `like_chance_percent` do configu
konta ręcznie. To jedyny plugin, którego przebieg wolno puścić na świeżym
klonie bez zastanowienia, i ta własność ma wynikać z kodu, a nie z tego, że
nikt nie przestawił suwaka.

Jednostką budżetu jest POST, nie sesja relacji — `max_items` liczy przewinięcia
feedu, a oglądanie tacki jest domknięciem przebiegu, po pętli scrolla.
"""
from __future__ import annotations

import random

from v2.plugins import stepcall
from v2.plugins.base import Budget, PluginResult, draw_range
from v2.plugins.registry import register
from v2.steps import registry as steps

HOME = "home_feed"


@register
class FeedBrowser:
    id = "feed_browser"
    entry_screen = HOME
    exit_screen = HOME

    def run(self, ctx, s: dict, budget: Budget) -> PluginResult:
        rng = random.Random()
        stats = {"posts": 0, "ads": 0, "content_aware": 0, "fallback_dwell": 0,
                 "story_users": 0, "story_segments": 0}

        # NAJPIERW relacje, POTEM scroll — tacka jest na samej górze feedu
        # i znika po kilku przewinięciach. Pierwszy przebieg E5 na żywo poszedł
        # w odwrotnej kolejności i zobaczył `in_tray=0`, choć na górze ekranu
        # czekało trzech kandydatów. To jest zresztą naturalna kolejność:
        # po otwarciu aplikacji relacje widać, zanim cokolwiek przewiniemy.
        tray = s.get("tray_stories") or {}
        if tray.get("enabled") and not budget.user_stopped():
            self._watch_tray(ctx, tray, budget, rng, stats)

        stopped_by = self._scroll_feed(ctx, s, budget, rng, stats)

        ctx.navigator.ensure(HOME, max_steps=4)
        ctx.log("feed_browser.done", stopped_by=stopped_by, **stats)
        return PluginResult.done(budget, stopped_by, **stats)

    # ------------------------------------------------------------------
    def _scroll_feed(self, ctx, s: dict, budget: Budget, rng, stats) -> str:
        scroll = s.get("scroll") or {}
        every_n = int(scroll.get("long_pause_every_n", 0) or 0)

        while True:
            reason = budget.stop_reason()
            if reason:
                return reason

            res = _step(ctx, "feed.scroll", {
                "dwell_mode": scroll.get("dwell_mode", "content_aware"),
                "dwell_fallback_s": scroll.get("dwell_fallback_s"),
                # zakres dystansu swipe'a (min≈pełny post, max=ekstra) — z katalogu
                "distance": scroll.get("distance"),
            })
            if res.status == "failed":
                return "error"
            payload = res.payload or {}
            if payload.get("reason") == "end_of_feed":
                ctx.log("feed_browser.end_of_feed", posts=stats["posts"])
                return "empty"

            stats["posts"] += 1
            stats["ads"] += int(bool(payload.get("is_ad")))
            if payload.get("dwell_mode") == "fallback":
                stats["fallback_dwell"] += 1
            elif payload.get("dwell_mode") == "content_aware":
                stats["content_aware"] += 1
            budget.tick()

            if every_n and stats["posts"] % every_n == 0 and budget.stop_reason() is None:
                self._long_pause(ctx)

    def _long_pause(self, ctx) -> None:
        # Długa przerwa w scrollowaniu feedu — zakres z centralnego profilu
        # humanizera, a nie z `scroll.long_pause_s` (usunięte z katalogu).
        ctx.humanizer.pause("long_feed")

    # ------------------------------------------------------------------
    def _watch_tray(self, ctx, tray: dict, budget: Budget, rng, stats) -> None:
        """Otwórz relację z tacki i przechodź do kolejnych osób swipe'm w lewo.

        Zamykanie i otwieranie podglądu dla każdej osoby dałoby ten sam efekt,
        ale dwa razy więcej przejść UI — i inny ślad niż natywne przewijanie.
        """
        want = draw_range(tray.get("max_users"), rng, 3)
        watch = dict(tray.get("watch") or {})
        watch["like_chance_percent"] = 0        # wymuszone, patrz docstring modułu

        opened = _step(ctx, "feed.open_tray_story", {"prefer_unseen": True})
        if opened.status != "ok":
            return

        try:
            for i in range(want):
                if budget.user_stopped():
                    break
                seg = _step(ctx, "stories.watch_segments", watch)
                payload = seg.payload or {}
                if seg.status == "ok":
                    stats["story_users"] += 1
                    stats["story_segments"] += int(payload.get("watched") or 0)
                if i >= want - 1:
                    break
                if payload.get("advanced_to_next_user"):
                    # Krok pominął reklamę swipe'm i JUŻ jest u następnej osoby.
                    # Własne „dalej" przeskoczyłoby ją w całości.
                    continue
                nxt = _step(ctx, "stories.next_user", {})
                if nxt.status != "ok":
                    break            # ostatnia osoba w tacce — podgląd się zamknął
        finally:
            # ZAWSZE — inaczej epilog sesji zamyka aplikację stojąc w relacji
            _step(ctx, "stories.close", {"target": HOME})


def _step(ctx, name: str, params: dict):
    return stepcall.call(steps, ctx, name, params, source="feed_browser")
