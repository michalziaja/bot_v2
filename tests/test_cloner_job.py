"""Job klona — mock subprocess i adb, bez telefonu."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.cloner import job as clone_job
from v2.cloner.job import (
    CloneBusy, CloneJobError, create_clone, preflight_create, signed_apk_path,
)
from v2.core.device_lock import DeviceLock


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
                p = signed_apk_path(ac, ident["package"])
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
                    p = signed_apk_path(ac, ident["package"])
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


if __name__ == "__main__":
    unittest.main()
