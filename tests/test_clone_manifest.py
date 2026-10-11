"""Sanitizer splitów AAB w AndroidManifest — bez telefonu / bez apktool."""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
# Komentarz: skrypt joba to appcloner/clone_instagram.py (nie pakiet v2)
sys.path.insert(0, str(ROOT / "appcloner"))
import clone_instagram as clone_ig  # noqa: E402

# Komentarz: mini manifest jak z apktool d na split/AAB IG
FAKE_MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" android:compileSdkVersion="37" android:isSplitRequired="true" android:requiredSplitTypes="base__density" android:splitTypes="" android:isolatedSplits="true" package="com.instagram.android">
    <uses-permission android:name="android.permission.INTERNET"/>
    <application android:name="com.instagram.app.InstagramAppShell" android:isFeatureSplit="true">
        <meta-data android:name="com.android.vending.splits.required" android:value="true"/>
        <meta-data android:name="com.android.vending.splits" android:resource="@xml/splits0"/>
        <meta-data android:name="com.android.vending.derived.apk.id" android:value="1"/>
        <meta-data android:name="keep.me" android:value="ok"/>
    </application>
</manifest>
"""


class SanitizeSplitManifestTests(unittest.TestCase):
    def test_strips_split_attrs_and_play_metadata(self):
        xml, removed = clone_ig.sanitize_split_manifest(FAKE_MANIFEST)
        self.assertIn('package="com.instagram.android"', xml)
        self.assertNotIn("isSplitRequired", xml)
        self.assertNotIn("requiredSplitTypes", xml)
        self.assertNotIn("splitTypes", xml)
        self.assertNotIn("isFeatureSplit", xml)
        self.assertNotIn("isolatedSplits", xml)
        self.assertNotIn("com.android.vending.splits", xml)
        self.assertNotIn("com.android.vending.derived.apk.id", xml)
        self.assertIn('android:name="keep.me"', xml)
        self.assertIn("android:isSplitRequired", removed)
        self.assertIn("android:requiredSplitTypes", removed)
        self.assertIn("android:splitTypes", removed)
        self.assertIn("android:isFeatureSplit", removed)
        self.assertIn("android:isolatedSplits", removed)
        self.assertTrue(any("vending.splits" in x for x in removed))

    def test_strips_play_metadata_open_and_close_tags(self):
        # Komentarz: tak wygląda meta-data po decode_axml (nie self-closing)
        xml_in = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.instagram.android">
    <application>
        <meta-data android:name="com.android.vending.splits.required" android:value="true">
        </meta-data>
        <meta-data android:name="com.android.vending.splits" android:resource="@7f17000a">
        </meta-data>
        <meta-data android:name="com.android.vending.derived.apk.id" android:value="1">
        </meta-data>
        <meta-data android:name="keep.me" android:value="ok">
        </meta-data>
    </application>
</manifest>
"""
        xml, removed = clone_ig.sanitize_split_manifest(xml_in)
        self.assertNotIn("com.android.vending.splits", xml)
        self.assertNotIn("com.android.vending.derived.apk.id", xml)
        self.assertIn('android:name="keep.me"', xml)
        self.assertTrue(any("vending.splits" in x for x in removed))

    def test_patch_manifest_renames_package_and_strips_splits(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "AndroidManifest.xml"
            path.write_text(FAKE_MANIFEST, encoding="utf-8")
            clone_ig.patch_manifest(path, "com.instagram.androig")
            text = path.read_text(encoding="utf-8")
        self.assertIn('package="com.instagram.androig"', text)
        self.assertNotIn('package="com.instagram.android"', text)
        self.assertNotIn("isSplitRequired", text)
        self.assertNotIn("requiredSplitTypes", text)
        self.assertNotIn("com.android.vending.splits", text)
        self.assertIn("keep.me", text)


class ApktoolDecodeFlagsTests(unittest.TestCase):
    """Decode przez java -jar apktool.jar d --no-res (nie wrapper PATH)."""

    def test_decode_args_uses_no_res(self):
        # Komentarz: --no-res tylko przy `d`; `b --no-res` w 3.0.3 nie istnieje
        args = clone_ig.apktool_decode_args(
            Path("instagram.apk"), Path("/tmp/instagram_src")
        )
        self.assertEqual(args[0], "d")
        self.assertIn("--no-res", args)
        self.assertIn("-f", args)
        self.assertIn("instagram.apk", args)

    def test_unsigned_apk_name_matches_manual_build(self):
        # Komentarz: `java -Xmx6g -jar apktool.jar b … -o instagram_unsigned.apk`
        self.assertEqual(clone_ig.UNSIGNED_APK_NAME, "instagram_unsigned.apk")
        self.assertEqual(clone_ig.APKTOOL_BUILD_HEAP, "-Xmx6g")

    def test_apktool_cmd_uses_java_jar_and_heap(self):
        fake_jar = Path("/tmp/apktool.jar")
        with patch.object(clone_ig, "java_bin", return_value="java"), patch.object(
            clone_ig, "apktool_jar", return_value=fake_jar
        ):
            decode = clone_ig.apktool_cmd(
                Path("/tmp"), *clone_ig.apktool_decode_args(
                    Path("instagram.apk"), Path("/tmp/src")
                )
            )
            build = clone_ig.apktool_cmd(
                Path("/tmp"),
                "b",
                "/tmp/src",
                "-o",
                "/tmp/instagram_unsigned.apk",
            )
        self.assertEqual(decode[0], "java")
        self.assertIn("-Xmx6g", decode)
        self.assertIn("-jar", decode)
        self.assertIn("--no-res", decode)
        self.assertNotEqual(decode[0], "apktool")
        self.assertIn("-Xmx6g", build)
        self.assertIn("b", build)
        self.assertNotIn("--no-res", build)

    def test_sign_apk_uses_long_apk_flag(self):
        # Komentarz: `java -jar uber-apk-signer --apk <unsigned>`
        captured: list[list[str]] = []

        def run_and_write(cmd, cwd=None):
            captured.append(list(cmd))
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "instagram_unsigned-aligned-debugSigned.apk").write_bytes(b"signed")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            unsigned = root / "instagram_unsigned.apk"
            unsigned.write_bytes(b"apk")
            dest = root / "com.instagram.androii.apk"
            with patch.object(clone_ig, "java_bin", return_value="java"), patch.object(
                clone_ig, "signer_jar", return_value=root / "uber-apk-signer-1.3.0.jar"
            ), patch.object(clone_ig, "run", side_effect=run_and_write):
                clone_ig.sign_apk(root, unsigned, dest)
            self.assertTrue(captured)
            self.assertIn("--apk", captured[0])
            self.assertNotIn("-a", captured[0])
            self.assertTrue(dest.is_file())

    def test_sanitize_split_still_exported(self):
        xml, removed = clone_ig.sanitize_split_manifest(FAKE_MANIFEST)
        self.assertTrue(removed)
        self.assertNotIn("isSplitRequired", xml)

    def test_axml_decode_instagram_apk_manifest(self):
        # Komentarz: prawdziwy AXML z base.apk — offset atrybutów musi być pos+16+attrStart
        from axml import decode_axml, is_binary_axml
        apk = ROOT / "appcloner" / "instagram.apk"
        if not apk.is_file():
            self.skipTest("brak instagram.apk")
        with zipfile.ZipFile(apk) as z:
            data = z.read("AndroidManifest.xml")
        self.assertTrue(is_binary_axml(data))
        xml = decode_axml(data)
        self.assertIn('package="com.instagram.android"', xml)
        self.assertIn("android.permission.INTERNET", xml)


class ApkManifestPackTests(unittest.TestCase):
    """apktool b (no-res) gubi tekstowy manifest — pakujemy binarny AXML."""

    MINI_XML = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="com.instagram.androii" android:versionCode="1">\n'
        '    <application android:name="com.instagram.app.InstagramAppShell">\n'
        '        <meta-data android:name="keep.me" android:value="ok"/>\n'
        "    </application>\n"
        "</manifest>\n"
    )

    def test_axml_encode_decode_keeps_package(self):
        # Komentarz: mini XML z package= — bez pełnego rebuild 135 MB
        from axml import decode_axml, encode_axml, is_binary_axml

        raw = encode_axml(self.MINI_XML)
        self.assertTrue(is_binary_axml(raw))
        xml = decode_axml(raw)
        self.assertIn('package="com.instagram.androii"', xml)
        self.assertIn("keep.me", xml)
        self.assertIn("com.instagram.app.InstagramAppShell", xml)

    def test_fake_zip_must_contain_android_manifest(self):
        # Komentarz: helper „APK musi zawierać AndroidManifest.xml”
        with tempfile.TemporaryDirectory() as td:
            apk = Path(td) / "fake.apk"
            with zipfile.ZipFile(apk, "w") as zf:
                zf.writestr("classes.dex", b"dex")
            self.assertFalse(clone_ig.apk_has_android_manifest(apk))
            from axml import encode_axml, is_binary_axml

            axml = encode_axml(self.MINI_XML)
            clone_ig.inject_android_manifest(apk, axml)
            self.assertTrue(clone_ig.apk_has_android_manifest(apk))
            with zipfile.ZipFile(apk) as zf:
                names = zf.namelist()
                data = zf.read("AndroidManifest.xml")
                info = zf.getinfo("AndroidManifest.xml")
            self.assertEqual(names[0], "AndroidManifest.xml")
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            self.assertTrue(is_binary_axml(data))
            self.assertIn("classes.dex", names)

    def test_ensure_injects_when_manifest_missing(self):
        from axml import decode_axml, encode_axml

        axml = encode_axml(self.MINI_XML)
        with tempfile.TemporaryDirectory() as td:
            apk = Path(td) / "instagram_unsigned.apk"
            with zipfile.ZipFile(apk, "w") as zf:
                zf.writestr("resources.arsc", b"arsc")
            clone_ig.ensure_unsigned_apk_manifest(
                apk, axml, "com.instagram.androii"
            )
            with zipfile.ZipFile(apk) as zf:
                xml = decode_axml(zf.read("AndroidManifest.xml"))
            self.assertIn('package="com.instagram.androii"', xml)

    def test_compile_patched_manifest_overwrites_original(self):
        # Komentarz: original/ dostaje zpatchowany AXML, nie stock IG
        from axml import decode_axml, is_binary_axml

        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "original").mkdir()
            manifest = src / "AndroidManifest.xml"
            manifest.write_text(self.MINI_XML, encoding="utf-8")
            (src / "original" / "AndroidManifest.xml").write_bytes(b"stock")
            raw = clone_ig.compile_patched_manifest(manifest)
            self.assertTrue(is_binary_axml(raw))
            self.assertTrue(is_binary_axml(manifest.read_bytes()))
            orig = (src / "original" / "AndroidManifest.xml").read_bytes()
            self.assertEqual(orig, raw)
            self.assertIn('package="com.instagram.androii"', decode_axml(orig))


class AxmlMetaDataRoundTripTests(unittest.TestCase):
    """encode_axml musi zachować value/resource tak, by PackageManager je widział."""

    META_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.instagram.androii">
    <application android:name="com.instagram.app.InstagramAppShell">
        <meta-data android:name="keep.string" android:value="ok"/>
        <meta-data android:name="keep.bool" android:value="false"/>
        <meta-data android:name="keep.emptyish" android:value="0"/>
        <meta-data android:name="keep.res.hex" android:resource="@7f170009"/>
        <meta-data android:name="keep.res.sym" android:resource="@string/x"/>
    </application>
</manifest>
"""

    def test_attr_ids_match_framework_value_and_resource(self):
        # Komentarz: PM szuka 0x01010024 / 0x01010025, nie nazwy „value”
        from axml import (
            ANDROID_ATTR_RESOURCE,
            ANDROID_ATTR_VALUE,
            axml_resource_map,
            encode_axml,
        )

        raw = encode_axml(self.META_XML)
        mapped = dict(axml_resource_map(raw))
        self.assertEqual(mapped.get("value"), ANDROID_ATTR_VALUE)
        self.assertEqual(mapped.get("resource"), ANDROID_ATTR_RESOURCE)
        self.assertEqual(ANDROID_ATTR_VALUE, 0x01010024)
        self.assertEqual(ANDROID_ATTR_RESOURCE, 0x01010025)

    def test_encode_decode_preserves_meta_value_and_resource(self):
        # Komentarz: string / typowany value / @hex / @string/x muszą wrócić
        from axml import decode_axml, encode_axml

        xml = decode_axml(encode_axml(self.META_XML))
        self.assertIn('android:name="keep.string"', xml)
        self.assertIn('android:value="ok"', xml)
        self.assertIn('android:name="keep.bool"', xml)
        self.assertIn('android:value="false"', xml)
        self.assertIn('android:name="keep.emptyish"', xml)
        self.assertIn('android:value="0"', xml)
        self.assertIn('android:name="keep.res.hex"', xml)
        self.assertIn('android:resource="@7f170009"', xml)
        self.assertIn('android:name="keep.res.sym"', xml)
        self.assertIn('android:resource="@string/x"', xml)

    def test_every_meta_data_has_value_or_resource_for_pm(self):
        # Komentarz: jak PackageManager — atrybut musi mieć ID value albo resource
        from axml import (
            ANDROID_ATTR_RESOURCE,
            ANDROID_ATTR_VALUE,
            decode_axml,
            encode_axml,
        )

        from axml import axml_resource_map

        raw = encode_axml(self.META_XML)
        xml = decode_axml(raw)
        tags = re.findall(r"<meta-data\b[^>]*/?>", xml)
        self.assertEqual(len(tags), 5)
        for tag in tags:
            has_val = "android:value=" in tag
            has_res = "android:resource=" in tag
            self.assertTrue(has_val or has_res, tag)
        mapped = dict(axml_resource_map(raw))
        self.assertEqual(mapped["value"], ANDROID_ATTR_VALUE)
        self.assertEqual(mapped["resource"], ANDROID_ATTR_RESOURCE)

    def test_typed_bool_survives_as_non_string(self):
        # Komentarz: android:value="false" to TYPE_INT_BOOLEAN, nie pusty string
        from axml import TYPE_INT_BOOLEAN, TYPE_INT_DEC, TYPE_REFERENCE, encode_axml
        from axml import RES_XML_START_ELEMENT, _StringPool, _i32, _u16, _u32

        raw = encode_axml(self.META_XML)
        file_size = min(_u32(raw, 4), len(raw))
        pool = None
        pos = 8
        found: dict[str, int] = {}
        while pos + 8 <= file_size:
            typ = _u16(raw, pos)
            size = _u32(raw, pos + 4)
            if size < 8 or pos + size > len(raw):
                break
            if typ == 0x0001:
                pool = _StringPool(raw, pos)
            elif typ == RES_XML_START_ELEMENT and pool is not None:
                name = pool.get(_i32(raw, pos + 20))
                if name == "meta-data":
                    attr_start = _u16(raw, pos + 24)
                    attr_size = _u16(raw, pos + 26)
                    attr_count = _u16(raw, pos + 28)
                    attr_off = pos + 16 + attr_start
                    md_name = ""
                    md_type = None
                    for i in range(attr_count):
                        a = attr_off + i * max(attr_size, 20)
                        key = pool.get(_i32(raw, a + 4))
                        packed = _u32(raw, a + 12)
                        datatype = (packed >> 24) & 0xFF
                        raw_str = _i32(raw, a + 8)
                        if key == "name":
                            md_name = pool.get(raw_str if raw_str >= 0 else _u32(raw, a + 16))
                        if key in ("value", "resource"):
                            md_type = datatype
                    if md_name:
                        found[md_name] = md_type
            pos += size
        self.assertEqual(found.get("keep.bool"), TYPE_INT_BOOLEAN)
        self.assertEqual(found.get("keep.emptyish"), TYPE_INT_DEC)
        self.assertEqual(found.get("keep.res.hex"), TYPE_REFERENCE)


class ResolveInputApkTests(unittest.TestCase):
    """Sama nazwa pliku z panelu → appcloner/apks/, nie appcloner/."""

    def test_bare_filename_uses_apks_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dest = root / "apks" / "instagram-444.0.0.46.85.apk"
            dest.parent.mkdir()
            dest.write_bytes(b"PK")
            found = clone_ig.resolve_input_apk(root, Path("instagram-444.0.0.46.85.apk"))
            self.assertEqual(found.resolve(), dest.resolve())

    def test_relative_apks_path_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dest = root / "apks" / "instagram-444.0.0.46.85.apk"
            dest.parent.mkdir()
            dest.write_bytes(b"PK")
            found = clone_ig.resolve_input_apk(
                root, Path("apks/instagram-444.0.0.46.85.apk"))
            self.assertEqual(found.resolve(), dest.resolve())


class ParseArgsTests(unittest.TestCase):
    """CLI: --force musi być w parserze (panel i ręczny clone)."""

    def test_force_flag_is_recognized(self):
        # Komentarz: mock argv — bez prawdziwego apktool d
        argv = [
            "clone_instagram.py",
            "--package",
            "com.instagram.androii",
            "--android-id",
            "7f3a8b1c0d9e2468",
            "--fingerprint",
            "samsung/a54xeea/a54x:14/x:user/release-keys",
            "--force",
            "--build",
            "--sign",
            "--mac",
            "02:00:00:00:00:01",
            "--imei",
            "123456789012347",
            "--skip-decompile",
        ]
        with patch.object(sys, "argv", argv):
            args = clone_ig.parse_args()
        self.assertTrue(args.force)
        self.assertTrue(args.build)
        self.assertTrue(args.sign)
        self.assertTrue(args.skip_decompile)
        self.assertEqual(args.mac, "02:00:00:00:00:01")
        self.assertEqual(args.imei, "123456789012347")

    def test_force_defaults_to_false(self):
        # Komentarz: bez --force Namespace.force musi istnieć (store_true → False)
        argv = [
            "clone_instagram.py",
            "--package",
            "com.instagram.androii",
            "--android-id",
            "7f3a8b1c0d9e2468",
            "--fingerprint",
            "samsung/a54xeea/a54x:14/x:user/release-keys",
        ]
        with patch.object(sys, "argv", argv):
            args = clone_ig.parse_args()
        self.assertFalse(args.force)


class ArscSplitMergeTests(unittest.TestCase):
    """Merge density: TYPE_STRING z overlayu musi być widoczny pod tym samym ID."""

    def _mini_table(self, path_str: str, *, type_id: int = 8, entry_id: int = 0):
        """Minimalny ResTable z jednym drawable (string → ścieżka pliku)."""
        import struct
        from arsc_merge import (
            RES_TABLE,
            RES_TABLE_PACKAGE,
            RES_TABLE_TYPE,
            RES_TABLE_TYPE_SPEC,
            TYPE_STRING,
            build_utf8_string_pool,
        )

        gpool = build_utf8_string_pool([path_str])
        tpool = build_utf8_string_pool(["drawable"])
        kpool = build_utf8_string_pool(["icon"])
        entry_count = entry_id + 1
        # TYPE SPEC
        spec = bytearray(16 + 4 * entry_count)
        struct.pack_into("<HHI", spec, 0, RES_TABLE_TYPE_SPEC, 16, len(spec))
        spec[8] = type_id
        struct.pack_into("<I", spec, 12, entry_count)
        # TYPE + config 64 + offsety + prosty wpis
        header_size = 84
        entries_start = header_size + 4 * entry_count
        entry_blob = struct.pack("<HHI", 8, 0, 0) + struct.pack(
            "<HBBI", 8, 0, TYPE_STRING, 0
        )
        chunk_size = entries_start + len(entry_blob)
        typ = bytearray(chunk_size)
        struct.pack_into("<HHI", typ, 0, RES_TABLE_TYPE, header_size, chunk_size)
        typ[8] = type_id
        struct.pack_into("<I", typ, 12, entry_count)
        struct.pack_into("<I", typ, 16, entries_start)
        struct.pack_into("<I", typ, 20, 64)
        for i in range(entry_count):
            struct.pack_into(
                "<I", typ, header_size + i * 4,
                0xFFFFFFFF if i != entry_id else 0,
            )
        typ[entries_start:] = entry_blob
        inner = tpool + kpool + bytes(spec) + bytes(typ)
        pkg = bytearray(288)
        name = "pkg".encode("utf-16-le").ljust(256, b"\x00")
        struct.pack_into("<HHI", pkg, 0, RES_TABLE_PACKAGE, 288, 288 + len(inner))
        struct.pack_into("<I", pkg, 8, 0x7F)
        pkg[12:268] = name
        struct.pack_into("<I", pkg, 268, 288)
        struct.pack_into("<I", pkg, 276, 288 + len(tpool))
        pkg_blob = bytes(pkg) + inner
        body = gpool + pkg_blob
        header = struct.pack("<HHII", RES_TABLE, 12, 12 + len(body), 1)
        return header + body

    def test_merge_exposes_overlay_string_path(self):
        from arsc_merge import arsc_entry_values, merge_arsc_tables

        resid = 0x7F080002
        base = self._mini_table("res/base.png", entry_id=0)
        over = self._mini_table("res/s8z.png", entry_id=2)
        merged = merge_arsc_tables(base, over)
        vals = arsc_entry_values(merged, resid)
        self.assertTrue(vals)
        self.assertEqual(vals[-1].get("string"), "res/s8z.png")

    def test_overlay_adds_res_file_to_clone_zip(self):
        from arsc_merge import SPLASH_DRAWABLE_ID, overlay_splits_onto_apk

        resid = SPLASH_DRAWABLE_ID
        entry = resid & 0xFFFF
        base_arsc = self._mini_table("res/base.png", entry_id=0)
        over_arsc = self._mini_table("res/s8z.png", entry_id=entry)
        with tempfile.TemporaryDirectory() as td:
            clone = Path(td) / "clone.apk"
            split = Path(td) / "split_config.xxhdpi.apk"
            with zipfile.ZipFile(clone, "w") as zf:
                zf.writestr("resources.arsc", base_arsc)
                zf.writestr("classes.dex", b"dex")
            with zipfile.ZipFile(split, "w") as zf:
                zf.writestr("resources.arsc", over_arsc)
                zf.writestr("res/s8z.png", b"png")
            self.assertTrue(overlay_splits_onto_apk(clone, [split]))
            with zipfile.ZipFile(clone) as zf:
                self.assertIn("res/s8z.png", zf.namelist())
                self.assertEqual(zf.read("res/s8z.png"), b"png")
                arsc = zf.read("resources.arsc")
            from arsc_merge import arsc_has_entry_value

            self.assertTrue(arsc_has_entry_value(arsc, resid))


if __name__ == "__main__":
    unittest.main()
