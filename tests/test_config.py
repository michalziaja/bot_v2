"""Testy loadera configu."""
from __future__ import annotations
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.config.schema import (load_account, new_account_from_template,
                              ConfigError, CONFIG_ROOT)


class ConfigTests(unittest.TestCase):

    def test_load_real_account(self):
        cfg = load_account("technature699")
        self.assertEqual(cfg.account_id, "technature699")
        self.assertEqual(cfg.ig_username, "technature699")
        # device merge — powinniśmy mieć dane sprzętowe pod _hardware
        self.assertEqual(cfg.device["device_alias"], "phone_A")
        self.assertIn("_hardware", cfg.device)
        self.assertEqual(cfg.device["_hardware"]["adb_id"], "R58M74B5FRN")

    def test_step_toggle_helpers(self):
        cfg = load_account("technature699")
        self.assertTrue(cfg.step_enabled("stats_snapshot"))
        self.assertTrue(cfg.step_option("stats_snapshot", "on_session_start"))
        self.assertFalse(cfg.step_option("stats_snapshot", "on_session_end"))
        # Nieznany plugin musi być domyślnie WYŁĄCZONY — asercja na konkretnym
        # id z żywego configu była krucha (użytkownik zmienia te przełączniki
        # z panelu, co psuło test).
        self.assertFalse(cfg.plugin_enabled("nie_ma_takiego_pluginu"))

    def test_plugin_enabled_prefers_new_section_over_legacy_toggles(self):
        """`plugins.<id>.enabled` wygrywa nad starym `toggles.plugins.<id>`.

        Stara sekcja zostaje jako fallback dla kont zapisanych przed
        wprowadzeniem katalogu pluginów — patrz v2/plugins/catalog.py.
        """
        cfg = load_account("technature699")
        cfg.toggles = {"plugins": {"x": {"enabled": True}}}
        cfg.plugins = {}
        self.assertTrue(cfg.plugin_enabled("x"))      # fallback na legacy
        cfg.plugins = {"x": {"enabled": False}}
        self.assertFalse(cfg.plugin_enabled("x"))     # nowa lokalizacja wygrywa

    def test_missing_account_file_raises(self):
        with self.assertRaises(ConfigError):
            load_account("does_not_exist_xxx")

    def test_missing_device_ref_raises(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            # skopiuj strukturę configu do tempdir i celowo zepsuj referencję
            (tmp / "accounts").mkdir()
            (tmp / "devices").mkdir()
            src = CONFIG_ROOT / "accounts" / "technature699.json"
            data = json.loads(src.read_text(encoding="utf-8"))
            data["device"]["device_alias"] = "nonexistent_phone"
            (tmp / "accounts" / "bad.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_account("bad", config_root=tmp)
        finally:
            shutil.rmtree(tmp)

    def test_template_roundtrip(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            # skopiuj szablon i device do tmp
            (tmp / "_template").mkdir()
            (tmp / "devices").mkdir()
            (tmp / "accounts").mkdir()
            shutil.copy(CONFIG_ROOT / "_template" / "account.json", tmp / "_template" / "account.json")
            shutil.copy(CONFIG_ROOT / "devices" / "phone_A.json", tmp / "devices" / "phone_A.json")
            path = new_account_from_template("test_alias", "test_ig_user", "phone_A", config_root=tmp)
            self.assertTrue(path.exists())
            cfg = load_account("test_alias", config_root=tmp)
            self.assertEqual(cfg.ig_username, "test_ig_user")
            # nie można dwa razy tego samego aliasu
            with self.assertRaises(ConfigError):
                new_account_from_template("test_alias", "x", "phone_A", config_root=tmp)
        finally:
            shutil.rmtree(tmp)

    def test_template_with_clone_package(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "_template").mkdir()
            (tmp / "devices").mkdir()
            (tmp / "accounts").mkdir()
            shutil.copy(CONFIG_ROOT / "_template" / "account.json", tmp / "_template" / "account.json")
            shutil.copy(CONFIG_ROOT / "devices" / "phone_A.json", tmp / "devices" / "phone_A.json")
            path = new_account_from_template("clone_alias", "clone_user", "phone_A",
                                             app_clone_package="com.instagram.androip",
                                             config_root=tmp)
            cfg = load_account("clone_alias", config_root=tmp)
            self.assertEqual(cfg.device["app_clone_package"], "com.instagram.androip")
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
