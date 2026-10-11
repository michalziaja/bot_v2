#!/usr/bin/env python3

"""
Przygotowanie klona Instagrama (źródła, opcjonalnie --build --sign).

Pipeline:
  1) cache HIT: kopia clone/_cache/<stem-apk>/instagram_src/ → clone/<prefix>/<APPID>/
     cache MISS: java -Xmx6g -jar apktool.jar d --no-res  →  zapis cache + workdir
  2) AXML → tekst + patch package / split / permissions / authorities
     potem tekst → binarny AXML (apktool b w no-res nie kompiluje XML)
    3) podmiana tożsamości w smali
    4) identity.json
    5) opcjonalnie: java -Xmx6g -jar apktool.jar b → instagram_unsigned.apk
     (apktool 3.0.3: flaga --no-res jest tylko przy `d`, nie przy `b`)
     jeśli w ZIP brak AndroidManifest.xml — doklejamy skompilowany AXML (stored)
     jeśli źródło to sam base.apk — doklejamy res/ + ARSC ze split_config.xxhdpi
     (splash 0x7f082060 = drawable instagram / res/s8z.png)

Przykład (Zestaw 1 — Galaxy A54 / Play):
  python3 clone_instagram.py \\
    --package com.instagram.androia \\
    --android-id 7f3a8b1c0d9e2468 \\
    --model "Samsung Galaxy A54 5G" \\
    --fingerprint "samsung/a54xeea/a54x:14/UP1A.231005.007/A546BXXU7CXB1:user/release-keys" \\
    --gaid d3b07384-d113-4f4a-a1b8-39281a9420e1 \\
    --media-drm-id "kL9+mR2vT8wX1zQ7aP4oN6eJ3iU5yB0cW8vX9zA1bC2=" \\
    --build-time "2026-05-14 09:12:00" \\
    --boot-time "2026-08-01 08:10:15" \\
    --gps-lat 51.10788 --gps-lon 17.03854 \\
    --dns "1.1.1.1,1.0.0.1" \\
    --sim-country PL --sim-operator Play --sim-operator-code 26006 \\
    --force
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from arsc_merge import (
    SPLASH_DRAWABLE_ID,
    arsc_has_entry_value,
    find_split_apks,
    overlay_splits_onto_apk,
    read_apk_arsc,
)
from axml import (
    decode_axml,
    ensure_binary_manifest,
    ensure_text_manifest,
    is_binary_axml,
)


# --- stałe dla tej wersji APK ---
ORIG_PACKAGE = "com.instagram.android"
DEFAULT_APK = "apks/instagram-444.0.0.46.85.apk"
# Heap jak w ręcznym `java -Xmx6g -jar apktool.jar …`
APKTOOL_HEAP = "-Xmx6g"
UNSIGNED_APK_NAME = "instagram_unsigned.apk"
# Alias pod testy / job — ten sam heap przy `b`
APKTOOL_BUILD_HEAP = APKTOOL_HEAP
# Komentarz: stan PO decode, PRZED patch — kolejne klony tej samej wersji APK
SRC_CACHE_DIRNAME = "_cache"
SRC_CACHE_SRC = "instagram_src"
SRC_CACHE_MARKER = "cache.json"

# Atrybuty manifestu, które zmuszają PM do oczekiwania splitów AAB
SPLIT_MANIFEST_ATTRS = (
    "isSplitRequired",
    "requiredSplitTypes",
    "splitTypes",
    "isFeatureSplit",
    "isolatedSplits",
)
SPLIT_METADATA_NAMES = (
    "com.android.vending.splits.required",
    "com.android.vending.splits",
    "com.android.vending.derived.apk.id",
)
DEFAULT_MODEL = "Samsung Galaxy A54 5G"
# Przykładowy fingerprint (dokumentacja CLI) — każdy klon musi dostać własny.
EXAMPLE_FINGERPRINT = (
    "samsung/a54xeea/a54x:14/UP1A.231005.007/A546BXXU7CXB1:user/release-keys"
)

# Uprawnienia do zakomentowania w manifeście
BLOCK_PERMISSIONS = [
    "android.permission.GET_ACCOUNTS",
    "android.permission.BLUETOOTH",
    "android.permission.BLUETOOTH_CONNECT",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.READ_PHONE_STATE",
    "android.permission.READ_PHONE_NUMBERS",
    "android.permission.READ_BASIC_PHONE_STATE",
]

# Własne permissiony IG (deklaracje + uses + android:permission=)
CUSTOM_PERMISSION_SUFFIXES = [
    "permission.SYSTEM_ONLY",
    "permission.CROSS_PROCESS_BROADCAST_MANAGER",
    "DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION",
    "permission.CREATE_SHORTCUT",
]

# Znane pliki tej wersji — Android ID (po Settings$Secure.getString)
ANDROID_ID_HINT_FILES = [
    "smali/X/00Tm.smali",
    "smali/X/07wt.smali",
    "smali_classes2/X/03hD.smali",
    "smali_classes3/X/03PA.smali",
    "smali_classes4/X/09LN.smali",
    "smali_classes12/X/0OSE.smali",
    "smali_classes12/com/facebook/react/modules/systeminfo/AndroidInfoModule.smali",
    "smali_classes12/com/facebook/browser/lite/BrowserLiteFragment.smali",
    "smali_classes16/X/0am8.smali",
]

# Znane pliki tej wersji — lokalne nadpisanie MODEL / FINGERPRINT po sget
BUILD_SPOOF_HINT_FILES = [
    "smali/X/07wt.smali",
    "smali_classes12/com/facebook/react/modules/systeminfo/AndroidInfoModule.smali",
    "smali_classes16/X/0am8.smali",
]

# GAID — FB helper + GMS AdvertisingIdClient
GAID_HINT_FILES = [
    "smali_classes12/X/0TnZ.smali",
    "smali_classes18/com/google/android/gms/ads/identifier/AdvertisingIdClient.smali",
]

# SIM / operator — device fingerprint helpers
SIM_HINT_FILES = [
    "smali/X/07wt.smali",
    "smali_classes16/X/0am8.smali",
    "smali/X/02nz.smali",
]

# GPS — device_lat/device_long + RN LocationModule
GPS_HINT_FILES = [
    "smali/X/02np.smali",
    "smali_classes16/com/facebook/react/modules/location/LocationModule.smali",
]

# Proxy + uptime (MobileDevice*)
PROXY_UPTIME_HINT_FILES = [
    "smali_classes16/X/0am8.smali",
]

# Markery wstrzyknięć smali
MARKER_ANDROID_ID = "# NADPISANIE: wymuszenie Android ID"
MARKER_MODEL = "# NADPISANIE: Model urządzenia"
MARKER_FINGERPRINT = "# NADPISANIE: Fingerprint systemu"
MARKER_GAID = "# NADPISANIE: GAID"
MARKER_SIM_OPERATOR = "# NADPISANIE: SIM operator name"
MARKER_SIM_COUNTRY = "# NADPISANIE: SIM country ISO"
MARKER_SIM_CODE = "# NADPISANIE: SIM operator code"
MARKER_GPS = "# NADPISANIE: GPS"
MARKER_PROXY = "# NADPISANIE: Proxy — brak"
MARKER_UPTIME = "# NADPISANIE: Uptime (z Boot Time)"
MARKER_MEDIA_DRM = "# NADPISANIE: Media DRM ID"


def log(msg: str) -> None:
    # Komentarz: jednolity prefix logów skryptu
    print(f"[clone] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    # Komentarz: uruchomienie komendy zewnętrznej z widocznym poleceniem
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def java_bin() -> str:
    """`java` z PATH albo JAVA_HOME — nie polecenie `apktool`."""
    found = shutil.which("java")
    if found:
        return found
    home = (os.environ.get("JAVA_HOME") or "").strip()
    if home:
        cand = Path(home) / "bin" / "java"
        if cand.is_file():
            return str(cand)
    raise RuntimeError("brak `java` w PATH — potrzebny JDK do apktool.jar")


def apktool_jar(root: Path) -> Path:
    """apktool.jar obok clone_instagram.py — bez ruszania JAR-a."""
    p = root / "apktool.jar"
    if not p.is_file():
        raise RuntimeError(f"brak apktool.jar w {root}")
    return p


def resolve_input_apk(root: Path, apk: Path) -> Path:
    """Źródło: absolutna ścieżka, `root/apk`, albo `root/apks/<nazwa>`.

    Panel często podaje samą nazwę (`instagram-444…apk`) — plik leży w apks/.
    """
    if apk.is_absolute():
        return apk
    rel = root / apk
    if rel.is_file():
        return rel
    # Komentarz: sama nazwa z selecta kn-apk → appcloner/apks/<plik>
    in_apks = root / "apks" / apk.name
    if in_apks.is_file():
        return in_apks
    return in_apks if (root / "apks").is_dir() else rel


def apktool_cmd(root: Path, *args: str, heap: str | None = APKTOOL_HEAP) -> list[str]:
    """java -Xmx6g -jar apktool.jar … (nie wrapper `apktool` z PATH)."""
    cmd = [java_bin()]
    if heap:
        cmd.append(heap)
    cmd.extend(["-jar", str(apktool_jar(root)), *args])
    return cmd


def apktool_decode_args(apk: Path, workdir: Path) -> list[str]:
    """`apktool d --no-res` — surowe resources.arsc; --no-res jest tylko przy decode.

    `apktool b` w 3.0.3 nie ma flagi --no-res (Unrecognized option).
    """
    return ["d", "--no-res", str(apk), "-o", str(workdir), "-f"]


def signer_jar(root: Path) -> Path:
    matches = sorted(root.glob("uber-apk-signer*.jar"))
    if not matches:
        raise RuntimeError(f"brak uber-apk-signer*.jar w {root}")
    return matches[0]


def sign_apk(root: Path, unsigned: Path, dest_apk: Path) -> Path:
    """java -jar uber-apk-signer --apk <unsigned> -o _signed → dest_apk."""
    out_dir = unsigned.parent / "_signed"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run([
        java_bin(), "-jar", str(signer_jar(root)),
        "--apk", str(unsigned), "-o", str(out_dir),
    ])
    signed = next(out_dir.glob("*.apk"), None)
    if signed is None:
        raise RuntimeError(f"uber-apk-signer nie wyprodukował APK w {out_dir}")
    dest_apk.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(signed, dest_apk)
    log(f"Podpisany APK: {dest_apk}")
    return dest_apk


def apk_has_android_manifest(apk: Path) -> bool:
    """True gdy ZIP APK zawiera AndroidManifest.xml."""
    if not apk.is_file():
        return False
    with zipfile.ZipFile(apk) as zf:
        return "AndroidManifest.xml" in zf.namelist()


def inject_android_manifest(apk: Path, axml: bytes) -> None:
    """Wstaw binarny AndroidManifest.xml jako pierwszy wpis ZIP (stored)."""
    tmp = apk.with_name(apk.name + ".manifest-tmp")
    now = datetime.now()
    try:
        with zipfile.ZipFile(apk, "r") as src, zipfile.ZipFile(tmp, "w") as dst:
            info = zipfile.ZipInfo("AndroidManifest.xml")
            info.compress_type = zipfile.ZIP_STORED
            info.date_time = (
                now.year, now.month, now.day, now.hour, now.minute, now.second,
            )
            dst.writestr(info, axml)
            for item in src.infolist():
                if item.filename == "AndroidManifest.xml":
                    continue
                dst.writestr(item, src.read(item.filename))
        tmp.replace(apk)
    finally:
        if tmp.exists():
            tmp.unlink()


def compile_patched_manifest(manifest: Path) -> bytes:
    """Po patchu tekstowym zapisz AXML — apktool b (no-res) kopiuje raw.

    Nadpisuje też original/AndroidManifest.xml, żeby apktool nie wrzucił
    stockowego pakietu com.instagram.android.
    """
    # Komentarz: binarny plik, żeby `apktool b` skopiował raw zamiast aapt2
    axml = ensure_binary_manifest(manifest)
    original = manifest.parent / "original" / "AndroidManifest.xml"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(axml)
    return axml


def ensure_unsigned_apk_manifest(
    apk: Path, axml: bytes, expected_package: str
) -> None:
    """Po `apktool b`: brak / tekst / zły package → doklej zpatchowany AXML."""
    need = True
    if apk_has_android_manifest(apk):
        with zipfile.ZipFile(apk) as zf:
            data = zf.read("AndroidManifest.xml")
        if is_binary_axml(data) and (
            f'package="{expected_package}"' in decode_axml(data)
        ):
            need = False
    if not need:
        return
    log(
        "APK bez poprawnego AndroidManifest.xml — "
        f"doklejam binarny AXML (package={expected_package})"
    )
    inject_android_manifest(apk, axml)
    if not apk_has_android_manifest(apk):
        raise RuntimeError(f"nie udało się wstawić AndroidManifest.xml do {apk}")


def sanitize_split_manifest(xml: str) -> tuple[str, list[str]]:
    """Usuń wymagania split/AAB — standalone APK pod `adb install`."""
    removed: list[str] = []
    text = xml
    for attr in SPLIT_MANIFEST_ATTRS:
        pat = re.compile(rf'\s+android:{re.escape(attr)}="[^"]*"')
        text, n = pat.subn("", text)
        if n:
            removed.append(f"android:{attr}" + (f" ×{n}" if n > 1 else ""))
    for name in SPLIT_METADATA_NAMES:
        # Komentarz: decode_axml daje <meta-data>...</meta-data>, nie zawsze />
        pat = re.compile(
            rf'[ \t]*<meta-data\b[^>]*android:name="{re.escape(name)}"[^>]*'
            rf'(?:/>|>\s*</meta-data>)[ \t]*\n?',
            re.DOTALL,
        )
        text, n = pat.subn("", text)
        if n:
            removed.append(f"meta-data {name}")
    return text, removed


def map_authority(auth: str, new_pkg: str) -> str:
    """Przemapuj authority na unikalne dla klona; Facebook queries zostaw."""
    if auth.startswith("com.facebook."):
        return auth
    if auth.startswith(ORIG_PACKAGE + "."):
        return new_pkg + auth[len(ORIG_PACKAGE) :]
    if auth.startswith(ORIG_PACKAGE):
        return new_pkg + auth[len(ORIG_PACKAGE) :]
    # com.instagram.fileprovider → com.instagram.XXX.fileprovider
    if auth.startswith("com.instagram.") and not auth.startswith(new_pkg):
        return new_pkg + auth[len("com.instagram") :]
    return auth


def comment_uses_permission(xml: str, perm: str) -> str:
    """Zakomentuj pojedyncze <uses-permission ... perm .../> jeśli jeszcze aktywne."""
    # Już w komentarzu — nic nie rób
    if re.search(rf"<!--[^>]*android:name=\"{re.escape(perm)}\"", xml):
        return xml

    pattern = re.compile(
        rf'(^[ \t]*)(<uses-permission\b[^>]*android:name="{re.escape(perm)}"[^>]*/>)',
        re.MULTILINE,
    )

    def repl(m: re.Match) -> str:
        return f"{m.group(1)}<!-- {m.group(2)} -->"

    return pattern.sub(repl, xml, count=1)


def patch_manifest(manifest: Path, new_pkg: str) -> None:
    """Podmień package, zdejmij splity AAB, zablokuj permissiony, przenieś authorities."""
    text = manifest.read_text(encoding="utf-8")

    # 1) package=
    text2, n = re.subn(
        r'(\bpackage=")com\.instagram\.android(")',
        rf"\1{new_pkg}\2",
        text,
        count=1,
    )
    if n == 0:
        # Może już podmienione albo inny format
        text2, n = re.subn(
            r'(\bpackage=")[^"]+(")',
            rf"\1{new_pkg}\2",
            text,
            count=1,
        )
    text = text2
    log(f"Manifest package → {new_pkg}")

    # 1b) Standalone APK: zdejmij wymagania splitów (AAB / feature modules)
    text, stripped = sanitize_split_manifest(text)
    if stripped:
        log("Usunięto wymagania split/AAB: " + ", ".join(stripped))
    else:
        log("Brak atrybutów split/AAB do usunięcia")

    # 2) Zakomentuj wrażliwe uses-permission
    for perm in BLOCK_PERMISSIONS:
        before = text
        text = comment_uses_permission(text, perm)
        if text != before:
            log(f"Zakomentowano: {perm}")

    # 3) Custom permissiony: ORIG_PACKAGE → new_pkg
    for suffix in CUSTOM_PERMISSION_SUFFIXES:
        old = f"{ORIG_PACKAGE}.{suffix}"
        new = f"{new_pkg}.{suffix}"
        if old in text:
            text = text.replace(old, new)
            log(f"Permission rename: {old} → {new}")

    # Upewnij się, że CREATE_SHORTCUT ma deklarację <permission>
    create_perm = f"{new_pkg}.permission.CREATE_SHORTCUT"
    if create_perm in text and f'<permission android:name="{create_perm}"' not in text:
        sys_only = f'<permission android:name="{new_pkg}.permission.SYSTEM_ONLY"'
        decl = (
            f'    <!-- Klon: deklaracja CREATE_SHORTCUT -->\n'
            f'    <permission android:name="{create_perm}" '
            f'android:protectionLevel="signature"/>\n'
        )
        if sys_only in text:
            text = re.sub(
                rf'(<permission android:name="{re.escape(new_pkg)}\.permission\.SYSTEM_ONLY"[^>]*/>\n)',
                rf"\1{decl}",
                text,
                count=1,
            )
        else:
            text = text.replace(
                "<application ",
                decl + "    <application ",
                1,
            )
        log(f"Dodano deklarację <permission> {create_perm}")

    # 4) Authorities — tylko atrybuty android:authorities="..."
    def auth_repl(m: re.Match) -> str:
        raw = m.group(1)
        parts = [map_authority(p.strip(), new_pkg) for p in raw.split(";")]
        return f'android:authorities="{";".join(parts)}"'

    text, auth_n = re.subn(
        r'android:authorities="([^"]+)"',
        auth_repl,
        text,
    )
    log(f"Przemapowano authorities: {auth_n} wystąpień")

    manifest.write_text(text, encoding="utf-8")


def update_marked_const_string(content: str, marker: str, value: str) -> tuple[str, int]:
    """Zaktualizuj istniejące const-string zaraz po markerze NADPISANIE."""
    # Komentarz: pozwala ponownie odpalić patch na już przygotowanych źródłach
    pat = re.compile(
        rf'^([ \t]*{re.escape(marker)}\n)([ \t]*const-string (\w+), ")[^"]*(")',
        re.MULTILINE,
    )
    updated = 0

    def repl(m: re.Match) -> str:
        nonlocal updated
        updated += 1
        return f'{m.group(1)}{m.group(2)}{value}{m.group(4)}'

    return pat.sub(repl, content), updated


def inject_after_move_result(content: str, android_id: str) -> tuple[str, int]:
    """
    Po Settings$Secure;->getString + move-result-object REG wstaw const-string,
    ale TYLKO gdy w oknie ~12 linii wstecz widać "android_id".
    Jeśli marker już jest — tylko zaktualizuj wartość.
    """
    content, updated = update_marked_const_string(content, MARKER_ANDROID_ID, android_id)
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    added = 0
    i = 0
    invoke_re = re.compile(
        r"invoke-static \{[^}]+\}, "
        r"Landroid/provider/Settings\$Secure;->getString"
        r"\(Landroid/content/ContentResolver;Ljava/lang/String;\)Ljava/lang/String;"
    )
    move_re = re.compile(r"^([ \t]*)move-result-object (\w+)[ \t]*$")

    while i < len(lines):
        line = lines[i]
        out.append(line)
        if invoke_re.search(line):
            window = "".join(lines[max(0, i - 12) : i + 1])
            is_android_id = (
                'const-string' in window and '"android_id"' in window
            ) or ("android_id" in window)
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                out.append(lines[j])
                j += 1
            if j < len(lines):
                mm = move_re.match(lines[j].rstrip("\n"))
                if mm and is_android_id:
                    indent, reg = mm.group(1), mm.group(2)
                    out.append(lines[j])
                    nxt = j + 1
                    while nxt < len(lines) and lines[nxt].strip() == "":
                        nxt += 1
                    already = nxt < len(lines) and MARKER_ANDROID_ID in lines[nxt]
                    if not already:
                        out.append(f"{indent}{MARKER_ANDROID_ID}\n")
                        out.append(f'{indent}const-string {reg}, "{android_id}"\n')
                        added += 1
                    i = j + 1
                    continue
                out.append(lines[j])
                i = j + 1
                continue
        i += 1

    return "".join(out), added + updated


def inject_build_spoof(content: str, model: str, fingerprint: str) -> tuple[str, int]:
    """Po sget MODEL/FINGERPRINT nadpisz lokalny rejestr (bez sput). Aktualizuj istniejące."""
    count = 0

    # Najpierw odśwież wartości przy już istniejących markerach
    content, n_m = update_marked_const_string(content, MARKER_MODEL, model)
    content, n_f = update_marked_const_string(content, MARKER_FINGERPRINT, fingerprint)
    count += n_m + n_f

    def make_repl(field: str, value: str, marker: str):
        nonlocal content, count
        pat = re.compile(
            rf"(sget-object (\w+), Landroid/os/Build;->{field}:Ljava/lang/String;\s*\n)"
            rf"(?!\s*{re.escape(marker)})"
        )

        def repl(m: re.Match) -> str:
            nonlocal count
            reg = m.group(2)
            count += 1
            return (
                f"{m.group(1)}"
                f"    {marker}\n"
                f'    const-string {reg}, "{value}"\n'
            )

        content = pat.sub(repl, content)

    make_repl("MODEL", model, MARKER_MODEL)
    make_repl("FINGERPRINT", fingerprint, MARKER_FINGERPRINT)
    return content, count


def iter_smali_files(src: Path):
    for d in sorted(src.glob("smali*")):
        if d.is_dir():
            yield from d.rglob("*.smali")


def patch_smali_android_id(src: Path, android_id: str) -> int:
    """Patchuj znane pliki tej wersji + pliki z literalem android_id + Secure.getString."""
    total = 0
    candidates: list[Path] = []
    seen: set[Path] = set()
    for rel in ANDROID_ID_HINT_FILES:
        p = src / rel
        if p.is_file():
            candidates.append(p)
            seen.add(p.resolve())

    for p in iter_smali_files(src):
        rp = p.resolve()
        if rp in seen:
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "Settings$Secure;->getString" not in head:
            continue
        if "android_id" not in head:
            continue
        candidates.append(p)
        seen.add(rp)

    for p in candidates:
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "Settings$Secure;->getString" not in text and MARKER_ANDROID_ID not in text:
            continue
        new_text, added = inject_after_move_result(text, android_id)
        if added > 0 and new_text != text:
            p.write_text(new_text, encoding="utf-8")
            log(f"Android ID ×{added}: {p.relative_to(src)}")
            total += added
    return total


def patch_smali_build(src: Path, model: str, fingerprint: str) -> int:
    """Wstrzyknij / zaktualizuj lokalne nadpisania Build.MODEL i Build.FINGERPRINT."""
    total = 0
    files: list[Path] = []
    seen: set[Path] = set()
    for rel in BUILD_SPOOF_HINT_FILES:
        p = src / rel
        if p.is_file():
            files.append(p)
            seen.add(p.resolve())

    for p in files:
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "Landroid/os/Build;->" not in text and MARKER_MODEL not in text:
            continue
        new_text, n = inject_build_spoof(text, model, fingerprint)
        if n and new_text != text:
            p.write_text(new_text, encoding="utf-8")
            log(f"Build spoof ×{n}: {p.relative_to(src)}")
            total += n
    return total


def ensure_no_build_sput(src: Path) -> None:
    """Usuń niebezpieczne sput na Build.MODEL/FINGERPRINT (crash ART)."""
    pat = re.compile(
        r"[ \t]*# NADPISANIE:.*\n"
        r"[ \t]*const-string \w+, \"[^\"]+\"\n"
        r"[ \t]*sput-object \w+, Landroid/os/Build;->(?:MODEL|FINGERPRINT):Ljava/lang/String;\n",
    )
    for p in iter_smali_files(src):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "sput-object" not in text or "Build;->" not in text:
            continue
        new_text, n = pat.subn("", text)
        new_text2, n2 = re.subn(
            r"[ \t]*sput-object \w+, Landroid/os/Build;->(?:MODEL|FINGERPRINT):Ljava/lang/String;\n",
            "",
            new_text,
        )
        if n or n2:
            p.write_text(new_text2, encoding="utf-8")
            log(f"Usunięto sput Build (×{n + n2}): {p.relative_to(src)}")


def double_to_smali_wide(value: float) -> str:
    """Konwersja float → const-wide (bity IEEE754) dla smali."""
    bits = struct.unpack(">Q", struct.pack(">d", float(value)))[0]
    return f"0x{bits:016x}L"


def long_to_smali_wide(value: int) -> str:
    """Konwersja int → const-wide dla smali."""
    return f"0x{value & 0xFFFFFFFFFFFFFFFF:x}L"


def inject_before_iput_string(
    content: str, field_suffix: str, marker: str, value: str
) -> tuple[str, int]:
    """
    Przed iput-object REG, ...->FIELD wstaw const-string REG, value.
    Używane do GAID (0TnZ.A01 / AdvertisingIdClient$Info.A00).
    """
    content, updated = update_marked_const_string(content, marker, value)
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    added = 0
    iput_re = re.compile(
        rf"^([ \t]*)iput-object (\w+), (\w+), {re.escape(field_suffix)}\s*$"
    )

    i = 0
    while i < len(lines):
        line = lines[i]
        mm = iput_re.match(line.rstrip("\n"))
        if mm:
            # Sprawdź czy poprzednia znacząca linia to już nasz marker
            prev = i - 1
            while prev >= 0 and lines[prev].strip() == "":
                prev -= 1
            # marker jest 2 linie wstecz (marker + const-string)
            already = False
            if prev >= 0 and "const-string" in lines[prev]:
                prev2 = prev - 1
                while prev2 >= 0 and lines[prev2].strip() == "":
                    prev2 -= 1
                if prev2 >= 0 and marker in lines[prev2]:
                    already = True
            if not already:
                indent, reg = mm.group(1), mm.group(2)
                out.append(f"{indent}{marker}\n")
                out.append(f'{indent}const-string {reg}, "{value}"\n')
                added += 1
        out.append(line)
        i += 1

    return "".join(out), added + updated


def patch_smali_gaid(src: Path, gaid: str) -> int:
    """Wymuś GAID w 0TnZ (FB) i AdvertisingIdClient (GMS)."""
    if not gaid:
        return 0
    total = 0
    targets = [
        (
            "smali_classes12/X/0TnZ.smali",
            "LX/0TnZ;->A01:Ljava/lang/String;",
        ),
        (
            "smali_classes18/com/google/android/gms/ads/identifier/AdvertisingIdClient.smali",
            "Lcom/google/android/gms/ads/identifier/AdvertisingIdClient$Info;->A00:Ljava/lang/String;",
        ),
    ]
    for rel, field in targets:
        p = src / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        new_text, n = inject_before_iput_string(text, field, MARKER_GAID, gaid)
        if n and new_text != text:
            p.write_text(new_text, encoding="utf-8")
            log(f"GAID ×{n}: {rel}")
            total += n
    return total


def inject_after_invoke_move_result_string(
    content: str,
    method_substr: str,
    marker: str,
    value: str,
) -> tuple[str, int]:
    """Po invoke ...method... + move-result-object REG wstaw/zaktualizuj const-string."""
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    changed = 0
    move_re = re.compile(r"^([ \t]*)move-result-object (\w+)[ \t]*$")
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if method_substr in line and "invoke-" in line:
            j = i + 1
            while j < len(lines) and (
                lines[j].strip() == "" or lines[j].lstrip().startswith(".line")
            ):
                out.append(lines[j])
                j += 1
            if j < len(lines):
                mm = move_re.match(lines[j].rstrip("\n"))
                if mm:
                    indent, reg = mm.group(1), mm.group(2)
                    out.append(lines[j])
                    nxt = j + 1
                    while nxt < len(lines) and lines[nxt].strip() == "":
                        nxt += 1
                    if nxt < len(lines) and marker in lines[nxt]:
                        # Już zmarkowane — zaktualizuj następną const-string
                        out.append(lines[nxt])
                        cs = nxt + 1
                        while cs < len(lines) and lines[cs].strip() == "":
                            out.append(lines[cs])
                            cs += 1
                        if cs < len(lines) and "const-string" in lines[cs]:
                            out.append(f'{indent}const-string {reg}, "{value}"\n')
                            changed += 1
                            i = cs + 1
                            continue
                        i = nxt + 1
                        continue
                    out.append(f"{indent}{marker}\n")
                    out.append(f'{indent}const-string {reg}, "{value}"\n')
                    changed += 1
                    i = j + 1
                    continue
                out.append(lines[j])
                i = j + 1
                continue
        i += 1
    return "".join(out), changed


def patch_smali_sim(
    src: Path,
    country: str,
    operator: str,
    operator_code: str,
) -> int:
    """Nadpisz getSim*/getNetwork* w helperach fingerprint."""
    if not (country or operator or operator_code):
        return 0
    country_iso = country.lower() if country else ""
    # Mapa: fragment sygnatury → (marker, wartość)
    method_values: list[tuple[str, str, str]] = []
    if operator:
        method_values.append(
            (
                "Landroid/telephony/TelephonyManager;->getSimOperatorName()",
                MARKER_SIM_OPERATOR,
                operator,
            )
        )
        method_values.append(
            (
                "Landroid/telephony/TelephonyManager;->getNetworkOperatorName()",
                MARKER_SIM_OPERATOR,
                operator,
            )
        )
    if country_iso:
        method_values.append(
            (
                "Landroid/telephony/TelephonyManager;->getSimCountryIso()",
                MARKER_SIM_COUNTRY,
                country_iso,
            )
        )
        method_values.append(
            (
                "Landroid/telephony/TelephonyManager;->getNetworkCountryIso()",
                MARKER_SIM_COUNTRY,
                country_iso,
            )
        )
    if operator_code:
        method_values.append(
            (
                "Landroid/telephony/TelephonyManager;->getSimOperator()",
                MARKER_SIM_CODE,
                operator_code,
            )
        )
        method_values.append(
            (
                "Landroid/telephony/TelephonyManager;->getNetworkOperator()",
                MARKER_SIM_CODE,
                operator_code,
            )
        )

    total = 0
    for rel in SIM_HINT_FILES:
        p = src / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "TelephonyManager" not in text:
            continue
        new_text = text
        file_n = 0
        for method, marker, value in method_values:
            new_text, n = inject_after_invoke_move_result_string(
                new_text, method, marker, value
            )
            file_n += n
        if file_n and new_text != text:
            p.write_text(new_text, encoding="utf-8")
            log(f"SIM ×{file_n}: {rel}")
            total += file_n
    return total


def inject_after_invoke_move_result_wide(
    content: str,
    method_substr: str,
    marker: str,
    wide_lit: str,
) -> tuple[str, int]:
    """Po invoke + move-result-wide REG wstaw/zaktualizuj const-wide REG, lit."""
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    changed = 0
    move_re = re.compile(r"^([ \t]*)move-result-wide (\w+)[ \t]*$")
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if method_substr in line and "invoke-" in line:
            j = i + 1
            while j < len(lines) and (
                lines[j].strip() == "" or lines[j].lstrip().startswith(".line")
            ):
                out.append(lines[j])
                j += 1
            if j < len(lines):
                mm = move_re.match(lines[j].rstrip("\n"))
                if mm:
                    indent, reg = mm.group(1), mm.group(2)
                    out.append(lines[j])
                    nxt = j + 1
                    while nxt < len(lines) and lines[nxt].strip() == "":
                        nxt += 1
                    if nxt < len(lines) and marker in lines[nxt]:
                        out.append(lines[nxt])
                        cs = nxt + 1
                        while cs < len(lines) and lines[cs].strip() == "":
                            out.append(lines[cs])
                            cs += 1
                        if cs < len(lines) and "const-wide" in lines[cs]:
                            out.append(f"{indent}const-wide {reg}, {wide_lit}\n")
                            changed += 1
                            i = cs + 1
                            continue
                        i = nxt + 1
                        continue
                    out.append(f"{indent}{marker}\n")
                    out.append(f"{indent}const-wide {reg}, {wide_lit}\n")
                    changed += 1
                    i = j + 1
                    continue
                out.append(lines[j])
                i = j + 1
                continue
        i += 1
    return "".join(out), changed


def patch_smali_gps(src: Path, lat: float | None, lon: float | None) -> int:
    """Nadpisz getLatitude/getLongitude w device_lat/device_long i LocationModule."""
    if lat is None and lon is None:
        return 0
    total = 0
    for rel in GPS_HINT_FILES:
        p = src / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "getLatitude" not in text and "getLongitude" not in text:
            continue
        new_text = text
        file_n = 0
        if lat is not None:
            new_text, n = inject_after_invoke_move_result_wide(
                new_text,
                "Landroid/location/Location;->getLatitude()D",
                MARKER_GPS,
                double_to_smali_wide(lat),
            )
            file_n += n
        if lon is not None:
            new_text, n = inject_after_invoke_move_result_wide(
                new_text,
                "Landroid/location/Location;->getLongitude()D",
                MARKER_GPS,
                double_to_smali_wide(lon),
            )
            file_n += n
        if file_n and new_text != text:
            p.write_text(new_text, encoding="utf-8")
            log(f"GPS ×{file_n}: {rel}")
            total += file_n
    return total


def patch_smali_proxy(src: Path, proxy: str) -> int:
    """Wymuś MobileDeviceProxySet=false gdy proxy='Not set' / puste."""
    # Komentarz: w 0am8 null → ProxySet=true; pusty "" → ProxySet=false
    if proxy.strip().lower() not in ("", "not set", "brak", "none", "null"):
        log(f"Proxy ustawione na '{proxy}' — pomijam smali spoof (obsługiwany tylko brak)")
        return 0
    total = 0
    for rel in PROXY_UPTIME_HINT_FILES:
        p = src / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "http.proxyHost" not in text:
            continue

        lines = text.splitlines(keepends=True)
        out: list[str] = []
        changed = 0
        move_re = re.compile(r"^([ \t]*)move-result-object (\w+)[ \t]*$")
        i = 0
        while i < len(lines):
            line = lines[i]
            out.append(line)
            window = "".join(lines[max(0, i - 6) : i + 1])
            if (
                "Ljava/lang/System;->getProperty(Ljava/lang/String;)Ljava/lang/String;"
                in line
                and "http.proxyHost" in window
            ):
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    out.append(lines[j])
                    j += 1
                if j < len(lines):
                    mm = move_re.match(lines[j].rstrip("\n"))
                    if mm:
                        indent, reg = mm.group(1), mm.group(2)
                        out.append(lines[j])
                        nxt = j + 1
                        while nxt < len(lines) and lines[nxt].strip() == "":
                            nxt += 1
                        if nxt < len(lines) and MARKER_PROXY in lines[nxt]:
                            out.append(lines[nxt])
                            cs = nxt + 1
                            while cs < len(lines) and lines[cs].strip() == "":
                                out.append(lines[cs])
                                cs += 1
                            if cs < len(lines) and "const-string" in lines[cs]:
                                out.append(f'{indent}const-string {reg}, ""\n')
                                changed += 1
                                i = cs + 1
                                continue
                            i = nxt + 1
                            continue
                        out.append(f"{indent}{MARKER_PROXY}\n")
                        out.append(f'{indent}const-string {reg}, ""\n')
                        changed += 1
                        i = j + 1
                        continue
                    out.append(lines[j])
                    i = j + 1
                    continue
            i += 1
        new_text = "".join(out)
        if changed and new_text != text:
            p.write_text(new_text, encoding="utf-8")
            log(f"Proxy ×{changed}: {rel}")
            total += changed
    return total


def compute_uptime_ms(boot_time: str, uptime_ms: int | None) -> int | None:
    """Oblicz uptime z --uptime-ms albo (teraz - boot_time)."""
    if uptime_ms is not None:
        return uptime_ms
    if not boot_time:
        return None
    try:
        boot = datetime.strptime(boot_time.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        log(f"BŁĄD formatu --boot-time: {boot_time} (oczekiwano YYYY-MM-DD HH:MM:SS)")
        return None
    ms = int((datetime.now() - boot).total_seconds() * 1000)
    return max(ms, 0)


def patch_smali_uptime(src: Path, uptime_ms: int | None) -> int:
    """Nadpisz SystemClock.uptimeMillis() → MobileDeviceUptime w 0am8."""
    if uptime_ms is None:
        return 0
    wide = long_to_smali_wide(uptime_ms)
    total = 0
    for rel in PROXY_UPTIME_HINT_FILES:
        p = src / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "uptimeMillis" not in text:
            continue
        new_text, n = inject_after_invoke_move_result_wide(
            text,
            "Landroid/os/SystemClock;->uptimeMillis()J",
            MARKER_UPTIME,
            wide,
        )
        if n and new_text != text:
            p.write_text(new_text, encoding="utf-8")
            log(f"Uptime ×{n}: {rel} ({uptime_ms} ms)")
            total += n
    return total


def try_patch_media_drm(src: Path, media_drm_id: str) -> int:
    """
    Spróbuj znaleźć getPropertyByteArray/deviceUniqueId.
    W tym buildzie IG zwykle BRAK call-site tożsamości DRM — wtedy 0.
    """
    if not media_drm_id:
        return 0
    # Komentarz: skan ograniczony do plików z MediaDrm (bez pełnego rglob po wszystkim)
    hits: list[Path] = []
    for p in iter_smali_files(src):
        try:
            # szybki filtr po nazwie ścieżki / małym podglądzie jest kosztowny;
            # sprawdzamy tylko gdy plik jest „mały” w ścieżce Media / Drm / 0ig
            name = p.name.lower()
            if "drm" not in name and "media" not in str(p).lower():
                # pełny skan tylko hintów typowych
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "getPropertyByteArray" in text or "deviceUniqueId" in text:
            hits.append(p)

    # Dodatkowy szybki skan stringów w znanych klasach ExoPlayer/FrameworkMediaDrm
    for rel in (
        "smali_classes19/X/0igD.smali",
        "smali_classes19/X/0ig7.smali",
    ):
        p = src / rel
        if p.is_file() and p not in hits:
            t = p.read_text(encoding="utf-8", errors="ignore")
            if "getPropertyByteArray" in t or "deviceUniqueId" in t:
                hits.append(p)

    if not hits:
        log(
            "Media DRM ID: brak call-site getPropertyByteArray/deviceUniqueId "
            "w tym APK — zapisuję tylko do identity.json"
        )
        return 0

    # Gdyby kiedyś pojawił się string path — zostaw marker informacyjny (byte[] trudne w smali)
    log(
        f"Media DRM ID: znaleziono {len(hits)} plik(ów), ale spoof byte[]→Base64 "
        "wymaga ręcznego patcha — patrz identity.json"
    )
    for p in hits[:5]:
        log(f"  DRM candidate: {p.relative_to(src)}")
    return 0


def apk_cache_fingerprint(apk: Path) -> dict:
    """Marker cache: stem + rozmiar + mtime — inny plik o tej samej nazwie = MISS."""
    st = apk.stat()
    return {
        "apk": apk.stem,
        "size": int(st.st_size),
        "mtime": int(st.st_mtime),
    }


def src_cache_base(clone_root: Path, apk: Path) -> Path:
    """clone/_cache/<stem-apk>/ — nie mylić z clone/<APPID>/."""
    return Path(clone_root) / SRC_CACHE_DIRNAME / apk.stem


def src_cache_src_dir(clone_root: Path, apk: Path) -> Path:
    """Rozpakowane źródła (przed patch) w cache."""
    return src_cache_base(clone_root, apk) / SRC_CACHE_SRC


def src_cache_marker_path(clone_root: Path, apk: Path) -> Path:
    """cache.json obok instagram_src w cache."""
    return src_cache_base(clone_root, apk) / SRC_CACHE_MARKER


def src_cache_is_hit(apk: Path, clone_root: Path) -> bool:
    """True gdy marker zgadza się z APK i jest AndroidManifest w cache."""
    marker = src_cache_marker_path(clone_root, apk)
    cached = src_cache_src_dir(clone_root, apk)
    if not marker.is_file() or not (cached / "AndroidManifest.xml").is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    expect = apk_cache_fingerprint(apk)
    return (
        str(data.get("apk") or "") == expect["apk"]
        and int(data.get("size") or -1) == expect["size"]
        and int(data.get("mtime") or -1) == expect["mtime"]
    )


def write_src_cache_marker(apk: Path, clone_root: Path) -> None:
    """Zapisz cache.json po udanym decode (przed patch)."""
    path = src_cache_marker_path(clone_root, apk)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(apk_cache_fingerprint(apk), indent=2) + "\n",
        encoding="utf-8",
    )


def copy_src_tree(src: Path, dest: Path) -> None:
    """Kopia drzewa źródeł — dest nadpisywany w całości."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)


def wipe_clone_dir(clone_dir: Path) -> None:
    """--force: tylko clone/<prefix>/<APPID>/, nigdy clone/_cache/."""
    if clone_dir.exists():
        shutil.rmtree(clone_dir)


def sanitize_dir_prefix(raw: str) -> str:
    """Alias urządzenia → segment katalogu (bez _cache, bez dziwnych znaków)."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw or "").strip()).strip("._-")
    if text.lower() in ("_cache", "cache"):
        text = f"dev_{text}"
    return text[:80]


def clone_package_dir(clone_root: Path, package: str, prefix: str = "") -> Path:
    """clone/<prefix>/<APPID>/ gdy jest telefon; inaczej clone/<APPID>/ (CLI)."""
    root = Path(clone_root)
    pkg = (package or "").strip()
    pre = sanitize_dir_prefix(prefix)
    if pre:
        return root / pre / pkg
    return root / pkg


def populate_instagram_src(
    apk: Path,
    workdir: Path,
    clone_root: Path,
    *,
    app_root: Path | None = None,
    decode_fn=None,
) -> str:
    """Wypełnij workdir ze stanu PO decode / PRZED patch.

    HIT: kopia cache, bez apktool d.
    MISS: decode → zapis cache → workdir (albo decode prosto do workdir + kopia).
    Zwraca ``HIT`` albo ``MISS`` (do logu panelu).
    """
    # Komentarz: decode_fn(apk, workdir) — testy podstawiają mock bez JDK
    if src_cache_is_hit(apk, clone_root):
        cached = src_cache_src_dir(clone_root, apk)
        log(f"cache HIT: {cached} → {workdir} (bez apktool d)")
        copy_src_tree(cached, workdir)
        return "HIT"

    log(f"cache MISS: apktool d --no-res → {workdir}")
    log("apktool d --no-res: surowe resources.arsc (pełny decode XML gubi layout_height)")
    workdir.parent.mkdir(parents=True, exist_ok=True)
    if decode_fn is None:
        root = app_root or Path(__file__).resolve().parent
        run(apktool_cmd(root, *apktool_decode_args(apk, workdir)))
    else:
        decode_fn(apk, workdir)

    # Komentarz: cache = stock po decode; patch idzie tylko w clone/<APPID>/
    if not (workdir / "AndroidManifest.xml").is_file():
        log("cache MISS: brak AndroidManifest po decode — nie zapisuję cache")
        return "MISS"
    cached = src_cache_src_dir(clone_root, apk)
    copy_src_tree(workdir, cached)
    write_src_cache_marker(apk, clone_root)
    log(f"cache zapisany: {src_cache_base(clone_root, apk)}")
    return "MISS"


def write_identity_json(clone_dir: Path, data: dict) -> None:
    """Zapisz pełny zestaw tożsamości obok instagram_src (do Twojej kompilacji / audytu)."""
    path = clone_dir / "identity.json"
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log(f"Zapisano: {path}")


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(
        description=(
            "Przygotowanie źródeł klona IG: decompile → patch → clone/<prefix>/<APPID>/instagram_src "
            "(bez build/sign)"
        )
    )
    p.add_argument(
        "--apk",
        type=Path,
        default=root / DEFAULT_APK,
        help=f"Źródłowy APK (domyślnie {DEFAULT_APK})",
    )
    p.add_argument(
        "--package",
        required=True,
        help="APPID / package name, np. com.instagram.androia",
    )
    p.add_argument(
        "--android-id",
        required=True,
        help="Wymuszony Android ID (hex string)",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Build.MODEL (lokalnie; warto różnicować per klon)",
    )
    p.add_argument(
        "--fingerprint",
        required=True,
        help=(
            "Build.FINGERPRINT — UNIKALNY per klon "
            f"(przykład: {EXAMPLE_FINGERPRINT})"
        ),
    )
    # Pola z zestawu tożsamości — patch smali + identity.json
    p.add_argument("--gaid", default="", help="Google Advertising ID (GAID)")
    p.add_argument(
        "--media-drm-id",
        default="",
        help="Media DRM ID (base64; w tym APK zwykle brak call-site)",
    )
    p.add_argument("--build-time", default="", help="Czas kompilacji (tylko identity.json)")
    p.add_argument("--boot-time", default="", help="Czas bootu → MobileDeviceUptime")
    p.add_argument(
        "--uptime-ms",
        type=int,
        default=None,
        help="Jawny uptime ms (nadpisuje wyliczenie z --boot-time)",
    )
    p.add_argument("--gps-lat", type=float, default=None, help="Szerokość GPS")
    p.add_argument("--gps-lon", type=float, default=None, help="Długość GPS")
    p.add_argument(
        "--dns",
        default="",
        help="Serwery DNS (tylko identity.json — brak call-site w APK)",
    )
    p.add_argument("--sim-country", default="", help="Kraj SIM, np. PL")
    p.add_argument("--sim-operator", default="", help="Nazwa operatora, np. Play")
    p.add_argument("--sim-operator-code", default="", help="Kod MCC+MNC, np. 26006 (Play PL)")
    p.add_argument(
        "--proxy",
        default="Not set",
        help='Serwer proxy (domyślnie "Not set" = wymuś brak)',
    )
    p.add_argument(
        "--clone-root",
        type=Path,
        default=None,
        help="Katalog bazowy klonów (domyślnie ./clone)",
    )
    p.add_argument(
        "--dir-prefix",
        default="",
        help="Prefix katalogu = alias telefonu (clone/<prefix>/<APPID>/)",
    )
    p.add_argument(
        "--skip-decompile",
        action="store_true",
        help="Użyj istniejącego clone/<APPID>/instagram_src (bez apktool d)",
    )
    # Komentarz: panel (build_clone_argv) i CLI wołają --force przed decode/cache
    p.add_argument(
        "--force",
        action="store_true",
        help="Usuń clone/<APPID>/ przed decode (nie rusza cache _cache/)",
    )
    p.add_argument(
        "--mac",
        default="",
        help="Spoofowany MAC (identity.json; panel przekazuje ten argument)",
    )
    p.add_argument(
        "--imei",
        default="",
        help="Spoofowany IMEI (identity.json; panel przekazuje ten argument)",
    )
    p.add_argument(
        "--build",
        action="store_true",
        help="java -Xmx6g -jar apktool.jar b → instagram_unsigned.apk",
    )
    p.add_argument(
        "--sign",
        action="store_true",
        help="podpisz uber-apk-signer --apk (wymusza --build)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent
    new_pkg = args.package.strip()

    if new_pkg == ORIG_PACKAGE:
        log("BŁĄD: --package nie może być równe oryginałowi (com.instagram.android)")
        return 2
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+", new_pkg):
        log(f"BŁĄD: nieprawidłowy package: {new_pkg}")
        return 2

    # Komentarz: clone/<telefon>/<APPID>/ — ten sam APPID na dwóch telefonach
    clone_root: Path = args.clone_root or (root / "clone")
    dir_prefix = sanitize_dir_prefix(getattr(args, "dir_prefix", "") or "")
    clone_dir: Path = clone_package_dir(clone_root, new_pkg, dir_prefix)
    workdir: Path = clone_dir / "instagram_src"

    apk = resolve_input_apk(root, args.apk)
    if not args.skip_decompile and not apk.is_file():
        log(f"BŁĄD: brak APK: {apk}")
        return 2

    log(f"APPID:       {new_pkg}")
    log(f"Android ID:  {args.android_id}")
    log(f"Model:       {args.model}")
    log(f"Fingerprint: {args.fingerprint}")
    log(f"Workdir:     {workdir}")

    # --- decompile / cache (PO decode, PRZED patch) ---
    if not args.skip_decompile:
        if clone_dir.exists():
            # Komentarz: --force czyści tylko clone/<APPID>/, NIE clone/_cache/
            if args.force:
                log(f"Usuwam katalog klona: {clone_dir} (cache _cache/ zostaje)")
                wipe_clone_dir(clone_dir)
            else:
                log(
                    f"BŁĄD: {clone_dir} istnieje — użyj --force albo --skip-decompile"
                )
                return 2
        clone_dir.mkdir(parents=True, exist_ok=True)
        # Komentarz: HIT = kopia cache; MISS = apktool d + zapis cache
        cache_status = populate_instagram_src(
            apk, workdir, clone_root, app_root=root)
        log(f"cache {cache_status}")
    else:
        if not (workdir / "AndroidManifest.xml").is_file():
            log(f"BŁĄD: brak AndroidManifest.xml w {workdir}")
            return 2

    # --no-res zostawia binarny AXML — dekoduj sam manifest, bez layoutów
    manifest_path = workdir / "AndroidManifest.xml"
    if ensure_text_manifest(manifest_path):
        log("Zdekodowano binarny AndroidManifest.xml (AXML → tekst)")

    # --- patch ---
    patch_manifest(manifest_path, new_pkg)
    # Komentarz: apktool b (no-res) nie woła aapt2 — wracamy do AXML
    compiled_manifest = compile_patched_manifest(manifest_path)
    log("Zakodowano zpatchowany AndroidManifest.xml (tekst → AXML)")
    ensure_no_build_sput(workdir)
    aid_n = patch_smali_android_id(workdir, args.android_id)
    build_n = patch_smali_build(workdir, args.model, args.fingerprint)
    gaid_n = patch_smali_gaid(workdir, args.gaid)
    sim_n = patch_smali_sim(
        workdir, args.sim_country, args.sim_operator, args.sim_operator_code
    )
    gps_n = patch_smali_gps(workdir, args.gps_lat, args.gps_lon)
    proxy_n = patch_smali_proxy(workdir, args.proxy)
    uptime_ms = compute_uptime_ms(args.boot_time, args.uptime_ms)
    uptime_n = patch_smali_uptime(workdir, uptime_ms)
    drm_n = try_patch_media_drm(workdir, args.media_drm_id)

    log(
        "Patch smali: "
        f"AndroidID={aid_n}, Build={build_n}, GAID={gaid_n}, SIM={sim_n}, "
        f"GPS={gps_n}, Proxy={proxy_n}, Uptime={uptime_n}, MediaDRM={drm_n}"
    )

    # Komentarz: pełny zestaw tożsamości + status patchy smali
    identity = {
        "package": new_pkg,
        "model": args.model,
        "android_id": args.android_id,
        "media_drm_id": args.media_drm_id,
        "fingerprint": args.fingerprint,
        "gaid": args.gaid,
        "mac": getattr(args, "mac", "") or "",
        "imei": getattr(args, "imei", "") or "",
        "build_time": args.build_time,
        "boot_time": args.boot_time,
        "uptime_ms": uptime_ms,
        "gps": {
            "lat": args.gps_lat,
            "lon": args.gps_lon,
        },
        "dns": [x.strip() for x in args.dns.split(",") if x.strip()],
        "sim": {
            "country": args.sim_country,
            "operator": args.sim_operator,
            "operator_code": args.sim_operator_code,
        },
        "proxy": args.proxy,
        "blocked_permissions": BLOCK_PERMISSIONS,
        "instagram_src": str(workdir),
        "dir_prefix": dir_prefix,
        "apk": None,
        "apk_signed": False,
        "smali_patched": {
            "android_id": aid_n > 0,
            "model": build_n > 0,
            "fingerprint": build_n > 0,
            "gaid": gaid_n > 0,
            "media_drm_id": drm_n > 0,
            "gps": gps_n > 0,
            "dns": False,
            "sim": sim_n > 0,
            "build_time": False,
            "boot_time_as_uptime": uptime_n > 0,
            "proxy": proxy_n > 0,
        },
        "patch_counts": {
            "android_id": aid_n,
            "build": build_n,
            "gaid": gaid_n,
            "sim": sim_n,
            "gps": gps_n,
            "proxy": proxy_n,
            "uptime": uptime_n,
            "media_drm": drm_n,
        },
        "notes": (
            "Źródła gotowe. Skompiluj: "
            f"java -Xmx6g -jar apktool.jar b {workdir} "
            f"-o {UNSIGNED_APK_NAME} && podpisz uber-apk-signer --apk. "
            "Media DRM ID / DNS / Build.TIME: brak call-site w tym APK "
            "(zapisane tylko w identity.json)."
        ),
    }

    do_build = bool(getattr(args, "build", False) or getattr(args, "sign", False))
    unsigned_apk = clone_dir / UNSIGNED_APK_NAME
    signed_apk = None
    if do_build:
        # Komentarz: jak ręcznie: java -Xmx6g -jar apktool.jar b <src> -o instagram_unsigned.apk
        # `b --no-res` w apktool 3.0.3 = Unrecognized option — zasoby raw zostają z `d --no-res`
        run(apktool_cmd(root, "b", str(workdir), "-o", str(unsigned_apk)))
        # Komentarz: no-res + tekstowy manifest = ZIP bez AndroidManifest.xml
        ensure_unsigned_apk_manifest(unsigned_apk, compiled_manifest, new_pkg)
        # Komentarz: base-only nie ma splash 0x7f082060 — doklej split_config.xxhdpi
        split_apks = find_split_apks(apk)
        if split_apks and overlay_splits_onto_apk(unsigned_apk, split_apks):
            log(
                "Doklejono zasoby density ze splitów Play "
                f"(drawable 0x{SPLASH_DRAWABLE_ID:08x})"
            )
        if unsigned_apk.is_file() and not arsc_has_entry_value(
            read_apk_arsc(unsigned_apk), SPLASH_DRAWABLE_ID
        ):
            log(
                "UWAGA: brak 0x7f082060 w resources.arsc — "
                "zciągnij splity (`pm path`) i zmerguj przed klonem"
            )
        identity["apk"] = str(unsigned_apk)
        identity["notes"] = f"APK niepodpisany: {unsigned_apk}. " + identity["notes"]
        if getattr(args, "sign", False):
            signed_apk = clone_dir / f"{new_pkg}.apk"
            sign_apk(root, unsigned_apk, signed_apk)
            identity["apk"] = str(signed_apk)
            identity["apk_signed"] = True
            identity["notes"] = f"APK podpisany: {signed_apk}. " + identity["notes"]

    write_identity_json(clone_dir, identity)

    log("Źródła gotowe.")
    log(f"Katalog: {clone_dir}")
    log("  └─ instagram_src/  ← podmienione źródła")
    log("  └─ identity.json   ← pełny zestaw danych")
    if signed_apk:
        log(f"  └─ {signed_apk.name}  ← podpisany APK")
    elif do_build:
        log(f"  └─ {UNSIGNED_APK_NAME}  ← niepodpisany APK")
    log("OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        log(f"Komenda zakończona kodem {e.returncode}")
        raise SystemExit(e.returncode)
    except Exception as e:
        log(f"BŁĄD: {e}")
        raise SystemExit(1)
