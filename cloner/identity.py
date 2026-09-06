"""Generatory unikalnej tożsamości klona IG (APPID, android-id, MAC, IMEI…)."""
from __future__ import annotations

import itertools
import random
import string
import uuid
from typing import Iterable

# Komentarz: stock IG — nigdy nie nadpisujemy tego package przy nowym klonie
ORIG_PACKAGE = "com.instagram.android"
# Komentarz: istniejące klony (androia / androip) mieszczą się w tym prefiksie
PACKAGE_PREFIX = "com.instagram.androi"


class IdentityError(ValueError):
    """Nie da się wygenerować unikalnego APPID / pola tożsamości."""


def next_clone_package(used: Iterable[str]) -> str:
    """Pierwszy wolny `com.instagram.androi` + sufiks liter (a, b, …, aa, …).

    Pomija `com.instagram.android` (stock)."""
    taken = {p.strip().lower() for p in used if p}
    taken.add(ORIG_PACKAGE)
    alphabet = string.ascii_lowercase
    for n in range(1, 6):
        for combo in itertools.product(alphabet, repeat=n):
            pkg = PACKAGE_PREFIX + "".join(combo)
            if pkg.lower() not in taken:
                return pkg
    raise IdentityError("wyczerpano przestrzeń APPID com.instagram.androi*")


def random_android_id(rng: random.Random | None = None) -> str:
    """16 znaków hex — Settings.Secure.ANDROID_ID."""
    r = rng or random.Random()
    return "".join(r.choice("0123456789abcdef") for _ in range(16))


def random_gaid(rng: random.Random | None = None) -> str:
    """UUID v4 jako Google Advertising ID."""
    r = rng or random.Random()
    return str(uuid.UUID(bytes=bytes(r.getrandbits(8) for _ in range(16)), version=4))


def random_mac(rng: random.Random | None = None) -> str:
    """MAC locally-administered unicast (bit 1 ustawiony, multicast skasowany)."""
    r = rng or random.Random()
    b = [r.randrange(256) for _ in range(6)]
    b[0] = (b[0] | 0x02) & 0xFE
    return ":".join(f"{x:02x}" for x in b)


def is_laa_unicast_mac(mac: str) -> bool:
    """True gdy locally administered i nie multicast."""
    parts = (mac or "").split(":")
    if len(parts) != 6:
        return False
    try:
        b0 = int(parts[0], 16)
    except ValueError:
        return False
    return (b0 & 0x02) != 0 and (b0 & 0x01) == 0


def luhn_check_digit(body14: str) -> int:
    """Cyfra kontrolna IMEI z 14 cyfr (Luhn; skrajna prawa body jest podwajana)."""
    if len(body14) != 14 or not body14.isdigit():
        raise IdentityError("IMEI body musi mieć 14 cyfr")
    total = 0
    double = True
    for ch in reversed(body14):
        d = int(ch)
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return (10 - (total % 10)) % 10


def imei_is_valid(imei: str) -> bool:
    """Pełne 15 cyfr z poprawnym Luhnem."""
    if len(imei) != 15 or not imei.isdigit():
        return False
    return luhn_check_digit(imei[:14]) == int(imei[14])


def random_imei(rng: random.Random | None = None) -> str:
    """15-cyfrowy IMEI z Luhnem (losowy TAC+SNR, bez basebandu)."""
    r = rng or random.Random()
    body = "".join(str(r.randrange(10)) for _ in range(14))
    return body + str(luhn_check_digit(body))


def make_fingerprint(device: dict, rng: random.Random | None = None) -> str:
    """Unikalny Build.FINGERPRINT z pól devices/<alias>.json."""
    r = rng or random.Random()
    mfr = str(device.get("manufacturer") or "samsung").lower().replace(" ", "")
    product = str(device.get("product") or "device").lower().replace(" ", "")
    ver = str(device.get("android_version") or "11")
    token = "".join(r.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(10))
    return f"{mfr}/{product}/{product}:{ver}/RP1A.200720.012/{token}:user/release-keys"


def collect_used_identity(device: dict) -> dict[str, set[str]]:
    """Zbiory package/mac/imei/android_id już zapisane w device.clones[]."""
    packages: set[str] = set()
    macs: set[str] = set()
    imeis: set[str] = set()
    aids: set[str] = set()
    for c in device.get("clones") or []:
        pkg = (c.get("package") or "").strip()
        if pkg:
            packages.add(pkg.lower())
        ident = c.get("identity") or {}
        mac = (ident.get("mac") or "").strip().lower()
        if mac:
            macs.add(mac)
        imei = (ident.get("imei") or "").strip()
        if imei:
            imeis.add(imei)
        aid = (ident.get("android_id") or "").strip().lower()
        if aid:
            aids.add(aid)
    return {
        "packages": packages,
        "macs": macs,
        "imeis": imeis,
        "android_ids": aids,
    }


def generate_clone_identity(
    device: dict,
    extra_packages: Iterable[str] = (),
    rng: random.Random | None = None,
) -> dict:
    """Losowa tożsamość bez kolizji z clones[] i extra_packages (pm list)."""
    r = rng or random.Random()
    used = collect_used_identity(device)
    used["packages"].update(p.strip().lower() for p in extra_packages if p)

    pkg = next_clone_package(used["packages"])
    mac = random_mac(r)
    while mac in used["macs"]:
        mac = random_mac(r)
    imei = random_imei(r)
    while imei in used["imeis"]:
        imei = random_imei(r)
    aid = random_android_id(r)
    while aid in used["android_ids"]:
        aid = random_android_id(r)

    model = device.get("model") or "SM_A505FN"
    return {
        "package": pkg,
        "android_id": aid,
        "gaid": random_gaid(r),
        "fingerprint": make_fingerprint(device, r),
        "mac": mac,
        "imei": imei,
        "model": str(model).replace("_", " "),
    }
