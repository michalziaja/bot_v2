"""Testy system.signup_if_needed — start rejestracji z ekranu logowania."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.config import credentials
from datetime import date

from v2.steps.system.signup_if_needed import (
    SignupIfNeeded,
    _age_years,
    _code_screen,
    _find_next,
    _looks_like_age_screen,
    _looks_like_date_picker,
    _parse_birthday,
)
from v2.tests.test_login_if_needed import (
    FakeCtx,
    REAL_LOGIN_BTN,
    REAL_PASS_FIELD,
    REAL_USER_FIELD,
    button,
    edit_text,
)


def _profile():
    credentials.set("test_acc", "nowa_ania", "Haslo#1", signup={
        "full_name": "Anna Nowak",
        "birthday": "1998-04-12",
        "contact": "ania@example.com",
    })


class SignupIfNeededTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._orig_path = credentials.CREDENTIALS_PATH
        credentials.CREDENTIALS_PATH = Path(self._tmp.name) / "credentials.json"
        self._sleep = patch("v2.steps.system.signup_if_needed.time.sleep")
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        credentials.CREDENTIALS_PATH = self._orig_path
        self._tmp.cleanup()

    def test_skip_bez_profilu(self):
        ctx = FakeCtx([[]])
        res = SignupIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "no_signup_configured")

    def test_tap_utworz_konto_potem_kod(self):
        _profile()
        landing = [REAL_USER_FIELD, REAL_PASS_FIELD, REAL_LOGIN_BTN,
                   button("Utwórz nowe konto", x1=42, y1=1500, x2=1038, y2=1600)]
        contact = [
            edit_text("Adres e-mail", 84, 400, 901, 460),
            button("Dalej", x1=42, y1=800, x2=1038, y2=900),
        ]
        code = [
            edit_text("Wprowadź kod", 84, 400, 901, 460),
            button("Kontynuuj", x1=42, y1=800, x2=1038, y2=900),
        ]
        frames = [copy.deepcopy(landing) for _ in range(2)]
        frames += [copy.deepcopy(contact) for _ in range(4)]
        frames += [copy.deepcopy(code) for _ in range(4)]
        ctx = FakeCtx(frames)
        res = SignupIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.payload.get("opened"))
        self.assertIn("contact", res.payload.get("filled") or [])
        self.assertTrue(res.payload.get("waiting_for_code"))

    def test_wiek_z_urodzin_przed_i_po_rocznicy(self):
        # Komentarz: 2000-10-09 przy 2026-09-10 → 25 (rocznica jeszcze nie minęła)
        self.assertEqual(
            _age_years("2000-10-09", today=date(2026, 9, 10)), 25)
        self.assertEqual(
            _age_years("2000-10-09", today=date(2026, 10, 9)), 26)

    def test_ekran_wieku_nie_jest_datepickerem(self):
        age_flat = [
            {"class": "android.view.View", "text": "Ile masz lat?",
             "content-desc": "Ile masz lat?", "clickable": False,
             "bounds": "[48,259][1032,376]"},
            edit_text("Wiek", 96, 501, 876, 560),
            button("Dalej", x1=48, y1=657, x2=1032, y2=789),
        ]
        self.assertTrue(_looks_like_age_screen(age_flat))
        self.assertFalse(_looks_like_date_picker(age_flat))

    def test_parser_urodzin_i_datepicker(self):
        self.assertEqual(_parse_birthday("2000-09-09"), (2000, 9, 9))
        self.assertIsNone(_parse_birthday("09.09.2000"))
        picker = [
            {"class": "android.widget.TextView", "text": "Ustaw datę",
             "content-desc": "", "clickable": False, "bounds": "[1,1][2,2]"},
            {"class": "android.widget.DatePicker", "text": "",
             "content-desc": "", "clickable": False, "bounds": "[1,1][2,2]"},
        ]
        self.assertTrue(_looks_like_date_picker(picker))

    def test_ekran_kodu_po_blobie_gdy_sheet_zaslania_pole(self):
        # Komentarz: modal „Potrzebujesz pomocy?” — bez EditText, ale to nadal kod
        help_flat = [
            button("Zamknij", x1=36, y1=806, x2=108, y2=938),
            {"class": "android.view.View", "text": "Potrzebujesz pomocy?",
             "content-desc": "Potrzebujesz pomocy?", "clickable": False,
             "bounds": "[48,950][1032,1055]"},
            {"class": "android.view.View",
             "text": "Wprowadź kod potwierdzający",
             "content-desc": "Wprowadź kod potwierdzający",
             "clickable": False, "bounds": "[48,200][1032,300]"},
        ]
        self.assertTrue(_code_screen(help_flat))

    def test_dalej_nie_myli_z_emailem(self):
        # Komentarz: live OnePlus — szary CTA e-mail nie jest przyciskiem Dalej
        dalej = button("Dalej", x1=42, y1=800, x2=1038, y2=900)
        email = button(
            "Zarejestruj się przy użyciu adresu e-mail",
            x1=42, y1=950, x2=1038, y2=1050)
        dalej["clickable"] = True
        email["clickable"] = True
        nxt = _find_next([email, dalej])
        self.assertIsNotNone(nxt)
        lab = ((nxt.get("text") or "") + " " + (nxt.get("content-desc") or "")).lower()
        self.assertIn("dalej", lab)
        self.assertNotIn("e-mail", lab)

    def test_brak_przycisku_nowe_konto(self):
        _profile()
        landing = [REAL_USER_FIELD, REAL_PASS_FIELD, REAL_LOGIN_BTN]
        ctx = FakeCtx([copy.deepcopy(landing) for _ in range(6)])
        res = SignupIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.error, "create_account_not_found")


if __name__ == "__main__":
    unittest.main()
