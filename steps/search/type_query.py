"""search.type_query — tap w input, wpisz zapytanie, poczekaj na sugestie.

Po wpisaniu tekstu Android potrafi wyświetlić **systemowe okienko wyboru
metody wprowadzania** („Przełączanie metody wprowadzania"). Nie należy do
Instagrama, przykrywa ekran i przechwytuje kolejne tapy — na żywym przebiegu
skończyło się to tapnięciem w ten dialog zamiast w wynik wyszukiwania.
Dlatego krok sprawdza po sobie, na czym wylądował, i zamyka wszystko, co nie
jest wyszukiwarką.

Live bug (2026-08): drugi `press_back` przy `overlay=system_ui` BEZ markera
IME gubił focus pola (pole zostawało „Szukaj"), a `input text` lądował w
pustkę — mimo że lista sugestii bywała widoczna. Dismiss działa teraz tylko
na znany picker; goły system_ui na ekranie search nie dostaje BACK.

Live bug (2026-08): `adb input text` z diakrytykami (ą/ć/ę/ł/ń/ó/ś/ź/ż)
rzuca NPE (`InputText.sendText` → null array) — live: `#bólpleców` zostawał
„Szukaj", `#postawaciała` → `#postawaciaa`, `kobiecość` → pole puste.
Przed wpisaniem foldujemy do ASCII; IG i tak szuka bez ogonków.

Live bug (2026-08): po `content_search.back_failed` (home_feed) type_query
spamował 3× retry w próżnię bez `identify`. Po failu najpierw logujemy
top/overlay/pkg; poza SEARCH_SCREENS — jeden recover (ensure search_top),
inaczej fail z kontekstem ekranu (plugin może open/skip).
"""
from __future__ import annotations

import re
import time
import unicodedata

from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.system._ui_helpers import escape_input_text

SEARCH_SCREENS = ("search_input_active", "search_results", "search_top")

INPUT_ID = "com.instagram.android:id/action_bar_search_edit_text"
# Krzyżyk czyszczący pole — bez resource-id, ale ma stały opis.
CLEAR_HINTS = ("usuń tekst", "wyczyść", "clear text", "clear")

KEY_ENTER = 66
KEY_DEL = 67
KEY_MOVE_END = 123

# Okienko wyboru klawiatury — rozpoznajemy po treści, bo pakiet bywa różny
# (android / systemui / settings) zależnie od nakładki producenta.
IME_PICKER_TEXTS = ("przełączanie metody wprowadzania",
                    "metody wprowadzania",
                    "change keyboard", "choose input method",
                    "select input method", "switch input method")

# nicki / domeny / #tagi BEZ spacji — char-by-char fallback po failu bulku
_SAFE_QUERY_RE = re.compile(r"^[A-Za-z0-9._#+@\-]+$")
# + spacja: jeden `input text "polish%sbeauty"` (escape → %s). Live: keyword
# ze spacją szedł znak-po-znaku i przy race dwóch sesji garbował pole.
_BULK_QUERY_RE = re.compile(r"^[A-Za-z0-9._#+@\- ]+$")

# ł/Ł nie rozkłada NFKD — mapujemy PL ręcznie, reszta przez combining marks
_PL_FOLD = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N",
    "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z",
})

PLACEHOLDER_TEXTS = ("szukaj", "search", "suchen", "buscar")

# ID wierszy userów na liście sugestii (jak w pick_result) — soft-success
_USERNAME_IDS = (
    "com.instagram.android:id/row_search_user_username",
    "com.instagram.android:id/row_user_primary_name",
)


@register
class SearchTypeQuery:
    name = "search.type_query"
    requires_screen = None

    def run(self, ctx, params):
        query = str(params.get("query") or "").strip()
        if not query:
            return StepResult.failed("params.query wymagany")

        # adb input text nie znosi Unicode — fold przed wpisaniem
        type_as = _fold_for_adb(query)
        if type_as != query:
            ctx.log("search.type_query.fold_diacritics",
                    raw=query[:60], typed=type_as[:60])

        # Jeden dump na wejściu; identify() robił tu pełny HTTP-dump.
        s = ctx.navigator.identify()
        ctx.log("search.type_query.screen", phase="start",
                top=s.top, overlay=s.overlay, pkg=s.package)
        if s.top not in SEARCH_SCREENS:
            # Nie tapuj „w próżnię" na home_feed — najpierw wróć na search
            if not _recover_search_ui(ctx, reason="start_off_search"):
                st = ctx.navigator.identify()
                return StepResult.failed(
                    f"poza search przed type (top={st.top})",
                    query=query, top=st.top, overlay=st.overlay, pkg=st.package,
                    off_search=True)

        # Po recover mamy nowy ekran; jeden identify do sprawdzenia focusu.
        if ctx.navigator.identify().top != "search_input_active":
            c = ctx.screen_map.anchor_center("search.input")
            if not c:
                st = ctx.navigator.identify()
                return StepResult.failed(
                    "brak kotwicy search.input",
                    query=query, top=st.top, overlay=st.overlay, pkg=st.package)
            ctx.ht.tap(*c, pause_kind="tap")

        # Dialog IME potrafi wyskoczyć JUŻ przy focusie pola — zamknij zanim
        # cokolwiek wpiszemy, inaczej `input text` ląduje w pustkę.
        dismissed = _dismiss_ime_picker(ctx)

        cleared = False
        if params.get("clear_first", True):
            cleared = _force_clear_input(ctx)
            # Oddech po clear — IME/pole czasem jeszcze „trzyma" stary tekst
            time.sleep(0.12)

        typed_ok = False
        recovered_once = False
        for attempt in range(1, 4):
            # Jedna iteracja: jeden dump na początku, helperki go reuse'ują
            # dopóki nie wykonamy gestu. Po geście helperki same robią nowy.
            flat = flatten_hierarchy(ctx.device.hierarchy())

            # 1) zamknij picker JEŚLI jest (bez zbędnego BACK na system_ui)
            dismissed = _dismiss_ime_picker(ctx, flat=flat) or dismissed
            # 2) focus na polu — tap czasem znowu otwiera picker
            if not _field_focused(ctx, flat=flat):
                _focus_search_input(ctx, flat=flat)
                dismissed = _dismiss_ime_picker(ctx) or dismissed
                flat = None  # tap invaliduje dump
            # 3) wpisz dopiero gdy picker zniknął
            if _ime_picker_visible(ctx, flat=flat):
                ctx.log("search.type_query.ime_still_up", attempt=attempt)
                dismissed = _dismiss_ime_picker(ctx, max_attempts=3,
                                                 flat=flat) or dismissed
                _focus_search_input(ctx)
                flat = None

            # Przed KAŻDYM wpisaniem (także retry) — puste pole, nie doklejaj
            if attempt > 1 or cleared:
                cleared = _ensure_field_clear(ctx, flat=flat) or cleared
                flat = None  # clear może tapnąć, po geście dump jest świeży

            _type_query_chars(ctx, type_as)
            time.sleep(0.4)
            # picker potrafi wyskoczyć TUŻ PO input text — zamknij, nie kasuj tekstu
            dismissed = _dismiss_ime_picker(ctx) or dismissed
            time.sleep(0.2)

            # Świeży dump po wpisaniu tekstu (UI może się zmienić).
            flat = flatten_hierarchy(ctx.device.hierarchy())
            current = _read_input_text(ctx, flat=flat)
            if _query_landed(current, query):
                typed_ok = True
                break
            # Soft-success: lista sugestii już pokazuje szukanego usera
            # (focus/pole flaky, a pick_result i tak czyta hierarchy)
            if _results_show_username(ctx, query, flat=flat):
                ctx.log("search.type_query.soft_success_results",
                        attempt=attempt, got=(current or "")[:60], want=query[:40])
                typed_ok = True
                break

            # Fail → NAJPIERW identify (nie spamuj probe bez wiedzy o ekranie)
            state = ctx.navigator.identify()
            ctx.log("search.type_query.retry",
                    attempt=attempt, got=(current or "")[:60], want=query[:40],
                    typed_as=type_as[:40],
                    top=state.top, overlay=state.overlay, pkg=state.package)

            if state.top not in SEARCH_SCREENS:
                if recovered_once:
                    # Już próbowaliśmy wrócić — nie kręć 3× w home_feed
                    return StepResult.failed(
                        f"nie udało się wpisać zapytania poza search "
                        f"(top={state.top}, pole='{current}')",
                        query=query, cleared=cleared, dismissed_dialog=dismissed,
                        top=state.top, overlay=state.overlay, pkg=state.package,
                        off_search=True)
                if not _recover_search_ui(ctx, reason="retry_off_search"):
                    st = ctx.navigator.identify()
                    return StepResult.failed(
                        f"nie udało się wrócić na search (top={st.top})",
                        query=query, cleared=cleared, dismissed_dialog=dismissed,
                        top=st.top, overlay=st.overlay, pkg=st.package,
                        off_search=True)
                recovered_once = True
                continue

            # Na search, ale śmieci/puste — twarde clear; attempt=1: char-by-char
            # tylko dla zapytań BEZ spacji (ze spacją bulk %s jest właściwy)
            cleared = _ensure_field_clear(ctx, flat=flat) or cleared
            flat = None
            if attempt == 1 and _SAFE_QUERY_RE.match(type_as):
                dismissed = _dismiss_ime_picker(ctx) or dismissed
                _focus_search_input(ctx)
                dismissed = _dismiss_ime_picker(ctx) or dismissed
                for ch in type_as:
                    # każdy znak osobno — też w cudzysłowach (# = komentarz shella)
                    ctx.device.shell(_shell_input_text_cmd(ch))
                    time.sleep(0.03)
                time.sleep(0.3)
                dismissed = _dismiss_ime_picker(ctx) or dismissed
                flat = flatten_hierarchy(ctx.device.hierarchy())
                current = _read_input_text(ctx, flat=flat)
                if _query_landed(current, query) or _results_show_username(ctx, query, flat=flat):
                    typed_ok = True
                    break

        if not typed_ok:
            # Ostatnia deska: wyniki widoczne mimo failed field-read
            flat = flatten_hierarchy(ctx.device.hierarchy())
            if _results_show_username(ctx, query, flat=flat):
                ctx.log("search.type_query.soft_success_after_fail",
                        query=query[:40], field=_read_input_text(ctx, flat=flat)[:40])
                typed_ok = True
            else:
                st = ctx.navigator.identify()
                ctx.log("search.type_query.screen", phase="fail",
                        top=st.top, overlay=st.overlay, pkg=st.package,
                        field=_read_input_text(ctx, flat=flat)[:60])
                return StepResult.failed(
                    f"nie udało się wpisać zapytania (pole='{_read_input_text(ctx, flat=flat)}')",
                    query=query, cleared=cleared, dismissed_dialog=dismissed,
                    top=st.top, overlay=st.overlay, pkg=st.package,
                    off_search=st.top not in SEARCH_SCREENS)

        # Samo wpisanie zostawia LISTĘ SUGESTII, a nie siatkę treści —
        # do wyników trzeba zatwierdzić frazę.
        submitted = False
        if params.get("submit"):
            submitted = _submit(ctx)

        # daj czas na dociągnięcie wierszy userów (pick_result czyta hierarchy)
        time.sleep(0.4)
        st = ctx.navigator.identify()
        ctx.log("search.type_query.screen", phase="ok",
                top=st.top, overlay=st.overlay, pkg=st.package,
                submitted=submitted)
        return StepResult.ok(query=query, typed_as=type_as, cleared=cleared,
                             submitted=submitted, dismissed_dialog=dismissed)


def _fold_for_adb(text: str) -> str:
    """Zamień diakrytyki na ASCII — `adb input text` NPE na Unicode (Samsung/A11).

    ł/Ł nie ma combining mark → tabela PL; reszta: NFKD + drop combining.
    Spacje i znaki bezpieczne dla shella zostają; inne non-ASCII → drop.
    """
    t = (text or "").translate(_PL_FOLD)
    out = []
    for ch in unicodedata.normalize("NFKD", t):
        if unicodedata.combining(ch):
            continue
        if ord(ch) < 128:
            out.append(ch)
        # non-ASCII poza PL — pomijamy (nie da się wpisać przez input text)
    return "".join(out)


def _shell_input_text_cmd(text: str) -> str:
    """Komenda `input text \"…\"` z escapowaniem pod adb shell.

    Bez cudzysłowów `#hashtag` jest komentarzem shella → `input text` dostaje
    pusty argument i w pole wpada samo słowo (albo nic). Spacja → `%s`.
    """
    return f'input text "{escape_input_text(text)}"'


def _type_query_chars(ctx, query: str) -> None:
    """Wpisz zapytanie — najpierw jednym `input text`, fallback znak-po-znaku.

    Char-by-char + osobne adb na znak było bardzo wolne (~1s/znak).
    ASCII + spacja (keyword „polish beauty") → JEDEN strzał z `%s`.
    Wywołujący powinien podać już `_fold_for_adb(query)`.
    """
    if _BULK_QUERY_RE.match(query):
        # cudzysłów chroni `#`; escape_input_text zamienia spacje na %s
        ctx.device.shell(_shell_input_text_cmd(query))
        return
    for ch in query:
        if ch == " ":
            ctx.device.shell("input text %s")
        elif ch in ("'", '"', "\\", "`", ";", "|", "&", "<", ">", "(", ")"):
            if ch == "'":
                continue
            ctx.device.shell("input text %s")
        elif ord(ch) >= 128:
            # obrona: nie wołaj input text na Unicode (NPE na urządzeniu)
            continue
        else:
            ctx.device.shell(_shell_input_text_cmd(ch))
        time.sleep(0.03)


def _field_is_empty(ctx, flat: list[dict] | None = None) -> bool:
    """Puste albo sam placeholder — wolno wpisywać od zera."""
    cur = _read_input_text(ctx, flat=flat)
    return (not cur) or cur.casefold() in PLACEHOLDER_TEXTS


def _ensure_field_clear(ctx, flat: list[dict] | None = None) -> bool:
    """Twarde clear przed retry — nie doklejaj do śmieci po race/failu."""
    did = _force_clear_input(ctx, flat=flat)
    # Po tapie w clear_button musimy wziąć nowy dump; wariant bez tapu
    # (_force_clear_input zwrócił False) też bezpiecznie zrobi nowy.
    if _field_is_empty(ctx):
        return did
    # Krzyżyk nie zabrał wszystkiego — seria DEL
    cur = _read_input_text(ctx)
    n = min(len(cur) + 8, 48)
    keys = " ".join([str(KEY_DEL)] * n)
    ctx.device.shell(f"input keyevent {KEY_MOVE_END}")
    ctx.device.shell(f"input keyevent {keys}")
    ctx.log("search.type_query.cleared", had=cur[:40], how="keys_retry", n=n)
    return True


def _recover_search_ui(ctx, *, reason: str) -> bool:
    """Jeden attempt wrócić na search UX — bez ślepego type w home_feed."""
    before = ctx.navigator.identify()
    ctx.log("search.type_query.recover_search",
            reason=reason, top=before.top, overlay=before.overlay,
            pkg=before.package)
    try:
        ok = ctx.navigator.ensure("search_top", max_steps=4)
    except Exception as exc:
        ctx.log("search.type_query.recover_search_err", error=str(exc)[:120])
        return False
    after = ctx.navigator.identify()
    ctx.log("search.type_query.screen", phase="after_recover",
            top=after.top, overlay=after.overlay, pkg=after.package,
            ensure_ok=bool(ok))
    return after.top in SEARCH_SCREENS


def _focus_search_input(ctx, flat: list[dict] | None = None) -> None:
    """Tap w kotwicę / bounds pola wyszukiwania.

    Jeśli `flat` jest podany, używa go do fallback-bounds; po tapie cache
    hierarchii jest invalidowany, więc kolejne ready muszą wziąć nowy dump.
    """
    c = ctx.screen_map.anchor_center("search.input")
    if not c and flat is not None:
        for e in flat:
            if (e.get("resource-id") or "") == INPUT_ID:
                c = _center(e.get("bounds"))
                break
    if c:
        ctx.ht.tap(*c, pause_kind="tap")


def _force_clear_input(ctx, flat: list[dict] | None = None) -> bool:
    """Wyczyść pole na twardo (krzyżyk albo seria DEL) — także śmieci / placeholder.

    Opcjonalny `flat` pozwala uniknąć osobnego dumpu gdy krok już go zrobił.
    """
    if flat is None:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    current = ""
    clear_btn = None
    for e in flat:
        if (e.get("resource-id") or "") == INPUT_ID:
            current = (e.get("text") or "").strip()
        blob = ((e.get("content-desc") or "") + " " + (e.get("text") or "")).lower()
        if clear_btn is None and any(h in blob for h in CLEAR_HINTS):
            clear_btn = _center(e.get("bounds"))

    is_placeholder = (not current) or current.casefold() in PLACEHOLDER_TEXTS
    # puste / placeholder — NIE spamuj 24× DEL (to kosztowało ~30–50 s)
    if is_placeholder:
        return False

    if clear_btn:
        ctx.ht.tap(*clear_btn, pause_kind="tap")
        ctx.log("search.type_query.cleared", had=current[:40], how="button")
        return True

    # DEL w jednej komendzie shell (batch), nie N osobnych adb
    n = min(len(current) + 4, 40)
    keys = " ".join([str(KEY_DEL)] * n)
    ctx.device.shell(f"input keyevent {KEY_MOVE_END}")
    ctx.device.shell(f"input keyevent {keys}")
    ctx.log("search.type_query.cleared", had=current[:40], how="keys", n=n)
    return True


def _read_input_text(ctx, flat: list[dict] | None = None) -> str:
    if flat is None:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    for e in flat:
        if (e.get("resource-id") or "") == INPUT_ID:
            return (e.get("text") or "").strip()
    return ""


def _query_landed(current: str, query: str) -> bool:
    """Czy w polu jest nasza fraza (nie placeholder / śmieci).

    Przy hashtagu wymagamy też `#` w polu — inaczej uznalibyśmy sukces,
    gdy shell „zjadł" `#` jako komentarz i zostało samo słowo.
    Porównanie po fold ASCII — w polu ląduje `kobiecosc`, want=`kobiecość`.
    """
    cur = (current or "").strip()
    if not cur or cur.casefold() in PLACEHOLDER_TEXTS:
        return False
    q = _fold_for_adb(query.lstrip("@")).casefold()
    c = _fold_for_adb(cur.lstrip("@")).casefold()
    if not q:
        return False
    # Pełna fraza w polu OK; odwrotnie tylko gdy IME ucięło 1–2 znaki —
    # NIE akceptuj „polish" jako sukces dla „polish beauty" (live race).
    if q in c:
        pass
    elif c in q and len(c) >= max(4, len(q) - 2):
        pass
    else:
        return False
    # komentarz: # bez escapowania → w polu brak hash → retry / fail
    if query.lstrip().startswith("#") and "#" not in cur:
        return False
    return True


def _results_show_username(ctx, query: str, flat: list[dict] | None = None) -> bool:
    """Czy na liście sugestii widać już szukanego usera (soft-success)."""
    want = _fold_for_adb(query.lstrip("@").strip()).casefold()
    if not want or want.startswith("#"):
        return False
    if flat is None:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    for e in flat:
        if (e.get("resource-id") or "") not in _USERNAME_IDS:
            continue
        u = _fold_for_adb((e.get("text") or "").strip().lstrip("@")).casefold()
        if u == want:
            return True
    return False


def _field_focused(ctx, flat: list[dict] | None = None) -> bool:
    if flat is None:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    for e in flat:
        if (e.get("resource-id") or "") == INPUT_ID:
            return bool(e.get("focused"))
    return False


def _clear_input(ctx, flat: list[dict] | None = None) -> bool:
    """Kompat: stare wywołania → force clear."""
    return _force_clear_input(ctx, flat=flat)


def _submit(ctx) -> bool:
    """Zatwierdź frazę (ENTER) i poczekaj na siatkę wyników.

    Tu czekamy na ZMIANĘ ekranu (search → results), więc używamy szybszej
    stabilizacji video zamiast pełnego wait_for_stable_ui, a potem i tak
    robimy identify() do weryfikacji.
    """
    from v2.steps.system._ui_helpers import settle_for_animation
    ctx.device.shell(f"input keyevent {KEY_ENTER}")
    settle_for_animation(ctx, max_wait_s=2.5, fallback_wait_s=1.0)
    top = ctx.navigator.identify().top
    if top != "search_results":
        ctx.log("search.type_query.no_results_grid", top=top)
        return False
    return True


def _center(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None


def _screen_h(ctx) -> int:
    try:
        return int(ctx.account.device["_hardware"]["resolution"][1])
    except Exception:
        return 2340


def _ime_picker_visible(ctx, flat: list[dict] | None = None) -> bool:
    if flat is None:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    return _ime_picker_marker(flat, _screen_h(ctx)) is not None


# Fragmenty UI klawiatury — NIE mylić z opcjami dialogu IME (live: tap w emoji)
_IME_TAP_BLOCKLIST = (
    "emoji", "buźki", "buzki", "smiley", "gif", "sticker", "naklejk",
    "pasek narzędzi", "toolbar", "clipboard", "schowek", "voice", "głosow",
    "settings", "ustawienia", "ikony",
)


def _dismiss_ime_picker(ctx, max_attempts: int = 3, flat: list[dict] | None = None) -> bool:
    """Zamknij TYLKO okienko wyboru IME — bez ślepego BACK na system_ui.

    Wcześniej `_dismiss_system_dialog` robił BACK także przy gołym
    `overlay=system_ui` (status bar / residual), co zdejmowało klawiaturę
    i focus z pola „Szukaj". Teraz działamy wyłącznie gdy widać marker
    pickera.

    Kolejność: najpierw BACK (pewnie zamyka chooser), potem ostrożny tap
    w RadioButton POWYŻEJ klawiatury — nigdy w chrome klawiatury (emoji…).
    """
    did = False
    h = _screen_h(ctx)
    for attempt in range(max_attempts):
        if flat is None:
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        marker = _ime_picker_marker(flat, h)
        if not marker:
            return did

        state = ctx.navigator.identify()
        ctx.log("search.type_query.system_dialog", marker=marker,
                top=state.top, overlay=state.overlay, attempt=attempt + 1)

        # 1) BACK — na Samsungu/Gboard zamyka chooser bez trafiania w emoji
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
        did = True
        time.sleep(0.3)

        # Po geście musimy wziąć nowy dump; flat jest teraz nieaktualny
        flat = None
        flat2 = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if not _ime_picker_marker(flat2, h):
            return did

        # 2) Picker nadal stoi — tap tylko w prawdziwą opcję (radio / checked)
        if _tap_ime_choice(ctx, flat2):
            time.sleep(0.35)
        # Tap invaliduje cache; kolejna iteracja zrobi nowy dump
        flat = None

    return did


# Alias kompatybilności — stare testy / importy
def _dismiss_system_dialog(ctx) -> bool:
    return _dismiss_ime_picker(ctx)


def _tap_ime_choice(ctx, flat: list[dict]) -> bool:
    """Tapnij opcję w dialogu IME — wyłącznie radio/checked powyżej klawiatury."""
    try:
        screen_h = int(ctx.account.device["_hardware"]["resolution"][1])
    except Exception:
        screen_h = 2340
    # Klawiatura zajmuje dół — dialog choosera jest wyżej
    y_max = int(screen_h * 0.62)

    candidates = []
    for e in flat:
        cls = (e.get("class") or "")
        blob = ((e.get("text") or "") + " " + (e.get("content-desc") or "")).strip()
        if not blob:
            continue
        low = blob.lower()
        if any(h in low for h in IME_PICKER_TEXTS):
            continue
        if any(b in low for b in _IME_TAP_BLOCKLIST):
            continue
        checked = bool(e.get("checked") or e.get("selected"))
        is_radio = "RadioButton" in cls or "CheckedTextView" in cls
        # Bez radio/checked łatwo trafić w pasek klawiatury — odpuść
        if not (checked or is_radio):
            continue
        c = _center(e.get("bounds"))
        if not c or c[1] > y_max:
            continue
        score = (2 if checked else 0) + (1 if is_radio else 0)
        candidates.append((score, c, blob[:40]))

    if not candidates:
        return False
    candidates.sort(key=lambda t: -t[0])
    _, xy, label = candidates[0]
    ctx.log("search.type_query.ime_tap_choice", label=label, xy=list(xy))
    ctx.ht.tap(*xy, pause_kind="tap")
    return True


def _ime_picker_marker(flat: list[dict], screen_h: int = 2340) -> str | None:
    """Wykryj TYTUŁ dialogu IME — nie przycisk globusa na klawiaturze.

    Samsung/Gboard mają w chrome klawiatury ten sam opis
    („Przełączanie metody wprowadzania") — to NIE jest dialog. Dialog siedzi
    wyżej; przycisk globusa na dole. Match w dolnych ~45% ekranu ignorujemy.
    """
    y_cutoff = int(screen_h * 0.55)
    for e in flat:
        blob = ((e.get("text") or "") + " " + (e.get("content-desc") or "")).lower()
        for hint in IME_PICKER_TEXTS:
            if hint not in blob:
                continue
            c = _center(e.get("bounds"))
            # klikalny node nisko = globus klawiatury, nie chooser
            if c and c[1] > y_cutoff:
                continue
            return hint
    return None
