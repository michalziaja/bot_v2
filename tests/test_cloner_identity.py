"""Generatory tożsamości klona — bez telefonu / bez APK."""
from __future__ import annotations

import random
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.cloner.identity import (
    generate_clone_identity,
    imei_is_valid,
    is_laa_unicast_mac,
    next_clone_package,
    random_imei,
    random_mac,
)


class NextPackageTests(unittest.TestCase):
    def test_skips_stock_and_used(self):
        used = {"com.instagram.android", "com.instagram.androia", "com.instagram.androip"}
        pkg = next_clone_package(used)
        self.assertTrue(pkg.startswith("com.instagram.androi"))
        self.assertNotEqual(pkg, "com.instagram.android")
        self.assertNotIn(pkg.lower(), {p.lower() for p in used})

    def test_first_is_androia_when_empty(self):
        self.assertEqual(next_clone_package([]), "com.instagram.androia")


class MacImeiGenTests(unittest.TestCase):
    def test_mac_is_laa_unicast(self):
        rng = random.Random(1)
        for _ in range(20):
            mac = random_mac(rng)
            self.assertTrue(is_laa_unicast_mac(mac), mac)

    def test_imei_luhn(self):
        rng = random.Random(2)
        for _ in range(20):
            imei = random_imei(rng)
            self.assertTrue(imei_is_valid(imei), imei)

    def test_generate_avoids_existing_packages(self):
        device = {
            "model": "SM_A505FN",
            "product": "a50eea",
            "manufacturer": "Samsung",
            "android_version": "11",
            "clones": [
                {"package": "com.instagram.android"},
                {"package": "com.instagram.androia",
                 "identity": {"mac": "02:00:00:00:00:01", "imei": "123456789012347",
                              "android_id": "aaaaaaaaaaaaaaaa"}},
            ],
        }
        ident = generate_clone_identity(device, extra_packages=["com.instagram.androib"],
                                        rng=random.Random(0))
        self.assertNotEqual(ident["package"], "com.instagram.androia")
        self.assertNotEqual(ident["package"], "com.instagram.androib")
        self.assertTrue(ident["package"].startswith("com.instagram.androi"))
        self.assertTrue(is_laa_unicast_mac(ident["mac"]))
        self.assertTrue(imei_is_valid(ident["imei"]))
        self.assertEqual(len(ident["android_id"]), 16)
        # Komentarz: kolizja MAC/IMEI/android-id z clones[] → nowa wartość
        self.assertNotEqual(ident["mac"].lower(), "02:00:00:00:00:01")
        self.assertNotEqual(ident["imei"], "123456789012347")
        self.assertNotEqual(ident["android_id"], "aaaaaaaaaaaaaaaa")


if __name__ == "__main__":
    unittest.main()
