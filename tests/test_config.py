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

from v2.config.schema import (load_account, load_bot, save_bot,
                              new_account_from_template, ConfigError, CONFIG_ROOT)


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

    def _fleet_tmp(self):
        """Tmp config: kompletny device (bez ADB) + szkielet konta."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "accounts").mkdir()
        (tmp / "devices").mkdir()
        shutil.copy(CONFIG_ROOT / "devices" / "phone_A.json",
                    tmp / "devices" / "phone_A.json")
        return tmp

    def _write_account(self, tmp: Path, alias: str, **sections):
        data = {
            "meta": {"account_id": alias, "ig_username": alias},
            "device": {"device_alias": "phone_A"},
        }
        data.update(sections)
        (tmp / "accounts" / f"{alias}.json").write_text(
            json.dumps(data), encoding="utf-8")

    def test_limits_merge_bot_100_konto_50(self):
        """bot.json daily_likes=100, konto 50 → effective 50."""
        tmp = self._fleet_tmp()
        try:
            (tmp / "bot.json").write_text(json.dumps({
                "limits": {"daily_likes": 100, "daily_follows": 30},
                "humanizer": {"profile_name": "default", "speed": 1.0},
            }), encoding="utf-8")
            self._write_account(tmp, "ovr", limits={"daily_likes": 50})
            cfg = load_account("ovr", config_root=tmp)
            self.assertEqual(cfg.limits["daily_likes"], 50)
            self.assertEqual(cfg.limits["daily_follows"], 30)
        finally:
            shutil.rmtree(tmp)

    def test_limits_dziedzicza_z_bota_gdy_brak_klucza(self):
        tmp = self._fleet_tmp()
        try:
            (tmp / "bot.json").write_text(json.dumps({
                "limits": {"daily_likes": 100},
                "humanizer": {"profile_name": "default"},
            }), encoding="utf-8")
            self._write_account(tmp, "inh", limits={})
            cfg = load_account("inh", config_root=tmp)
            self.assertEqual(cfg.limits["daily_likes"], 100)
        finally:
            shutil.rmtree(tmp)

    def test_zero_na_koncie_to_brak_limitu_nie_global(self):
        tmp = self._fleet_tmp()
        try:
            (tmp / "bot.json").write_text(json.dumps({
                "limits": {"daily_likes": 100},
                "humanizer": {"profile_name": "default"},
            }), encoding="utf-8")
            self._write_account(tmp, "zero", limits={"daily_likes": 0})
            cfg = load_account("zero", config_root=tmp)
            self.assertEqual(cfg.limits["daily_likes"], 0)
        finally:
            shutil.rmtree(tmp)

    def test_humanizer_speed_override(self):
        tmp = self._fleet_tmp()
        try:
            (tmp / "bot.json").write_text(json.dumps({
                "humanizer": {"profile_name": "default", "speed": 1.0},
            }), encoding="utf-8")
            self._write_account(tmp, "spd", humanizer={"speed": 0.4})
            cfg = load_account("spd", config_root=tmp)
            self.assertEqual(cfg.humanizer["speed"], 0.4)
            self.assertEqual(cfg.humanizer["profile_name"], "default")
        finally:
            shutil.rmtree(tmp)

    def test_load_bot_czyta_plik(self):
        tmp = self._fleet_tmp()
        try:
            (tmp / "bot.json").write_text(json.dumps({
                "limits": {"daily_likes": 80},
            }), encoding="utf-8")
            bot = load_bot(tmp)
            self.assertEqual(bot["limits"]["daily_likes"], 80)
        finally:
            shutil.rmtree(tmp)

    def test_save_bot_80_dziedziczy_override_zostaje(self):
        """POST-kształt: global 80 → konto bez klucza 80, konto z 50 zostaje 50."""
        tmp = self._fleet_tmp()
        try:
            (tmp / "bot.json").write_text(json.dumps({
                "limits": {"daily_likes": 100, "daily_follows": 30},
                "humanizer": {"profile_name": "default"},
            }), encoding="utf-8")
            self._write_account(tmp, "inh", limits={})
            self._write_account(tmp, "ovr", limits={"daily_likes": 50})
            saved = save_bot({"limits": {"daily_likes": 80}}, tmp)
            self.assertEqual(saved["limits"]["daily_likes"], 80)
            self.assertEqual(load_account("inh", config_root=tmp).limits["daily_likes"], 80)
            self.assertEqual(load_account("ovr", config_root=tmp).limits["daily_likes"], 50)
        finally:
            shutil.rmtree(tmp)

    def test_save_bot_odrzuca_ujemne(self):
        tmp = self._fleet_tmp()
        try:
            with self.assertRaises(ConfigError):
                save_bot({"limits": {"daily_likes": -1}}, tmp)
        finally:
            shutil.rmtree(tmp)

    def test_template_copies_clone_identity_spoof_fields(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "_template").mkdir()
            (tmp / "devices").mkdir()
            (tmp / "accounts").mkdir()
            shutil.copy(CONFIG_ROOT / "_template" / "account.json", tmp / "_template" / "account.json")
            dev = json.loads((CONFIG_ROOT / "devices" / "phone_A.json").read_text(encoding="utf-8"))
            dev["clones"] = [{
                "slot": 2, "package": "com.instagram.androia",
                "identity": {"mac": "02:11:22:33:44:55", "imei": "490154203237518",
                             "android_id": "0123456789abcdef"},
            }]
            (tmp / "devices" / "phone_A.json").write_text(json.dumps(dev), encoding="utf-8")
            new_account_from_template("spoof_alias", "user", "phone_A",
                                      app_clone_package="com.instagram.androia",
                                      config_root=tmp)
            cfg = load_account("spoof_alias", config_root=tmp)
            self.assertEqual(cfg.device["spoofed_mac"], "02:11:22:33:44:55")
            self.assertEqual(cfg.device["spoofed_imei"], "490154203237518")
            self.assertEqual(cfg.device["spoofed_android_id"], "0123456789abcdef")
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
