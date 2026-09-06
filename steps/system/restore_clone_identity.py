"""system.restore_clone_identity — przywróć MAC wlan0 po zamknięciu IG."""
from __future__ import annotations

from pathlib import Path

from v2.cloner import mac_runtime
from v2.runner.step import StepResult
from v2.steps.registry import register

_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


@register
class RestoreCloneIdentity:
    name = "system.restore_clone_identity"
    requires_screen = None

    def run(self, ctx, params):
        if not ctx.account.step_enabled("restore_clone_identity"):
            return StepResult.skipped("disabled_in_config")
        alias = ctx.account.account_id
        persist = mac_runtime.persist_path(_DATA_ROOT, alias)
        try:
            info = mac_runtime.restore_wifi_mac(ctx.device, persist)
        except mac_runtime.MacSpoofError as e:
            return StepResult.skipped(f"mac_restore_failed:{e}")
        ctx.log("clone.mac_restore", **info)
        if not info.get("restored"):
            return StepResult.skipped(info.get("reason") or "nothing_to_restore")
        return StepResult.ok(**info)
