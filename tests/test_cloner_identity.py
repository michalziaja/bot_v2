"""Generatory tożsamości klona — bez telefonu / bez APK."""
from __future__ import annotations

import random
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from v2.cloner.identity import (
    DEFAULT_SAFEGUARDS,
    PL_OPERATORS,
    SPOOF_PROFILES,
    clone_occupancy,
    fingerprint_for_profile,
    generate_clone_identity,
    imei_is_valid,
    is_clone_package,
    is_ig_package,
    is_laa_unicast_mac,
    list_spoof_profiles,
    next_clone_package,
    next_clone_packages,
    normalize_safeguards,
    profile_by_model,
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

    def test_occupancy_adb_ok_zwalnia_stary_androia(self):
        # Komentarz: clones[] ma androia, telefon już nie — nazwa znowu wolna
        device = {
            "clones": [
                {"package": "com.instagram.android"},
                {"package": "com.instagram.androia"},
            ],
        }
        occ = clone_occupancy(
            device,
            ["com.instagram.android", "com.chrome"],
            adb_ok=True,
        )
        self.assertIn("com.instagram.android", occ)
        self.assertNotIn("com.instagram.androia", occ)
        self.assertEqual(next_clone_package(occ), "com.instagram.androia")
        ident = generate_clone_identity(
            device, occupied_packages=occ, rng=random.Random(0))
        self.assertEqual(ident["package"], "com.instagram.androia")

    def test_occupancy_bez_adb_zostawia_clones(self):
        device = {"clones": [{"package": "com.instagram.androia"}]}
        occ = clone_occupancy(device, (), adb_ok=False)
        self.assertIn("com.instagram.androia", occ)
        self.assertNotEqual(next_clone_package(occ), "com.instagram.androia")

    def test_occupancy_held_packages_rezerwuje_androia(self):
        # Komentarz: konto trzyma APPID — nawet przy żywym pm list bez tego pakietu
        device = {
            "clones": [
                {"package": "com.instagram.android"},
                {"package": "com.instagram.androia"},
            ],
        }
        occ = clone_occupancy(
            device,
            ["com.instagram.android", "com.chrome"],
            adb_ok=True,
            held_packages=["com.instagram.androia"],
        )
        self.assertIn("com.instagram.androia", occ)
        self.assertIn("com.instagram.android", occ)
        self.assertNotEqual(next_clone_package(occ), "com.instagram.androia")
        ident = generate_clone_identity(
            device, occupied_packages=occ, rng=random.Random(0))
        self.assertNotEqual(ident["package"], "com.instagram.androia")

    def test_next_clone_packages_skips_stock(self):
        pkgs = next_clone_packages([], 4)
        self.assertEqual(pkgs[0], "com.instagram.androia")
        self.assertNotIn("com.instagram.android", pkgs)
        self.assertEqual(len(pkgs), 4)

    def test_is_clone_package_never_stock(self):
        self.assertFalse(is_clone_package("com.instagram.android"))
        self.assertFalse(is_clone_package("COM.INSTAGRAM.ANDROID"))
        self.assertTrue(is_clone_package("com.instagram.androia"))
        self.assertFalse(is_clone_package("com.whatsapp"))
        self.assertFalse(is_clone_package(""))

    def test_is_ig_package_stock_and_clones(self):
        # Komentarz: stock też jest IG — force-stop dotyczy obu
        self.assertTrue(is_ig_package("com.instagram.android"))
        self.assertTrue(is_ig_package("COM.INSTAGRAM.ANDROID"))
        self.assertTrue(is_ig_package("com.instagram.androia"))
        self.assertFalse(is_ig_package("com.instagram.lite"))
        self.assertFalse(is_ig_package("com.whatsapp"))
        self.assertFalse(is_ig_package(""))


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
        # Komentarz: model i operator z puli rotacji, nie sztywny A54/Play
        models = {p["model"] for p in SPOOF_PROFILES}
        ops = {o["sim_operator"] for o in PL_OPERATORS}
        self.assertIn(ident["model"], models)
        self.assertIn(ident["sim_operator"], ops)
        self.assertTrue(ident["gaid"])
        self.assertTrue(ident["media_drm_id"])
        self.assertTrue(ident["build_time"])
        self.assertTrue(ident["boot_time"])
        self.assertIsInstance(ident["gps_lat"], float)
        self.assertIsInstance(ident["gps_lon"], float)
        self.assertEqual(ident["sim_country"], "PL")
        self.assertTrue(ident["sim_operator_code"])
        self.assertIn(ident["model"].split()[0].lower(), ident["fingerprint"])

    def test_rotates_model_and_operator_away_from_used(self):
        """Kolejny klon omija model i operator już zapisane w clones[]."""
        used_model = SPOOF_PROFILES[0]["model"]
        used_op = PL_OPERATORS[0]["sim_operator"]
        device = {
            "clones": [
                {"package": "com.instagram.androia",
                 "identity": {
                     "mac": "02:00:00:00:00:01",
                     "imei": "123456789012347",
                     "android_id": "aaaaaaaaaaaaaaaa",
                     "model": used_model,
                     "sim_operator": used_op,
                     "sim_operator_code": PL_OPERATORS[0]["sim_operator_code"],
                 }},
            ],
        }
        ident = generate_clone_identity(device, rng=random.Random(3))
        self.assertNotEqual(ident["model"], used_model)
        self.assertNotEqual(ident["sim_operator"], used_op)
        self.assertIn(ident["model"], {p["model"] for p in SPOOF_PROFILES})
        self.assertIn(ident["sim_operator"], {o["sim_operator"] for o in PL_OPERATORS})


class SpoofProfileAndSafeguardTests(unittest.TestCase):
    """Lista modeli (OnePlus 8T) + preferred_model bez rotacji."""

    def test_oneplus_8t_in_profiles(self):
        # Komentarz: fingerprint kebab/OnePlus8T + {token}
        found = profile_by_model("OnePlus 8T")
        self.assertIsNotNone(found)
        self.assertEqual(found["model"], "OnePlus 8T")
        self.assertIn("kebab", found["fingerprint"])
        self.assertIn("OnePlus8T", found["fingerprint"])
        self.assertIn("{token}", found["fingerprint"])

    def test_profile_by_model_is_case_insensitive(self):
        self.assertEqual(profile_by_model("oneplus 8t")["model"], "OnePlus 8T")
        self.assertIsNone(profile_by_model(""))
        self.assertIsNone(profile_by_model("Nokia 3310"))

    def test_list_spoof_profiles_templates(self):
        rows = list_spoof_profiles()
        self.assertEqual(len(rows), len(SPOOF_PROFILES))
        models = {p["model"] for p in rows}
        self.assertIn("OnePlus 8T", models)
        self.assertIn("Samsung Galaxy A54 5G", models)
        op = next(p for p in rows if p["model"] == "OnePlus 8T")
        self.assertIn("{token}", op["fingerprint"])

    def test_fingerprint_for_profile_replaces_token(self):
        profile = profile_by_model("OnePlus 8T")
        fp = fingerprint_for_profile(profile, random.Random(9))
        self.assertNotIn("{token}", fp)
        self.assertIn("kebab", fp)
        self.assertIn("OnePlus8T", fp)

    def test_preferred_model_does_not_rotate(self):
        """Operator wybrał OnePlus 8T — nawet gdy clones[] już go ma."""
        used = {
            "package": "com.instagram.androia",
            "identity": {
                "mac": "02:00:00:00:00:01",
                "imei": "123456789012347",
                "android_id": "aaaaaaaaaaaaaaaa",
                "model": "OnePlus 8T",
                "sim_operator": "Play",
            },
        }
        ident = generate_clone_identity(
            {"clones": [used]}, preferred_model="OnePlus 8T",
            rng=random.Random(1),
        )
        self.assertEqual(ident["model"], "OnePlus 8T")
        self.assertIn("kebab", ident["fingerprint"])
        self.assertIn("OnePlus8T", ident["fingerprint"])
        # Komentarz: android_id / MAC / IMEI / GAID i tak nowe (unikalne)
        self.assertNotEqual(ident["android_id"], "aaaaaaaaaaaaaaaa")
        self.assertNotEqual(ident["mac"].lower(), "02:00:00:00:00:01")
        self.assertNotEqual(ident["imei"], "123456789012347")
        self.assertTrue(ident["gaid"])
        self.assertEqual(ident["safeguards"], DEFAULT_SAFEGUARDS)

    def test_normalize_safeguards_defaults_and_override(self):
        base = normalize_safeguards(None)
        self.assertTrue(base["kill_before_verify"])
        self.assertTrue(base["no_parallel_ig"])
        self.assertTrue(base["one_clone_job"])
        self.assertTrue(base["stop_stock"])
        self.assertTrue(base["sole_ig_pid"])
        # Komentarz: OFF z identity jest ignorowany — tylko 1 IG
        custom = normalize_safeguards({"stop_stock": False, "sole_ig_pid": 0})
        self.assertTrue(custom["stop_stock"])
        self.assertTrue(custom["sole_ig_pid"])
        self.assertTrue(custom["kill_before_verify"])
        self.assertTrue(custom["no_parallel_ig"])


if __name__ == "__main__":
    unittest.main()
