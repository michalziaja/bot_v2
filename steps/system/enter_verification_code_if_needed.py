"""system.enter_verification_code_if_needed — jeśli IG pokazuje ekran
'wpisz kod' (weryfikacja e-mail/SMS po logowaniu), CZEKA na kod wpisany
w panelu (dashboard) i go wpisuje.

Ustalone z użytkownikiem 2026-08-02: to jedyny etap weryfikacji, który bot
obsługuje automatycznie — resztą (captcha, suspicious login, ban) nadal
zajmuje się człowiek fizycznie na telefonie. Krok NIGDY nie zgaduje kodu —
jeśli panel go nie dostarczy w oknie czasowym, kończy się `skipped`.

POTWIERDZONE na żywym dumpie (klon `com.instagram.androjb`, 2026-08-02,
`calibration/samples/55_verify_email_code`): ekran „Sprawdź pocztę
elektroniczną" — pole „Wprowadź kod" i przycisk „Kontynuuj" bez
resource-id, ten sam wzorzec co `login_if_needed`.
"""
from __future__ import annotations

import time
from pathlib import Path

from v2.core import verification_code as vc
from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.system import login_log
from v2.steps.system._ui_helpers import BUTTON_CLASSES, center, escape_input_text, find_field
from v2.steps.system.signup_if_needed import _code_screen, _dismiss_help_sheet

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data"

CODE_FIELD_HINTS = ("wprowadź kod", "enter code", "enter the code", "kod potwierdzający")
SUBMIT_HINTS = ("kontynuuj", "dalej", "prześlij", "wyślij",
               "continue", "next", "submit", "send")

DEFAULT_WAIT_TIMEOUT_S = 300   # 5 minut na dostarczenie kodu z panelu
WAIT_POLL_S = 2.0


@register
class EnterVerificationCodeIfNeeded:
    name = "system.enter_verification_code_if_needed"
    requires_screen = None

    def run(self, ctx, params):
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        # Komentarz: live OnePlus — sheet „Potrzebujesz pomocy?” zasłania EditText
        if _dismiss_help_sheet(ctx, flat):
            login_log.say("Zamknąłem sheet pomocy przy kodzie")
            time.sleep(0.6)
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        # Komentarz: live OnePlus klon2 — 6 kratek OTP bez hintu EditText
        code_field = _find_code_input(flat)
        if not code_field and not _code_screen(flat):
            login_log.say("Brak ekranu kodu weryfikacyjnego — skip")
            return StepResult.skipped("no_code_screen_detected")

        account = ctx.account.account_id
        prompt = _screen_prompt(flat)
        data_root = getattr(ctx, "data_root", None) or DATA_ROOT

        vc.request_code(data_root, account, prompt)
        login_log.say("IG prosi o kod — czekam na wpis z panelu…")
        ctx.log("system.enter_verification_code_if_needed.waiting", prompt=prompt)

        timeout_s = params.get("timeout_s", DEFAULT_WAIT_TIMEOUT_S)
        code = vc.wait_for_code(data_root, account, timeout_s, poll_s=WAIT_POLL_S)
        vc.clear_request(data_root, account)

        if not code:
            login_log.say("Kod nie przyszedł z panelu w czasie — skip")
            return StepResult.skipped("code_not_provided_in_time", prompt=prompt)

        # ekran mógł się przez czas oczekiwania odświeżyć — świeży dump
        login_log.say("Wpisuję kod weryfikacyjny…")
        flat2 = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if _dismiss_help_sheet(ctx, flat2):
            time.sleep(0.5)
            flat2 = flatten_hierarchy(ctx.device.hierarchy(force=True))
        field2 = _find_code_input(flat2) or code_field
        if field2:
            ctx.ht.tap(*center(field2["bounds"]), pause_kind="tap")
            time.sleep(0.2)
        # Komentarz: kratki OTP przyjmują cały kod przez input text po focusie
        ctx.device.shell(f'input text "{escape_input_text(code)}"')
        time.sleep(0.6)

        flat3 = flatten_hierarchy(ctx.device.hierarchy(force=True))
        btn = find_field(flat3, (), SUBMIT_HINTS, BUTTON_CLASSES)
        if not btn:
            login_log.say("Brak przycisku po kodzie — FAIL")
            return StepResult.failed("nie znaleziono przycisku po wpisaniu kodu",
                                     code_typed=True)
        login_log.say("Klikam „Kontynuuj” po kodzie…")
        ctx.ht.tap(*center(btn["bounds"]), pause_kind="tap")
        ctx.humanizer.pause("after_launch")

        # Po kodzie IG często pokazuje „Zapisać dane logowania?" / nowy telefon
        login_log.say("Potwierdzam popupy po kodzie…")
        from v2.steps.system.login_if_needed import _confirm_post_login_popups
        popups = _confirm_post_login_popups(ctx)

        top_after = ctx.navigator.identify().top
        login_log.say(
            f"Koniec weryfikacji — ekran={top_after or '?'}, "
            f"potwierdzonych popupów={popups}")
        ctx.log("system.enter_verification_code_if_needed.submitted",
                landed_on=top_after, popups_confirmed=popups)
        return StepResult.ok(submitted=True, landed_on=top_after,
                             popups_confirmed=popups)


def _find_code_input(flat: list[dict]) -> dict | None:
    """Pole kodu: hint EditText, albo pierwsza kratka OTP (6× EditText)."""
    hinted = find_field(flat, (), CODE_FIELD_HINTS, "EditText")
    if hinted:
        return hinted
    edits = [
        e for e in flat
        if "EditText" in (e.get("class") or "")
    ]
    if not edits:
        return None

    def _left(node: dict) -> int:
        # Komentarz: sort od lewej — pierwsza kratka OTP
        raw = str(node.get("bounds") or "[0,0][0,0]")
        try:
            return int(raw.split(",")[0].strip("["))
        except ValueError:
            return 0

    edits.sort(key=_left)
    return edits[0]


def _screen_prompt(flat: list[dict]) -> str:
    """Najdłuższy fragment tekstu na ekranie — zwykle nagłówek + treść
    ('Sprawdź pocztę elektroniczną… Wprowadź kod wysłany na adres…'),
    do pokazania w panelu, żeby użytkownik wiedział, o co proszony."""
    texts = [(e.get("text") or e.get("content-desc") or "") for e in flat]
    texts = [t for t in texts if t]
    return max(texts, key=len) if texts else ""
