"""Testy system.enter_verification_code_if_needed — wpisanie kodu z panelu
na ekranie weryfikacji IG (email/SMS)."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core import verification_code as vc
from v2.steps.system.enter_verification_code_if_needed import (
    EnterVerificationCodeIfNeeded,
    _find_code_input,
)


def edit_text(desc, x1=84, y1=1041, x2=901, y2=1098):
    return {"resource-id": "", "class": "android.widget.EditText",
            "content-desc": desc, "text": "", "bounds": f"[{x1},{y1}][{x2},{y2}]"}


def button(desc, x1=42, y1=1336, x2=1038, y2=1452):
    return {"resource-id": "", "class": "android.widget.Button",
            "text": "", "content-desc": desc, "bounds": f"[{x1},{y1}][{x2},{y2}]"}


# Ekran z żywego dumpu (calibration/samples/55_verify_email_code, 2026-08-02).
REAL_CODE_FIELD = edit_text("Wprowadź kod,")
REAL_SUBMIT_BTN = button("Kontynuuj")


class FakeNav:
    def identify(self):
        class S: pass
        s = S(); s.top = None; s.overlay = None
        return s


class FakeDevice:
    def __init__(self, flat):
        self.flat = flat
        self.shells = []

    def hierarchy(self, force=False):
        return self.flat

    def shell(self, cmd):
        self.shells.append(cmd)
        return ""


class FakeCtx:
    def __init__(self, flat, data_root):
        self.account = type("A", (), {"account_id": "test_acc"})()
        self.device = FakeDevice(flat)
        self.navigator = FakeNav()
        self.ht = type("H", (), {"tap": lambda self_, *a, **k: None})()
        self.humanizer = type("Hu", (), {"pause": lambda self_, *a, **k: None})()
        self.data_root = data_root
        self.logs = []

    def log(self, kind, **payload):
        self.logs.append((kind, payload))


class EnterVerificationCodeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        import v2.steps.system.enter_verification_code_if_needed as mod
        import v2.steps.system.login_if_needed as login_mod
        self.mod = mod
        self.login_mod = login_mod
        self._orig_flatten = mod.flatten_hierarchy
        mod.flatten_hierarchy = lambda h: h
        # Statyczny flat testowy dalej ma „Kontynuuj" — nie kręć pętli popupów
        self._orig_confirm = login_mod._confirm_post_login_popups
        login_mod._confirm_post_login_popups = lambda ctx, max_steps=8: 0

    def tearDown(self):
        self._tmp.cleanup()
        self.mod.flatten_hierarchy = self._orig_flatten
        self.login_mod._confirm_post_login_popups = self._orig_confirm

    def test_no_code_screen_is_skipped_without_requesting(self):
        ctx = FakeCtx([], self.data_root)
        res = EnterVerificationCodeIfNeeded().run(ctx, {})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "no_code_screen_detected")
        self.assertIsNone(vc.get_request(self.data_root, "test_acc"))

    def test_code_field_detected_requests_code_from_panel(self):
        ctx = FakeCtx([REAL_CODE_FIELD, REAL_SUBMIT_BTN], self.data_root)

        def submit_soon():
            time.sleep(0.05)
            vc.submit_code(self.data_root, "test_acc", "681145")
        threading.Thread(target=submit_soon).start()

        res = EnterVerificationCodeIfNeeded().run(ctx, {"timeout_s": 3})
        self.assertEqual(res.status, "ok")
        self.assertTrue(any("681145" in s for s in ctx.device.shells))
        # request skasowany po odebraniu kodu
        self.assertIsNone(vc.get_request(self.data_root, "test_acc"))

    def test_timeout_without_code_is_skipped_not_guessed(self):
        ctx = FakeCtx([REAL_CODE_FIELD, REAL_SUBMIT_BTN], self.data_root)
        res = EnterVerificationCodeIfNeeded().run(ctx, {"timeout_s": 0.05})
        self.assertEqual(res.status, "skipped")
        self.assertEqual(res.payload["reason"], "code_not_provided_in_time")
        self.assertEqual(ctx.device.shells, [])   # nic nie wpisano

    def test_otp_kratki_bez_hintu_sa_wpisywane(self):
        # Komentarz: live klon2 — 6 kratek bez „Wprowadź kod” na EditText
        boxes = [
            edit_text("", x1=80 + i * 140, y1=700, x2=200 + i * 140, y2=820)
            for i in range(6)
        ]
        title = {"resource-id": "", "class": "android.view.View",
                 "text": "Wprowadź kod potwierdzający",
                 "content-desc": "Wprowadź kod potwierdzający",
                 "bounds": "[48,200][1032,300]"}
        first = _find_code_input(boxes + [title])
        self.assertIsNotNone(first)
        self.assertEqual(first["bounds"], boxes[0]["bounds"])
        ctx = FakeCtx(boxes + [title, REAL_SUBMIT_BTN], self.data_root)
        vc.submit_code(self.data_root, "test_acc", "667721")
        res = EnterVerificationCodeIfNeeded().run(ctx, {"timeout_s": 3})
        self.assertEqual(res.status, "ok", res.error)
        self.assertTrue(any("667721" in s for s in ctx.device.shells))

    def test_missing_submit_button_is_reported_not_guessed(self):
        ctx = FakeCtx([REAL_CODE_FIELD], self.data_root)
        vc.submit_code(self.data_root, "test_acc", "681145")
        res = EnterVerificationCodeIfNeeded().run(ctx, {"timeout_s": 3})
        self.assertEqual(res.status, "failed")

    def test_prompt_sent_to_panel_includes_screen_text(self):
        flat = [edit_text("nagłówek", 0, 0, 1, 1),
                {"resource-id": "", "class": "android.view.View",
                 "text": "Sprawdź pocztę elektroniczną — wpisz kod wysłany na adres testowy@example.com",
                 "content-desc": "", "bounds": "[0,0][1,1]"},
                REAL_CODE_FIELD, REAL_SUBMIT_BTN]
        ctx = FakeCtx(flat, self.data_root)
        vc.submit_code(self.data_root, "test_acc", "111111")
        EnterVerificationCodeIfNeeded().run(ctx, {"timeout_s": 3})
        waiting_logs = [p for k, p in ctx.logs if "waiting" in k]
        self.assertTrue(waiting_logs)
        self.assertIn("testowy@example.com", waiting_logs[0]["prompt"])


class RealVerifyEmailScreenSampleTests(unittest.TestCase):
    """Prawdziwy dump z `calibration/samples/55_verify_email_code` (klon
    com.instagram.androjb, checkpoint weryfikacji e-mail, 2026-08-02)."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        import v2.steps.system.enter_verification_code_if_needed as mod
        import v2.steps.system.login_if_needed as login_mod
        self.mod = mod
        self.login_mod = login_mod
        self._orig_flatten = mod.flatten_hierarchy
        mod.flatten_hierarchy = lambda h: h
        self._orig_confirm = login_mod._confirm_post_login_popups
        login_mod._confirm_post_login_popups = lambda ctx, max_steps=8: 0

    def tearDown(self):
        self._tmp.cleanup()
        self.mod.flatten_hierarchy = self._orig_flatten
        self.login_mod._confirm_post_login_popups = self._orig_confirm

    def test_real_sample_is_recognized_and_filled(self):
        import json
        p = ROOT / "calibration" / "samples" / "55_verify_email_code.flat.json"
        flat = json.loads(p.read_text(encoding="utf-8"))
        ctx = FakeCtx(flat, self.data_root)
        vc.submit_code(self.data_root, "test_acc", "681145")
        res = EnterVerificationCodeIfNeeded().run(ctx, {"timeout_s": 3})
        self.assertEqual(res.status, "ok", res.error)
        self.assertTrue(any("681145" in s for s in ctx.device.shells))


if __name__ == "__main__":
    unittest.main()
