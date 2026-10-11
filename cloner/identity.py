"""Generatory unikalnej tożsamości klona IG (APPID, android-id, MAC, IMEI…)."""
from __future__ import annotations

import base64
import itertools
import random
import string
import uuid
from datetime import datetime, timedelta
from typing import Iterable

# Komentarz: fallback gdy puli nie ma / stary CLI bez rotacji
DEFAULT_SPOOF_MODEL = "Samsung Galaxy A54 5G"
DEFAULT_DNS = "1.1.1.1,1.0.0.1"
DEFAULT_SIM_COUNTRY = "PL"
DEFAULT_SIM_OPERATOR = "Play"
DEFAULT_SIM_OPERATOR_CODE = "26006"
# Komentarz: Wrocław — jak w przykładzie CLI
DEFAULT_GPS_LAT = 51.10788
DEFAULT_GPS_LON = 17.03854

# Komentarz: rotacja modelu — fingerprint z tego samego profilu (nie z fizycznego A50)
SPOOF_PROFILES = (
    {
        "model": "Samsung Galaxy A54 5G",
        "fingerprint": "samsung/a54xeea/a54x:14/UP1A.231005.007/{token}:user/release-keys",
    },
    {
        "model": "Samsung Galaxy A34 5G",
        "fingerprint": "samsung/a34xeea/a34x:14/UP1A.231005.007/{token}:user/release-keys",
    },
    {
        "model": "Samsung Galaxy A53 5G",
        "fingerprint": "samsung/a53xeea/a53x:13/TP1A.220624.014/{token}:user/release-keys",
    },
    {
        "model": "Samsung Galaxy A15",
        "fingerprint": "samsung/a15xx/a15:14/UP1A.231005.007/{token}:user/release-keys",
    },
    {
        "model": "Samsung Galaxy S23",
        "fingerprint": "samsung/dm1qxxx/dm1q:14/UP1A.231005.007/{token}:user/release-keys",
    },
    {
        "model": "Samsung Galaxy A25 5G",
        "fingerprint": "samsung/a25xxx/a25:14/UP1A.231005.007/{token}:user/release-keys",
    },
    {
        "model": "Xiaomi Redmi Note 12",
        "fingerprint": "xiaomi/tapas_eea/tapas:13/TKQ1.221114.001/{token}:user/release-keys",
    },
    {
        "model": "Google Pixel 7",
        "fingerprint": "google/panther/panther:14/UP1A.231005.007/{token}:user/release-keys",
    },
    # Komentarz: OnePlus 8T — kebab/OnePlus8T + {token} (panel: select modelu)
    {
        "model": "OnePlus 8T",
        "fingerprint": "oneplus/kebab/OnePlus8T:13/SKQ1.211006.001/{token}:user/release-keys",
    },
)

# Komentarz: zabezpieczenia startu — tylko 1 IG; tych flag nie da się wyłączyć
DEFAULT_SAFEGUARDS: dict[str, bool] = {
    "kill_before_verify": True,
    "no_parallel_ig": True,
    "one_clone_job": True,
    "stop_stock": True,
    "sole_ig_pid": True,
}
# Komentarz: OFF z identity/panelu jest ignorowany — stock + klony zawsze gaszą
ALWAYS_ON_SAFEGUARDS = (
    "kill_before_verify",
    "no_parallel_ig",
    "stop_stock",
    "sole_ig_pid",
)

# Komentarz: rotacja operatora PL (MCC 260 + MNC) — każdy klon inny, póki starczy puli
PL_OPERATORS = (
    {"sim_country": "PL", "sim_operator": "Play", "sim_operator_code": "26006"},
    {"sim_country": "PL", "sim_operator": "Plus", "sim_operator_code": "26001"},
    {"sim_country": "PL", "sim_operator": "Orange", "sim_operator_code": "26003"},
    {"sim_country": "PL", "sim_operator": "T-Mobile", "sim_operator_code": "26002"},
    {"sim_country": "PL", "sim_operator": "Heyah", "sim_operator_code": "26002"},
    {"sim_country": "PL", "sim_operator": "nju", "sim_operator_code": "26003"},
)

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


def next_clone_packages(used: Iterable[str], count: int = 8) -> list[str]:
    """Kolejne wolne APPID (podpowiedź w panelu), bez stocka i `used`."""
    taken = {p.strip().lower() for p in used if p}
    taken.add(ORIG_PACKAGE)
    out: list[str] = []
    alphabet = string.ascii_lowercase
    for n in range(1, 6):
        for combo in itertools.product(alphabet, repeat=n):
            pkg = PACKAGE_PREFIX + "".join(combo)
            if pkg.lower() in taken:
                continue
            out.append(pkg)
            taken.add(pkg.lower())
            if len(out) >= max(1, int(count)):
                return out
    return out


def is_clone_package(package: str) -> bool:
    """True dla klona App Cloner — nigdy stock `com.instagram.android`."""
    pkg = (package or "").strip()
    if not pkg or pkg.lower() == ORIG_PACKAGE:
        return False
    return pkg.lower().startswith(PACKAGE_PREFIX)


def is_ig_package(package: str) -> bool:
    """Stock albo klon — prefiks `com.instagram.androi*` (bez lite)."""
    pkg = (package or "").strip()
    return bool(pkg) and pkg.lower().startswith(PACKAGE_PREFIX)


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


def random_media_drm_id(rng: random.Random | None = None) -> str:
    """Losowy MediaDrm ID (base64, 32 bajty) — pole z zestawu CLI."""
    r = rng or random.Random()
    raw = bytes(r.getrandbits(8) for _ in range(32))
    return base64.b64encode(raw).decode("ascii")


def random_clock_times(rng: random.Random | None = None) -> tuple[str, str]:
    """Para (build_time, boot_time) w formacie `YYYY-MM-DD HH:MM:SS`."""
    r = rng or random.Random()
    now = datetime.now()
    build = now - timedelta(days=r.randrange(30, 200), hours=r.randrange(24))
    boot = now - timedelta(
        days=r.randrange(1, 40), hours=r.randrange(24), minutes=r.randrange(60),
    )
    fmt = "%Y-%m-%d %H:%M:%S"
    return build.strftime(fmt), boot.strftime(fmt)


def jitter_gps(rng: random.Random | None = None) -> tuple[float, float]:
    """GPS wokół domyślnego Wrocławia — każdy klon trochę inny."""
    r = rng or random.Random()
    lat = DEFAULT_GPS_LAT + (r.random() - 0.5) * 0.08
    lon = DEFAULT_GPS_LON + (r.random() - 0.5) * 0.08
    return round(lat, 5), round(lon, 5)


def _fingerprint_token(rng: random.Random) -> str:
    """Losowy identyfikator buildu w fingerprintcie."""
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(10))


def make_fingerprint(device: dict, rng: random.Random | None = None) -> str:
    """Unikalny Build.FINGERPRINT z pól devices/<alias>.json (fallback)."""
    r = rng or random.Random()
    mfr = str(device.get("manufacturer") or "samsung").lower().replace(" ", "")
    product = str(device.get("product") or "device").lower().replace(" ", "")
    ver = str(device.get("android_version") or "11")
    token = _fingerprint_token(r)
    return f"{mfr}/{product}/{product}:{ver}/RP1A.200720.012/{token}:user/release-keys"


def fingerprint_for_profile(profile: dict, rng: random.Random | None = None) -> str:
    """Fingerprint z rotowanego profilu — `{token}` zastępujemy unikalnym buildem."""
    r = rng or random.Random()
    tmpl = str(profile.get("fingerprint") or "")
    if "{token}" in tmpl:
        return tmpl.replace("{token}", _fingerprint_token(r))
    return tmpl or make_fingerprint({}, r)


def profile_by_model(model: str) -> dict | None:
    """Profil SPOOF_PROFILES po nazwie modelu (bez rotacji). None gdy brak."""
    want = (model or "").strip().lower()
    if not want:
        return None
    for profile in SPOOF_PROFILES:
        if str(profile.get("model") or "").strip().lower() == want:
            return profile
    return None


def list_spoof_profiles() -> list[dict]:
    """Szablony {model, fingerprint} z `{token}` — lista do preview / select."""
    return [
        {"model": p["model"], "fingerprint": p["fingerprint"]}
        for p in SPOOF_PROFILES
    ]


def normalize_safeguards(raw) -> dict[str, bool]:
    """Safeguards z identity / panelu — brakujące klucze biorą DEFAULT.

    `ALWAYS_ON_SAFEGUARDS` zostają True nawet gdy identity ma OFF.
    """
    out = dict(DEFAULT_SAFEGUARDS)
    if isinstance(raw, dict):
        for key in DEFAULT_SAFEGUARDS:
            if key in raw:
                out[key] = bool(raw[key])
    # Komentarz: twarda reguła „tylko 1 IG” — checkboxy są tylko informacyjne
    for key in ALWAYS_ON_SAFEGUARDS:
        out[key] = True
    return out


def rotate_choice(items: tuple | list, used: set[str], key, rng: random.Random):
    """Weź element spoza `used`; gdy pula wyczerpana — losuj z całości."""
    free = [x for x in items if str(key(x)).strip().lower() not in used]
    return rng.choice(free or list(items))


def collect_used_identity(device: dict) -> dict[str, set[str]]:
    """Zbiory package/mac/imei/android_id/model/operator już w clones[]."""
    packages: set[str] = set()
    macs: set[str] = set()
    imeis: set[str] = set()
    aids: set[str] = set()
    models: set[str] = set()
    operators: set[str] = set()
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
        model = (ident.get("model") or "").strip().lower()
        if model:
            models.add(model)
        # Komentarz: rotacja po nazwie (Play/Plus/Orange…) — widać to w panelu
        op = (ident.get("sim_operator") or "").strip().lower()
        if op:
            operators.add(op)
    return {
        "packages": packages,
        "macs": macs,
        "imeis": imeis,
        "android_ids": aids,
        "models": models,
        "operators": operators,
    }


def clone_occupancy(
    device: dict,
    extra_packages: Iterable[str] = (),
    *,
    adb_ok: bool | None = None,
    held_packages: Iterable[str] = (),
) -> set[str]:
    """Które APPID są zajęte — pod listę „wolnych nazw”.

    Przy żywym `pm list` (`adb_ok`) źródłem jest telefon + konta, nie stale
    wpisy w clones[] (odinstalowany androia znów jest wolny).
    Bez ADB zostaje clones[] — nie zgadujemy, że telefon jest pusty.
    """
    extra = {p.strip().lower() for p in extra_packages if p}
    held = {p.strip().lower() for p in held_packages if p}
    if adb_ok is None:
        adb_ok = bool(extra)
    if adb_ok:
        occ = {p for p in extra if is_ig_package(p)}
    else:
        occ = set(collect_used_identity(device)["packages"]) | extra
    occ |= held
    occ.add(ORIG_PACKAGE)
    return occ


def generate_clone_identity(
    device: dict,
    extra_packages: Iterable[str] = (),
    rng: random.Random | None = None,
    preferred_model: str | None = None,
    occupied_packages: Iterable[str] | None = None,
) -> dict:
    """Losowa tożsamość bez kolizji z clones[] i extra_packages (pm list).

    `occupied_packages` — gdy podane, to jedyna lista zajętych APPID
    (preview po odinstalowaniu klonów). Inaczej clones[] + extra.
    `preferred_model` (wybór operatora) — nie rotujemy na inny profil;
    android_id / MAC / IMEI / GAID nadal losowe (unikalne).
    """
    r = rng or random.Random()
    used = collect_used_identity(device)
    if occupied_packages is not None:
        used["packages"] = {p.strip().lower() for p in occupied_packages if p}
        used["packages"].add(ORIG_PACKAGE)
    else:
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

    # Komentarz: operator wybrał model → fingerprint z profilu, bez rotacji
    chosen = (preferred_model or "").strip()
    if chosen:
        profile = profile_by_model(chosen)
        if profile:
            model = profile["model"]
            fingerprint = fingerprint_for_profile(profile, r)
        else:
            # Komentarz: model spoza listy (np. „jak ten telefon”) — zostaw nazwę
            model = chosen
            fingerprint = make_fingerprint(device, r)
    elif device.get("spoof_model"):
        model = str(device.get("spoof_model")).replace("_", " ")
        fingerprint = make_fingerprint(device, r)
    else:
        # Komentarz: rotacja modelu + operatora — unikamy tych już w clones[]
        profile = rotate_choice(
            SPOOF_PROFILES, used["models"], lambda p: p["model"], r)
        model = profile["model"]
        fingerprint = fingerprint_for_profile(profile, r)
    operator = rotate_choice(
        PL_OPERATORS, used["operators"], lambda o: o["sim_operator"], r)
    build_time, boot_time = random_clock_times(r)
    gps_lat, gps_lon = jitter_gps(r)
    return {
        "package": pkg,
        "android_id": aid,
        "gaid": random_gaid(r),
        "fingerprint": fingerprint,
        "mac": mac,
        "imei": imei,
        "model": model,
        "media_drm_id": random_media_drm_id(r),
        "build_time": build_time,
        "boot_time": boot_time,
        "gps_lat": gps_lat,
        "gps_lon": gps_lon,
        "dns": DEFAULT_DNS,
        "sim_country": operator["sim_country"],
        "sim_operator": operator["sim_operator"],
        "sim_operator_code": operator["sim_operator_code"],
        # Komentarz: domyślne checkboxy startu — panel może nadpisać
        "safeguards": normalize_safeguards(None),
    }
