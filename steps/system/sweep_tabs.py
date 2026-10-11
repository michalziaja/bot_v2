"""system.sweep_tabs — Home → Szukaj → Rolki → Profil; na każdej zamykamy popupy.

Nowe konto dostaje intro / sheet na kolejnych zakładkach. Przechodzimy
wszystkie taby dolnego paska, potwierdzamy/odrzucamy popupy i jeśli
wyskoczy ekran kodu — wołamy weryfikację z panelu.
"""
from __future__ import annotations

import time

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.system import login_log
from v2.steps.system._ui_helpers import center, find_field
from v2.steps.system.enter_verification_code_if_needed import (
    CODE_FIELD_HINTS,
    EnterVerificationCodeIfNeeded,
)
from v2.steps.system.login_if_needed import _confirm_post_login_popups

# Komentarz: kolejność jak na A50 (feed / search / clips / profile)
TAB_RIDS = (
    "com.instagram.android:id/feed_tab",
    "com.instagram.android:id/search_tab",
    "com.instagram.android:id/clips_tab",
    "com.instagram.android:id/profile_tab",
)


@register
class SweepTabs:
    name = "system.sweep_tabs"
    requires_screen = None

    def run(self, ctx, params):
        visited: list[str] = []
        popups_total = 0
        verify_hits = 0
        for rid in TAB_RIDS:
            short = rid.rsplit("/", 1)[-1]
            if not _tap_tab(ctx, rid):
                login_log.say(f"Zakładka {short} — brak na pasku, pomijam")
                continue
            time.sleep(0.7)
            login_log.say(f"Zakładka {short} — zamykam popupy…")
            popups_total += _confirm_post_login_popups(ctx, max_steps=6)
            if _code_screen_visible(ctx):
                login_log.say("Na zakładce widać weryfikację — wpisuję kod…")
                res = EnterVerificationCodeIfNeeded().run(ctx, params)
                if res.status == "ok":
                    verify_hits += 1
            visited.append(short)
        top = ctx.navigator.identify().top
        login_log.say(
            f"Przeszłem zakładki {visited or '—'} "
            f"(popupów={popups_total}, weryfikacji={verify_hits})")
        ctx.log("system.sweep_tabs",
                visited=visited, popups=popups_total, verify=verify_hits, top=top)
        return StepResult.ok(
            visited=visited, popups=popups_total, verify=verify_hits, top=top)


def _tap_tab(ctx, rid: str) -> bool:
    """Tap w tab po resource-id (klony mają znormalizowany prefiks android)."""
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    node = next((e for e in flat if (e.get("resource-id") or "") == rid), None)
    if not node or not node.get("bounds"):
        return False
    ctx.ht.tap(*center(node["bounds"]), pause_kind="tap")
    return True


def _code_screen_visible(ctx) -> bool:
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    return find_field(flat, (), CODE_FIELD_HINTS, "EditText") is not None
