"""Pass 4: po captchy — dump aktualnego ekranu i dokończ kroki (bez ponownego hasła).

Komentarze PL. Nie omija captchy. Nie commituje. Stock IG nie rusza.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.cli import build_context
from v2.core.screen_map import flatten_hierarchy
from v2.core.uiauto_server import ensure_running as ensure_uiauto
from v2.steps.registry import get as get_step
from v2.steps.registry import load_all
from v2.steps.system import login_log
from v2.steps.system.login_if_needed import _mentions_captcha

from _recon import (
    BASE,
    OUT,
    PKG,
    ActionRecorder,
    append_jsonl,
    dump,
    dump_activity,
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

# Komentarz: po captchy NIE wołamy login_if_needed (hasło już weszło)
POST_CAPTCHA_STEPS = [
    ("system.enter_verification_code_if_needed", {}, "Kod weryfikacyjny (jeśli jest)"),
    ("system.dismiss_notifications", {}, "Zamknięcie powiadomień"),
    ("system.sweep_tabs", {}, "Zakładki + popupy + weryfikacja"),
    ("profile.set_avatar", {}, "Zdjęcie profilowe (jeśli wybrane)"),
]

# Czekamy, aż człowiek wpisze captchę — polling, bez tapania Kontynuuj
CAPTCHA_POLL_S = 120
CAPTCHA_EVERY_S = 8


def _load_map() -> dict:
    return json.loads(STEP_MAP_PATH.read_text(encoding="utf-8"))


def _is_captcha(dev, ctx) -> tuple[bool, list]:
    """Czy aktualny flat to captcha IG."""
    try:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    except Exception:
        flat = []
    return _mentions_captcha(flat if isinstance(flat, list) else []), flat


def main() -> int:
    print("[pass4] uiautodev…", flush=True)
    ensure_uiauto(BASE)
    load_all()
    ctx = build_context(ACCOUNT)
    print(
        f"[pass4] session={ctx.session_id} pkg={ctx.ig_package}",
        flush=True,
    )
    if ctx.ig_package != PKG:
        print(f"[pass4] FATAL ig_package={ctx.ig_package} ≠ {PKG}", flush=True)
        return 2

    recorder = ActionRecorder(ctx)
    recorder.install()
    say_lines: list[str] = []
    login_log.say = _say_wrap(login_log.say, say_lines)  # type: ignore[method-assign]

    doc = _load_map()
    doc["pass4_session_id"] = ctx.session_id
    doc["pass4_started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_step_map(doc)

    rc = 0
    try:
        # Komentarz: ekran mógł zgasnąć — najpierw wake/unlock
        for name, label, nn in (
            ("system.wake", "Budzenie", "30"),
            ("system.unlock", "Odblokowanie", "31"),
        ):
            login_log.say(f"— {label}")
            say_lines.clear()
            recorder.pop_events()
            res = get_step(name)().run(ctx, {})
            meta = dump(ctx.device, f"{nn}_{name.replace('.', '_')}",
                        note=f"pass4 {label}", ctx=ctx)
            doc["steps"].append(_entry(
                nn, name.replace(".", "_"), label, res.status,
                res.payload, res.error, meta, recorder.pop_events(), say_lines))
            _write_step_map(doc)

        now = dump(ctx.device, "32_after_unlock_now",
                   note="pass4: ekran po wake/unlock", ctx=ctx)
        doc["steps"].append(_entry(
            "32", "after_unlock_now", "Ekran teraz", "ok", {}, None, now, []))
        _absorb_flags(doc, now)
        _write_step_map(doc)

        captcha, _ = _is_captcha(ctx.device, ctx)
        if captcha:
            print(
                "[pass4] CAPTCHA nadal na ekranie — czekam aż wpiszesz znaki "
                f"(max {CAPTCHA_POLL_S}s, NIE tapam Kontynuuj)",
                flush=True,
            )
            deadline = time.monotonic() + CAPTCHA_POLL_S
            n = 0
            while time.monotonic() < deadline:
                time.sleep(CAPTCHA_EVERY_S)
                n += 1
                captcha, _ = _is_captcha(ctx.device, ctx)
                print(f"[pass4] poll {n} captcha={captcha}", flush=True)
                if not captcha:
                    break
            poll = dump(
                ctx.device, "33_captcha_poll",
                note="pass4 po czekaniu na człowieka",
                ctx=ctx,
            )
            doc["steps"].append(_entry(
                "33", "captcha_poll", "Poll captcha",
                "ok" if not (poll.get("special") or {}).get("captcha") else "waiting",
                {"still_captcha": bool((poll.get("special") or {}).get("captcha"))},
                None, poll, []))
            _absorb_flags(doc, poll)
            _write_step_map(doc)
            if (poll.get("special") or {}).get("captcha"):
                print("[pass4] captcha nadal — STOP, człowiek na telefonie",
                      flush=True)
                doc["login_result"] = "częściowo"
                doc["stopped_at_step"] = "captcha_still_on_screen"
                doc["captcha_or_suspicious"] = True
                rc = 1
                return rc

        # Komentarz: captcha zniknęła — dokończ oficjalne kroki po loginie
        for i, (name, params, label) in enumerate(POST_CAPTCHA_STEPS, start=34):
            nn = str(i)
            slug = name.replace(".", "_")
            print(f"\n[pass4] === {nn} {name} — {label} ===", flush=True)
            login_log.say(f"— {label}")
            say_lines.clear()
            recorder.pop_events()
            if name == "system.enter_verification_code_if_needed":
                print(
                    f"[pass4] jeśli kod — POST /api/verification-code/{ACCOUNT}",
                    flush=True,
                )
            t0 = time.monotonic()
            try:
                res = get_step(name)().run(ctx, params)
            except Exception as e:
                dt = int((time.monotonic() - t0) * 1000)
                print(f"[pass4] WYJĄTEK {name}: {e}\n{traceback.format_exc()}",
                      flush=True)
                meta = dump(ctx.device, f"{nn}_{slug}",
                            note=f"wyjątek {name}: {e}", ctx=ctx,
                            extra={"want_fatal": True})
                doc["steps"].append(_entry(
                    nn, slug, label, "failed", {}, str(e), meta,
                    recorder.pop_events(), say_lines, duration_ms=dt))
                doc["stopped_at_step"] = name
                doc["login_result"] = "nie"
                rc = 1
                break
            dt = int((time.monotonic() - t0) * 1000)
            print(
                f"   {res.status:8s} {name}  {redact(res.payload)}"
                + (f"  error={res.error}" if res.error else ""),
                flush=True,
            )
            meta = dump(ctx.device, f"{nn}_{slug}",
                        note=f"pass4 po {name}", ctx=ctx)
            clicked = recorder.pop_events()
            doc["steps"].append(_entry(
                nn, slug, label, res.status, res.payload, res.error,
                meta, clicked, say_lines, duration_ms=dt))
            _absorb_flags(doc, meta, res)
            _write_step_map(doc)
            if (meta.get("special") or {}).get("captcha"):
                print("[pass4] captcha wróciła — STOP", flush=True)
                doc["stopped_at_step"] = name
                doc["login_result"] = "częściowo"
                doc["captcha_or_suspicious"] = True
                rc = 1
                break
            if res.status == "failed":
                doc["stopped_at_step"] = name
                doc["login_result"] = "nie"
                rc = 1
                break
        else:
            doc["login_result"] = "częściowo"
    finally:
        try:
            rest = get_step("system.restore_clone_identity")()
            rres = rest.run(ctx, {})
            meta = dump(ctx.device, "38_restore",
                        note="pass4 restore MAC", ctx=ctx)
            doc["steps"].append(_entry(
                "38", "restore", "Restore MAC", rres.status,
                rres.payload, rres.error, meta, []))
        except Exception as e:
            print(f"[pass4] restore: {e}", flush=True)
        final = dump_activity(ctx.device)
        doc["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        doc["final_focused_package"] = final.get("focused_package")
        doc["final_focused_activity"] = final.get("focused_activity")
        doc["final_clone_owns_focus"] = final.get("clone_owns_focus")
        if rc == 0 and final.get("clone_owns_focus") and not doc.get(
                "captcha_or_suspicious"):
            doc["login_result"] = "tak"
            doc["stopped_at_step"] = None
        doc["manual_todo"] = _manual_todo(doc, final)
        _write_step_map(doc)
        PLAYBOOK_PATH.write_text(_build_playbook(doc), encoding="utf-8")
        print(
            f"[pass4] result={doc.get('login_result')} focus="
            f"{doc.get('final_focused_package')}/"
            f"{doc.get('final_focused_activity')} "
            f"STEP_MAP={STEP_MAP_PATH}",
            flush=True,
        )
    append_jsonl(LOG_JSONL, {
        "kind": "pass4_done",
        "result": doc.get("login_result"),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
