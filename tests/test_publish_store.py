"""Store Treści: nazwa kanoniczna, filtr po device, plugin tylko swój alias."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.data.publish_store import (  # noqa: E402
    PublishStore,
    _query_media_uri,
    _uri_from_insert_out,
    canonical_name,
    list_publish,
    parse_multipart,
    phone_path_for,
    push_to_phone,
    slugify,
)
from v2.plugins.base import Budget  # noqa: E402
from v2.plugins.impl.publish_media import (  # noqa: E402
    PublishMedia,
    _items_for_run,
    _selected_ids,
    send_to_clone,
)
from v2.steps.publish.send_intent import COMP_STORY  # noqa: E402
from v2.runner.step import StepResult  # noqa: E402


def _ok_adb(args, timeout=30.0):
    """Atrapa ADB — sukces, zapisuje wywołania na liście w atrybucie .calls."""
    rec = SimpleNamespace(returncode=0, stdout="ok", stderr="", args=list(args))
    _ok_adb.calls.append(list(args))
    return rec


_ok_adb.calls = []


class MultipartParseTests(unittest.TestCase):
    def test_pola_i_plik(self):
        boundary = "----TestBoundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="account"\r\n\r\n'
            "jobsaver\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="a.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode() + b"abc" + f"\r\n--{boundary}--\r\n".encode()
        fields, upload = parse_multipart(
            f"multipart/form-data; boundary={boundary}", body)
        self.assertEqual(fields["account"], "jobsaver")
        self.assertEqual(upload["filename"], "a.jpg")
        self.assertEqual(upload["blob"], b"abc")


class CanonicalNameTests(unittest.TestCase):
    def test_account_id_slug_ext(self):
        self.assertEqual(
            canonical_name("jobsaver", "p7f3a2", "sunset.jpg", "jpg"),
            "jobsaver__p7f3a2__sunset.jpg",
        )

    def test_slug_wycina_ogonki_i_spacje(self):
        self.assertEqual(slugify("Zachód słońca!!.JPG"), "zachdsoca")
        self.assertEqual(slugify(""), "plik")

    def test_unikaty_id_w_calym_store(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        a = PublishStore("jobsaver", root)
        b = PublishStore("kasia", root)
        r1 = a.add(kind="post", filename="a.jpg", blob=b"aaa")
        r2 = b.add(kind="story", filename="b.jpg", blob=b"bbb")
        self.assertNotEqual(r1["id"], r2["id"])
        self.assertTrue(r1["files"][0]["canonical_name"].startswith("jobsaver__"))
        self.assertTrue(r2["files"][0]["canonical_name"].startswith("kasia__"))
        self.assertIn(r1["id"], r1["files"][0]["canonical_name"])


class FilterDeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.js = PublishStore("jobsaver", self.root)
        self.ka = PublishStore("kasia", self.root)
        self.js.add(kind="post", filename="sun.jpg", blob=b"xx",
                    device_alias="phone_A")
        self.ka.add(kind="story", filename="night.jpg", blob=b"yy",
                    device_alias="galaxy")

    def test_filtr_po_device(self):
        rows = list_publish(self.root, device="phone_A")
        self.assertEqual([r["account"] for r in rows], ["jobsaver"])
        gal = list_publish(self.root, device="galaxy")
        self.assertEqual([r["account"] for r in gal], ["kasia"])

    def test_plugin_widzi_tylko_swoj_alias(self):
        mine = self.js.pending_for_plugin("both")
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["account"], "jobsaver")
        self.assertFalse(any(r["account"] == "kasia" for r in mine))


class AdbPushTests(unittest.TestCase):
    def test_push_do_folderu_konta_i_skan(self):
        _ok_adb.calls = []
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        blob = Path(tmp.name) / "jobsaver__p1__x.jpg"
        blob.write_bytes(b"img")
        item = {
            "account": "jobsaver",
            "kind": "post",
            "files": [{"canonical_name": "jobsaver__p1__x.jpg",
                       "mime": "image/jpeg"}],
        }
        out = push_to_phone(item, serial="SERIAL_A", blob_path=blob,
                            run_adb=_ok_adb)
        self.assertEqual(
            out["phone_path"],
            "/sdcard/Pictures/bot_v2/jobsaver/post/jobsaver__p1__x.jpg",
        )
        joined = [" ".join(c) for c in _ok_adb.calls]
        self.assertTrue(any("push" in c and "SERIAL_A" in c for c in joined))
        self.assertTrue(any("MEDIA_SCANNER_SCAN_FILE" in c for c in joined))
        self.assertTrue(any("content insert" in c or "content" in c for c in joined))
        self.assertFalse(any("DCIM" in c for c in joined))
        self.assertFalse(any(" LIKE " in c for c in joined))
        self.assertTrue(any("_display_name=" in c for c in joined))

    def test_query_po_display_name_nie_po_data(self):
        def fake(args, timeout=15.0):
            fake.calls.append(list(args))
            return SimpleNamespace(
                returncode=0, stdout="Row: 0 _id=217, _data=/x/a.jpg", stderr="")
        fake.calls = []
        uri = _query_media_uri(
            fake, "S",
            "/storage/emulated/0/Pictures/bot_v2/jobsaver/post/jobsaver__p1__x.jpg",
            "content://media/external/images/media",
        )
        self.assertEqual(uri, "content://media/external/images/media/217")
        joined = " ".join(fake.calls[0])
        self.assertIn("_display_name='jobsaver__p1__x.jpg'", joined)
        self.assertNotIn("LIKE", joined)
        # Całe query w jednym argumencie `adb shell` (A11 nie lubi splitu).
        self.assertTrue(any(a.startswith("content query") for a in fake.calls[0]))

    def test_insert_stdout_daje_content_uri(self):
        self.assertEqual(
            _uri_from_insert_out(
                "content://media/external/images/media/214\n",
                "content://media/external/images/media",
            ),
            "content://media/external/images/media/214",
        )


class FakeDevice:
    def __init__(self, serial="SERIAL_A", pkg="com.instagram.androip"):
        self.serial = serial
        self.shells = []
        self._pkg = pkg

    def shell(self, command: str) -> str:
        self.shells.append(command)
        return "ok"

    def current_pkg(self):
        return self._pkg

    def launch_app(self, package):
        self.shells.append(f"launch {package}")

    def force_stop_app(self, package):
        self.shells.append(f"stop {package}")

    def hierarchy(self, force=False):
        return {"properties": {}, "children": []}

    def wait_for_stable_ui(self, **kw):
        return True

    def tap(self, x, y):
        self.shells.append(f"tap {x} {y}")


class FakeEvents:
    def __init__(self, data_root, account_id):
        self.data_root = Path(data_root)
        self.account_id = account_id

    def log(self, kind, **payload):
        return {"kind": kind, **payload}


class FakeCtx:
    def __init__(self, data_root, account_id="jobsaver",
                 pkg="com.instagram.androip"):
        self.account = SimpleNamespace(account_id=account_id)
        self.events = FakeEvents(data_root, account_id)
        self.device = FakeDevice(pkg=pkg)
        self.ig_package = pkg
        self.session_id = "t"
        self.scratch = {}
        self.session_stats = None
        self.logs = []

    def log(self, kind, **payload):
        self.logs.append((kind, payload))


class PluginOnlyOwnAccountTests(unittest.TestCase):
    """Dwa konta na phone_A, mieszana kolejka — sesja jobsaver rusza tylko swoje."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.js = PublishStore("jobsaver", self.root)
        self.other = PublishStore("acc_b", self.root)
        self.mine = self.js.add(
            kind="post", filename="sunset.jpg", blob=b"sun",
            device_alias="phone_A", caption="hej",
        )
        self.theirs = self.other.add(
            kind="post", filename="other.jpg", blob=b"oth",
            device_alias="phone_A",
        )
        # Na telefonie — plugin nie musi wołać prawdziwego ADB.
        self.js.update(
            self.mine["id"], status="on_device",
            phone_path=phone_path_for(
                "jobsaver", "post",
                self.mine["files"][0]["canonical_name"]),
        )
        self.other.update(
            self.theirs["id"], status="on_device",
            phone_path=phone_path_for(
                "acc_b", "post",
                self.theirs["files"][0]["canonical_name"]),
        )

    def test_sesja_jobsaver_nie_rusza_acc_b(self):
        ctx = FakeCtx(self.root, "jobsaver", pkg="com.instagram.androip")
        ok = StepResult.ok(was_running=True, pkg=ctx.ig_package)
        with patch("v2.plugins.impl.publish_media.push_to_phone",
                   return_value={
                       "phone_path": phone_path_for(
                           "jobsaver", "post",
                           self.mine["files"][0]["canonical_name"]),
                       "content_uri": "content://x/1",
                   }), \
             patch("v2.plugins.impl.publish_media._step", return_value=ok):
            res = PublishMedia().run(
                ctx, {"item_ids": [self.mine["id"]]}, Budget(max_items=5),
            )
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.stats.get("published"), 1)
        mine = self.js.get(self.mine["id"])
        theirs = self.other.get(self.theirs["id"])
        self.assertEqual(mine["status"], "published")
        self.assertEqual(theirs["status"], "on_device")
        cmd = " ".join(ctx.device.shells)
        self.assertIn("android.intent.action.SEND", cmd)
        self.assertIn("com.instagram.androip", cmd)
        self.assertIn("content://x/1", cmd)
        self.assertIn(self.mine["files"][0]["canonical_name"], mine["phone_path"])
        self.assertNotIn("DCIM", cmd)
        self.assertNotIn(self.theirs["files"][0]["canonical_name"], cmd)

    def test_intent_do_pakietu_klona_nie_dcim(self):
        ctx = FakeCtx(self.root, "jobsaver", pkg="com.instagram.androjb")
        item = self.js.get(self.mine["id"])
        self.assertTrue(send_to_clone(ctx, item))
        cmd = ctx.device.shells[-1]
        self.assertIn(
            "-n com.instagram.androjb/com.instagram.share.handleractivity.ShareHandlerActivity",
            cmd)
        self.assertIn("android.intent.action.SEND", cmd)
        self.assertNotIn("DCIM", cmd)


class PluginReadyPushThenCreatorTests(unittest.TestCase):
    """Happy path: ready na PC, bez ręcznego Push — plugin: push, potem creator."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.js = PublishStore("jobsaver", self.root)
        self.other = PublishStore("acc_b", self.root)
        # Oba konta na tym samym telefonie — sesja widzi tylko swój alias.
        self.mine = self.js.add(
            kind="post", filename="sunset.jpg", blob=b"sun",
            device_alias="phone_A", caption="hej",
        )
        self.theirs = self.other.add(
            kind="post", filename="other.jpg", blob=b"oth",
            device_alias="phone_A",
        )
        self.assertEqual(self.mine["status"], "ready")
        self.assertIsNone(self.mine.get("phone_path"))
        self.assertEqual(self.theirs["status"], "ready")

    def test_ready_bez_wczesniejszego_push_kolejnosc(self):
        ctx = FakeCtx(self.root, "jobsaver", pkg="com.instagram.androip")
        order = []

        def fake_push(item, *, serial, blob_path, run_adb=None):
            # Intent jeszcze nie poszedł — plik najpierw na telefon.
            self.assertEqual(ctx.device.shells, [])
            order.append("push")
            self.assertEqual(item.get("account"), "jobsaver")
            cname = item["files"][0]["canonical_name"]
            return {
                "phone_path": phone_path_for("jobsaver", "post", cname),
                "content_uri": "content://media/external/images/media/42",
            }

        def fake_step(ctx_in, name, params, source=None):
            order.append(name)
            return StepResult.ok(was_running=True, pkg=ctx_in.ig_package)

        with patch("v2.plugins.impl.publish_media.push_to_phone",
                   side_effect=fake_push), \
             patch("v2.plugins.impl.publish_media._step",
                   side_effect=fake_step):
            res = PublishMedia().run(
                ctx, {"item_ids": [self.mine["id"]]}, Budget(max_items=5),
            )
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.stats.get("pushed"), 1)
        self.assertEqual(res.stats.get("published"), 1)
        self.assertIn("push", order)
        self.assertIn("publish.drive_creator", order)
        self.assertLess(order.index("push"),
                        order.index("publish.drive_creator"))
        mine = self.js.get(self.mine["id"])
        theirs = self.other.get(self.theirs["id"])
        self.assertEqual(mine["status"], "published")
        self.assertEqual(theirs["status"], "ready")
        self.assertIsNone(theirs.get("phone_path"))
        cmd = " ".join(ctx.device.shells)
        self.assertIn("android.intent.action.SEND", cmd)
        self.assertIn(self.mine["files"][0]["canonical_name"], mine["phone_path"])
        self.assertNotIn(self.theirs["files"][0]["canonical_name"], cmd)
        self.assertNotIn("DCIM", cmd)

    def test_mieszane_konta_ready_tylko_alias_sesji(self):
        ctx = FakeCtx(self.root, "jobsaver")
        pushed_accounts = []

        def fake_push(item, *, serial, blob_path, run_adb=None):
            pushed_accounts.append(item.get("account"))
            cname = item["files"][0]["canonical_name"]
            return {
                "phone_path": phone_path_for(item["account"], "post", cname),
                "content_uri": "content://x/1",
            }

        ok = StepResult.ok(was_running=True)
        with patch("v2.plugins.impl.publish_media.push_to_phone",
                   side_effect=fake_push), \
             patch("v2.plugins.impl.publish_media._step", return_value=ok):
            PublishMedia().run(
                ctx, {"item_ids": [self.mine["id"]]}, Budget(max_items=9))
        self.assertEqual(pushed_accounts, ["jobsaver"])
        self.assertEqual(self.other.get(self.theirs["id"])["status"], "ready")

    def test_stop_before_share_nie_oznacza_published(self):
        ctx = FakeCtx(self.root, "jobsaver")

        def fake_push(item, *, serial, blob_path, run_adb=None):
            cname = item["files"][0]["canonical_name"]
            return {
                "phone_path": phone_path_for("jobsaver", "post", cname),
                "content_uri": "content://x/9",
            }

        ok = StepResult.ok(screen="editor")
        with patch("v2.plugins.impl.publish_media.push_to_phone",
                   side_effect=fake_push), \
             patch("v2.plugins.impl.publish_media._step", return_value=ok):
            res = PublishMedia().run(
                ctx, {"item_ids": [self.mine["id"]],
                      "stop_before_share": True},
                Budget(max_items=1),
            )
        self.assertEqual(res.stats.get("dry_ok"), 1)
        self.assertEqual(res.stats.get("published"), 0)
        self.assertEqual(self.js.get(self.mine["id"])["status"], "on_device")
        self.assertEqual(self.other.get(self.theirs["id"])["status"], "ready")

    def test_puste_zaznaczenie_nie_zgaduje_ready(self):
        ctx = FakeCtx(self.root, "jobsaver")
        extra = self.js.add(kind="post", filename="drugi.jpg", blob=b"dd")
        res = PublishMedia().run(ctx, {"kind": "both"}, Budget(max_items=5))
        self.assertEqual(res.status, "failed")
        self.assertIn("nie wybrano", (res.error or "").lower())
        self.assertEqual(self.js.get(self.mine["id"])["status"], "ready")
        self.assertEqual(self.js.get(extra["id"])["status"], "ready")
        self.assertEqual(self.other.get(self.theirs["id"])["status"], "ready")

    def test_tylko_wybrany_id_inny_ready_nietkniety(self):
        ctx = FakeCtx(self.root, "jobsaver")
        extra = self.js.add(kind="story", filename="rel.jpg", blob=b"rr")
        order = []

        def fake_push(item, *, serial, blob_path, run_adb=None):
            order.append("push")
            cname = item["files"][0]["canonical_name"]
            return {
                "phone_path": phone_path_for("jobsaver", "post", cname),
                "content_uri": "content://x/2",
            }

        def fake_step(ctx_in, name, params, source=None):
            order.append(name)
            if name == "publish.drive_creator":
                self.assertFalse(params.get("stop_before_share"))
            return StepResult.ok()

        with patch("v2.plugins.impl.publish_media.push_to_phone",
                   side_effect=fake_push), \
             patch("v2.plugins.impl.publish_media._step",
                   side_effect=fake_step):
            res = PublishMedia().run(
                ctx, {"item_ids": [self.mine["id"]]}, Budget(max_items=9),
            )
        self.assertEqual(res.stats.get("published"), 1)
        self.assertEqual(self.js.get(self.mine["id"])["status"], "published")
        self.assertEqual(self.js.get(extra["id"])["status"], "ready")
        self.assertLess(order.index("push"),
                        order.index("publish.drive_creator"))
        self.assertEqual(_selected_ids({"item_id": extra["id"]}), [extra["id"]])
        picked = _items_for_run(self.js, {"item_ids": [self.mine["id"]]})
        self.assertEqual([p["id"] for p in picked], [self.mine["id"]])

    def test_intent_story_handler(self):
        ctx = FakeCtx(self.root, "jobsaver", pkg="com.instagram.androip")
        item = {
            "kind": "story",
            "account": "jobsaver",
            "phone_path": "/sdcard/Pictures/bot_v2/jobsaver/story/x.jpg",
            "content_uri": "content://media/external/images/media/9",
            "files": [{"canonical_name": "jobsaver__p1__x.jpg",
                       "mime": "image/jpeg"}],
        }
        self.assertTrue(send_to_clone(ctx, item))
        cmd = ctx.device.shells[-1]
        self.assertIn(COMP_STORY, cmd)
        self.assertIn("content://media/external/images/media/9", cmd)


if __name__ == "__main__":
    unittest.main()
