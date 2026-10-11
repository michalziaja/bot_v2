"""system.apply_clone_identity — MAC na wlan0 przed startem klona IG."""
from __future__ import annotations

from pathlib import Path

from v2.cloner import mac_runtime
from v2.runner.step import StepResult
from v2.steps.registry import register

# Komentarz: cli.py i JobRunner trzymają DATA_ROOT — tu ten sam układ v2/data
_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


@register
class ApplyCloneIdentity:
    name = "system.apply_clone_identity"
    requires_screen = None

    def run(self, ctx, params):
        if not ctx.account.step_enabled("apply_clone_identity"):
            return StepResult.skipped("disabled_in_config")
        # Komentarz: IMEI tylko w APK (hook smali) — tu wyłącznie MAC na wlan0
        mac = (ctx.account.device.get("spoofed_mac") or "").strip()
        if not mac:
            return StepResult.skipped("no_spoofed_mac")
        alias = ctx.account.account_id
        persist = mac_runtime.persist_path(_DATA_ROOT, alias)
        try:
            info = mac_runtime.apply_wifi_mac(ctx.device, mac, persist)
        except mac_runtime.MacSpoofError as e:
            # Komentarz: brak roota nie blokuje sesji — hook APK nadal działa
            return StepResult.skipped(f"mac_spoof_failed:{e}")
        ctx.log("clone.mac_apply", **info)
        return StepResult.ok(**info)
