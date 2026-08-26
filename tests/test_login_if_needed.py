"""Testy system.login_if_needed — wpisanie danych logowania na ekranie IG.

Ekran login_screen nie ma jeszcze fingerprintu w ScreenMap (brak kalibracji
ScreenMap — jest za to potwierdzony dump w calibration/samples/50_login_screen),
więc krok rozpoznaje pola po (klasa + fragment content-desc), bo POTWIERDZONO
na żywym dumpie (klon com.instagram.androip, 2026-08-02), że w tej wersji IG
pola logowania NIE MAJĄ resource-id — cała treść siedzi w content-desc.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.config import credentials
from v2.steps.system.login_if_needed import LoginIfNeeded, _confirm_post_login_popups


def edit_text(desc, x1=84, y1=1037, x2=901, y2=1094, rid=""):
    return {"resource-id": rid, "class": "android.widget.EditText",
            "content-desc": desc, "text": "", "bounds": f"[{x1},{y1}][{x2},{y2}]"}


def button(desc="", rid="", x1=42, y1=1344, x2=1038, y2=1460):
    return {"resource-id": rid, "class": "android.widget.Button",
            "text": "", "content-desc": desc, "bounds": f"[{x1},{y1}][{x2},{y2}]"}


# Pola z prawdziwego dumpu (calibration/samples/50_login_screen), 2026-08-02.
REAL_USER_FIELD = edit_text("Nazwa użytkownika, adres e-mail lub numer telefonu "
                            "komórkowego,", 84, 1037, 901, 1094)
REAL_PASS_FIELD = edit_text("Hasło,", 84, 1228, 901, 1285)
REAL_LOGIN_BTN = button("Zaloguj się", x1=42, y1=1344, x2=1038, y2=1460)


class FakeNav:
    def __init__(self, top="login_screen"):
        self.top = top
    def identify(self):
        class S: pass
        s = S(); s.top = self.top; s.overlay = None
        return s


class FakeDevice:
    def __init__(self, frames):
        self.frames = list(frames)
        self.shells = []

    def hierarchy(self, force=False):
        return self.frames[0] if self.frames else []

    def shell(self, cmd):
        self.shells.append(cmd)
        return ""


class FakeCtx:
    def __init__(self, frames, top="login_screen"):
        self.account = type("A", (), {"account_id": "test_acc"})()
        self.device = FakeDevice(frames)
        self.navigator = FakeNav(top)
        self.ht = type("H", (), {"tap": lambda self_, *a, **k: None})()
        self.humanizer = type("Hu", (), {"pause": lambda self_, *a, **k: None})()
        self.logs = []

    def log(self, kind, **payload):
        self.logs.append((kind, payload))


class LoginIfNeededTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._orig_path = credentials.CREDENTIALS_PATH
        credentials.CREDENTIALS_PATH = Path(self._tmp.name) / "credentials.json"
        import v2.steps.system.login_if_needed as mod
        self.mod = mod
        self._orig_flatten = mod.flatten_hierarchy
        mod.flatten_hierarchy = lambda h: h   # ctx.device.hierarchy zwraca już flat listę
        self._sleep_patcher = patch("v2.steps.system.login_if_needed.time.sleep")
        self._sleep_patcher.start()

    def tearDown(self):
        credentials.CREDENTIALS_PATH = self._orig_path
        self._tmp.cleanup()
        self.mod.flatten_hierarchy = self._orig_flatten
        self._sleep_patcher.stop()

    def test_no_credentials_configured_is_skipped(self):
        ctx = FakeCtx(frames=[[]])
        res = LoginIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "no_credentials_configured")

    def test_fields_not_found_is_skipped_not_guessed(self):
        credentials.set("test_acc", "jan", "sekret123")
        ctx = FakeCtx(frames=[[]])   # brak pól loginu na ekranie
        res = LoginIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "login_screen_not_detected")
        self.assertEqual(ctx.device.shells, [])   # nic nie wpisano

    def test_real_content_desc_fields_are_recognized(self):
        """Regresja na dokładnym kształcie danych z żywego dumpu — bez
        resource-id, tylko content-desc. Zanim to poprawiono, krok zawsze
        kończył się `login_screen_not_detected`."""
        credentials.set("test_acc", "jan_kowalski", "sekret123")
        flat = [REAL_USER_FIELD, REAL_PASS_FIELD, REAL_LOGIN_BTN]
        ctx = FakeCtx(frames=[flat])
        res = LoginIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.payload["submitted"])
        self.assertTrue(any("jan_kowalski" in s for s in ctx.device.shells))
        self.assertTrue(any("sekret123" in s for s in ctx.device.shells))

    def test_resource_id_still_works_if_present(self):
        """Gdyby inna wersja IG jednak miała resource-id, krok ma go użyć
        zamiast content-desc — ID sprawdzane jest pierwsze."""
        credentials.set("test_acc", "jan", "sekret123")
        flat = [edit_text("cokolwiek", rid="com.instagram.android:id/login_username",
                          y1=100, y2=160),
                edit_text("cokolwiek", rid="com.instagram.android:id/login_password",
                          y1=200, y2=260),
                button(rid="com.instagram.android:id/log_in_button", y1=900, y2=960)]
        ctx = FakeCtx(frames=[flat])
        res = LoginIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "ok")

    def test_missing_login_button_is_reported_not_guessed(self):
        credentials.set("test_acc", "jan", "sekret123")
        flat = [REAL_USER_FIELD, REAL_PASS_FIELD]
        ctx = FakeCtx(frames=[flat])
        res = LoginIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "failed")

    def test_english_hint_is_also_recognized(self):
        """UWAGA: EN nieprzetestowane na żywo (telefon dziś w PL) — pilnujemy
        tylko, że nie stracimy tej ścieżki przy refaktorze."""
        credentials.set("test_acc", "jan", "sekret123")
        flat = [edit_text("Username, email or mobile number", 84, 1037, 901, 1094),
                edit_text("Password", 84, 1228, 901, 1285),
                button("Log in", x1=42, y1=1344, x2=1038, y2=1460)]
        ctx = FakeCtx(frames=[flat])
        res = LoginIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "ok")


def perm_node(text, x1, y1, x2, y2, cls="android.widget.Button", pkg="com.instagram.androip"):
    return {"resource-id": "", "class": cls, "text": text, "content-desc": "",
            "package": pkg, "bounds": f"[{x1},{y1}][{x2},{y2}]"}


class ScriptedCtx:
    """Ctx do testowania `_confirm_post_login_popups`: `seq` to lista
    (top, flat) kolejnych ekranów; `ht.tap()` przesuwa na następny — bo stan
    telefonu zmienia się w WYNIKU tapnięcia, nie samoistnie."""

    def __init__(self, seq):
        self.account = type("A", (), {"account_id": "test_acc"})()
        self.seq = list(seq)
        self.i = 0
        self.taps = []
        self.logs = []
        self.humanizer = type("Hu", (), {"pause": lambda self_, *a, **k: None})()

        outer = self

        class Dev:
            def hierarchy(self_, force=False):
                return outer.seq[outer.i][1]
        self.device = Dev()

        class Nav:
            def identify(self_):
                class S: pass
                s = S(); s.top = outer.seq[outer.i][0]; s.overlay = None
                return s
        self.navigator = Nav()

        class Ht:
            def tap(self_, *a, **k):
                outer.taps.append(a)
                if outer.i < len(outer.seq) - 1:
                    outer.i += 1
        self.ht = Ht()

    def log(self, kind, **payload):
        self.logs.append((kind, payload))


# Natywny dialog uprawnień lokalizacji — dokładny tekst z żywego dumpu
# (calibration/samples/53_native_permission_location, 2026-08-02), włącznie
# z niełamliwą spacją w "Podczas używania\xa0aplikacji".
NATIVE_LOCATION_DIALOG = [
    perm_node("Zezwolić aplikacji Insta0012 na dostęp do lokalizacji urządzenia?",
              54, 1400, 1025, 1500, cls="android.widget.TextView",
              pkg="com.google.android.permissioncontroller"),
    perm_node("Podczas używania\xa0aplikacji", 64, 1876, 1016, 1971,
              pkg="com.google.android.permissioncontroller"),
    perm_node("Tylko tym razem", 64, 2013, 1016, 2108,
              pkg="com.google.android.permissioncontroller"),
    perm_node("Nie zezwalaj", 64, 2150, 1016, 2245,
              pkg="com.google.android.permissioncontroller"),
]

IG_LOCATION_INTRO = [
    perm_node("Aby korzystać z Usług lokalizacji, zezwól Instagramowi na dostęp",
              0, 100, 900, 700, cls="android.widget.TextView"),
    perm_node("Kontynuuj", 42, 2081, 1038, 2191),
]

# Intro IG o kontaktach — Pomiń, NIE Kontynuuj (nawet gdy oba widoczne)
IG_CONTACTS_INTRO = [
    perm_node("Znajdź znajomych — zezwól na dostęp do kontaktów",
              0, 100, 900, 700, cls="android.widget.TextView"),
    perm_node("Kontynuuj", 42, 1900, 1038, 2010),
    perm_node("Pomiń", 42, 2081, 1038, 2191),
]

# Natywny dialog kontaktów — jak lokalizacja: „Nie zezwalaj"
NATIVE_CONTACTS_DIALOG = [
    perm_node("Zezwolić aplikacji Insta0012 na dostęp do kontaktów?",
              54, 1400, 1025, 1500, cls="android.widget.TextView",
              pkg="com.google.android.permissioncontroller"),
    perm_node("Zezwól", 64, 1876, 1016, 1971,
              pkg="com.google.android.permissioncontroller"),
    perm_node("Nie zezwalaj", 64, 2150, 1016, 2245,
              pkg="com.google.android.permissioncontroller"),
]

SAVE_LOGIN_PROMPT = [
    perm_node("Zapisać dane logowania?", 0, 200, 900, 300, cls="android.widget.TextView"),
    perm_node("Zapisz", 63, 1749, 856, 1848),
    perm_node("Nie teraz", 63, 1878, 856, 1949),
]


class ConfirmPostLoginPopupsTests(unittest.TestCase):
    def setUp(self):
        import v2.steps.system.login_if_needed as mod
        self.mod = mod
        self._orig_flatten = mod.flatten_hierarchy
        mod.flatten_hierarchy = lambda h: h   # ctx.device.hierarchy zwraca już flat listę
        self._sleep_patcher = patch("v2.steps.system.login_if_needed.time.sleep")
        self._sleep_patcher.start()

    def tearDown(self):
        self.mod.flatten_hierarchy = self._orig_flatten
        self._sleep_patcher.stop()

    def test_stops_at_home_screen_without_tapping(self):
        ctx = ScriptedCtx([("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 0)
        self.assertEqual(ctx.taps, [])

    def test_home_feed_with_continue_still_taps(self):
        """Live clone3 2026-08-12: „Skonfiguruj na nowym telefonie" bywa
        top=home_feed — musi i tak kliknąć Kontynuuj, nie wyjść od razu."""
        screen = [
            perm_node("Skonfiguruj Instagram na nowym telefonie",
                      0, 200, 900, 400, cls="android.widget.TextView"),
            perm_node("Kontynuuj", 42, 2081, 1038, 2191,
                      cls="android.widget.TextView"),
        ]
        ctx = ScriptedCtx([("home_feed", screen), ("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 1)
        self.assertEqual(ctx.taps[0][:2], (540, 2136))

    def test_save_login_prompt_taps_save_not_not_now(self):
        ctx = ScriptedCtx([(None, SAVE_LOGIN_PROMPT), ("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 1)
        # środek przycisku "Zapisz" (63+856)/2=459, (1749+1848)/2=1798
        self.assertEqual(ctx.taps[0][:2], (459, 1798))

    def test_save_login_textview_without_button_class(self):
        """Live IG: „Zapisz" często siedzi na TextView, nie na Button —
        stary match po BUTTON_CLASSES dawał popups_confirmed=0."""
        screen = [
            perm_node("Zapisać dane logowania?", 0, 200, 900, 300,
                      cls="android.widget.TextView"),
            perm_node("Zapisz", 63, 1749, 856, 1848,
                      cls="android.widget.TextView"),
            perm_node("Nie teraz", 63, 1878, 856, 1949,
                      cls="android.widget.TextView"),
        ]
        ctx = ScriptedCtx([(None, screen), ("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 1)
        self.assertEqual(ctx.taps[0][:2], (459, 1798))

    def test_save_login_by_resource_id(self):
        screen = [
            {"resource-id": "com.instagram.android:id/save_login_prompt",
             "class": "android.widget.Button", "text": "", "content-desc": "",
             "package": "com.instagram.androia",
             "bounds": "[100,1700][900,1800]"},
            {"resource-id": "com.instagram.android:id/not_now_text",
             "class": "android.widget.Button", "text": "Nie teraz",
             "content-desc": "", "package": "com.instagram.androia",
             "bounds": "[100,1850][900,1950]"},
        ]
        ctx = ScriptedCtx([(None, screen), ("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 1)
        self.assertEqual(ctx.taps[0][:2], (500, 1750))

    def test_ig_feature_intro_taps_continue(self):
        """Ekran wprowadzający samego IG (mówi o lokalizacji, ale niczego
        jeszcze nie nadaje) idzie przez zwykłe CONFIRM_HINTS."""
        ctx = ScriptedCtx([(None, IG_LOCATION_INTRO), ("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 1)
        self.assertEqual(ctx.taps[0][:2], (540, 2136))   # środek "Kontynuuj"

    def test_native_location_permission_dialog_is_denied(self):
        """Kluczowy przypadek ustalony z użytkownikiem: lokalizacji NIE
        udostępniamy — musi tapnąć „Nie zezwalaj", nie „Podczas używania…"."""
        ctx = ScriptedCtx([(None, NATIVE_LOCATION_DIALOG), ("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 1)
        # środek "Nie zezwalaj": (64+1016)/2=540, (2150+2245)/2=2197
        self.assertEqual(ctx.taps[0][:2], (540, 2197))

    def test_full_chain_save_login_then_intro_then_deny_location(self):
        ctx = ScriptedCtx([
            (None, SAVE_LOGIN_PROMPT),
            (None, IG_LOCATION_INTRO),
            (None, NATIVE_LOCATION_DIALOG),
            ("home_feed", []),
        ])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 3)
        self.assertEqual(ctx.taps[0][:2], (459, 1798))   # Zapisz
        self.assertEqual(ctx.taps[1][:2], (540, 2136))   # Kontynuuj
        self.assertEqual(ctx.taps[2][:2], (540, 2197))   # Nie zezwalaj

    def test_unknown_screen_stops_without_guessing(self):
        mystery = [perm_node("Coś zupełnie nieznanego", 0, 0, 900, 200,
                             cls="android.widget.TextView")]
        ctx = ScriptedCtx([(None, mystery)])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 0)
        self.assertEqual(ctx.taps, [])

    def test_max_steps_caps_the_loop(self):
        """Gdyby ta sama fraza pojawiała się w kółko (błąd w dopasowaniu),
        pętla ma twardy limit, a nie kręci się w nieskończoność. Sekwencja
        z JEDNYM ekranem nigdy nie awansuje (`i` zostaje na 0), więc symuluje
        dokładnie taką pętlę."""
        loop_screen = [perm_node("Kontynuuj", 42, 2081, 1038, 2191)]
        ctx = ScriptedCtx([(None, loop_screen)])
        n = _confirm_post_login_popups(ctx, max_steps=5)
        self.assertEqual(n, 5)

    def test_contacts_intro_taps_skip_not_continue(self):
        """Ekran kontaktów IG: Pomiń wygrywa z Kontynuuj (nie nadajemy kontaktów)."""
        ctx = ScriptedCtx([(None, IG_CONTACTS_INTRO), ("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 1)
        self.assertEqual(ctx.taps[0][:2], (540, 2136))  # środek „Pomiń"
        self.assertEqual(ctx.logs[0][1].get("action"), "skip")
        self.assertIn("pomiń", (ctx.logs[0][1].get("label") or "").lower())

    def test_contacts_intro_nie_teraz_when_no_pomin(self):
        """Bez „Pomiń" — „Nie teraz" też OK; nadal nie Kontynuuj."""
        screen = [
            perm_node("Zsynchronizuj kontakty, żeby znaleźć znajomych",
                      0, 100, 900, 700, cls="android.widget.TextView"),
            perm_node("Kontynuuj", 42, 1900, 1038, 2010),
            perm_node("Nie teraz", 42, 2081, 1038, 2191),
        ]
        ctx = ScriptedCtx([(None, screen), ("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 1)
        self.assertEqual(ctx.taps[0][:2], (540, 2136))  # Nie teraz
        self.assertEqual(ctx.logs[0][1].get("action"), "skip")

    def test_native_contacts_permission_is_denied(self):
        """Natywny dialog kontaktów — jak lokalizacja: Nie zezwalaj."""
        ctx = ScriptedCtx([(None, NATIVE_CONTACTS_DIALOG), ("home_feed", [])])
        n = _confirm_post_login_popups(ctx)
        self.assertEqual(n, 1)
        self.assertEqual(ctx.taps[0][:2], (540, 2197))  # Nie zezwalaj
        self.assertEqual(ctx.logs[0][1].get("action"), "deny")


class RealLoginScreenSampleTests(unittest.TestCase):
    """Prawdziwy dump z `calibration/samples/50_login_screen` (klon
    com.instagram.androip, niezalogowany, 2026-08-02) — pilnuje, że krok
    faktycznie działa na danych z telefonu, nie tylko na ręcznie sklejonych."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._orig_path = credentials.CREDENTIALS_PATH
        credentials.CREDENTIALS_PATH = Path(self._tmp.name) / "credentials.json"
        credentials.set("test_acc", "jan_kowalski", "sekret123")
        import v2.steps.system.login_if_needed as mod
        self.mod = mod
        self._orig_flatten = mod.flatten_hierarchy
        mod.flatten_hierarchy = lambda h: h
        self._sleep_patcher = patch("v2.steps.system.login_if_needed.time.sleep")
        self._sleep_patcher.start()

    def tearDown(self):
        credentials.CREDENTIALS_PATH = self._orig_path
        self._tmp.cleanup()
        self.mod.flatten_hierarchy = self._orig_flatten
        self._sleep_patcher.stop()

    def test_real_sample_is_recognized_and_filled(self):
        import json
        p = ROOT / "calibration" / "samples" / "50_login_screen.flat.json"
        flat = json.loads(p.read_text(encoding="utf-8"))
        ctx = FakeCtx(frames=[flat])
        res = LoginIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "ok", res.error)
        self.assertTrue(any("jan_kowalski" in s for s in ctx.device.shells))
        self.assertTrue(any("sekret123" in s for s in ctx.device.shells))


if __name__ == "__main__":
    unittest.main()
