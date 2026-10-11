"""system.login_if_needed — wpisz login/hasło na ekranie logowania IG, jeśli
się na nim znajdujemy, i potwierdź łańcuch popupów, które IG pokazuje zaraz
po pierwszym logowaniu na nowym urządzeniu/klonie.

Świadomie NIE omija captchy / checkpointu — to kończy się ręcznie na
telefonie (ustalone). Captcha BLOKUJE łańcuch popupów: NIE wolno tapnąć
„Kontynuuj” przy „Wpisz widoczne znaki” (live kasia21sum 2026-09-06:
puste pole → czerwony błąd). Weryfikację kodem e-mail/SMS obsługuje
osobny krok `system.enter_verification_code_if_needed` (panel→bot).

**Overlay Google Passkey** (live kasia21sum 2026-09-06) —
`AssistedSignInActivity` przykrywa pola logowania; hierarchia widzi tylko
GMS → `login_screen_not_detected`. Najpierw X
`com.google.android.gms:id/cancel` (desc „Anuluj”). NIGDY nie tapamy
„Użyj klucza z innego urządzenia”.
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
# „Nie pamiętasz hasła?" też zawiera „hasł" — przycisku logowania nie mylić z tym.
LOGIN_BUTTON_SKIP_HINTS = ("nie pamiętasz", "forgot", "nowe konto", "create new",
                           "pokaż hasło", "show password")

# Po `input text` w polu hasła klawiatura zasłania CTA (live galaxy / SM_A520F
# 2026-08-28: pola wpisane, dump bez „Zaloguj się", FAIL). Chowamy IME i
# dajemy UI chwilę na powrót przycisku.
LOGIN_BUTTON_WAIT_ATTEMPTS = 6
LOGIN_BUTTON_WAIT_S = 0.8

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

# Komentarz: powiadomienia — NIGDY „Włącz”; zamykamy Nie teraz / Pomiń / Deny
NOTIFICATION_HINTS = (
    "powiadomien", "notification", "powiadomienia",
)
# Komentarz: live OnePlus 2026-09-09 — intro mapy po tap Direct
MAP_INTRO_HINTS = (
    "przedstawiamy mapę", "mapę instagramu", "instagram map",
)

# Overlay GMS passkey — zamknąć X zanim szukamy pól IG (kasia21sum 2026-09-06)
GMS_PACKAGES = ("com.google.android.gms",)
GMS_CANCEL_IDS = (
    "com.google.android.gms:id/cancel",
    "com.google.android.gms:id/touch_outside",
)
GMS_CANCEL_HINTS = ("anuluj", "cancel")
GMS_PASSKEY_HINTS = (
    "zaloguj się w inny sposób",
    "użyj klucza z innego urządzenia",
    "use a passkey",
    "use a key from another device",
    "sign in another way",
)

# Captcha IG — człowiek na telefonie; „Kontynuuj” NIE jest popupem
CAPTCHA_HINTS = (
    "captcha",
    "nie jestem robotem",
    "i'm not a robot",
    "sprawdzamy, czy to ty",
    "wpisz widoczne znaki",
    "wpisz znaki",
    "real human behind this login",
    "wygeneruj nowy kod",
)

# Ekran/popup o kontaktach (IG intro LUB flat z „kontakt…") — NIGDY Kontynuuj.
# Preferujemy „Pomiń", potem „Nie teraz", potem natywne „Nie zezwalaj".
CONTACTS_HINTS = ("kontakt", "contacts")
SKIP_PREFERRED_HINTS = ("pomiń", "skip")
SKIP_FALLBACK_HINTS = ("nie teraz", "not now", "nie zezwalaj", "don't allow", "deny")

MAX_POST_LOGIN_POPUPS = 10
HOME_SCREENS = {"home_feed", "reels_feed", "profile_self", "search_top", "direct_inbox"}

# Świeżo uruchomiony klon bywa jeszcze na splashu / overlayu (live kasia21sum
# 2026-09-06: pierwszy start androic ~10s). Dwa kolejne dumpy z polami =
# naprawdę ekran logowania, nie błysk.
LOGIN_SCREEN_WAIT_ATTEMPTS = 12
LOGIN_SCREEN_WAIT_S = 2.0
LOGIN_SCREEN_STABLE_HITS = 2

# Po „Zaloguj się” musimy zejść z pól logowania (save-login / captcha / feed).
# Live 2026-09-06: bot tapnął IME „Dalej”/„OK” i „pokaż hasło”, ekran nie zszedł.
LEFT_LOGIN_ATTEMPTS = 10
LEFT_LOGIN_WAIT_S = 1.2

# Chrome ekranu logowania / IME — NIE są popupami po zalogowaniu
LOGIN_CHROME_HINTS = (
    "pokaż hasło", "show password",
    "nie pamiętasz", "forgot",
    "zaloguj się", "log in",
    "nowe konto", "create new", "utwórz konto",
)
IME_CONFIRM_LABELS = ("dalej", "next", "ok", "done", "gotowe")
# Krótkie frazy CONFIRM tylko jako cała etykieta — „ok” w środku słowa to fałsz
SHORT_CONFIRM_HINTS = ("ok",)

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
        user_field, pass_field = _wait_login_screen(ctx)
        if not user_field or not pass_field:
            top = ctx.navigator.identify().top
            login_log.say(f"Ekran logowania nie wykryty (top={top}) — skip")
            return StepResult.skipped("login_screen_not_detected", top=top)
        login_log.say("Ekran logowania potwierdzony (oba pola, 2 dumpy)")

        filled_user = _type_and_confirm_field(
            ctx, kind="login", hints=USERNAME_HINTS, ids=USERNAME_IDS,
            expected=creds["ig_username"], first_field=user_field)
        if not filled_user:
            login_log.say("Login nie wszedł do pola — FAIL")
            return StepResult.failed("username_not_filled")

        filled_pass = _type_and_confirm_field(
            ctx, kind="hasło", hints=PASSWORD_HINTS, ids=PASSWORD_IDS,
            expected=creds["ig_password"], first_field=pass_field,
            secret=True)
        if not filled_pass:
            login_log.say("Hasło nie weszło do pola — FAIL")
            return StepResult.failed("password_not_filled", username_filled=True)

        # Klawiatura po polu hasła zasłania „Zaloguj się" (galaxy 2026-08-28).
        _dismiss_ime(ctx)
        btn = None
        for attempt in range(LOGIN_BUTTON_WAIT_ATTEMPTS):
            flat3 = flatten_hierarchy(ctx.device.hierarchy(force=True))
            btn = _find_login_button(flat3)
            if btn:
                break
            if attempt < LOGIN_BUTTON_WAIT_ATTEMPTS - 1:
                login_log.say(
                    f"Szukam „Zaloguj się”… ({attempt + 1}/{LOGIN_BUTTON_WAIT_ATTEMPTS})")
                if attempt == 0:
                    _dismiss_ime(ctx)
                time.sleep(LOGIN_BUTTON_WAIT_S)
        if not btn:
            login_log.say("Brak przycisku „Zaloguj się” — FAIL")
            return StepResult.failed("nie znaleziono przycisku logowania",
                                     username_typed=True, password_typed=True)
        login_log.say("Klikam „Zaloguj się”…")
        ctx.ht.tap(*center(btn["bounds"]), pause_kind="tap")
        ctx.humanizer.pause("after_launch")

        left = _wait_left_login_screen(ctx)
        if left.get("captcha"):
            login_log.say("CAPTCHA — przerwa, wpisz znaki ręcznie na telefonie")
            return StepResult.failed("captcha_requires_human", submitted=True)
        if not left.get("left"):
            login_log.say("Ekran logowania nie zszedł po Zaloguj się — FAIL")
            return StepResult.failed("login_screen_unchanged", submitted=True)

        login_log.say("Ekran po logowaniu zmienił się — potwierdzam popupy…")
        popups_confirmed = _confirm_post_login_popups(ctx)

        # Komentarz: captcha mogła wejść w trakcie popupów
        flat_after = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if _mentions_captcha(flat_after):
            login_log.say("CAPTCHA — przerwa, wpisz znaki ręcznie na telefonie")
            return StepResult.failed(
                "captcha_requires_human",
                submitted=True, popups_confirmed=popups_confirmed)

        top_after = ctx.navigator.identify().top
        login_log.say(
            f"Koniec logowania — ekran={top_after or '?'}, "
            f"potwierdzonych popupów={popups_confirmed}")
        ctx.log("system.login_if_needed.submitted", landed_on=top_after,
                popups_confirmed=popups_confirmed)
        return StepResult.ok(submitted=True, landed_on=top_after,
                             popups_confirmed=popups_confirmed)


def _wait_login_screen(ctx) -> tuple[dict | None, dict | None]:
    """Czekaj aż oba pola będą na 2 kolejnych dumpach — pierwszy start jest wolny."""
    hits = 0
    last_user = last_pass = None
    for attempt in range(LOGIN_SCREEN_WAIT_ATTEMPTS):
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if _dismiss_gms_passkey(ctx, flat):
            login_log.say("Zamknąłem overlay Google Passkey")
            hits = 0
            time.sleep(0.8)
            continue
        user_field = find_field(flat, USERNAME_IDS, USERNAME_HINTS, "EditText")
        pass_field = find_field(flat, PASSWORD_IDS, PASSWORD_HINTS, "EditText")
        if user_field and pass_field:
            hits += 1
            last_user, last_pass = user_field, pass_field
            login_log.say(
                f"Pola logowania widoczne ({hits}/{LOGIN_SCREEN_STABLE_HITS})")
            if hits >= LOGIN_SCREEN_STABLE_HITS:
                return last_user, last_pass
        else:
            hits = 0
            last_user = last_pass = None
            if attempt < LOGIN_SCREEN_WAIT_ATTEMPTS - 1:
                login_log.say(
                    f"Jeszcze nie ma pól logowania — czekam "
                    f"({attempt + 1}/{LOGIN_SCREEN_WAIT_ATTEMPTS}, "
                    f"pierwszy start bywa długi)")
        time.sleep(LOGIN_SCREEN_WAIT_S)
    return last_user, last_pass


def _field_value(node: dict | None) -> str:
    """Tekst z EditText — hint zostawiamy jako pusty."""
    if not node:
        return ""
    return (node.get("text") or "").strip()


def _looks_like_hint(value: str, hints: tuple) -> bool:
    """Puste albo sam placeholder (np. „Hasło,")."""
    low = normalize_ws(value)
    if not low:
        return True
    return any(low == h or low == h + "," or low.startswith(h + ",")
               for h in hints)


def _username_is_filled(node: dict | None, username: str) -> bool:
    val = _field_value(node)
    if username and username.lower() in val.lower():
        return True
    if val and not _looks_like_hint(val, USERNAME_HINTS) and len(val) >= 2:
        return True
    return False


def _password_is_filled(node: dict | None) -> bool:
    """Hasło rzadko wraca jawnym tekstem — byle nie hint."""
    val = _field_value(node)
    if not val or _looks_like_hint(val, PASSWORD_HINTS):
        return False
    return len(val) >= 1


def _clear_edit(ctx) -> None:
    """DEL po skoku na koniec — retry bez doklejania drugiej kopii."""
    ctx.device.shell("input keyevent KEYCODE_MOVE_END")
    for _ in range(24):
        ctx.device.shell("input keyevent KEYCODE_DEL")


def _refresh_field(ctx, ids: tuple, hints: tuple, fallback: dict | None) -> dict | None:
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    return find_field(flat, ids, hints, "EditText") or fallback


def _type_and_confirm_field(ctx, *, kind: str, hints: tuple, ids: tuple,
                            expected: str, first_field: dict,
                            secret: bool = False) -> bool:
    """Tap + input text + dump: pole ma wartość, nie sam hint.

    Hasła nie logujemy. Live: overlay/popup potrafi zjeść pierwszy input
    (kasia21sum 2026-09-06 — weszło samo hasło).
    """
    field = first_field
    for attempt in range(2):
        if attempt:
            login_log.say(f"Ponawiam wpisanie pola {kind}…")
            _clear_edit(ctx)
        login_log.say(f"Wpisuję {kind}…")
        ctx.ht.tap(*center(field["bounds"]), pause_kind="tap")
        time.sleep(0.25)
        ctx.device.shell(f'input text "{escape_input_text(expected)}"')
        time.sleep(0.6)
        field = _refresh_field(ctx, ids, hints, field)
        ok = (_password_is_filled(field) if secret
              else _username_is_filled(field, expected))
        if ok:
            login_log.say(f"Pole {kind} potwierdzone")
            return True
        login_log.say(f"Pole {kind} puste po wpisaniu — retry")
        if field:
            continue
        return False
    return False


def _login_fields_visible(flat: list[dict]) -> bool:
    """Nadal formularz logowania — nie łańcuch popupów po zalogowaniu."""
    user = find_field(flat, USERNAME_IDS, USERNAME_HINTS, "EditText")
    pwd = find_field(flat, PASSWORD_IDS, PASSWORD_HINTS, "EditText")
    return bool(user and pwd)


def _wait_left_login_screen(ctx) -> dict:
    """Po tapnięciu Zaloguj się — poczekaj aż znikną pola albo wejdzie captcha."""
    for attempt in range(LEFT_LOGIN_ATTEMPTS):
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if _mentions_captcha(flat):
            return {"left": True, "captcha": True}
        if not _login_fields_visible(flat):
            login_log.say("Ekran logowania zszedł")
            return {"left": True, "captcha": False}
        login_log.say(
            f"Czekam aż zniknie formularz logowania "
            f"({attempt + 1}/{LEFT_LOGIN_ATTEMPTS})")
        time.sleep(LEFT_LOGIN_WAIT_S)
    return {"left": False, "captcha": False}


def _is_login_chrome(node: dict, flat: list[dict]) -> bool:
    """Pokaż hasło / IME Dalej / Zaloguj się — nie popup."""
    label = _node_label(node)
    if any(h in label for h in LOGIN_CHROME_HINTS):
        return True
    if _login_fields_visible(flat) and label in IME_CONFIRM_LABELS:
        return True
    return False


def _confirm_hint_matches(label: str, hints: tuple) -> bool:
    """„ok” tylko jako cała etykieta; dłuższe frazy — podciąg."""
    for h in hints:
        if h in SHORT_CONFIRM_HINTS:
            if label == h:
                return True
            continue
        if h in label:
            return True
    return False


def _dismiss_ime(ctx) -> None:
    """Schowaj klawiaturę tylko gdy naprawdę wisi — BACK na logowaniu bez IME
    zamykałby ekran logowania."""
    overlay = getattr(ctx.navigator.identify(), "overlay", None)
    shown = False
    try:
        out = (ctx.device.shell("dumpsys input_method | grep mInputShown") or "")
        shown = "minputshown=true" in out.lower().replace(" ", "")
    except Exception:
        shown = False
    if overlay == "keyboard" or shown:
        ctx.device.press_back()
        time.sleep(0.35)


def _find_login_button(flat: list[dict]) -> dict | None:
    """Przycisk logowania — ID, Button/FrameLayout, albo dowolny klikalny z etykietą.

    Live SM_A520F: `android.widget.Button` + content-desc „Zaloguj się", bez
    resource-id. Nie brać „Nie pamiętasz hasła?" / „Pokaż hasło".
    """
    btn = find_field(flat, LOGIN_BUTTON_IDS, LOGIN_BUTTON_HINTS, BUTTON_CLASSES)
    if btn and not _login_button_is_decoy(btn):
        return btn
    matches = []
    for e in flat:
        if not e.get("clickable"):
            continue
        label = _node_label(e)
        if not any(h in label for h in LOGIN_BUTTON_HINTS):
            continue
        if _login_button_is_decoy(e):
            continue
        matches.append(e)
    if not matches:
        return None
    return matches[0]


def _login_button_is_decoy(node: dict) -> bool:
    label = _node_label(node)
    return any(h in label for h in LOGIN_BUTTON_SKIP_HINTS)


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

        # Komentarz: captcha — „Kontynuuj” to submit pustego pola, nie popup
        if _mentions_captcha(flat):
            login_log.say("CAPTCHA na ekranie — stop (nie tapam Kontynuuj)")
            break

        # Komentarz: live 2026-09-06 — IME Dalej/OK i „pokaż hasło” to NIE popup
        if _login_fields_visible(flat):
            login_log.say("Nadal formularz logowania — nie tapam chrome/IME")
            _dismiss_ime(ctx)
            empty_streak += 1
            if empty_streak >= POPUP_APPEAR_ATTEMPTS:
                break
            time.sleep(POPUP_APPEAR_WAIT_S)
            continue

        if any(p in pkg for p in PERMISSION_DIALOG_PACKAGES) and (
                _mentions_sensitive_permission(flat) or _mentions_notifications(flat)):
            # Natywny dialog lokalizacji/kontaktów/powiadomień — zawsze odmawiamy
            btn = (find_field(flat, (), DENY_HINTS, BUTTON_CLASSES)
                   or _find_label_on_textview(flat, DENY_HINTS, allow_not_now=True))
            action = "deny"
        elif _mentions_notifications(flat):
            # Intro IG o powiadomieniach — Pomiń / Nie teraz, nigdy Włącz
            btn = _find_contacts_skip_button(flat)
            action = "skip"
        elif any(h in _flat_blob(flat) for h in MAP_INTRO_HINTS):
            # Komentarz: „Eksploruj mapę” to NIE confirm — zamykamy Nie teraz
            btn = _find_contacts_skip_button(flat)
            action = "skip"
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
            if btn and _is_login_chrome(btn, flat):
                btn = None
            if btn and action == "confirm" and _find_save_login_button(flat) is not btn:
                if not _confirm_hint_matches(_node_label(btn), CONFIRM_HINTS):
                    btn = None

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
        if "powiadom" in blob or "notification" in blob:
            return "powiadomienia (pomijam)"
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


def _mentions_notifications(flat: list[dict]) -> bool:
    """Ekran/popup o powiadomieniach (IG intro albo POST_NOTIFICATIONS)."""
    for e in flat:
        blob = normalize_ws((e.get("text") or "") + " " + (e.get("content-desc") or ""))
        if any(h in blob for h in NOTIFICATION_HINTS):
            return True
    return False


def _flat_blob(flat: list[dict]) -> str:
    """Cały widoczny tekst + desc — do detekcji overlay/captcha."""
    return normalize_ws(" ".join(
        (e.get("text") or "") + " " + (e.get("content-desc") or "")
        for e in flat))


def _mentions_captcha(flat: list[dict]) -> bool:
    """Captcha IG — człowiek wpisuje znaki; bot nie klika Kontynuuj."""
    blob = _flat_blob(flat)
    return any(h in blob for h in CAPTCHA_HINTS)


def _looks_like_gms_passkey(flat: list[dict]) -> bool:
    """Bottom sheet Google Passkey / AssistedSignIn nad logowaniem IG."""
    for e in flat:
        pkg = (e.get("package") or "").lower()
        if any(p in pkg for p in GMS_PACKAGES):
            return True
    blob = _flat_blob(flat)
    return any(h in blob for h in GMS_PASSKEY_HINTS)


def _find_gms_cancel(flat: list[dict]) -> dict | None:
    """X / Anuluj na sheet GMS — nie mylić z Anuluj IG."""
    if not _looks_like_gms_passkey(flat):
        return None
    for e in flat:
        rid = e.get("resource-id") or ""
        pkg = (e.get("package") or "").lower()
        if rid in GMS_CANCEL_IDS or any(rid.endswith("/" + i.split("/")[-1])
                                        and "gms" in rid
                                        for i in GMS_CANCEL_IDS):
            return e
        if "gms" not in pkg:
            continue
        label = _node_label(e)
        if any(label == h or label.endswith(h) for h in GMS_CANCEL_HINTS):
            return e
    return None


def _dismiss_gms_passkey(ctx, flat: list[dict]) -> bool:
    """Zamknij overlay passkey (X). True gdy tapnięto. NIE tapaj klucza."""
    btn = _find_gms_cancel(flat)
    if not btn:
        return False
    xy = center(btn.get("bounds"))
    if not xy:
        return False
    login_log.say("Overlay Google Passkey — tap X / Anuluj")
    ctx.ht.tap(*xy, pause_kind="tap")
    return True
