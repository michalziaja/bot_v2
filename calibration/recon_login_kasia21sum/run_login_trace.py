"""LIVE tracer logowania kasia21sum — te same kroki co cli.cmd_login.

Komentarze PL. Lock przez build_context (owner=kasia21sum).
Nie commituje. Hasła nie drukuje. Stock IG nie rusza.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.cli import build_context
from v2.core.uiauto_server import ensure_running as ensure_uiauto
from v2.steps.registry import get as get_step
from v2.steps.registry import load_all
from v2.steps.system import login_log

# Komentarz: helper dumpów z tego samego katalogu
from _recon import (
    BASE,
    OUT,
    PKG,
    STOCK_PKG,
    ActionRecorder,
    append_jsonl,
    clear_logcat,
    dump,
    dump_activity,
    redact,
)

ACCOUNT = "kasia21sum"
LOG_JSONL = OUT / "TRACE.jsonl"
STEP_MAP_PATH = OUT / "STEP_MAP.json"
PLAYBOOK_PATH = OUT / "PLAYBOOK.md"

# Komentarz: 1:1 z cli.py cmd_login linie ~263-273
STEPS = [
    ("system.wake", {}, "Budzenie telefonu"),
    ("system.unlock", {}, "Odblokowanie"),
    ("system.apply_clone_identity", {}, "Tożsamość klona (MAC)"),
    ("system.ensure_ig_running", {}, "Uruchamiam Instagram"),
    ("system.login_if_needed", {}, "Logowanie (login/hasło + popupy)"),
    ("system.enter_verification_code_if_needed", {}, "Kod weryfikacyjny (jeśli jest)"),
    ("system.dismiss_notifications", {}, "Zamknięcie powiadomień"),
    ("system.sweep_tabs", {}, "Zakładki + popupy + weryfikacja"),
    ("profile.set_avatar", {}, "Zdjęcie profilowe (jeśli wybrane)"),
]


def _step_slug(name: str) -> str:
    return name.replace(".", "_")


def _write_step_map(doc: dict) -> None:
    STEP_MAP_PATH.write_text(
        json.dumps(redact(doc), ensure_ascii=False, indent=2), encoding="utf-8")


def _say_wrap(orig, lines: list[str]):
    """Przechwyt login_log.say — m.in. 'czekam na kod z panelu'."""

    def wrapped(msg: str) -> None:
        lines.append(msg or "")
        low = (msg or "").lower()
        if "czekam" in low and "panel" in low:
            print(
                f"[TRACE] CZEKAM na POST /api/verification-code/{ACCOUNT} "
                f"(krok enter_verification_code_if_needed)",
                flush=True,
            )
            append_jsonl(LOG_JSONL, {
                "kind": "waiting_verification",
                "account": ACCOUNT,
                "msg": msg,
                "hint": f"POST /api/verification-code/{ACCOUNT}",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
        return orig(msg)

    return wrapped


def _build_playbook(doc: dict) -> str:
    """Krótki PLAYBOOK z zaobserwowanej sekwencji — na przyszłe logowania."""
    lines = [
        f"# PLAYBOOK logowania `{ACCOUNT}`",
        "",
        f"Pakiet klona: `{PKG}` (NIE stock `{STOCK_PKG}`).",
        f"Wynik: **{doc.get('login_result')}**  "
        f"stanęło: `{doc.get('stopped_at_step') or 'koniec'}`.",
        f"Sesja: `{doc.get('session_id')}`",
        "",
        "## Sekwencja",
        "",
    ]
    popups: list[str] = []
    for st in doc.get("steps") or []:
        status = st.get("status") or "?"
        top = st.get("identify_top") or "?"
        focus = f"{st.get('focused_package') or '?'}/{st.get('focused_activity') or '?'}"
        lines.append(
            f"- `{st.get('nn')}_{st.get('slug')}` **{status}** "
            f"top=`{top}` focus=`{focus}`"
        )
        if st.get("error"):
            lines.append(f"  - błąd: {st['error']}")
        note = st.get("note_pl") or ""
        if note:
            lines.append(f"  - {note}")
        clicked = st.get("clicked") or []
        if clicked:
            bits = []
            for c in clicked:
                bits.append(
                    (c.get("text") or c.get("rid") or c.get("kind") or "?")
                )
            lines.append(f"  - kliknięto: {', '.join(bits)}")
            popups.extend(bits)
        special = st.get("special") or {}
        flags = [k for k, v in special.items() if v]
        if flags:
            lines.append(f"  - flagi: {', '.join(flags)}")
        texts = st.get("visible_texts") or []
        if texts[:8]:
            preview = "; ".join(texts[:8])
            lines.append(f"  - teksty: {preview}")
    lines += [
        "",
        "## Popupy / kliknięcia",
        "",
    ]
    if popups:
        for p in popups:
            lines.append(f"- {p}")
    else:
        lines.append("- (brak zarejestrowanych tapów albo kroki skip)")
    lines += [
        "",
        "## Co działa / co pada",
        "",
        f"- weryfikacja kodu: {'TAK' if doc.get('verification') else 'nie'}",
        f"- captcha / suspicious: {'TAK — człowiek na telefonie' if doc.get('captcha_or_suspicious') else 'nie'}",
        f"- crash splash `Resources$NotFoundException #0x7f082060`: "
        f"{'TAK — NIE naprawiać APK w tym reconie' if doc.get('splash_crash') else 'nie'}",
        f"- focus końcowy: `{doc.get('final_focused_package')}/"
        f"{doc.get('final_focused_activity')}`",
        f"- clone_owns_focus: {doc.get('final_clone_owns_focus')}",
        "",
        "## Ręcznie dokończyć",
        "",
        doc.get("manual_todo") or "- nic, albo patrz STEP_MAP",
        "",
        f"Dumpy: `{OUT}`",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if LOG_JSONL.exists():
        LOG_JSONL.unlink()

    print("[TRACE] upewniam uiautodev…", flush=True)
    ensure_uiauto(BASE)
    print("[TRACE] uiautodev żyje", flush=True)

    load_all()
    ctx = build_context(ACCOUNT)
    print(
        f"[TRACE] account={ACCOUNT} session={ctx.session_id} "
        f"ig_package={ctx.ig_package} lock=acquired",
        flush=True,
    )
    if ctx.ig_package != PKG:
        print(f"[TRACE] FATAL: ig_package={ctx.ig_package} ≠ {PKG}", flush=True)
        return 2

    recorder = ActionRecorder(ctx)
    recorder.install()
    say_lines: list[str] = []
    login_log.say = _say_wrap(login_log.say, say_lines)  # type: ignore[method-assign]

    doc = {
        "account": ACCOUNT,
        "session_id": ctx.session_id,
        "clone_package": PKG,
        "stock_package": STOCK_PKG,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ended_at": None,
        "login_result": "partial",
        "stopped_at_step": None,
        "verification": False,
        "captcha_or_suspicious": False,
        "splash_crash": False,
        "final_focused_package": None,
        "final_focused_activity": None,
        "final_clone_owns_focus": None,
        "manual_todo": "",
        "steps": [],
    }
    _write_step_map(doc)

    # 00 — stan startowy zanim ruszymy kroki CLI
    pre = dump(ctx.device, "00_pre", note="stan startowy przed wake", ctx=ctx)
    doc["steps"].append(_entry("00", "pre", "Stan startowy", "ok", {}, None, pre, []))
    _write_step_map(doc)
    append_jsonl(LOG_JSONL, {"kind": "pre", **{k: pre.get(k) for k in (
        "identify_top", "focused_package", "focused_activity",
        "dominant_package", "visible_texts", "resource_ids")}})

    rc = 0
    try:
        for i, (name, params, label) in enumerate(STEPS, start=1):
            nn = f"{i:02d}"
            slug = _step_slug(name)
            print(f"\n[TRACE] === {nn} {name} — {label} ===", flush=True)
            login_log.say(f"— {label}")
            say_lines.clear()
            recorder.pop_events()

            if name == "system.ensure_ig_running":
                clear_logcat(ctx.device)
                print("[TRACE] logcat wyczyszczony przed launch klona", flush=True)

            if name == "system.enter_verification_code_if_needed":
                print(
                    f"[TRACE] jeśli IG prosi o kod — czekam na "
                    f"POST /api/verification-code/{ACCOUNT}",
                    flush=True,
                )

            step = get_step(name)()
            t0 = time.monotonic()
            try:
                res = step.run(ctx, params)
            except Exception as e:
                dt = int((time.monotonic() - t0) * 1000)
                tb = traceback.format_exc()
                print(f"[TRACE] WYJĄTEK w {name}: {e}\n{tb}", flush=True)
                meta = dump(
                    ctx.device, f"{nn}_{slug}",
                    note=f"wyjątek {name}: {e}", ctx=ctx,
                    extra={"want_fatal": True, "exception": str(e)},
                )
                clicked = recorder.pop_events()
                doc["steps"].append(_entry(
                    nn, slug, label, "failed", {"exception": str(e)},
                    str(e), meta, clicked, say_lines, duration_ms=dt))
                doc["stopped_at_step"] = name
                doc["login_result"] = "nie"
                _absorb_flags(doc, meta)
                _write_step_map(doc)
                rc = 1
                break

            dt = int((time.monotonic() - t0) * 1000)
            print(
                f"   {res.status:8s} {name}  {redact(res.payload)}"
                + (f"  error={res.error}" if res.error else ""),
                flush=True,
            )
            ctx.log("step", step=name, status=res.status, payload=res.payload)

            want_fatal = (
                name == "system.ensure_ig_running"
                or res.status == "failed"
            )
            meta = dump(
                ctx.device, f"{nn}_{slug}",
                note=f"{label} status={res.status}",
                ctx=ctx,
                extra={
                    "want_fatal": want_fatal,
                    "step_status": res.status,
                    "step_payload": redact(res.payload),
                    "step_error": res.error,
                    "login_log": list(say_lines),
                },
            )
            clicked = recorder.pop_events()
            doc["steps"].append(_entry(
                nn, slug, label, res.status, res.payload, res.error,
                meta, clicked, say_lines, duration_ms=dt))
            _absorb_flags(doc, meta, res)
            _write_step_map(doc)
            append_jsonl(LOG_JSONL, {
                "kind": "step",
                "nn": nn,
                "name": name,
                "status": res.status,
                "payload": redact(res.payload),
                "error": res.error,
                "identify_top": meta.get("identify_top"),
                "focused_activity": meta.get("focused_activity"),
                "focused_package": meta.get("focused_package"),
                "dominant_package": meta.get("dominant_package"),
                "visible_texts": meta.get("visible_texts"),
                "resource_ids": meta.get("resource_ids"),
                "clicked": clicked,
                "login_log": list(say_lines),
                "fatal_logcat": meta.get("fatal_logcat") or "",
            })

            if name == "system.ensure_ig_running":
                if not meta.get("clone_owns_focus"):
                    print(
                        "[TRACE] launch NIE ma focused/resumed klona "
                        f"(pidof nie liczy się). fatal={bool(meta.get('fatal_logcat'))}",
                        flush=True,
                    )
                if meta.get("special", {}).get("splash_crash_hint"):
                    print(
                        "[TRACE] splash crash Resources$NotFoundException "
                        "#0x7f082060 — zapisuję, NIE naprawiam APK",
                        flush=True,
                    )
                    doc["splash_crash"] = True

            if res.status == "failed":
                login_log.say(f"PRZERWANO: {res.error}")
                print(f"\n[TRACE] PRZERWANO na {name} — {res.error}\n", flush=True)
                doc["stopped_at_step"] = name
                doc["login_result"] = "nie"
                rc = 1
                break
        else:
            login_log.say(
                "GOTOWE — jeśli został captcha/2FA poza panelem, dokończ ręcznie")
            print("\n[TRACE] pętla kroków skończona bez failed\n", flush=True)
            # Komentarz: wynik tak/częściowo ustawi finally po dumpie focusu
            doc["login_result"] = "częściowo"
    finally:
        # Komentarz: zawsze wróć z MAC-iem, nawet po failu
        try:
            login_log.say("— Restore tożsamości klona (MAC)")
            rest = get_step("system.restore_clone_identity")()
            rres = rest.run(ctx, {})
            print(
                f"   {rres.status:8s} system.restore_clone_identity  "
                f"{redact(rres.payload)}",
                flush=True,
            )
            meta = dump(
                ctx.device, "10_system_restore_clone_identity",
                note="finally restore MAC", ctx=ctx,
            )
            clicked = recorder.pop_events()
            doc["steps"].append(_entry(
                "10", "system_restore_clone_identity",
                "Restore MAC (finally)", rres.status, rres.payload,
                rres.error, meta, clicked, []))
        except Exception as e:
            print(f"[TRACE] restore MAC: {e}", flush=True)
            meta = dump(
                ctx.device, "10_system_restore_clone_identity",
                note=f"restore wyjątek: {e}", ctx=ctx,
                extra={"want_fatal": True},
            )
            doc["steps"].append(_entry(
                "10", "system_restore_clone_identity",
                "Restore MAC (finally)", "failed", {}, str(e), meta, []))

        final = dump_activity(ctx.device)
        doc["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        doc["final_focused_package"] = final.get("focused_package")
        doc["final_focused_activity"] = final.get("focused_activity")
        doc["final_clone_owns_focus"] = final.get("clone_owns_focus")
        if doc["login_result"] != "nie":
            if final.get("clone_owns_focus") and not doc.get("captcha_or_suspicious"):
                if doc.get("verification") and any(
                    s.get("slug") == "system_enter_verification_code_if_needed"
                    and s.get("status") == "skipped"
                    and (s.get("payload") or {}).get("reason") == "code_not_provided_in_time"
                    for s in doc["steps"]
                ):
                    doc["login_result"] = "częściowo"
                    doc["manual_todo"] = (
                        "- wpisz kod w panelu POST /api/verification-code/"
                        f"{ACCOUNT} i powtórz weryfikację"
                    )
                else:
                    doc["login_result"] = "tak"
            else:
                doc["login_result"] = "częściowo"
        doc["manual_todo"] = doc.get("manual_todo") or _manual_todo(doc, final)
        _write_step_map(doc)
        PLAYBOOK_PATH.write_text(_build_playbook(doc), encoding="utf-8")
        print(
            f"[TRACE] STEP_MAP={STEP_MAP_PATH} PLAYBOOK={PLAYBOOK_PATH} "
            f"result={doc['login_result']} focus="
            f"{doc['final_focused_package']}/{doc['final_focused_activity']}",
            flush=True,
        )

    return rc


def _entry(nn, slug, label, status, payload, error, meta, clicked,
           say=None, duration_ms=0) -> dict:
    return redact({
        "nn": nn,
        "slug": slug,
        "label": label,
        "status": status,
        "payload": payload or {},
        "error": error,
        "duration_ms": duration_ms,
        "identify_top": (meta or {}).get("identify_top"),
        "focused_activity": (meta or {}).get("focused_activity"),
        "focused_package": (meta or {}).get("focused_package"),
        "dominant_package": (meta or {}).get("dominant_package"),
        "visible_texts": (meta or {}).get("visible_texts") or [],
        "resource_ids": (meta or {}).get("resource_ids") or [],
        "clicked": clicked or [],
        "login_log": list(say or []),
        "fatal_logcat": (meta or {}).get("fatal_logcat") or "",
        "special": (meta or {}).get("special") or {},
        "note_pl": (meta or {}).get("note_pl") or "",
        "clone_owns_focus": (meta or {}).get("clone_owns_focus"),
        "files": {
            "hierarchy": f"{nn}_{slug}.hierarchy.json" if slug != "pre"
            else "00_pre.hierarchy.json",
            "flat": f"{nn}_{slug}.flat.json" if slug != "pre"
            else "00_pre.flat.json",
            "activity": f"{nn}_{slug}.activity.json" if slug != "pre"
            else "00_pre.activity.json",
            "screenshot": (meta or {}).get("screenshot") or "",
        },
    })


def _absorb_flags(doc: dict, meta: dict, res=None) -> None:
    special = (meta or {}).get("special") or {}
    if special.get("verification_code"):
        doc["verification"] = True
    if special.get("captcha") or special.get("suspicious_login"):
        doc["captcha_or_suspicious"] = True
    fatal = (meta or {}).get("fatal_logcat") or ""
    if special.get("splash_crash_hint") or "Resources$NotFoundException" in fatal:
        if "Resources$NotFoundException" in fatal:
            doc["splash_crash"] = True
    payload = getattr(res, "payload", None) or {}
    if getattr(res, "status", None) == "ok" and payload.get("submitted"):
        if "popups_confirmed" in payload:
            doc["verification"] = True


def _manual_todo(doc: dict, final: dict) -> str:
    bits = []
    if doc.get("splash_crash"):
        bits.append(
            "- crash splash klona — NIE merge splitów / NIE naprawiać APK tu; "
            "osobny bug"
        )
    if doc.get("captcha_or_suspicious"):
        bits.append("- captcha / suspicious login: człowiek na telefonie, nie omijać")
    if doc.get("verification") and doc.get("login_result") != "tak":
        bits.append(
            f"- kod email/SMS: panel POST /api/verification-code/{ACCOUNT}"
        )
    focus = final.get("focused_package") or ""
    if focus and focus != PKG:
        bits.append(
            f"- na pierwszym planie jest `{focus}`, nie `{PKG}`"
        )
    if not bits:
        bits.append("- nic pilnego; sprawdź czy feed/profil klona jest zalogowany")
    return "\n".join(bits)


if __name__ == "__main__":
    raise SystemExit(main())
