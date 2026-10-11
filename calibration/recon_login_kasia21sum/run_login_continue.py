"""Pass 2: zamknij overlay GMS passkey i dokończ login kasia21sum.

Komentarze PL. Oficjalny cmd_login padł na AssistedSignIn — tu tap X
(`com.google.android.gms:id/cancel`) i te same stepy login/verify/sweep.
Hasła nie drukujemy. Stock IG nie ruszamy.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.cli import build_context
from v2.core.screen_map import flatten_hierarchy
from v2.core.uiauto_server import ensure_running as ensure_uiauto
from v2.steps.registry import get as get_step
from v2.steps.registry import load_all
from v2.steps.system import login_log
from v2.steps.system._ui_helpers import center

from _recon import (
    BASE,
    OUT,
    PKG,
    ActionRecorder,
    append_jsonl,
    dump,
    dump_activity,
    node_at,
    parse_bounds,
    redact,
)
from run_login_trace import (
    ACCOUNT,
    LOG_JSONL,
    PLAYBOOK_PATH,
    STEP_MAP_PATH,
    _absorb_flags,
    _build_playbook,
    _entry,
    _manual_todo,
    _say_wrap,
    _write_step_map,
)

GMS_CANCEL_RID = "com.google.android.gms:id/cancel"
GMS_OUTSIDE_RID = "com.google.android.gms:id/touch_outside"


def _load_map() -> dict:
    return json.loads(STEP_MAP_PATH.read_text(encoding="utf-8"))


def _find_gms_cancel(flat: list) -> dict | None:
    """X / Anuluj na bottom sheet GMS — nie tapaj passkey."""
    for n in flat:
        rid = (n.get("resource-id") or "")
        desc = (n.get("content-desc") or "").lower()
        if GMS_CANCEL_RID in rid or desc == "anuluj":
            if n.get("clickable") or parse_bounds(n.get("bounds")):
                return n
    return None


def _dismiss_gms(ctx, recorder: ActionRecorder) -> dict:
    """Zamknij AssistedSignIn — najpierw X, potem BACK, potem tap poza sheet."""
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    node = _find_gms_cancel(flat)
    how = ""
    if node:
        xy = center(node["bounds"])
        if xy:
            how = f"tap cancel {xy} rid={node.get('resource-id')}"
            ctx.device.tap(*xy)
            time.sleep(0.9)
    if not how:
        ctx.device.press_back()
        how = "press_back"
        time.sleep(0.9)
    act = dump_activity(ctx.device)
    if act.get("focused_package") == "com.google.android.gms":
        # Komentarz: tap w górną połowę (poza sheet ~y=1827)
        ctx.device.tap(540, 900)
        how += " + tap_outside(540,900)"
        time.sleep(0.8)
    return {"how": how, "clicked": recorder.pop_events()}


def main() -> int:
    print("[TRACE2] pass 2 — dismiss GMS + login", flush=True)
    ensure_uiauto(BASE)
    load_all()
    ctx = build_context(ACCOUNT)
    print(
        f"[TRACE2] session={ctx.session_id} ig={ctx.ig_package}",
        flush=True,
    )
    recorder = ActionRecorder(ctx)
    recorder.install()
    say_lines: list[str] = []
    login_log.say = _say_wrap(login_log.say, say_lines)  # type: ignore[method-assign]

    doc = _load_map()
    doc["continue_session_id"] = ctx.session_id
    doc["login_result"] = "częściowo"

    pre = dump(ctx.device, "11_gms_before_dismiss",
               note="overlay GMS przed tap X", ctx=ctx)
    doc["steps"].append(_entry(
        "11", "gms_before_dismiss", "GMS passkey przed zamknięciem",
        "ok", {}, None, pre, []))
    _write_step_map(doc)

    info = _dismiss_gms(ctx, recorder)
    print(f"[TRACE2] dismiss GMS: {info['how']}", flush=True)
    after = dump(
        ctx.device, "12_gms_after_dismiss",
        note=f"po zamknięciu GMS ({info['how']})", ctx=ctx,
        extra={"clicked": info["how"]},
    )
    doc["steps"].append(_entry(
        "12", "gms_after_dismiss", "Zamknięcie overlay GMS",
        "ok", {"how": info["how"]}, None, after, info["clicked"]))
    _absorb_flags(doc, after)
    _write_step_map(doc)

    # Jeśli GMS wrócił — jeszcze raz X
    if after.get("focused_package") == "com.google.android.gms":
        print("[TRACE2] GMS nadal focus — drugi dismiss", flush=True)
        info2 = _dismiss_gms(ctx, recorder)
        after = dump(
            ctx.device, "12b_gms_after_dismiss2",
            note=f"drugi dismiss ({info2['how']})", ctx=ctx)
        doc["steps"].append(_entry(
            "12b", "gms_after_dismiss2", "Drugi dismiss GMS",
            "ok", {"how": info2["how"]}, None, after, info2["clicked"]))
        _write_step_map(doc)

    extra_steps = [
        ("13", "system.login_if_needed", "Logowanie po dismiss GMS"),
        ("14", "system.enter_verification_code_if_needed", "Kod weryfikacyjny"),
        ("15", "system.dismiss_notifications", "Powiadomienia"),
        ("16", "system.sweep_tabs", "Zakładki"),
    ]
    rc = 0
    try:
        for nn, name, label in extra_steps:
            print(f"\n[TRACE2] === {nn} {name} ===", flush=True)
            login_log.say(f"— {label}")
            say_lines.clear()
            recorder.pop_events()
            if name == "system.enter_verification_code_if_needed":
                print(
                    f"[TRACE2] CZEKAM na POST /api/verification-code/{ACCOUNT} "
                    "jeśli widać kod",
                    flush=True,
                )
            t0 = time.monotonic()
            res = get_step(name)().run(ctx, {})
            dt = int((time.monotonic() - t0) * 1000)
            print(
                f"   {res.status:8s} {name}  {redact(res.payload)}"
                + (f"  error={res.error}" if res.error else ""),
                flush=True,
            )
            # Jeśli login znowu skip przez GMS — dismiss i raz jeszcze
            if (name == "system.login_if_needed"
                    and res.status == "skipped"
                    and (res.payload or {}).get("reason") == "login_screen_not_detected"):
                print("[TRACE2] login skip — GMS wrócił? dismiss + retry", flush=True)
                dump(ctx.device, "13b_login_skip_retry_pre",
                     note="login skip, retry po dismiss", ctx=ctx)
                _dismiss_gms(ctx, recorder)
                time.sleep(0.8)
                res = get_step(name)().run(ctx, {})
                dt = int((time.monotonic() - t0) * 1000)
                print(
                    f"   RETRY {res.status:8s} {name}  {redact(res.payload)}",
                    flush=True,
                )
            meta = dump(
                ctx.device, f"{nn}_{name.replace('.', '_')}",
                note=f"{label} status={res.status}", ctx=ctx,
                extra={"step_status": res.status,
                       "step_payload": redact(res.payload),
                       "step_error": res.error,
                       "login_log": list(say_lines),
                       "want_fatal": res.status == "failed"},
            )
            clicked = recorder.pop_events()
            doc["steps"].append(_entry(
                nn, name.replace(".", "_"), label, res.status,
                res.payload, res.error, meta, clicked, say_lines,
                duration_ms=dt))
            _absorb_flags(doc, meta, res)
            _write_step_map(doc)
            append_jsonl(LOG_JSONL, {
                "kind": "step2", "nn": nn, "name": name,
                "status": res.status, "payload": redact(res.payload),
                "error": res.error, "clicked": clicked,
                "identify_top": meta.get("identify_top"),
                "focused_package": meta.get("focused_package"),
                "focused_activity": meta.get("focused_activity"),
            })
            if res.status == "failed":
                doc["stopped_at_step"] = name
                doc["login_result"] = "nie"
                rc = 1
                break
    finally:
        try:
            rest = get_step("system.restore_clone_identity")()
            rres = rest.run(ctx, {})
            print(
                f"   {rres.status:8s} restore  {redact(rres.payload)}",
                flush=True,
            )
            meta = dump(ctx.device, "17_restore", note="finally restore", ctx=ctx)
            doc["steps"].append(_entry(
                "17", "restore", "Restore MAC (pass2)", rres.status,
                rres.payload, rres.error, meta, recorder.pop_events()))
        except Exception as e:
            print(f"[TRACE2] restore: {e}", flush=True)
        final = dump_activity(ctx.device)
        doc["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        doc["final_focused_package"] = final.get("focused_package")
        doc["final_focused_activity"] = final.get("focused_activity")
        doc["final_clone_owns_focus"] = final.get("clone_owns_focus")
        if doc["login_result"] != "nie":
            logged = any(
                s.get("slug") == "system_login_if_needed"
                and s.get("status") == "ok"
                and (s.get("payload") or {}).get("submitted")
                for s in doc["steps"]
            )
            if logged and final.get("clone_owns_focus") and not doc.get(
                    "captcha_or_suspicious"):
                doc["login_result"] = "tak"
            elif logged:
                doc["login_result"] = "częściowo"
            else:
                doc["login_result"] = "częściowo"
        doc["manual_todo"] = _manual_todo(doc, final)
        _write_step_map(doc)
        PLAYBOOK_PATH.write_text(_build_playbook(doc), encoding="utf-8")
        print(
            f"[TRACE2] result={doc['login_result']} focus="
            f"{doc['final_focused_package']}/{doc['final_focused_activity']}",
            flush=True,
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
