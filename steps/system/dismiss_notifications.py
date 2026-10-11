"""system.dismiss_notifications — zwiń shade + zamknij prompt powiadomień IG.

Po pierwszym logowaniu IG i Android potrafią zostawić:
  * rozwinięty pasek powiadomień,
  * dialog POST_NOTIFICATIONS,
  * intro „Włącz powiadomienia".
Zawsze zamykamy / odmawiamy — nigdy nie włączamy pushy.
"""
from __future__ import annotations

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.system import login_log
from v2.steps.system.login_if_needed import _confirm_post_login_popups


@register
class DismissNotifications:
    name = "system.dismiss_notifications"
    requires_screen = None

    def run(self, ctx, params):
        collapsed = _collapse_statusbar(ctx)
        login_log.say("Zamykam powiadomienia i prośby o push…")
        # Komentarz: ta sama pętla co po logowaniu — skip powiadomień / deny
        popups = _confirm_post_login_popups(ctx, max_steps=6)
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        top = ctx.navigator.identify().top
        login_log.say(
            f"Powiadomienia: shade={'zwinięty' if collapsed else 'skip'}, "
            f"popupów={popups}, ekran={top or '?'}")
        ctx.log("system.dismiss_notifications",
                collapsed=collapsed, popups=popups, top=top, nodes=len(flat))
        return StepResult.ok(collapsed=collapsed, popups=popups, top=top)


def _collapse_statusbar(ctx) -> bool:
    """Zwiń systemowy shade — `cmd statusbar collapse` (A11+)."""
    try:
        ctx.device.shell("cmd statusbar collapse")
        return True
    except Exception:
        return False
