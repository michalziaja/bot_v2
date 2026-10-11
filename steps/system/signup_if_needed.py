"""system.signup_if_needed — rejestracja IG na świeżym klonie.

Dane z `credentials.get_signup` (username, hasło, imię, urodziny, kontakt).
Kod SMS/email obsługuje `system.enter_verification_code_if_needed`.
Captcha / checkpoint — człowiek na telefonie (jak przy logowaniu).
"""
from __future__ import annotations

import time
from datetime import date

from v2.config import credentials
from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.system import login_log
from v2.steps.system._ui_helpers import (
    BUTTON_CLASSES,
    center,
    find_field,
    normalize_ws,
)
from v2.steps.system.login_if_needed import (
    HOME_SCREENS,
    _confirm_post_login_popups,
    _dismiss_gms_passkey,
    _dismiss_ime,
    _mentions_captcha,
    _type_and_confirm_field,
)

# Komentarz: start z ekranu logowania — tap w „nowe konto”, nie w Zaloguj
CREATE_ACCOUNT_HINTS = (
    "utwórz nowe konto", "utwórz konto", "załóż konto", "nowe konto",
    "create new account", "create account", "sign up", "signup",
)
CREATE_SKIP_HINTS = (
    "zaloguj", "log in", "nie pamiętasz", "forgot", "pokaż hasło",
)

NEXT_HINTS = (
    "dalej", "kontynuuj", "next", "continue", "zarejestruj", "sign up",
    "zgadzam się", "i agree", "akceptuję",
)
# Komentarz: live OnePlus IG 446 — „Zarejestruj się przy użyciu e-mail” to zmiana kanału, nie Dalej
NEXT_SKIP_HINTS = CREATE_SKIP_HINTS + (
    "przy użyciu", "using email", "using your email", "mam już konto",
    "already have",
)

CONTACT_HINTS = (
    "e-mail", "email", "adres e-mail", "adresu e-mail",
    "numer telefonu", "telefonu komórkowego", "telefon",
    "phone", "mobile",
)
EMAIL_TAB_HINTS = (
    "e-mail", "email", "adres e-mail", "adresu e-mail", "przy użyciu adresu",
)
PHONE_TAB_HINTS = ("telefon", "phone", "mobile")
NAME_HINTS = ("imię i nazwisko", "pełne imię", "full name", "your name", "imię")
USERNAME_HINTS = ("nazwa użytkownika", "username")
PASSWORD_HINTS = ("hasło", "password")
BIRTHDAY_HINTS = (
    "data urodzenia", "datę urodzenia", "urodzin", "birthday",
    "date of birth", "when were you born", "ustaw datę",
)
# Komentarz: live OnePlus — natywny DatePicker (dzień / miesiąc PL / rok)
DATE_PICKER_HINTS = ("ustaw datę", "set date")
PL_MONTHS = (
    "sty", "lut", "mar", "kwi", "maj", "cze",
    "lip", "sie", "wrz", "paź", "lis", "gru",
)
CODE_HINTS = (
    "wprowadź kod", "enter code", "kod potwierdzający",
    "wprowadź kod potwierdzający", "sprawdź pocztę",
    "kod wysłany", "6-cyfrowy", "cyfrowy kod",
)
# Komentarz: live OnePlus klon2 — po haśle IG pyta o wiek, nie DatePicker
AGE_SCREEN_HINTS = ("ile masz lat", "how old are you")
AGE_FIELD_HINTS = ("wiek", "age")
BIRTHDAY_CONFIRM_HINTS = (
    "ustawiasz datę urodzenia", "setting your birthday",
)
HELP_SHEET_HINTS = ("potrzebujesz pomocy", "need help")
HELP_CLOSE_HINTS = ("zamknij", "close")

MAX_WIZARD_STEPS = 18
WIZARD_WAIT_S = 0.8


@register
class SignupIfNeeded:
    name = "system.signup_if_needed"
    requires_screen = None

    def run(self, ctx, params):
        profile = credentials.get_signup(ctx.account.account_id)
        if not profile:
            login_log.say("Brak danych signup w credentials.json — skip")
            return StepResult.skipped("no_signup_configured")

        login_log.say(f"Szukam startu rejestracji (@{profile['ig_username']})…")
        filled = []
        opened = False
        for step_i in range(MAX_WIZARD_STEPS):
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
            if _dismiss_gms_passkey(ctx, flat):
                login_log.say("Zamknąłem overlay Google Passkey")
                time.sleep(0.8)
                continue
            if _dismiss_help_sheet(ctx, flat):
                login_log.say("Zamknąłem sheet „Potrzebujesz pomocy?”")
                time.sleep(0.6)
                continue
            if _confirm_inferred_birthday(ctx, flat):
                login_log.say("Potwierdzam dialog daty z wieku")
                time.sleep(0.6)
                continue
            if _mentions_captcha(flat):
                login_log.say("CAPTCHA — przerwa, dokończ na telefonie")
                return StepResult.failed(
                    "captcha_requires_human", opened=opened, filled=filled)
            if _code_screen(flat):
                login_log.say("Ekran kodu — oddaję krok weryfikacji")
                return StepResult.ok(
                    opened=opened, filled=filled, waiting_for_code=True)
            top = ctx.navigator.identify().top
            if top in HOME_SCREENS and filled:
                login_log.say(f"Home po rejestracji ({top})")
                popups = _confirm_post_login_popups(ctx)
                return StepResult.ok(
                    opened=opened, filled=filled, landed_on=top,
                    popups_confirmed=popups)

            if _looks_like_login_landing(flat) and not opened:
                btn = _find_create_account(flat)
                if not btn:
                    login_log.say("Brak „Utwórz nowe konto” — FAIL")
                    return StepResult.failed("create_account_not_found")
                login_log.say("Klikam „Utwórz nowe konto”…")
                ctx.ht.tap(*center(btn["bounds"]), pause_kind="tap")
                ctx.humanizer.pause("after_launch")
                opened = True
                time.sleep(WIZARD_WAIT_S)
                continue

            did = _fill_visible_fields(ctx, flat, profile, filled)
            if did:
                _dismiss_ime(ctx)
                time.sleep(0.35)

            # Komentarz: Dalej pod modalem DatePicker nie wolno tapnąć
            if _looks_like_date_picker(flat) and "birthday" not in filled:
                time.sleep(WIZARD_WAIT_S)
                continue
            # Komentarz: puste „Wiek” + Dalej = pętla (klon2 2026-09-10)
            if _looks_like_age_screen(flat) and "age" not in filled:
                time.sleep(WIZARD_WAIT_S)
                continue

            nxt = _find_next(flat)
            if nxt:
                login_log.say(f"Dalej [{step_i + 1}]: „{_label(nxt)}\"")
                ctx.ht.tap(*center(nxt["bounds"]), pause_kind="tap")
                ctx.humanizer.pause("after_launch")
                time.sleep(WIZARD_WAIT_S)
                continue

            login_log.say(
                f"Czekam na kolejny ekran rejestracji "
                f"({step_i + 1}/{MAX_WIZARD_STEPS}, top={top or '?'})")
            time.sleep(WIZARD_WAIT_S)

        login_log.say("Rejestracja nie doszła do kodu/home — FAIL")
        return StepResult.failed(
            "signup_wizard_stuck", opened=opened, filled=filled)


def _label(node: dict) -> str:
    return normalize_ws(
        (node.get("text") or "") + " " + (node.get("content-desc") or "")
    ).strip()


def _code_screen(flat: list[dict]) -> bool:
    """Ekran kodu — także gdy sheet pomocy zasłania EditText (live OnePlus)."""
    if find_field(flat, (), CODE_HINTS, "EditText"):
        return True
    blob = normalize_ws(" ".join(
        (e.get("text") or "") + " " + (e.get("content-desc") or "")
        for e in flat))
    return any(h in blob for h in CODE_HINTS)


def _dismiss_help_sheet(ctx, flat: list[dict]) -> bool:
    """X / Zamknij na „Potrzebujesz pomocy?” — nie tapaj „Wyślij ponownie”."""
    blob = normalize_ws(" ".join(
        (e.get("text") or "") + " " + (e.get("content-desc") or "")
        for e in flat))
    if not any(h in blob for h in HELP_SHEET_HINTS):
        return False
    btn = find_field(flat, (), HELP_CLOSE_HINTS, BUTTON_CLASSES)
    if not btn:
        return False
    ctx.ht.tap(*center(btn["bounds"]), pause_kind="tap")
    return True


def _looks_like_login_landing(flat: list[dict]) -> bool:
    """Ekran z polem logowania albo CTA zaloguj + nowe konto."""
    blob = normalize_ws(" ".join(
        (e.get("text") or "") + " " + (e.get("content-desc") or "")
        for e in flat))
    if any(h in blob for h in ("zaloguj się", "log in")) and any(
            h in blob for h in ("nowe konto", "create new", "sign up")):
        return True
    user = find_field(flat, (), ("nazwa użytkownika", "username"), "EditText")
    pwd = find_field(flat, (), PASSWORD_HINTS, "EditText")
    return bool(user and pwd)


def _find_create_account(flat: list[dict]) -> dict | None:
    matches = []
    for e in flat:
        if not e.get("clickable"):
            continue
        lab = _label(e)
        if any(h in lab for h in CREATE_SKIP_HINTS) and not any(
                h in lab for h in CREATE_ACCOUNT_HINTS):
            continue
        if any(h in lab for h in CREATE_ACCOUNT_HINTS):
            matches.append(e)
    if matches:
        return matches[0]
    return find_field(flat, (), CREATE_ACCOUNT_HINTS, BUTTON_CLASSES)


def _is_next_skip(lab: str) -> bool:
    return any(h in lab for h in NEXT_SKIP_HINTS)


def _find_next(flat: list[dict]) -> dict | None:
    btn = find_field(flat, (), NEXT_HINTS, BUTTON_CLASSES)
    if btn and not _is_next_skip(_label(btn)):
        return btn
    for e in flat:
        if not e.get("clickable"):
            continue
        lab = _label(e)
        if any(h in lab for h in NEXT_HINTS) and not _is_next_skip(lab):
            return e
    return None


def _pick_contact_channel(ctx, flat: list[dict], contact: str) -> None:
    """Email vs telefon — tap w zakładkę zgodną z kontaktem."""
    want_email = "@" in contact
    hints = EMAIL_TAB_HINTS if want_email else PHONE_TAB_HINTS
    for e in flat:
        if not e.get("clickable"):
            continue
        lab = _label(e)
        if any(h in lab for h in hints) and "edittext" not in (e.get("class") or "").lower():
            ctx.ht.tap(*center(e["bounds"]), pause_kind="tap")
            time.sleep(0.35)
            return


def _blob(flat: list[dict]) -> str:
    return normalize_ws(" ".join(
        (e.get("text") or "") + " " + (e.get("content-desc") or "")
        for e in flat))


def _looks_like_age_screen(flat: list[dict]) -> bool:
    """Ekran „Ile masz lat?” z polem Wiek — nie mylić z DatePickerem."""
    return any(h in _blob(flat) for h in AGE_SCREEN_HINTS)


def _age_years(birthday: str, today: date | None = None) -> int | None:
    """Wiek w pełnych latach z YYYY-MM-DD (IG liczy od dziś)."""
    parsed = _parse_birthday(birthday)
    if not parsed:
        return None
    y, m, d = parsed
    now = today or date.today()
    age = now.year - y
    if (now.month, now.day) < (m, d):
        age -= 1
    if age < 13 or age > 120:
        return None
    return age


def _confirm_inferred_birthday(ctx, flat: list[dict]) -> bool:
    """Dialog „Ustawiasz datę urodzenia jako…” po wpisaniu wieku — OK, nie Anuluj."""
    if not any(h in _blob(flat) for h in BIRTHDAY_CONFIRM_HINTS):
        return False
    for e in flat:
        if not e.get("clickable"):
            continue
        if _label(e) == "ok":
            ctx.ht.tap(*center(e["bounds"]), pause_kind="tap")
            return True
    return False


def _parse_birthday(raw: str) -> tuple[int, int, int] | None:
    parts = (raw or "").strip().split("-")
    if len(parts) != 3:
        return None
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if y < 1900 or not (1 <= m <= 12) or not (1 <= d <= 31):
        return None
    return y, m, d


def _looks_like_date_picker(flat: list[dict]) -> bool:
    blob = normalize_ws(" ".join(
        (e.get("text") or "") + " " + (e.get("content-desc") or "")
        for e in flat))
    if any(h in blob for h in DATE_PICKER_HINTS):
        return True
    return any((e.get("class") or "").endswith("DatePicker") for e in flat)


def _fill_date_picker(ctx, flat: list[dict], birthday: str) -> bool:
    """Ustaw rok/miesiąc/dzień w NumberPicker i zatwierdź USTAW (nie ANULUJ)."""
    parsed = _parse_birthday(birthday)
    if not parsed or not _looks_like_date_picker(flat):
        return False
    y, m, d = parsed
    login_log.say(f"DatePicker — ustawiam {y:04d}-{m:02d}-{d:02d}")
    year_ed = day_ed = month_ed = None
    for e in flat:
        if "EditText" not in (e.get("class") or ""):
            continue
        t = (e.get("text") or "").strip()
        low = t.lower()
        if t.isdigit() and len(t) == 4:
            year_ed = e
        elif t.isdigit() and 1 <= int(t) <= 31:
            day_ed = e
        elif low[:3] in PL_MONTHS:
            month_ed = e

    def _type_wheel(field, value: str) -> None:
        ctx.ht.tap(*center(field["bounds"]), pause_kind="tap")
        time.sleep(0.15)
        for _ in range(6):
            ctx.device.shell("input keyevent 67")
        ctx.device.shell(f'input text "{value}"')
        time.sleep(0.2)

    if year_ed:
        _type_wheel(year_ed, str(y))
    if day_ed:
        _type_wheel(day_ed, f"{d:02d}")
    if month_ed:
        _type_wheel(month_ed, PL_MONTHS[m - 1])
    time.sleep(0.25)
    for source in (
        flatten_hierarchy(ctx.device.hierarchy(force=True)),
        flat,
    ):
        for e in source:
            if not e.get("clickable"):
                continue
            lab = _label(e)
            if lab in ("ustaw", "set"):
                ctx.ht.tap(*center(e["bounds"]), pause_kind="tap")
                return True
    return False


def _fill_visible_fields(ctx, flat: list[dict], profile: dict,
                         filled: list[str]) -> bool:
    """Wpisz pierwsze puste znane pole na bieżącym ekranie."""
    # Komentarz: picker urodzin najpierw — żeby nie pomylić kółek z innymi polami
    if "birthday" not in filled and _fill_date_picker(ctx, flat, profile["birthday"]):
        filled.append("birthday")
        return True
    contact = profile["contact"]
    if "contact" not in filled and find_field(flat, (), CONTACT_HINTS, "EditText"):
        _pick_contact_channel(ctx, flat, contact)
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        field = find_field(flat, (), CONTACT_HINTS, "EditText")
        if field and _type_and_confirm_field(
                ctx, kind="kontakt", hints=CONTACT_HINTS, ids=(),
                expected=contact, first_field=field):
            filled.append("contact")
            return True
    if "password" not in filled:
        field = find_field(flat, (), PASSWORD_HINTS, "EditText")
        if field and not find_field(flat, (), ("nazwa użytkownika", "username"), "EditText"):
            if _type_and_confirm_field(
                    ctx, kind="hasło", hints=PASSWORD_HINTS, ids=(),
                    expected=profile["ig_password"], first_field=field,
                    secret=True):
                filled.append("password")
                return True
    if "age" not in filled and _looks_like_age_screen(flat):
        age = _age_years(profile["birthday"])
        field = find_field(flat, (), AGE_FIELD_HINTS, "EditText")
        if not field:
            field = next(
                (e for e in flat if "EditText" in (e.get("class") or "")),
                None,
            )
        if age is not None and field and _type_and_confirm_field(
                ctx, kind="wiek", hints=AGE_FIELD_HINTS, ids=(),
                expected=str(age), first_field=field):
            filled.append("age")
            return True
    if "name" not in filled:
        field = find_field(flat, (), NAME_HINTS, "EditText")
        if field and _type_and_confirm_field(
                ctx, kind="imię", hints=NAME_HINTS, ids=(),
                expected=profile["full_name"], first_field=field):
            filled.append("name")
            return True
    if "username" not in filled:
        field = find_field(flat, (), USERNAME_HINTS, "EditText")
        if field and _type_and_confirm_field(
                ctx, kind="username", hints=USERNAME_HINTS, ids=(),
                expected=profile["ig_username"], first_field=field):
            filled.append("username")
            return True
    if "birthday" not in filled:
        field = find_field(flat, (), BIRTHDAY_HINTS, "EditText")
        if field and _type_and_confirm_field(
                ctx, kind="urodziny", hints=BIRTHDAY_HINTS, ids=(),
                expected=profile["birthday"], first_field=field):
            filled.append("birthday")
            return True
        blob = normalize_ws(" ".join(
            (e.get("text") or "") + " " + (e.get("content-desc") or "")
            for e in flat))
        if any(h in blob for h in BIRTHDAY_HINTS):
            # Komentarz: tap w pole daty otwiera DatePicker (live OnePlus)
            field = next(
                (e for e in flat
                 if "EditText" in (e.get("class") or "") and e.get("clickable")),
                None,
            )
            if field:
                login_log.say("Otwieram DatePicker urodzin")
                ctx.ht.tap(*center(field["bounds"]), pause_kind="tap")
                return True
    return False
