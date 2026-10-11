"""Testy odczytu hardware z ADB — bez prawdziwego telefonu."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.core.adb_probe import (
    _parse_wm_density,
    _parse_wm_size,
    apply_probe,
    hardware_gaps,
    hydrate_device_doc,
    list_adb_devices,
    list_ig_packages,
    next_scrcpy_port,
    normalize_model,
    probe_hardware,
)


class ParseTests(unittest.TestCase):
    def test_wm_size_prefers_override(self):
        out = "Physical size: 1080x1920\nOverride size: 720x1280\n"
        self.assertEqual(_parse_wm_size(out), [720, 1280])

    def test_wm_size_physical(self):
        self.assertEqual(_parse_wm_size("Physical size: 1080x1920"), [1080, 1920])

    def test_wm_density_physical(self):
        self.assertEqual(_parse_wm_density("Physical density: 480"), 480)

    def test_normalize_model_hyphen(self):
        self.assertEqual(normalize_model("SM-A520F"), "SM_A520F")


class ProbeHardwareTests(unittest.TestCase):
    def test_probe_from_shell(self):
        answers = {
            "getprop ro.product.model": "SM-A520F",
            "getprop ro.product.device": "a5y17lte",
            "getprop ro.product.manufacturer": "samsung",
            "getprop ro.build.version.release": "8.0.0",
            "wm size": "Physical size: 1080x1920",
            "wm density": "Physical density: 480",
        }
        probed = probe_hardware("SERIAL", shell=lambda cmd: answers[cmd])
        self.assertEqual(probed["model"], "SM_A520F")
        self.assertEqual(probed["resolution"], [1080, 1920])
        self.assertEqual(probed["dpi"], 480)
        self.assertEqual(probed["android_version"], "8.0.0")

    def test_list_ig_packages_skips_installer(self):
        out = "\n".join([
            "package:com.android.certinstaller",
            "package:com.instagram.android",
            "package:com.instagram.androip",
            "package:com.google.android.packageinstaller",
        ])
        pkgs = list_ig_packages("S", shell=lambda cmd: out)
        self.assertEqual(pkgs, ["com.instagram.android", "com.instagram.androip"])


class HydrateTests(unittest.TestCase):
    def test_gaps_on_null_resolution(self):
        doc = {
            "adb_id": "x", "model": "SM_A590F", "resolution": None,
            "uiautodev": {"base_url": "http://127.0.0.1:20242"},
        }
        self.assertIn("resolution", hardware_gaps(doc))

    def test_hydrate_fills_and_corrects_model(self):
        answers = {
            "getprop ro.product.model": "SM-A520F",
            "getprop ro.product.device": "a5y17lte",
            "getprop ro.product.manufacturer": "samsung",
            "getprop ro.build.version.release": "8.0.0",
            "wm size": "Physical size: 1080x1920",
            "wm density": "Physical density: 480",
            "pm list packages": "package:com.instagram.android",
        }
        doc = {
            "alias": "galaxy",
            "adb_id": "52108b3459fe2457",
            "model": "SM-A590F",
            "resolution": None,
            "uiautodev": {"base_url": "http://127.0.0.1:20242"},
            "clones": [{"slot": 1, "package": "com.instagram.android",
                        "note": "oryginalna instalacja"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "devices").mkdir()
            path = root / "devices" / "galaxy.json"
            result = hydrate_device_doc(
                doc, persist_path=path, config_root=root,
                shell=lambda cmd: answers[cmd],
            )
            self.assertTrue(result["hydrated"])
            self.assertEqual(doc["model"], "SM_A520F")
            self.assertEqual(doc["resolution"], [1080, 1920])
            self.assertEqual(doc["scrcpy"]["port"], 27183)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["resolution"], [1080, 1920])

    def test_complete_device_skips_probe(self):
        doc = {
            "adb_id": "x",
            "model": "SM_A505FN",
            "product": "a50eea",
            "manufacturer": "Samsung",
            "android_version": "11",
            "resolution": [1080, 2340],
            "dpi": 420,
            "uiautodev": {"base_url": "http://127.0.0.1:20242"},
        }
        called = []
        result = hydrate_device_doc(
            doc, shell=lambda cmd: called.append(cmd) or "",
        )
        self.assertFalse(result["hydrated"])
        self.assertEqual(called, [])

    def test_scrcpy_port_skips_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "devices"
            d.mkdir()
            (d / "phone_A.json").write_text(json.dumps({
                "scrcpy": {"port": 27183},
            }), encoding="utf-8")
            self.assertEqual(next_scrcpy_port(root), 27184)

    def test_apply_probe_appends_new_clones(self):
        doc = {
            "clones": [{"slot": 1, "package": "com.instagram.android"}],
            "uiautodev": {"base_url": "http://127.0.0.1:20242"},
        }
        apply_probe(
            doc,
            {"model": "X", "resolution": [1, 2], "dpi": 320,
             "product": "p", "manufacturer": "m", "android_version": "8"},
            ig_packages=["com.instagram.android", "com.instagram.androip"],
            force_hardware=True,
        )
        pkgs = [c["package"] for c in doc["clones"]]
        self.assertEqual(pkgs, ["com.instagram.android", "com.instagram.androip"])


class ListDevicesParseTests(unittest.TestCase):
    def test_parse_adb_devices_l(self):
        sample = (
            "List of devices attached\n"
            "52108b3459fe2457       device usb:1-13 product:a5y17ltexx "
            "model:SM_A520F device:a5y17lte transport_id:24\n"
            "R58M74B5FRN            device usb:1-6 product:a50eea "
            "model:SM_A505FN device:a50 transport_id:23\n"
        )
        with patch("v2.core.adb_probe.adb", return_value=sample):
            rows = list_adb_devices()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["serial"], "52108b3459fe2457")
        self.assertEqual(rows[0]["model"], "SM_A520F")
        self.assertEqual(rows[1]["serial"], "R58M74B5FRN")


class LoadAccountHydrateTests(unittest.TestCase):
    def test_load_account_hydrates_incomplete_device(self):
        from v2.config.schema import load_account, new_account_from_template
        import shutil
        answers = {
            "getprop ro.product.model": "SM-A520F",
            "getprop ro.product.device": "a5y17lte",
            "getprop ro.product.manufacturer": "samsung",
            "getprop ro.build.version.release": "8.0.0",
            "wm size": "Physical size: 1080x1920",
            "wm density": "Physical density: 480",
            "pm list packages": "package:com.instagram.android",
        }
        from v2.config.schema import CONFIG_ROOT
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "_template").mkdir()
            (tmp / "devices").mkdir()
            (tmp / "accounts").mkdir()
            shutil.copy(CONFIG_ROOT / "_template" / "account.json",
                        tmp / "_template" / "account.json")
            (tmp / "devices" / "galaxy.json").write_text(json.dumps({
                "alias": "galaxy",
                "adb_id": "52108b3459fe2457",
                "model": "SM-A590F",
                "resolution": None,
                "uiautodev": {"base_url": "http://127.0.0.1:20242"},
            }), encoding="utf-8")
            new_account_from_template("kasia_t", "kasia21sum", "galaxy",
                                      config_root=tmp)
            with patch("v2.core.adb_probe.default_adb_shell",
                       side_effect=lambda serial, cmd: answers[cmd]):
                cfg = load_account("kasia_t", config_root=tmp)
            self.assertEqual(cfg.device["_hardware"]["resolution"], [1080, 1920])
            self.assertEqual(cfg.device["_hardware"]["model"], "SM_A520F")
            saved = json.loads((tmp / "devices" / "galaxy.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["dpi"], 480)
        finally:
            shutil.rmtree(tmp)


class ResolveUiMapTests(unittest.TestCase):
    def test_unknown_model_falls_back_to_a50_map(self):
        from v2.cli import CANONICAL_UI_MAP, _resolve_ui_map
        p = _resolve_ui_map({"model": "SM_A520F"})
        self.assertEqual(p.name, CANONICAL_UI_MAP.name)

    def test_known_model_uses_own_map(self):
        from v2.cli import _resolve_ui_map
        p = _resolve_ui_map({"model": "SM_A505FN"})
        self.assertEqual(p.name, "SM_A505FN_draft.json")

    def test_oneplus_mapa_gdy_jest_plik(self):
        from v2.cli import _resolve_ui_map
        from v2.core.device_calibrate import write_model_map
        from v2.cli import CALIB_ROOT
        dest = CALIB_ROOT / "KB2003_draft.json"
        if not dest.exists():
            write_model_map(
                model="KB2003", resolution=[1080, 2400],
                harvested={}, dest=dest)
        p = _resolve_ui_map({"model": "KB2003"})
        self.assertEqual(p.name, "KB2003_draft.json")
