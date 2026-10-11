"""Job klona — mock subprocess i adb, bez telefonu."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.cloner import job as clone_job
from v2.cloner.job import (
    CloneBusy, CloneJobError, apply_launch_safeguards, create_clone,
    describe_src_cache, install_apk, pick_appcloner_root, preflight_create,
    preview_clone, signed_apk_path, src_cache_is_hit, summarize_apktool_log,
    verify_clone_launch,
)
from v2.core.device_lock import DeviceLock

# Komentarz: cache źródeł żyje w appcloner/clone_instagram.py (nie pakiet v2)
sys.path.insert(0, str(ROOT / "appcloner"))
import clone_instagram as clone_ig  # noqa: E402


def _fake_java_home(root: Path) -> Path:
    """Minimalny JAVA_HOME: `bin/java` wykonywalny, bez prawdziwego JDK."""
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    java = bindir / "java"
    java.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    java.chmod(0o755)
    return root


@contextmanager
def _path_without_java(*, java_home: Path | None, empty_path: Path):
    """PATH bez `java`; opcjonalnie JAVA_HOME na fałszywy JVM."""
    old_home = os.environ.get("JAVA_HOME")
    old_path = os.environ.get("PATH")
    os.environ["PATH"] = str(empty_path)
    if java_home is None:
        os.environ.pop("JAVA_HOME", None)
    else:
        os.environ["JAVA_HOME"] = str(java_home)
    try:
        yield
    finally:
        if old_home is None:
            os.environ.pop("JAVA_HOME", None)
        else:
            os.environ["JAVA_HOME"] = old_home
        if old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old_path


def _write_device(root: Path, alias: str = "phone_T", serial: str = "SERIAL1") -> None:
    (root / "devices").mkdir(parents=True, exist_ok=True)
    doc = {
        "alias": alias,
        "adb_id": serial,
        "model": "SM_A505FN",
        "product": "a50eea",
        "manufacturer": "Samsung",
        "android_version": "11",
        "clones": [
            {"slot": 1, "package": "com.instagram.android", "note": "stock"},
        ],
    }
    (root / "devices" / f"{alias}.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")


class CreateCloneJobTests(unittest.TestCase):
    def test_generate_build_install_writes_clones(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            ac = Path(td) / "appcloner"
            _write_device(cfg)
            calls = []

            def run_build(ident, logf):
                p = signed_apk_path(ac, ident["package"], prefix=ident.get("dir_prefix"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"apk")

            def adb(serial, args):
                calls.append(list(args))
                return "Success"

            result = create_clone(
                "phone_T",
                config_root=cfg,
                data_root=data,
                appcloner=ac,
                adb=adb,
                list_packages=lambda s: set(),
                run_build=run_build,
                acquire_lock=True,
                verify=False,
            )
            pkg = result["identity"]["package"]
            self.assertTrue(pkg.startswith("com.instagram.androi"))
            self.assertNotEqual(pkg, "com.instagram.android")
            self.assertTrue(any(a[:1] == ["install"] for a in calls))
            saved = json.loads((cfg / "devices" / "phone_T.json").read_text(encoding="utf-8"))
            pkgs = [c["package"] for c in saved["clones"]]
            self.assertIn(pkg, pkgs)
            ident = next(c["identity"] for c in saved["clones"] if c["package"] == pkg)
            self.assertEqual(ident["mac"], result["identity"]["mac"])
            self.assertEqual(ident["dir_prefix"], "phone_T")
            self.assertTrue(
                signed_apk_path(ac, pkg, prefix="phone_T").is_file())
            self.assertFalse(
                (ac / "clone" / pkg / f"{pkg}.apk").is_file())

    def test_omitted_appcloner_kwarg_uses_env_root(self):
        """Panel podaje run_build bez cwd — APK musi iść z APPCLONER_ROOT."""
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            ac = Path(td) / "appcloner"
            ac.mkdir()
            _write_device(cfg)
            old = os.environ.get("APPCLONER_ROOT")
            os.environ["APPCLONER_ROOT"] = str(ac)
            calls = []
            try:
                def run_build(ident, logf):
                    p = signed_apk_path(ac, ident["package"], prefix=ident.get("dir_prefix"))
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b"apk")

                def adb(serial, args):
                    calls.append(list(args))
                    return "Success"

                result = create_clone(
                    "phone_T",
                    config_root=cfg,
                    data_root=data,
                    adb=adb,
                    list_packages=lambda s: set(),
                    run_build=run_build,
                    acquire_lock=False,
                    verify=False,
                )
                self.assertTrue(result["apk"].startswith(str(ac)))
                self.assertTrue(any(a[:1] == ["install"] for a in calls))
            finally:
                if old is None:
                    os.environ.pop("APPCLONER_ROOT", None)
                else:
                    os.environ["APPCLONER_ROOT"] = old

    def test_preflight_busy_when_lock_held(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            _write_device(cfg)
            lock = DeviceLock(data, "SERIAL1")
            lock.acquire(owner="bot", session_id="s1")
            try:
                with self.assertRaises(CloneBusy):
                    preflight_create("phone_T", config_root=cfg, data_root=data)
            finally:
                lock.release()

    def test_require_source_apk_picks_file_from_apks(self):
        """Panel podaje filename z apks/ — nie wymagamy instagram.apk."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "appcloner"
            apks = root / "apks"
            apks.mkdir(parents=True)
            src = apks / "instagram-444.0.0.46.85.apk"
            src.write_bytes(b"PK\x03\x04fake")
            found = clone_job.require_source_apk(
                root, apk="instagram-444.0.0.46.85.apk")
            self.assertEqual(found.resolve(), src.resolve())

    def test_preflight_missing_apk(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            ac = Path(td) / "appcloner"
            ac.mkdir()
            _write_device(cfg)
            old = os.environ.get("APPCLONER_ROOT")
            os.environ["APPCLONER_ROOT"] = str(ac)
            try:
                with self.assertRaises(CloneJobError) as ctx:
                    preflight_create("phone_T", config_root=cfg, data_root=data)
                self.assertIn("instagram.apk", str(ctx.exception))
            finally:
                if old is None:
                    os.environ.pop("APPCLONER_ROOT", None)
                else:
                    os.environ["APPCLONER_ROOT"] = old

    def test_start_job_does_not_mark_running_on_preflight_fail(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            ac = Path(td) / "appcloner"
            ac.mkdir()
            _write_device(cfg)
            old = os.environ.get("APPCLONER_ROOT")
            os.environ["APPCLONER_ROOT"] = str(ac)
            try:
                with clone_job._job_lock:
                    clone_job._job["running"] = False
                with self.assertRaises(CloneJobError):
                    clone_job.start_job_thread(
                        "phone_T", config_root=cfg, data_root=data)
                self.assertFalse(clone_job._job.get("running"))
            finally:
                if old is None:
                    os.environ.pop("APPCLONER_ROOT", None)
                else:
                    os.environ["APPCLONER_ROOT"] = old

    def test_busy_when_device_lock_held(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            _write_device(cfg)
            lock = DeviceLock(data, "SERIAL1")
            lock.acquire(owner="bot", session_id="s1")
            try:
                with self.assertRaises(CloneBusy):
                    create_clone(
                        "phone_T",
                        config_root=cfg,
                        data_root=data,
                        appcloner=Path(td),
                        adb=lambda s, a: "",
                        list_packages=lambda s: set(),
                        run_build=lambda i, f: None,
                        acquire_lock=True,
                    )
            finally:
                lock.release()

    def test_start_job_thread_409_when_running(self):
        with clone_job._job_lock:
            clone_job._job["running"] = True
            clone_job._job["device_alias"] = "x"
        try:
            with self.assertRaises(CloneBusy):
                clone_job.start_job_thread(
                    "phone_T",
                    config_root=Path("."),
                    data_root=Path("."),
                )
        finally:
            with clone_job._job_lock:
                clone_job._job["running"] = False


class UninstallAndIdentityCloneTests(unittest.TestCase):
    def test_create_clone_uses_identity_and_verify(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            ac = Path(td) / "appcloner"
            _write_device(cfg)
            ident = {
                "package": "com.instagram.androiz",
                "android_id": "0123456789abcdef",
                "gaid": "aaaaaaaa-bbbb-4ccc-dddd-eeeeeeeeeeee",
                "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
                "mac": "02:11:22:33:44:55",
                "imei": "123456789012347",
                "model": "SM A505FN",
            }

            def run_build(one, logf):
                p = signed_apk_path(ac, one["package"], prefix=one.get("dir_prefix"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"apk")

            def adb(serial, args):
                pkg = ident["package"]
                if args[:3] == ["shell", "pm", "path"]:
                    return f"package:/data/app/{args[3]}/base.apk"
                if args[:2] == ["shell", "pidof"]:
                    return "4321"
                # Komentarz: sam pid nie wystarcza — resumed musi należeć do klona
                if len(args) >= 2 and args[1] == "dumpsys":
                    return (
                        "  mResumedActivity: ActivityRecord{abc u0 "
                        f"{pkg}/com.instagram.mainactivity.InstagramMainActivity t1}}"
                    )
                if len(args) >= 2 and args[1] == "logcat":
                    return ""
                return "Success"

            result = create_clone(
                "phone_T",
                config_root=cfg,
                data_root=data,
                appcloner=ac,
                adb=adb,
                list_packages=lambda s: set(),
                run_build=run_build,
                acquire_lock=False,
                identity=ident,
                verify=True,
                verify_wait_s=0,
            )
            self.assertEqual(result["identity"]["package"], "com.instagram.androiz")
            self.assertTrue(result["verified"]["ok"])
            self.assertEqual(result["verified"]["pid"], "4321")
            self.assertTrue(result["verified"]["focus_hit"])
            self.assertIn("com.instagram.androiz/", result["verified"]["resumed"])

    def test_create_clone_resolves_bare_apk_filename_to_apks(self):
        """Panel podaje instagram-444…apk — job musi dać appcloner/apks/…"""
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            ac = Path(td) / "appcloner"
            apks = ac / "apks"
            apks.mkdir(parents=True)
            src = apks / "instagram-444.0.0.46.85.apk"
            src.write_bytes(b"PK\x03\x04src")
            _write_device(cfg)
            ident = {
                "package": "com.instagram.androiz",
                "android_id": "0123456789abcdef",
                "gaid": "aaaaaaaa-bbbb-4ccc-dddd-eeeeeeeeeeee",
                "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
                "mac": "02:11:22:33:44:55",
                "imei": "123456789012347",
                "model": "Samsung Galaxy A54 5G",
                "apk": "instagram-444.0.0.46.85.apk",
            }
            seen: dict = {}

            def run_build(one, logf):
                seen["apk"] = one.get("apk")
                p = signed_apk_path(ac, one["package"], prefix=one.get("dir_prefix"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"apk")

            def adb(_serial, args):
                if args[:3] == ["shell", "pm", "path"]:
                    return f"package:/data/app/{args[3]}/base.apk"
                if args[:2] == ["shell", "pidof"]:
                    return "1"
                if len(args) >= 2 and args[1] == "dumpsys":
                    return (
                        "  mResumedActivity: ActivityRecord{abc u0 "
                        "com.instagram.androiz/com.instagram.mainactivity.InstagramMainActivity t1}"
                    )
                if len(args) >= 2 and args[1] == "logcat":
                    return ""
                return "Success"

            create_clone(
                "phone_T",
                config_root=cfg,
                data_root=data,
                appcloner=ac,
                adb=adb,
                list_packages=lambda s: set(),
                run_build=run_build,
                acquire_lock=False,
                identity=ident,
                verify=False,
            )
            self.assertEqual(Path(seen["apk"]).resolve(), src.resolve())

    def test_uninstall_skips_stock(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            _write_device(cfg)
            calls = []

            def adb(s, a):
                calls.append(a)
                return "Success"

            r = clone_job.uninstall_clone_for_deleted_account(
                config_root=cfg, device_alias="phone_T",
                package="com.instagram.android", account_alias="acc",
                adb=adb)
            self.assertEqual(r["skipped"], "stock_or_foreign")
            self.assertFalse(r["uninstalled"])
            self.assertEqual(calls, [])

    def test_uninstall_drops_clone_when_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            _write_device(cfg)
            path = cfg / "devices" / "phone_T.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["clones"].append({"slot": 2, "package": "com.instagram.androip"})
            path.write_text(json.dumps(doc), encoding="utf-8")
            acc = cfg / "accounts"
            acc.mkdir(parents=True, exist_ok=True)
            (acc / "gone.json").write_text(json.dumps({
                "device": {"device_alias": "phone_T",
                           "app_clone_package": "com.instagram.androip"},
            }), encoding="utf-8")
            calls = []

            def adb(s, a):
                calls.append(list(a))
                return "Success"

            r = clone_job.uninstall_clone_for_deleted_account(
                config_root=cfg, device_alias="phone_T",
                package="com.instagram.androip", account_alias="gone",
                adb=adb)
            self.assertTrue(r["uninstalled"])
            self.assertTrue(r["dropped"])
            self.assertEqual(calls, [["uninstall", "com.instagram.androip"]])
            saved = json.loads(path.read_text(encoding="utf-8"))
            pkgs = [c["package"] for c in saved["clones"]]
            self.assertNotIn("com.instagram.androip", pkgs)
            self.assertIn("com.instagram.android", pkgs)

    def test_uninstall_skips_when_another_account_holds_package(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            _write_device(cfg)
            acc = cfg / "accounts"
            acc.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({
                "device": {"device_alias": "phone_T",
                           "app_clone_package": "com.instagram.androip"},
            })
            (acc / "a.json").write_text(payload, encoding="utf-8")
            (acc / "b.json").write_text(payload, encoding="utf-8")
            calls = []

            def adb(s, a):
                calls.append(a)
                return "Success"

            r = clone_job.uninstall_clone_for_deleted_account(
                config_root=cfg, device_alias="phone_T",
                package="com.instagram.androip", account_alias="a",
                adb=adb)
            self.assertEqual(r["skipped"], "shared")
            self.assertEqual(r["holders"], ["b"])
            self.assertEqual(calls, [])


class RequireJdkTests(unittest.TestCase):
    def test_require_jdk_uses_java_home_when_not_on_path(self):
        """Panel bez `java` w PATH nadal znajduje JDK przez JAVA_HOME."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            empty = base / "empty"
            empty.mkdir()
            home = _fake_java_home(base / "jvm")
            java = home / "bin" / "java"
            with _path_without_java(java_home=home, empty_path=empty):
                found = clone_job.require_jdk()
            self.assertEqual(Path(found).resolve(), java.resolve())

    def test_java_subprocess_env_puts_java_on_path(self):
        """clone_instagram.py dostaje PATH z katalogiem `bin` znalezionego JDK."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            empty = base / "empty"
            empty.mkdir()
            home = _fake_java_home(base / "jvm")
            with _path_without_java(java_home=home, empty_path=empty):
                env = clone_job.java_subprocess_env()
            self.assertEqual(Path(env["JAVA_HOME"]).resolve(), home.resolve())
            self.assertEqual(
                Path(env["PATH"].split(os.pathsep)[0]).resolve(),
                (home / "bin").resolve(),
            )

    def test_run_appcloner_build_passes_java_env(self):
        """Popen appclonera dostaje env z java, nie surowy PATH panelu."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            empty = base / "empty"
            empty.mkdir()
            home = _fake_java_home(base / "jvm")
            script = base / "clone_instagram.py"
            script.write_text("#\n", encoding="utf-8")
            captured: dict = {}

            class _Proc:
                returncode = 0

                def poll(self):
                    return 0

            def factory(cmd, **kw):
                captured["env"] = kw.get("env") or {}
                return _Proc()

            ident = {
                "package": "com.instagram.androiz",
                "android_id": "0123456789abcdef",
                "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
                "model": "SM A505FN",
                "gaid": "aaaaaaaa-bbbb-4ccc-dddd-eeeeeeeeeeee",
                "mac": "02:11:22:33:44:55",
                "imei": "123456789012347",
            }
            with _path_without_java(java_home=home, empty_path=empty):
                clone_job.run_appcloner_build(
                    ident, script=script, log_file=None, popen_factory=factory)
            path0 = Path(captured["env"]["PATH"].split(os.pathsep)[0]).resolve()
            self.assertEqual(path0, (home / "bin").resolve())

    def test_job_argv_build_sign_and_signed_apk_path(self):
        """Panel nadal podaje --build --sign; instalacja z clone/<telefon>/<pkg>/<pkg>.apk."""
        script = Path("/tmp/clone_instagram.py")
        ident = {
            "package": "com.instagram.androii",
            "android_id": "7f3a8b1c0d9e2468",
            "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
            "model": "Samsung Galaxy A54 5G",
            "gaid": "d3b07384-d113-4f4a-a1b8-39281a9420e1",
            "media_drm_id": "kL9+mR2vT8wX1zQ7aP4oN6eJ3iU5yB0cW8vX9zA1bC2=",
            "build_time": "2026-05-14 09:12:00",
            "boot_time": "2026-08-01 08:10:15",
            "gps_lat": 51.10788,
            "gps_lon": 17.03854,
            "dns": "1.1.1.1,1.0.0.1",
            "sim_country": "PL",
            "sim_operator": "Play",
            "sim_operator_code": "26006",
            "apk": "apks/instagram-444.0.0.46.85.apk",
            "dir_prefix": "phone_T",
        }
        argv = clone_job.build_clone_argv(ident, script=script, force=True)
        self.assertIn("--build", argv)
        self.assertIn("--sign", argv)
        self.assertIn("--force", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "Samsung Galaxy A54 5G")
        self.assertEqual(argv[argv.index("--package") + 1], "com.instagram.androii")
        self.assertEqual(
            argv[argv.index("--apk") + 1], "apks/instagram-444.0.0.46.85.apk")
        self.assertEqual(argv[argv.index("--gaid") + 1], ident["gaid"])
        self.assertEqual(argv[argv.index("--sim-operator-code") + 1], "26006")
        self.assertIn("--media-drm-id", argv)
        self.assertIn("--gps-lat", argv)
        self.assertEqual(argv[argv.index("--dir-prefix") + 1], "phone_T")
        ac = Path("/ac")
        self.assertEqual(
            signed_apk_path(ac, "com.instagram.androii", prefix="phone_T"),
            ac / "clone" / "phone_T" / "com.instagram.androii" / "com.instagram.androii.apk",
        )
        self.assertNotEqual(
            signed_apk_path(ac, "com.instagram.androii", prefix="phone_A"),
            signed_apk_path(ac, "com.instagram.androii", prefix="phone_B"),
        )

    def test_sanitize_use_skips_optional_mac_and_gaid(self):
        """Odznaczony MAC/GAID nie idzie do argv; android_id zostaje."""
        raw = {
            "package": "com.instagram.androii",
            "android_id": "7f3a8b1c0d9e2468",
            "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
            "model": "Samsung Galaxy A54 5G",
            "gaid": "d3b07384-d113-4f4a-a1b8-39281a9420e1",
            "mac": "02:11:22:33:44:55",
            "imei": "123456789012347",
            "use": {"mac": False, "gaid": False, "imei": True},
        }
        ident = clone_job.sanitize_identity(raw)
        self.assertEqual(ident["mac"], "")
        self.assertEqual(ident["gaid"], "")
        self.assertEqual(ident["imei"], "123456789012347")
        argv = clone_job.build_clone_argv(ident, script=Path("/tmp/c.py"))
        self.assertNotIn("--mac", argv)
        self.assertNotIn("--gaid", argv)
        self.assertIn("--imei", argv)

    def test_sanitize_without_use_still_requires_mac(self):
        """Stary klient bez `use` — MAC nadal obowiązkowy."""
        raw = {
            "package": "com.instagram.androii",
            "android_id": "7f3a8b1c0d9e2468",
            "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
            "model": "A54",
            "imei": "123456789012347",
        }
        with self.assertRaises(clone_job.CloneJobError) as ctx:
            clone_job.sanitize_identity(raw)
        self.assertIn("MAC", str(ctx.exception))

    def test_require_jdk_skips_mise_shim_on_path(self):
        """Shim mise na PATH nie udaje JDK — bierzemy JAVA_HOME."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mise = base / "mise"
            mise.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            mise.chmod(0o755)
            shimdir = base / "shims"
            shimdir.mkdir()
            (shimdir / "java").symlink_to(mise)
            home = _fake_java_home(base / "jvm")
            java = home / "bin" / "java"
            with _path_without_java(java_home=home, empty_path=shimdir):
                found = clone_job.require_jdk()
            self.assertEqual(Path(found).resolve(), java.resolve())


class PreviewCloneTests(unittest.TestCase):
    """POST preview: tożsamość z JSON, bez JDK / appclonera / żywego ADB."""

    def test_identity_when_adb_raises(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            _write_device(cfg)

            def boom(*_a, **_k):
                raise OSError("brak adb")

            out = preview_clone("phone_T", config_root=cfg, adb=boom)
            ident = out["identity"]
            self.assertTrue(ident["mac"])
            self.assertTrue(ident["imei"])
            self.assertTrue(ident["android_id"])
            self.assertTrue(ident["fingerprint"])
            self.assertTrue(ident["model"])
            self.assertTrue(ident["media_drm_id"])
            self.assertTrue(ident["package"].startswith("com.instagram.androi"))
            self.assertTrue(out["suggested_packages"])

    def test_short_adb_timeout_and_no_jdk(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            _write_device(cfg)
            seen = {}

            def spy(_serial, _args, timeout=999):
                seen["timeout"] = timeout
                raise CloneJobError("offline")

            with patch.object(
                    clone_job, "require_jdk",
                    side_effect=AssertionError("jdk")):
                with patch.object(
                        clone_job, "appcloner_root",
                        side_effect=AssertionError("appcloner")):
                    out = preview_clone("phone_T", config_root=cfg, adb=spy)
            self.assertLessEqual(seen.get("timeout", 999), 5)
            self.assertTrue(out["identity"]["mac"])

    def test_without_adb_flag_skips_pm_list(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            _write_device(cfg)

            def boom(*_a, **_k):
                raise AssertionError("adb nie powinno być wołane")

            out = preview_clone(
                "phone_T", config_root=cfg, extra_from_adb=False, adb=boom)
            self.assertTrue(out["identity"]["imei"])
            # Komentarz: kontrakt frontu — lista modeli + szablon fingerprint
            self.assertTrue(out["spoof_profiles"])
            models = {p["model"] for p in out["spoof_profiles"]}
            self.assertIn("OnePlus 8T", models)
            op = next(p for p in out["spoof_profiles"] if p["model"] == "OnePlus 8T")
            self.assertIn("{token}", op["fingerprint"])

    def test_preferred_model_stays_oneplus(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            _write_device(cfg)
            out = preview_clone(
                "phone_T", config_root=cfg, extra_from_adb=False,
                preferred_model="OnePlus 8T",
            )
            self.assertEqual(out["identity"]["model"], "OnePlus 8T")
            self.assertIn("kebab", out["identity"]["fingerprint"])
            self.assertTrue(out["spoof_profiles"])

    def test_preview_prunes_stale_androia_when_adb_stock_only(self):
        # Komentarz: clones[] ma androia, pm list tylko stock — nazwa wraca do dropdownu
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            _write_device(cfg)
            path = cfg / "devices" / "phone_T.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["clones"].append({"slot": 2, "package": "com.instagram.androia"})
            path.write_text(json.dumps(doc), encoding="utf-8")

            def adb(_serial, _args, timeout=None):
                # Komentarz: extra niepuste = ADB OK (pełny pm list ma też inne pakiety)
                return (
                    "package:com.instagram.android\n"
                    "package:com.android.chrome\n"
                )

            out = preview_clone("phone_T", config_root=cfg, adb=adb)
            self.assertIn("com.instagram.androia", out["suggested_packages"])
            self.assertNotIn("com.instagram.androia", out["used_packages"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            pkgs = [c["package"] for c in saved["clones"]]
            self.assertNotIn("com.instagram.androia", pkgs)
            self.assertIn("com.instagram.android", pkgs)


class AppclonerRootLookupTests(unittest.TestCase):
    """Pusty sibling nie zasłania clone_instagram.py w bot_v2/appcloner."""

    def test_prefers_local_script_over_empty_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            sibling = base / "sibling"
            local = base / "local"
            sibling.mkdir()
            local.mkdir()
            (local / "clone_instagram.py").write_text("# lokalny skrypt\n", encoding="utf-8")
            root = pick_appcloner_root(env="", sibling=sibling, local=local)
            self.assertEqual(root, local)
            self.assertTrue((root / "clone_instagram.py").is_file())

    def test_env_wins_even_without_script(self):
        with tempfile.TemporaryDirectory() as td:
            env_dir = Path(td) / "from_env"
            env_dir.mkdir()
            sibling = Path(td) / "sibling"
            local = Path(td) / "local"
            local.mkdir()
            (local / "clone_instagram.py").write_text("#\n", encoding="utf-8")
            root = pick_appcloner_root(
                env=str(env_dir), sibling=sibling, local=local)
            self.assertEqual(root.resolve(), env_dir.resolve())

    def test_create_true_uses_local_when_no_script(self):
        with tempfile.TemporaryDirectory() as td:
            sibling = Path(td) / "sibling"
            local = Path(td) / "local"
            sibling.mkdir()
            root = pick_appcloner_root(
                env="", sibling=sibling, local=local, create=True)
            self.assertEqual(root, local)
            self.assertTrue(local.is_dir())

    def test_clone_script_from_given_root(self):
        with tempfile.TemporaryDirectory() as td:
            ac = Path(td) / "appcloner"
            ac.mkdir()
            script = ac / "clone_instagram.py"
            script.write_text("#\n", encoding="utf-8")
            self.assertEqual(clone_job.clone_script(ac), script)

    def test_workspace_clone_script_without_env(self):
        """Bez APPCLONER_ROOT skrypt ma być w bot_v2/appcloner (nie pusty sibling)."""
        old = os.environ.pop("APPCLONER_ROOT", None)
        try:
            found = clone_job.clone_script()
            self.assertTrue(found.is_file(), found)
            self.assertEqual(found.name, "clone_instagram.py")
            local = Path(__file__).resolve().parent.parent / "appcloner" / "clone_instagram.py"
            self.assertEqual(found.resolve(), local.resolve())
        finally:
            if old is not None:
                os.environ["APPCLONER_ROOT"] = old


class InstallApkSplitRetryTests(unittest.TestCase):
    def test_success_uses_install_r_only(self):
        with tempfile.TemporaryDirectory() as td:
            apk = Path(td) / "com.instagram.androig.apk"
            apk.write_bytes(b"apk")
            calls = []

            def adb(_s, args):
                calls.append(list(args))
                return "Success"

            install_apk("SERIAL1", apk, adb=adb)
            self.assertEqual(
                calls, [["install", "-r", "--no-incremental", str(apk)]])
            self.assertTrue(all(c[0] != "install-multiple" for c in calls))

    def test_missing_split_retries_no_streaming(self):
        with tempfile.TemporaryDirectory() as td:
            apk = Path(td) / "com.instagram.androig.apk"
            apk.write_bytes(b"apk")
            calls = []

            def adb(_s, args):
                calls.append(list(args))
                if "--no-streaming" not in args:
                    raise CloneJobError(
                        "adb install -r x exit=1: Performing Streamed Install\n"
                        "Failure [INSTALL_FAILED_MISSING_SPLIT: "
                        "Missing split for com.instagram.androig]"
                    )
                return "Success"

            install_apk("SERIAL1", apk, adb=adb)
            self.assertEqual(
                calls[0], ["install", "-r", "--no-incremental", str(apk)])
            self.assertEqual(
                calls[1],
                ["install", "-r", "--no-incremental", "--no-streaming", str(apk)])
            self.assertTrue(all(c[0] != "install-multiple" for c in calls))
            self.assertTrue(all("com.instagram.android" not in c for c in calls))

    def test_other_install_error_does_not_retry(self):
        with tempfile.TemporaryDirectory() as td:
            apk = Path(td) / "x.apk"
            apk.write_bytes(b"apk")
            calls = []

            def adb(_s, args):
                calls.append(list(args))
                raise CloneJobError("adb install -r x exit=1: INSTALL_FAILED_UPDATE_INCOMPATIBLE")

            with self.assertRaises(CloneJobError) as ctx:
                install_apk("SERIAL1", apk, adb=adb)
            self.assertIn("UPDATE_INCOMPATIBLE", str(ctx.exception))
            self.assertEqual(len(calls), 1)


class ApktoolLogSummaryTests(unittest.TestCase):
    def test_summarize_drops_unresolved_and_keeps_install_error(self):
        raw = (
            "[clone] start\n"
            "W: Unresolved resource reference: ResReference{pkg=... name=com.instagram.android}\n"
            "W: Unresolved resource reference: ResReference{pkg=...}\n"
            "[clone] OK\n"
            "[clone-job] BŁĄD: adb install -r x exit=1: "
            "Failure [INSTALL_FAILED_MISSING_SPLIT: Missing split for com.instagram.androig]\n"
        )
        out = summarize_apktool_log(raw)
        self.assertNotIn("W: Unresolved resource reference:", out)
        self.assertIn("2 ostrzeżeń Unresolved resource", out)
        self.assertIn("INSTALL_FAILED_MISSING_SPLIT", out)
        self.assertIn("[clone] OK", out)

    def test_job_snapshot_error_is_install_not_apktool_wall(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "c.log"
            log.write_text(
                "W: Unresolved resource reference: ResReference{pkg=foo}\n" * 30
                + "[clone] OK\n",
                encoding="utf-8",
            )
            with clone_job._job_lock:
                old_path = clone_job._job.get("log_path")
                old_err = clone_job._job.get("error")
                old_state = clone_job._job.get("state")
                clone_job._job["log_path"] = str(log)
                clone_job._job["error"] = (
                    "adb install -r x exit=1: Failure "
                    "[INSTALL_FAILED_MISSING_SPLIT: Missing split for com.instagram.androig]"
                )
                clone_job._job["state"] = "error"
            try:
                st = clone_job.job_snapshot()
            finally:
                with clone_job._job_lock:
                    clone_job._job["log_path"] = old_path
                    clone_job._job["error"] = old_err
                    clone_job._job["state"] = old_state
            self.assertIn("INSTALL_FAILED_MISSING_SPLIT", st["error"])
            self.assertNotIn("W: Unresolved resource reference:", st["log"])
            self.assertTrue(
                all("Unresolved resource reference: ResReference" not in ln
                    for ln in st["log_tail"])
            )


class VerifyCloneLaunchTests(unittest.TestCase):
    """UI klona: focused/resumed activity, nie sam pidof."""

    _PKG = "com.instagram.androib"

    def _adb(self, *, pid="999", dumpsys="", logcat=""):
        """Mock adb: pm path OK, reszta z argumentów."""

        def adb(_serial, args):
            if args[:3] == ["shell", "pm", "path"]:
                return f"package:/data/app/{args[3]}/base.apk"
            if args[:2] == ["shell", "pidof"]:
                return pid
            if len(args) >= 2 and args[1] == "dumpsys":
                return dumpsys
            if len(args) >= 2 and args[1] == "logcat":
                return logcat
            return "Success"

        return adb

    def test_pid_without_focus_is_fail(self):
        # Komentarz: InstagramAppShell ma PID, MainTabActivity już padła
        r = verify_clone_launch(
            "SERIAL1", self._PKG, adb=self._adb(pid="4321", dumpsys=""), wait_s=0
        )
        self.assertFalse(r["ok"])
        self.assertEqual(r["pid"], "4321")
        self.assertFalse(r["focus_hit"])
        self.assertIn("nie wystarcza", r["error"])

    def test_stock_focus_does_not_count_as_clone(self):
        # Komentarz: com.instagram.android/ nie jest com.instagram.androib/
        dump = (
            "  mResumedActivity: ActivityRecord{x u0 "
            "com.instagram.android/com.instagram.mainactivity.InstagramMainActivity t1}"
        )
        r = verify_clone_launch(
            "SERIAL1", self._PKG, adb=self._adb(pid="1", dumpsys=dump), wait_s=0
        )
        self.assertFalse(r["ok"])
        self.assertFalse(r["focus_hit"])

    def test_recents_activity_without_resumed_is_fail(self):
        dump = (
            "    ACTIVITY com.instagram.androib/"
            "com.instagram.mainactivity.MainTabActivity 123\n"
            "  mResumedActivity: ActivityRecord{x u0 "
            "com.sec.android.app.launcher/.Launcher t0}"
        )
        r = verify_clone_launch(
            "SERIAL1", self._PKG, adb=self._adb(pid="8", dumpsys=dump), wait_s=0
        )
        self.assertFalse(r["ok"])

    def test_resumed_clone_activity_is_ok(self):
        dump = (
            "  mResumedActivity: ActivityRecord{abc u0 "
            "com.instagram.androib/com.instagram.mainactivity.InstagramMainActivity t2}"
        )
        r = verify_clone_launch(
            "SERIAL1", self._PKG, adb=self._adb(pid="77", dumpsys=dump), wait_s=0
        )
        self.assertTrue(r["ok"])
        self.assertTrue(r["focus_hit"])
        self.assertIn("com.instagram.androib/", r["resumed"])

    def test_fail_includes_fatal_from_logcat(self):
        fatal = (
            "FATAL EXCEPTION: main\n"
            "android.view.InflateException: Binary XML file line #40: "
            "You must supply a layout_height attribute."
        )
        r = verify_clone_launch(
            "SERIAL1",
            self._PKG,
            adb=self._adb(pid="5", dumpsys="", logcat=fatal),
            wait_s=0,
        )
        self.assertFalse(r["ok"])
        self.assertIn("InflateException", r["fatal"])
        self.assertIn("layout_height", r["error"])

    def test_create_clone_raises_when_pid_without_focus(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            ac = Path(td) / "appcloner"
            _write_device(cfg)
            ident = {
                "package": "com.instagram.androiz",
                "android_id": "0123456789abcdef",
                "gaid": "aaaaaaaa-bbbb-4ccc-dddd-eeeeeeeeeeee",
                "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
                "mac": "02:11:22:33:44:55",
                "imei": "123456789012347",
                "model": "SM A505FN",
            }

            def run_build(one, logf):
                p = signed_apk_path(ac, one["package"], prefix=one.get("dir_prefix"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"apk")

            def adb(serial, args):
                if args[:3] == ["shell", "pm", "path"]:
                    return f"package:/data/app/{args[3]}/base.apk"
                if args[:2] == ["shell", "pidof"]:
                    return "4321"
                if len(args) >= 2 and args[1] == "dumpsys":
                    return "mResumedActivity: ActivityRecord{x u0 com.android.launcher/.X t0}"
                if len(args) >= 2 and args[1] == "logcat":
                    return (
                        "FATAL EXCEPTION: main\n"
                        "android.view.InflateException: Binary XML file line #82: "
                        "You must supply a layout_height attribute."
                    )
                return "Success"

            with self.assertRaises(CloneJobError) as ctx:
                create_clone(
                    "phone_T",
                    config_root=cfg,
                    data_root=data,
                    appcloner=ac,
                    adb=adb,
                    list_packages=lambda s: set(),
                    run_build=run_build,
                    acquire_lock=False,
                    identity=ident,
                    verify=True,
                    verify_wait_s=0,
                )
            self.assertIn("nie wystarcza", str(ctx.exception))
            self.assertIn("InflateException", str(ctx.exception))


class CloneDirPrefixTests(unittest.TestCase):
    """Dwa telefony mogą mieć ten sam APPID — foldery rozdzielamy prefixem."""

    def test_sanitize_i_osobne_katalogi(self):
        self.assertEqual(clone_job.sanitize_dir_prefix("phone T"), "phone_T")
        self.assertEqual(clone_job.sanitize_dir_prefix("_cache"), "dev_cache")
        a = clone_ig.clone_package_dir(Path("/c"), "com.instagram.androia", "oneplus8t")
        b = clone_ig.clone_package_dir(Path("/c"), "com.instagram.androia", "galaxy")
        self.assertEqual(a, Path("/c/oneplus8t/com.instagram.androia"))
        self.assertEqual(b, Path("/c/galaxy/com.instagram.androia"))
        self.assertNotEqual(a, b)
        self.assertEqual(
            clone_ig.clone_package_dir(Path("/c"), "com.instagram.androia", ""),
            Path("/c/com.instagram.androia"),
        )


class SrcCacheTests(unittest.TestCase):
    """Cache instagram_src per wersja APK — drugi decode bez apktool d."""

    def _fake_apk(self, folder: Path, name: str = "instagram-444.0.0.46.85.apk") -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        apk = folder / name
        apk.write_bytes(b"PK\x03\x04fake-apk")
        return apk

    def _decode_fn(self, calls: list):
        """Mock apktool d — tworzy stockowy manifest, bez patcha."""

        def decode(_apk: Path, workdir: Path) -> None:
            calls.append("apktool d")
            workdir.mkdir(parents=True, exist_ok=True)
            (workdir / "AndroidManifest.xml").write_text(
                '<manifest package="com.instagram.android"/>\n', encoding="utf-8")
            (workdir / "smali").mkdir(exist_ok=True)
            (workdir / "smali" / "stock.smali").write_text("# stock\n", encoding="utf-8")

        return decode

    def test_second_populate_is_cache_hit_without_apktool(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clone_root = root / "clone"
            apk = self._fake_apk(root / "apks")
            calls: list = []
            decode = self._decode_fn(calls)
            wd1 = clone_root / "com.instagram.androia" / "instagram_src"
            wd2 = clone_root / "com.instagram.androib" / "instagram_src"
            miss = clone_ig.populate_instagram_src(
                apk, wd1, clone_root, decode_fn=decode)
            hit = clone_ig.populate_instagram_src(
                apk, wd2, clone_root, decode_fn=decode)
            self.assertEqual(miss, "MISS")
            self.assertEqual(hit, "HIT")
            self.assertEqual(calls, ["apktool d"])
            self.assertTrue((wd2 / "AndroidManifest.xml").is_file())
            self.assertTrue((wd2 / "smali" / "stock.smali").is_file())
            # Komentarz: cache = stan przed patch — stock package w manifeście
            self.assertIn(
                "com.instagram.android",
                (wd2 / "AndroidManifest.xml").read_text(encoding="utf-8"),
            )

    def test_force_wipes_appid_not_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clone_root = root / "clone"
            apk = self._fake_apk(root / "apks")
            calls: list = []
            wd = clone_root / "com.instagram.androiz" / "instagram_src"
            clone_ig.populate_instagram_src(
                apk, wd, clone_root, decode_fn=self._decode_fn(calls))
            clone_ig.wipe_clone_dir(clone_root / "com.instagram.androiz")
            self.assertFalse(wd.exists())
            self.assertTrue(clone_ig.src_cache_is_hit(apk, clone_root))
            hit = clone_ig.populate_instagram_src(
                apk, wd, clone_root, decode_fn=self._decode_fn(calls))
            self.assertEqual(hit, "HIT")
            self.assertEqual(calls, ["apktool d"])

    def test_different_apk_same_stem_rebuilds_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clone_root = root / "clone"
            apk = self._fake_apk(root / "apks")
            calls: list = []
            wd = clone_root / "com.instagram.androia" / "instagram_src"
            clone_ig.populate_instagram_src(
                apk, wd, clone_root, decode_fn=self._decode_fn(calls))
            apk.write_bytes(b"PK\x03\x04INNY-PLIK-TEN-SAM-STEM")
            miss = clone_ig.populate_instagram_src(
                apk, wd, clone_root, decode_fn=self._decode_fn(calls))
            self.assertEqual(miss, "MISS")
            self.assertEqual(calls, ["apktool d", "apktool d"])

    def test_job_describe_src_cache_hit_miss(self):
        with tempfile.TemporaryDirectory() as td:
            ac = Path(td) / "appcloner"
            apk = self._fake_apk(ac / "apks")
            self.assertIn("cache MISS", describe_src_cache(ac, apk))
            self.assertFalse(src_cache_is_hit(ac, apk))
            cached, marker = clone_job.src_cache_paths(ac, apk)
            cached.mkdir(parents=True)
            (cached / "AndroidManifest.xml").write_text("<manifest/>\n", encoding="utf-8")
            st = apk.stat()
            marker.write_text(json.dumps({
                "apk": apk.stem, "size": int(st.st_size), "mtime": int(st.st_mtime),
            }), encoding="utf-8")
            self.assertTrue(src_cache_is_hit(ac, apk))
            self.assertIn("cache HIT", describe_src_cache(ac, apk))

    def test_create_clone_logs_cache_hit(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            ac = Path(td) / "appcloner"
            apk = self._fake_apk(ac / "apks")
            cached, marker = clone_job.src_cache_paths(ac, apk)
            cached.mkdir(parents=True)
            (cached / "AndroidManifest.xml").write_text("<manifest/>\n", encoding="utf-8")
            st = apk.stat()
            marker.write_text(json.dumps({
                "apk": apk.stem, "size": int(st.st_size), "mtime": int(st.st_mtime),
            }), encoding="utf-8")
            _write_device(cfg)
            logs: list[str] = []
            ident = {
                "package": "com.instagram.androiz",
                "android_id": "0123456789abcdef",
                "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
                "mac": "02:11:22:33:44:55",
                "imei": "123456789012347",
                "model": "Samsung Galaxy A54 5G",
                "apk": apk.name,
            }

            def run_build(one, logf):
                p = signed_apk_path(ac, one["package"], prefix=one.get("dir_prefix"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"apk")

            create_clone(
                "phone_T", config_root=cfg, data_root=data, appcloner=ac,
                adb=lambda s, a: "Success", list_packages=lambda s: set(),
                run_build=run_build, acquire_lock=False, identity=ident,
                verify=False, log=logs.append,
            )
            self.assertTrue(any("cache HIT" in ln for ln in logs), logs)


class LaunchSafeguardTests(unittest.TestCase):
    """Force-stop stock + innych klonów przed launch; bez uninstall."""

    _TARGET = "com.instagram.androib"

    def _adb(self, *, calls, pkgs=None, dumpsys="", pids=None, path_ok=True):
        """Mock adb: pm list / force-stop / ps / pidof / dumpsys."""
        pids = pids or {}
        pkg_blob = "\n".join(f"package:{p}" for p in (pkgs or []))

        def adb(_serial, args):
            calls.append(list(args))
            if args[:3] == ["shell", "pm", "path"]:
                if path_ok:
                    return f"package:/data/app/{args[3]}/base.apk"
                return ""
            if args[:4] == ["shell", "pm", "list", "packages"]:
                return pkg_blob
            if args[:3] == ["shell", "am", "force-stop"]:
                return ""
            if args[:2] == ["shell", "ps"]:
                # Komentarz: NAME w ostatniej kolumnie — nie comm z pidof
                lines = ["USER PID NAME"]
                for pkg, pid in pids.items():
                    if pid:
                        lines.append(f"u0_a1 {pid} {pkg}")
                return "\n".join(lines)
            if args[:2] == ["shell", "pidof"]:
                return pids.get(args[2], "")
            if len(args) >= 2 and args[1] == "dumpsys":
                return dumpsys
            if len(args) >= 2 and args[1] == "logcat":
                return ""
            return "Success"

        return adb

    def test_force_stops_other_clones_and_stock(self):
        calls = []
        dump = (
            "  mResumedActivity: ActivityRecord{abc u0 "
            f"{self._TARGET}/com.instagram.mainactivity.InstagramMainActivity t2}}"
        )
        r = verify_clone_launch(
            "SERIAL1", self._TARGET,
            adb=self._adb(
                calls=calls,
                pkgs=["com.instagram.android", "com.instagram.androia", self._TARGET],
                dumpsys=dump,
                pids={self._TARGET: "77"},
            ),
            wait_s=0,
            installed={
                "com.instagram.android", "com.instagram.androia", self._TARGET,
            },
        )
        stops = [a[3] for a in calls if a[:3] == ["shell", "am", "force-stop"]]
        self.assertCountEqual(stops, ["com.instagram.android", "com.instagram.androia"])
        self.assertTrue(all(a[0] != "uninstall" for a in calls))
        self.assertTrue(r["ok"])
        self.assertCountEqual(r["stopped"], ["com.instagram.android", "com.instagram.androia"])

    def test_stop_stock_always_on_even_when_identity_off(self):
        # Komentarz: stop_stock OFF w identity nie wyłącza force-stop stocka
        calls = []
        dump = (
            "  mResumedActivity: ActivityRecord{abc u0 "
            f"{self._TARGET}/com.instagram.mainactivity.InstagramMainActivity t2}}"
        )
        r = verify_clone_launch(
            "SERIAL1", self._TARGET,
            adb=self._adb(calls=calls, dumpsys=dump, pids={self._TARGET: "1"}),
            wait_s=0,
            installed={"com.instagram.android", self._TARGET},
            safeguards={"stop_stock": False},
        )
        stops = [a[3] for a in calls if a[:3] == ["shell", "am", "force-stop"]]
        self.assertIn("com.instagram.android", stops)
        self.assertTrue(all(a[0] != "uninstall" for a in calls))
        self.assertTrue(r["ok"])

    def test_no_parallel_ig_blocks_start(self):
        calls = []
        dump = (
            "  mResumedActivity: ActivityRecord{x u0 "
            "com.instagram.androia/com.instagram.mainactivity.InstagramMainActivity t1}"
        )
        r = verify_clone_launch(
            "SERIAL1", self._TARGET,
            adb=self._adb(calls=calls, dumpsys=dump),
            wait_s=0,
            installed={"com.instagram.androia", self._TARGET},
            safeguards={"no_parallel_ig": True},
        )
        self.assertFalse(r["ok"])
        self.assertIn("no_parallel_ig", r["error"])
        self.assertIn("androia", r["error"])
        # Komentarz: nie wołamy monkey / am start gdy inny klon trzyma focus
        self.assertFalse(any(a[:2] == ["shell", "monkey"] for a in calls))
        self.assertFalse(any(a[:3] == ["shell", "am", "start"] for a in calls))

    def test_no_parallel_ig_blocks_when_stock_in_focus(self):
        calls = []
        dump = (
            "  mResumedActivity: ActivityRecord{x u0 "
            "com.instagram.android/com.instagram.mainactivity.InstagramMainActivity t1}"
        )
        r = verify_clone_launch(
            "SERIAL1", self._TARGET,
            adb=self._adb(calls=calls, dumpsys=dump),
            wait_s=0,
            installed={"com.instagram.android", self._TARGET},
        )
        self.assertFalse(r["ok"])
        self.assertIn("no_parallel_ig", r["error"])
        self.assertIn("com.instagram.android", r["error"])
        self.assertFalse(any(a[:2] == ["shell", "monkey"] for a in calls))

    def test_sole_ig_pid_waits_then_fails(self):
        calls = []
        r = apply_launch_safeguards(
            "SERIAL1", self._TARGET,
            adb=self._adb(
                calls=calls,
                pids={"com.instagram.androia": "999"},
            ),
            installed={"com.instagram.androia", self._TARGET},
            safeguards={"sole_ig_pid": True},
            wait_s=0,
        )
        self.assertFalse(r["ok"])
        self.assertIn("sole_ig_pid", r["error"])
        self.assertIn("androia", r["error"])

    def test_sole_ig_pid_stock_nie_myli_z_klonem(self):
        # Komentarz: w ps żyje tylko klon-target; stock nie ma wiersza NAME
        r = apply_launch_safeguards(
            "SERIAL1", self._TARGET,
            adb=self._adb(
                calls=[],
                pids={self._TARGET: "77"},
            ),
            installed={"com.instagram.android", self._TARGET},
            wait_s=0,
        )
        self.assertTrue(r["ok"], r)

    def test_create_clone_stops_other_clone_before_verify(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            data = Path(td) / "data"
            ac = Path(td) / "appcloner"
            _write_device(cfg)
            ident = {
                "package": "com.instagram.androiz",
                "android_id": "0123456789abcdef",
                "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
                "mac": "02:11:22:33:44:55",
                "imei": "123456789012347",
                "model": "SM A505FN",
                "safeguards": {"stop_stock": False, "no_parallel_ig": True},
                # Komentarz: OFF jest ignorowany — create i tak zamyka stock
            }
            calls = []

            def run_build(one, logf):
                p = signed_apk_path(ac, one["package"], prefix=one.get("dir_prefix"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"apk")

            def adb(_serial, args):
                calls.append(list(args))
                if args[:3] == ["shell", "pm", "path"]:
                    return f"package:/data/app/{args[3]}/base.apk"
                if args[:2] == ["shell", "pidof"]:
                    # Komentarz: leftover PID innych IG blokuje start (sole_ig_pid)
                    return "9" if args[2:] == ["com.instagram.androiz"] else ""
                if len(args) >= 2 and args[1] == "dumpsys":
                    return (
                        "  mResumedActivity: ActivityRecord{abc u0 "
                        "com.instagram.androiz/com.instagram.mainactivity.InstagramMainActivity t1}"
                    )
                if len(args) >= 2 and args[1] == "logcat":
                    return ""
                return "Success"

            result = create_clone(
                "phone_T", config_root=cfg, data_root=data, appcloner=ac,
                adb=adb,
                list_packages=lambda s: {
                    "com.instagram.android", "com.instagram.androia",
                    "com.instagram.androiz",
                },
                run_build=run_build, acquire_lock=False, identity=ident,
                verify=True, verify_wait_s=0,
            )
            self.assertTrue(result["verified"]["ok"])
            stops = [a[3] for a in calls if a[:3] == ["shell", "am", "force-stop"]]
            self.assertCountEqual(stops, ["com.instagram.android", "com.instagram.androia"])
            self.assertTrue(all(a[0] != "uninstall" for a in calls))
            self.assertIn("safeguards", result["identity"])
            self.assertTrue(result["identity"]["safeguards"]["stop_stock"])

    def test_sanitize_keeps_safeguards(self):
        raw = {
            "package": "com.instagram.androii",
            "android_id": "7f3a8b1c0d9e2468",
            "fingerprint": "samsung/a50/a50:11/x:user/release-keys",
            "model": "OnePlus 8T",
            "mac": "02:11:22:33:44:55",
            "imei": "123456789012347",
            "safeguards": {"stop_stock": False, "sole_ig_pid": False},
        }
        ident = clone_job.sanitize_identity(raw)
        # Komentarz: OFF z panelu nie przechodzi — always-on tylko 1 IG
        self.assertTrue(ident["safeguards"]["stop_stock"])
        self.assertTrue(ident["safeguards"]["sole_ig_pid"])
        self.assertTrue(ident["safeguards"]["kill_before_verify"])
        self.assertTrue(ident["safeguards"]["no_parallel_ig"])


if __name__ == "__main__":
    unittest.main()
