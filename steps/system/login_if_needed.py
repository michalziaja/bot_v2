"""system.login_if_needed — wpisz login/hasło na ekranie logowania IG, jeśli
się na nim znajdujemy, i potwierdź łańcuch popupów, które IG pokazuje zaraz
po pierwszym logowaniu na nowym urządzeniu/klonie.

Świadomie NIE obsługuje: checkpoint, captchę — to kończy się ręcznie na
telefonie (ustalone). Weryfikację kodem e-mail/SMS obsługuje osobny krok
`system.enter_verification_code_if_needed` (panel→bot, patrz ten moduł).
Popupy „zapisać dane logowania?" i wprowadzenia do funkcji SĄ potwierdzane
automatycznie (ustalone 2026-08-02, po zaobserwowaniu ich na żywym koncie) —
zawsze przyciskiem AFIRMATYWNYM z listy `CONFIRM_HINTS`, nigdy „nie teraz"/
„pomiń" — Z WYJĄTKAMI poniżej.

**Wyjątek: lokalizacja (natywny dialog)** (ustalone 2026-08-02) —
natywny dialog uprawnień Androida o lokalizację jest ODRZUCANY
(„Nie zezwalaj"). Ekran wprowadzający samego IG o lokalizacji (tylko CTA
„Kontynuuj", nic jeszcze nie nadaje) idzie przez CONFIRM.

**Wyjątek: kontakty** (ustalone 2026-08-12) —
zarówno natywny dialog permissioncontroller, jak i intro IG o kontaktach:
zawsze POMIŃ / „Nie teraz" / „Nie zezwalaj" — NIGDY „Kontynuuj", jeśli flat
wspomina kontakty (to prowadzi do nadania uprawnienia).

POTWIERDZONE na żywym dumpie (2026-08-02, dwa klony: `com.instagram.androip`,
`com.instagram.androjb`):
- `calibration/samples/50_login_screen` — pola username/hasło i przycisk
  logowania **nie mają `resource-id`** w tej wersji IG, cała treść siedzi
  w `content-desc`. Rozpoznajemy po (klasa + fragment opisu), nie po ID.
- `calibration/samples/51_save_login_prompt` — „Zapisać dane logowania?",
  przycisk „Zapisz" (afirmatywny) vs „Nie teraz".
- `calibration/samples/52_feature_intro_location` — wprowadzenie do funkcji
  (tu: lokalizacja) z jednym przyciskiem „Kontynuuj".
- `calibration/samples/53_native_permission_location` — natywny dialog
  Androida, trzy opcje („Podczas używania\xa0aplikacji" / „Tylko tym razem" /
  „Nie zezwalaj") — realny tekst różni się od intuicyjnego „Zezwól", i ma
  niełamliwą spację w środku.
- `calibration/samples/54_feature_tip_navigation` — przycisk „Rozumiem" jako
  klikalny `FrameLayout`, NIE `android.widget.Button`.

Live 2026-08-12 (clone3): ekran „Skonfiguruj na nowym telefonie" + Kontynuuj
bywa identyfikowany jako `home_feed` (activity Main) — NIE wolno wtedy
wychodzić z pętli popupów, dopóki widać CTA afirmatywne.
"""
from __future__ import annotations

import time

from v2.config import credentials
from v2.core.screen_map import dominant_package, flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.system import login_log
from v2.steps.system._ui_helpers import BUTTON_CLASSES, center, escape_input_text, find_field, normalize_ws

USERNAME_IDS = ("com.instagram.android:id/login_username",)
PASSWORD_IDS = ("com.instagram.android:id/login_password",)
LOGIN_BUTTON_IDS = ("com.instagram.android:id/log_in_button",)

# PL (potwierdzone) + EN (nie sprawdzone na żywo, telefon dziś jest w PL)
USERNAME_HINTS = ("nazwa użytkownika", "adres e-mail", "numer telefonu", "username")
PASSWORD_HINTS = ("hasło", "password")
LOGIN_BUTTON_HINTS = ("zaloguj się", "log in")

# Afirmatywne frazy popupów po zalogowaniu — TYLKO te, nigdy „nie teraz"/
# „pomiń"/„anuluj"/„nie zezwalaj"/„tylko tym razem". „zezwól" (nie „zezwalaj")
# celowo nie łapie natywnego „Nie zezwalaj" Androida — różny rdzeń słowa,
# bez wspólnego podciągu.
CONFIRM_HINTS = ("zapisz", "kontynuuj", "dalej", "zezwól", "podczas używania aplikacji",
                 "włącz", "gotowe", "rozumiem",
                 "save", "continue", "next", "allow", "while using the app",
                 "turn on", "enable", "done", "ok", "got it")

# Preferowane ID przycisku „Zapisz" na ekranie zapisu danych logowania
# (fingerprint ScreenMap: save_login_prompt / not_now_text).
SAVE_LOGIN_BUTTON_IDS = (
    "com.instagram.android:id/save_login_prompt",
)
# „Nie teraz" — nigdy nie tapamy na prompcie zapisu danych (nawet gdyby
# kiedyś wpadło w szerokie match). Na ekranie kontaktów — TAK (SKIP).
NOT_NOW_IDS = (
    "com.instagram.android:id/not_now_text",
)
NOT_NOW_HINTS = ("nie teraz", "not now")

# Uprawnienia, których NIE nadajemy (ustalone z użytkownikiem 2026-08-02) —
# lokalizacja i kontakty. Natywny dialog Androida → DENY.
# Intro IG o kontaktach → osobna ścieżka SKIP (Pomiń / Nie teraz), patrz niżej.
SENSITIVE_PERMISSION_HINTS = ("lokalizacj", "kontakt", "location", "contacts")
DENY_HINTS = ("nie zezwalaj", "don't allow", "deny")
PERMISSION_DIALOG_PACKAGES = ("permissioncontroller",)

# Ekran/popup o kontaktach (IG intro LUB flat z „kontakt…") — NIGDY Kontynuuj.
# Preferujemy „Pomiń", potem „Nie teraz", potem natywne „Nie zezwalaj".
CONTACTS_HINTS = ("kontakt", "contacts")
SKIP_PREFERRED_HINTS = ("pomiń", "skip")
SKIP_FALLBACK_HINTS = ("nie teraz", "not now", "nie zezwalaj", "don't allow", "deny")

MAX_POST_LOGIN_POPUPS = 10
HOME_SCREENS = {"home_feed", "reels_feed", "profile_self", "search_top", "direct_inbox"}

# Świeżo uruchomiony klon bywa jeszcze na splashu, gdy sprawdzamy pierwszy raz
# (zaobserwowane na żywo 2026-08-02: `ensure_ig_running` kończy się, zanim
# ekran logowania zdąży się w pełni załadować) — kilka prób z odczekaniem,
# zanim zgłosimy `login_screen_not_detected`.
LOGIN_SCREEN_WAIT_ATTEMPTS = 5
LOGIN_SCREEN_WAIT_S = 1.5

# Po „Zaloguj się" popup „Zapisać dane?" bywa opóźniony (live clone3 2026-08-12:
# popups_confirmed=0 mimo że prompt był na ekranie). Czekamy zanim uznamy
# brak przycisku za koniec łańcucha.
POPUP_APPEAR_ATTEMPTS = 6
POPUP_APPEAR_WAIT_S = 1.0


@register
class LoginIfNeeded:
    name = "system.login_if_needed"
    requires_screen = None

    def run(self, ctx, params):
        creds = credentials.get(ctx.account.account_id)
        if not creds:
            login_log.say("Brak danych logowania w credentials.json — skip")
            return StepResult.skipped("no_credentials_configured")

        login_log.say(f"Szukam ekranu logowania (@{creds['ig_username']})…")
        user_field = pass_field = None
        for attempt in range(LOGIN_SCREEN_WAIT_ATTEMPTS):
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
            user_field = find_field(flat, USERNAME_IDS, USERNAME_HINTS, "EditText")
            pass_field = find_field(flat, PASSWORD_IDS, PASSWORD_HINTS, "EditText")
            if user_field and pass_field:
                break
            if attempt < LOGIN_SCREEN_WAIT_ATTEMPTS - 1:
                login_log.say(f"Jeszcze nie ma pól logowania — czekam "
                              f"({attempt + 1}/{LOGIN_SCREEN_WAIT_ATTEMPTS})")
                time.sleep(LOGIN_SCREEN_WAIT_S)
        if not user_field or not pass_field:
            top = ctx.navigator.identify().top
            login_log.say(f"Ekran logowania nie wykryty (top={top}) — skip")
            return StepResult.skipped("login_screen_not_detected", top=top)

        login_log.say("Wpisuję login…")
        ctx.ht.tap(*center(user_field["bounds"]), pause_kind="tap")
        ctx.device.shell(f'input text "{escape_input_text(creds["ig_username"])}"')
        time.sleep(0.6)

        # pole hasła może przesunąć się po otwarciu klawiatury — świeży dump
        flat2 = flatten_hierarchy(ctx.device.hierarchy(force=True))
        pass_field2 = find_field(flat2, PASSWORD_IDS, PASSWORD_HINTS, "EditText") or pass_field
        login_log.say("Wpisuję hasło…")
        ctx.ht.tap(*center(pass_field2["bounds"]), pause_kind="tap")
        ctx.device.shell(f'input text "{escape_input_text(creds["ig_password"])}"')
        time.sleep(0.6)

        flat3 = flatten_hierarchy(ctx.device.hierarchy(force=True))
        btn = find_field(flat3, LOGIN_BUTTON_IDS, LOGIN_BUTTON_HINTS, "Button")
        if not btn:
            login_log.say("Brak przycisku „Zaloguj się” — FAIL")
            return StepResult.failed("nie znaleziono przycisku logowania",
                                     username_typed=True, password_typed=True)
        login_log.say("Klikam „Zaloguj się”…")
        ctx.ht.tap(*center(btn["bounds"]), pause_kind="tap")
        ctx.humanizer.pause("after_launch")

        login_log.say("Potwierdzam popupy po logowaniu…")
        popups_confirmed = _confirm_post_login_popups(ctx)

        top_after = ctx.navigator.identify().top
        login_log.say(
            f"Koniec logowania — ekran={top_after or '?'}, "
            f"potwierdzonych popupów={popups_confirmed}")
        ctx.log("system.login_if_needed.submitted", landed_on=top_after,
                popups_confirmed=popups_confirmed)
        return StepResult.ok(submitted=True, landed_on=top_after,
                             popups_confirmed=popups_confirmed)


def _confirm_post_login_popups(ctx, max_steps: int = MAX_POST_LOGIN_POPUPS) -> int:
    """Tapia afirmatywny przycisk w pętli, dopóki popupy się pojawiają —
    Z WYJĄTKIEM: lokalizacja/kontakty (deny/skip).

    WAŻNE: najpierw szukamy CTA, dopiero potem uznajemy HOME za koniec.
    Live: „Skonfiguruj na nowym telefonie” + Kontynuuj bywa top=`home_feed`.
    Gdy flat wspomina kontakty — Pomiń/Nie zezwalaj, nigdy Kontynuuj.
    """
    confirmed = 0
    empty_streak = 0
    for step_i in range(max_steps):
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        pkg = (dominant_package(flat) or "").lower()
        top = ctx.navigator.identify().top

        if any(p in pkg for p in PERMISSION_DIALOG_PACKAGES) and _mentions_sensitive_permission(flat):
            # Natywny dialog lokalizacji/kontaktów — zawsze odmawiamy
            btn = (find_field(flat, (), DENY_HINTS, BUTTON_CLASSES)
                   or _find_label_on_textview(flat, DENY_HINTS, allow_not_now=True))
            action = "deny"
        elif _mentions_contacts(flat):
            # Intro IG / dowolny flat o kontaktach — Pomiń, NIE Kontynuuj
            btn = _find_contacts_skip_button(flat)
            action = "skip"
        else:
            # 1) zapis danych logowania — resource-id / tekst „Zapisz"
            # 2) pozostałe popupy (Kontynuuj / Rozumiem / …) — też TextView
            #    w tym „Skonfiguruj na nowym telefonie" mimo top=home_feed
            btn = (_find_save_login_button(flat)
                   or find_field(flat, (), CONFIRM_HINTS, BUTTON_CLASSES)
                   or _find_hint_on_textview(flat, CONFIRM_HINTS))
            action = "confirm"

        if btn:
            empty_streak = 0
            label = _node_label(btn) or "(bez etykiety)"
            popup_kind = _describe_popup(flat, label, action)
            login_log.say(f"Popup [{step_i + 1}]: {popup_kind} → tap „{label}\"")
            ctx.ht.tap(*center(btn["bounds"]), pause_kind="tap")
            ctx.humanizer.pause("after_launch")
            confirmed += 1
            ctx.log("system.login_if_needed.popup_confirmed",
                    label=label, rid=btn.get("resource-id") or "",
                    action=action, popup_kind=popup_kind)
            continue

        # Brak CTA — jeśli naprawdę home, kończymy; inaczej czekamy na pojawienie
        if top in HOME_SCREENS:
            login_log.say(f"Jesteśmy na {top} bez dalszych popupów — stop")
            break
        empty_streak += 1
        if empty_streak >= POPUP_APPEAR_ATTEMPTS:
            login_log.say(
                f"Brak znanego przycisku (top={top or '?'}) — kończę łańcuch")
            break
        login_log.say(
            f"Czekam na popup… ({empty_streak}/{POPUP_APPEAR_ATTEMPTS}, "
            f"top={top or '?'})")
        time.sleep(POPUP_APPEAR_WAIT_S)
    return confirmed


def _describe_popup(flat: list[dict], label: str, action: str) -> str:
    """Krótka etykieta ekranu dla terminala."""
    blob = " ".join(
        normalize_ws((e.get("text") or "") + " " + (e.get("content-desc") or ""))
        for e in flat[:80])
    if action == "deny":
        return "uprawnienia (odmawiam)"
    if action == "skip":
        return "kontakty (pomijam)"
    if label in ("zapisz", "save") or "save_login" in blob:
        return "zapisz dane logowania"
    if "nowym telefon" in blob or "new phone" in blob or "skonfiguruj" in blob:
        return "skonfiguruj na nowym telefonie"
    if "lokalizacj" in blob or "location" in blob:
        return "wprowadzenie lokalizacji"
    if "kontakt" in blob or "contacts" in blob:
        return "wprowadzenie kontaktów"
    if label in ("kontynuuj", "continue", "dalej", "next"):
        return "kontynuuj"
    if label in ("rozumiem", "got it", "ok"):
        return "rozumiem / tip"
    return "popup"


def _node_label(node: dict) -> str:
    return normalize_ws((node.get("text") or "") + " " + (node.get("content-desc") or "")).strip()


def _is_not_now(node: dict) -> bool:
    rid = node.get("resource-id") or ""
    if any(rid.endswith(x.split(":")[-1]) or rid == x for x in NOT_NOW_IDS):
        return True
    label = _node_label(node)
    return any(h in label for h in NOT_NOW_HINTS)


def _find_save_login_button(flat: list[dict]) -> dict | None:
    """Przycisk „Zapisz" na prompcie zapisu danych logowania.

    Live: tekst bywa na TextView, a klikalny kontener nie niesie opisu —
    szukamy ID, potem tekstu „Zapisz" (nie „Zapisać…?" z tytułu).
    """
    for e in flat:
        rid = e.get("resource-id") or ""
        if rid in SAVE_LOGIN_BUTTON_IDS or rid.endswith("/save_login_prompt"):
            if not _is_not_now(e):
                return e
    # Dokładniejsze niż substring „zapisz" w „zapisać dane…"
    for e in flat:
        if _is_not_now(e):
            continue
        label = _node_label(e)
        # całe słowo / krótki label przycisku
        if label in ("zapisz", "save", "save login info", "save info"):
            return e
        if label.startswith("zapisz") and "dane" not in label:
            return e
    return None


def _find_contacts_skip_button(flat: list[dict]) -> dict | None:
    """Na ekranie kontaktów: Pomiń → Nie teraz → Nie zezwalaj.

    Świadomie NIE szukamy Kontynuuj — to prowadzi do nadania kontaktów.
    """
    return (_find_label_button(flat, SKIP_PREFERRED_HINTS, allow_not_now=True)
            or _find_label_button(flat, SKIP_FALLBACK_HINTS, allow_not_now=True))


def _find_label_button(flat: list[dict], hints: tuple, *,
                       allow_not_now: bool = False) -> dict | None:
    """Button/FrameLayout albo TextView z daną etykietą."""
    btn = find_field(flat, (), hints, BUTTON_CLASSES)
    if btn and (allow_not_now or not _is_not_now(btn)):
        return btn
    return _find_label_on_textview(flat, hints, allow_not_now=allow_not_now)


def _find_hint_on_textview(flat: list[dict], hints: tuple) -> dict | None:
    """Fallback: tekst afirmatywny na TextView (IG często tak buduje CTA)."""
    return _find_label_on_textview(flat, hints, allow_not_now=False)


def _find_label_on_textview(flat: list[dict], hints: tuple, *,
                            allow_not_now: bool) -> dict | None:
    matches = []
    for e in flat:
        cls = e.get("class") or ""
        if "TextView" not in cls and "Button" not in cls:
            continue
        if not allow_not_now and _is_not_now(e):
            continue
        label = _node_label(e)
        if any(h in label for h in hints):
            # Unikaj tytułu „Zapisać dane logowania?" — za długi / pytanie
            if "?" in label or len(label) > 40:
                continue
            matches.append(e)
    if not matches:
        return None
    return next((e for e in matches if e.get("clickable")), matches[0])


def _mentions_sensitive_permission(flat: list[dict]) -> bool:
    for e in flat:
        blob = normalize_ws((e.get("text") or "") + " " + (e.get("content-desc") or ""))
        if any(h in blob for h in SENSITIVE_PERMISSION_HINTS):
            return True
    return False


def _mentions_contacts(flat: list[dict]) -> bool:
    """Czy flat wygląda na ekran/popup o kontaktach (IG lub system)."""
    for e in flat:
        blob = normalize_ws((e.get("text") or "") + " " + (e.get("content-desc") or ""))
        if any(h in blob for h in CONTACTS_HINTS):
            return True
    return False
