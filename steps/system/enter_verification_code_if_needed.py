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
        code_field = find_field(flat, (), CODE_FIELD_HINTS, "EditText")
        if not code_field:
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
        field2 = find_field(flat2, (), CODE_FIELD_HINTS, "EditText") or code_field
        ctx.ht.tap(*center(field2["bounds"]), pause_kind="tap")
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


def _screen_prompt(flat: list[dict]) -> str:
    """Najdłuższy fragment tekstu na ekranie — zwykle nagłówek + treść
    ('Sprawdź pocztę elektroniczną… Wprowadź kod wysłany na adres…'),
    do pokazania w panelu, żeby użytkownik wiedział, o co proszony."""
    texts = [(e.get("text") or e.get("content-desc") or "") for e in flat]
    texts = [t for t in texts if t]
    return max(texts, key=len) if texts else ""
