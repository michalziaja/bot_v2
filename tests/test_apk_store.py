"""Lokalny magazyn APK — activate bez sieci."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.cloner.apk_store import (  # noqa: E402
    ApkStoreError, activate, active_apk, apks_dir, list_local,
    parse_dumpsys_version, parse_pm_paths, pull_from_device,
    rank_remote_versions, read_installed_version,
    resolve_stored_apk,
)


class ApkStoreLocalTests(unittest.TestCase):
    def test_activate_copies_to_instagram_apk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = apks_dir(root) / "instagram-400.0.0.apk"
            src.write_bytes(b"PK" + b"\x00" * 100)
            rec = activate(root, src)
            dst = active_apk(root)
            self.assertTrue(dst.is_file())
            self.assertEqual(dst.read_bytes(), src.read_bytes())
            self.assertEqual(rec["size"], src.stat().st_size)
            rows = list_local(root)
            self.assertTrue(any(r.get("active") for r in rows))

    def test_resolve_filename_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = apks_dir(root) / "instagram-1.apk"
            src.write_bytes(b"apk")
            got = resolve_stored_apk(root, filename="instagram-1.apk")
            self.assertEqual(got, src)
            outside = Path(td).parent / "nope.apk"
            outside.write_bytes(b"x")
            with self.assertRaises(ApkStoreError):
                resolve_stored_apk(root, path=str(outside))


def _row(ver: str, code, app_id: int) -> dict:
    """Fałszywy wiersz Aptoide — test bez sieci."""
    return {
        "app_id": app_id,
        "version": ver,
        "version_code": code,
        "local": False,
        "source": "aptoide",
    }


class RankRemoteVersionsTests(unittest.TestCase):
    """Ranking względem stock IG — wyłącznie fake wiersze, zero Aptoide."""

    def test_exact_first_then_nearest_same_major_minor(self):
        stock = "444.0.0.46.85"
        stock_code = 1000
        rows = [
            _row("390.0.0.1.1", 100, 1),
            _row("444.0.0.50.0", 1100, 2),
            _row("444.0.0.46.85", 1000, 3),
            _row("444.0.0.46.80", 990, 4),
            _row("445.0.0.1.1", 2000, 5),
        ]
        ranked = rank_remote_versions(
            rows, stock_version=stock, stock_version_code=stock_code)
        self.assertEqual(ranked[0]["version"], "444.0.0.46.85")
        self.assertEqual(ranked[0]["match"], "exact")
        self.assertEqual(ranked[0]["match_label"], "jak na telefonie")
        self.assertEqual(ranked[0]["distance"], 0)
        close = [r for r in ranked if r["match"] == "close"]
        self.assertEqual([r["version"] for r in close], [
            "444.0.0.46.80", "444.0.0.50.0",
        ])
        self.assertEqual(close[0]["match_label"], "blisko telefonu")
        self.assertLess(close[0]["distance"], close[1]["distance"])
        others = [r for r in ranked if r["match"] == "other"]
        self.assertEqual({r["version"] for r in others}, {
            "390.0.0.1.1", "445.0.0.1.1",
        })
        self.assertTrue(all(r.get("match") != "exact" or i == 0
                            for i, r in enumerate(ranked)))

    def test_exact_po_version_code_gdy_vername_inny(self):
        # Komentarz: ten sam versionCode co stock = exact, nawet przy innym napisie
        rows = [_row("444.0.0.46.85-alt", 1000, 1), _row("444.0.0.1.0", 10, 2)]
        ranked = rank_remote_versions(
            rows, stock_version="nie-ma-takiej", stock_version_code=1000)
        self.assertEqual(ranked[0]["version"], "444.0.0.46.85-alt")
        self.assertEqual(ranked[0]["match"], "exact")

    def test_no_exact_nearest_vername_among_same_major_minor(self):
        rows = [
            _row("445.1.0.0.0", None, 1),
            _row("444.0.0.50.0", None, 2),
            _row("444.0.0.46.80", None, 3),
            _row("390.0.0.1.1", None, 4),
        ]
        ranked = rank_remote_versions(
            rows, stock_version="444.0.0.46.85", stock_version_code=None)
        self.assertEqual(ranked[0]["version"], "444.0.0.46.80")
        self.assertEqual(ranked[0]["match"], "close")
        self.assertEqual(ranked[1]["version"], "444.0.0.50.0")
        self.assertEqual(ranked[1]["match"], "close")
        self.assertTrue(all(r["match"] != "exact" for r in ranked))

    def test_no_stock_keeps_order_without_badges(self):
        rows = [_row("1.0", 1, 1), _row("2.0", 2, 2)]
        ranked = rank_remote_versions(rows)
        self.assertEqual([r["version"] for r in ranked], ["1.0", "2.0"])
        self.assertTrue(all("match" not in r for r in ranked))
        self.assertTrue(all("match_label" not in r for r in ranked))

    def test_max_close_keeps_nearest_only(self):
        rows = [
            _row("444.0.0.1.0", 10, 1),
            _row("444.0.0.9.0", 90, 2),
            _row("444.0.0.2.0", 20, 3),
        ]
        ranked = rank_remote_versions(
            rows, stock_version="444.0.0.0.0", stock_version_code=0,
            max_close=2, max_other=0)
        self.assertEqual([r["version"] for r in ranked], [
            "444.0.0.1.0", "444.0.0.2.0",
        ])

    def test_parse_dumpsys_version_name_and_code(self):
        dump = (
            "  versionCode=384217305 minSdk=28 targetSdk=35\n"
            "  versionName=444.0.0.46.85\n"
        )
        rec = parse_dumpsys_version(dump)
        self.assertEqual(rec["version"], "444.0.0.46.85")
        self.assertEqual(rec["version_code"], 384217305)

    def test_read_installed_version_uses_fake_adb(self):
        def fake_adb(serial, args, timeout=180):
            self.assertEqual(serial, "SER")
            self.assertEqual(args[:3], ["shell", "dumpsys", "package"])
            self.assertIn("com.instagram.android", args)
            return (
                "  versionCode=99 minSdk=24\n"
                "  versionName=444.0.0.46.85\n"
            )
        rec = read_installed_version("SER", adb=fake_adb)
        self.assertEqual(rec["version"], "444.0.0.46.85")
        self.assertEqual(rec["version_code"], 99)
        self.assertEqual(rec["package"], "com.instagram.android")


class PullSplitsTests(unittest.TestCase):
    """`pm path` musi zciągnąć base + splity, nie sam base.apk."""

    def test_parse_pm_paths_keeps_all_splits(self):
        # Komentarz: A50 ma xxhdpi — bez tego splita splash 0x7f082060 znika
        out = (
            "package:/data/app/ig/base.apk\n"
            "package:/data/app/ig/split_config.xxhdpi.apk\n"
            "package:/data/app/ig/split_heliumiab.apk\n"
        )
        paths = parse_pm_paths(out)
        self.assertEqual(len(paths), 3)
        self.assertTrue(paths[0].endswith("base.apk"))
        self.assertTrue(any(p.endswith("split_config.xxhdpi.apk") for p in paths))

    def test_pull_from_device_pulls_every_pm_path(self):
        pulled: list[str] = []

        def fake_adb(serial, args, timeout=180):
            self.assertEqual(serial, "SER")
            if args[:3] == ["shell", "dumpsys", "package"]:
                return "  versionName=444.0.0.46.85\n  versionCode=1\n"
            if args[:3] == ["shell", "pm", "path"]:
                return (
                    "package:/data/app/ig/base.apk\n"
                    "package:/data/app/ig/split_config.xxhdpi.apk\n"
                )
            if args[0] == "pull":
                src, dest = args[1], args[2]
                pulled.append(src)
                # Komentarz: prawdziwy ZIP — merge wymaga resources.arsc tylko w base
                with zipfile.ZipFile(dest, "w") as zf:
                    if src.endswith("base.apk"):
                        zf.writestr("resources.arsc", b"arsc")
                    zf.writestr("classes.dex", b"dex" + b"\x00" * 2000)
                return "1 file pulled"
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rec = pull_from_device(root, "SER", adb=fake_adb)
            self.assertEqual(len(pulled), 2)
            self.assertTrue(any(p.endswith("split_config.xxhdpi.apk") for p in pulled))
            self.assertEqual(len(rec.get("splits") or []), 2)
            self.assertTrue(Path(rec["split_dir"]).is_dir())
            self.assertTrue(Path(rec["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
