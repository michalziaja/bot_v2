"""Pass 3: wake+unlock (ekran zdążył zgasnąć) → dismiss GMS → login.

Komentarze PL. Pass 2 trafił w AOD/lockscreen — stąd ten przebieg.
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
    ActionRecorder,
    append_jsonl,
    dump,
    dump_activity,
    parse_bounds,
    redact,
)
from run_login_continue import GMS_CANCEL_RID, _dismiss_gms
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


def main() -> int:
    print("[TRACE3] pass 3 — wake/unlock + GMS X + login", flush=True)
    ensure_uiauto(BASE)
    load_all()
    ctx = build_context(ACCOUNT)
    print(f"[TRACE3] session={ctx.session_id} ig={ctx.ig_package}", flush=True)
    recorder = ActionRecorder(ctx)
    recorder.install()
    say_lines: list[str] = []
    login_log.say = _say_wrap(login_log.say, say_lines)  # type: ignore[method-assign]
    doc = json.loads(STEP_MAP_PATH.read_text(encoding="utf-8"))
    doc["pass3_session_id"] = ctx.session_id
    rc = 0

    try:
        for nn, name, label in (
            ("20", "system.wake", "Budzenie (pass3)"),
            ("21", "system.unlock", "Unlock (pass3)"),
        ):
            print(f"[TRACE3] === {nn} {name} ===", flush=True)
            say_lines.clear()
            res = get_step(name)().run(ctx, {})
            print(f"   {res.status:8s} {name}  {redact(res.payload)}", flush=True)
            meta = dump(ctx.device, f"{nn}_{name.replace('.', '_')}",
                        note=f"{label} {res.status}", ctx=ctx)
            doc["steps"].append(_entry(
                nn, name.replace(".", "_"), label, res.status,
                res.payload, res.error, meta, recorder.pop_events(),
                say_lines))
            _write_step_map(doc)
            if res.status == "failed":
                doc["stopped_at_step"] = name
                doc["login_result"] = "nie"
                rc = 1
                return rc

        pre = dump(ctx.device, "22_after_unlock",
                   note="po wake/unlock — szukam GMS lub pól IG", ctx=ctx)
        doc["steps"].append(_entry(
            "22", "after_unlock", "Stan po unlock", "ok", {}, None, pre,
            recorder.pop_events()))
        _write_step_map(doc)

        if pre.get("focused_package") == "com.google.android.gms" or any(
            "gms:id/cancel" in (r or "") for r in (pre.get("resource_ids") or [])
        ):
            info = _dismiss_gms(ctx, recorder)
            print(f"[TRACE3] dismiss GMS: {info['how']}", flush=True)
            after = dump(ctx.device, "23_gms_dismissed",
                         note=f"GMS zamknięty {info['how']}", ctx=ctx)
            doc["steps"].append(_entry(
                "23", "gms_dismissed", "Dismiss GMS pass3", "ok",
                {"how": info["how"]}, None, after, info["clicked"]))
        else:
            # spróbuj X gdyby był w drzewie mimo innego focus
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
            node = None
            for n in flat:
                if GMS_CANCEL_RID in (n.get("resource-id") or ""):
                    node = n
                    break
            if node and center(node.get("bounds")):
                xy = center(node["bounds"])
                ctx.device.tap(*xy)
                time.sleep(0.8)
                after = dump(ctx.device, "23_gms_dismissed",
                             note=f"tap X {xy}", ctx=ctx)
                doc["steps"].append(_entry(
                    "23", "gms_dismissed", "Dismiss GMS pass3", "ok",
                    {"how": f"tap {xy}"}, None, after, recorder.pop_events()))
            else:
                after = pre

        extra = [
            ("24", "system.login_if_needed", "Logowanie pass3"),
            ("25", "system.enter_verification_code_if_needed", "Kod pass3"),
            ("26", "system.dismiss_notifications", "Notif pass3"),
            ("27", "system.sweep_tabs", "Zakładki pass3"),
        ]
        for nn, name, label in extra:
            print(f"\n[TRACE3] === {nn} {name} ===", flush=True)
            say_lines.clear()
            recorder.pop_events()
            if name == "system.enter_verification_code_if_needed":
                print(
                    f"[TRACE3] CZEKAM na POST /api/verification-code/{ACCOUNT} "
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
            if (name == "system.login_if_needed"
                    and res.status == "skipped"):
                print("[TRACE3] login skip — dismiss GMS + retry", flush=True)
                dump(ctx.device, "24b_pre_retry", note="przed retry", ctx=ctx)
                _dismiss_gms(ctx, recorder)
                time.sleep(0.7)
                res = get_step(name)().run(ctx, {})
                dt = int((time.monotonic() - t0) * 1000)
                print(f"   RETRY {res.status} {redact(res.payload)}", flush=True)
            meta = dump(
                ctx.device, f"{nn}_{name.replace('.', '_')}",
                note=f"{label} {res.status}", ctx=ctx,
                extra={"step_payload": redact(res.payload),
                       "login_log": list(say_lines),
                       "want_fatal": res.status == "failed"},
            )
            doc["steps"].append(_entry(
                nn, name.replace(".", "_"), label, res.status,
                res.payload, res.error, meta, recorder.pop_events(),
                say_lines, duration_ms=dt))
            _absorb_flags(doc, meta, res)
            _write_step_map(doc)
            append_jsonl(LOG_JSONL, {
                "kind": "step3", "nn": nn, "name": name,
                "status": res.status, "payload": redact(res.payload),
                "focused_package": meta.get("focused_package"),
                "focused_activity": meta.get("focused_activity"),
                "identify_top": meta.get("identify_top"),
            })
            if res.status == "failed":
                doc["stopped_at_step"] = name
                doc["login_result"] = "nie"
                rc = 1
                break
    finally:
        try:
            rres = get_step("system.restore_clone_identity")().run(ctx, {})
            print(f"   {rres.status:8s} restore {redact(rres.payload)}", flush=True)
            meta = dump(ctx.device, "28_restore", note="pass3 restore", ctx=ctx)
            doc["steps"].append(_entry(
                "28", "restore", "Restore pass3", rres.status,
                rres.payload, rres.error, meta, recorder.pop_events()))
        except Exception as e:
            print(f"[TRACE3] restore: {e}", flush=True)
        final = dump_activity(ctx.device)
        doc["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        doc["final_focused_package"] = final.get("focused_package")
        doc["final_focused_activity"] = final.get("focused_activity")
        doc["final_clone_owns_focus"] = final.get("clone_owns_focus")
        logged = any(
            "login_if_needed" in (s.get("slug") or "")
            and s.get("status") == "ok"
            and (s.get("payload") or {}).get("submitted")
            for s in doc["steps"]
        )
        if doc.get("login_result") != "nie":
            if logged and final.get("clone_owns_focus") and not doc.get(
                    "captcha_or_suspicious"):
                doc["login_result"] = "tak"
            else:
                doc["login_result"] = "częściowo" if logged or final.get(
                    "clone_owns_focus") else "nie"
        doc["manual_todo"] = _manual_todo(doc, final)
        _write_step_map(doc)
        PLAYBOOK_PATH.write_text(_build_playbook(doc), encoding="utf-8")
        print(
            f"[TRACE3] result={doc['login_result']} focus="
            f"{doc['final_focused_package']}/{doc['final_focused_activity']}",
            flush=True,
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
