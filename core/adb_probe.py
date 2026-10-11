"""Odczyt hardware z ADB — nowe telefony bez ręcznego wypełniania configu.

Panel zapisuje alias + serial; resolution / model / dpi / Android / klony IG
bot zczytuje z podłączonego urządzenia i dopisuje do `devices/<alias>.json`.
Bez uiautodev — surowy `adb`, żeby działało już przy rejestracji.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable

# Pola, które ADB jest źródłem prawdy (nie alias / clones / uiautodev).
HARDWARE_FIELDS = (
    "model",
    "product",
    "manufacturer",
    "android_version",
    "resolution",
    "dpi",
)

DEFAULT_UIAUTO = {"base_url": "http://127.0.0.1:20242"}
DEFAULT_SCRCPY_PORT = 27183
DEFAULT_IG_PACKAGE = "com.instagram.android"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

AdbShell = Callable[[str, str], str]


class AdbProbeError(RuntimeError):
    """Telefon nie odpowiada / adb nie widzi seriala."""


def adb(args: list[str], *, timeout: float = 8.0) -> str:
    """`adb …` — stdout+stderr, bez wyjątku przy exit ≠ 0."""
    try:
        r = subprocess.run(
            ["adb", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError as e:
        raise AdbProbeError("brak `adb` w PATH") from e
    except subprocess.TimeoutExpired as e:
        raise AdbProbeError(f"adb timeout ({timeout:.0f}s): {' '.join(args)}") from e
    return ((r.stdout or "") + (r.stderr or "")).strip()


def default_adb_shell(serial: str, command: str) -> str:
    """Surowy `adb -s SERIAL shell …` — ten sam kontrakt co airplane.py."""
    return adb(["-s", serial, "shell", command])


def list_adb_devices() -> list[dict]:
    """Podłączone serials z `adb devices -l` (pomija `offline` / unauthorized)."""
    try:
        out = adb(["devices", "-l"])
    except AdbProbeError:
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        # serial  state  key:value key:value …
        m = re.match(r"^(\S+)\s+(\S+)(?:\s+(.*))?$", line)
        if not m:
            continue
        serial, state, rest = m.group(1), m.group(2), m.group(3) or ""
        extra = {}
        for tok in rest.split():
            if ":" in tok:
                k, v = tok.split(":", 1)
                extra[k] = v
        rows.append({
            "serial": serial,
            "state": state,
            "model": extra.get("model"),
            "product": extra.get("product"),
            "usb": extra.get("usb"),
        })
    return rows


def normalize_model(raw: str | None) -> str | None:
    """SM-A520F → SM_A520F — spójnie z nazwą pliku kalibracji."""
    s = (raw or "").strip()
    if not s:
        return None
    return s.replace("-", "_").replace(" ", "_")


def _parse_wm_size(out: str) -> list[int] | None:
    """Preferuje Override size, potem Physical. Format `1080x1920`."""
    override = re.search(r"Override size:\s*(\d+)x(\d+)", out, re.I)
    physical = re.search(r"Physical size:\s*(\d+)x(\d+)", out, re.I)
    plain = re.search(r"(\d+)x(\d+)", out)
    m = override or physical or plain
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        return None
    return [w, h]


def _parse_wm_density(out: str) -> int | None:
    override = re.search(r"Override density:\s*(\d+)", out, re.I)
    physical = re.search(r"Physical density:\s*(\d+)", out, re.I)
    plain = re.search(r"(\d+)", out)
    m = override or physical or plain
    if not m:
        return None
    dpi = int(m.group(1))
    return dpi if dpi > 0 else None


def probe_hardware(serial: str, shell: AdbShell | None = None) -> dict:
    """Zczytaj model / rozdzielczość / dpi / Android z podłączonego telefonu."""
    sh = shell or (lambda cmd, _s=serial: default_adb_shell(_s, cmd))
    try:
        model = sh("getprop ro.product.model")
        product = sh("getprop ro.product.device")
        manufacturer = sh("getprop ro.product.manufacturer")
        android = sh("getprop ro.build.version.release")
        size_out = sh("wm size")
        dens_out = sh("wm density")
    except AdbProbeError:
        raise
    except Exception as e:
        raise AdbProbeError(f"adb shell nieudany dla {serial}: {e}") from e

    resolution = _parse_wm_size(size_out or "")
    dpi = _parse_wm_density(dens_out or "")
    if not resolution:
        raise AdbProbeError(
            f"nie odczytano rozdzielczości z `{serial}` (wm size={size_out!r})"
        )
    return {
        "model": normalize_model(model),
        "product": (product or "").strip() or None,
        "manufacturer": (manufacturer or "").strip() or None,
        "android_version": (android or "").strip() or None,
        "resolution": resolution,
        "dpi": dpi,
    }


def list_ig_packages(serial: str, shell: AdbShell | None = None) -> list[str]:
    """Pakiety IG (oryginał + klony App Clonera), bez `*installer`."""
    sh = shell or (lambda cmd, _s=serial: default_adb_shell(_s, cmd))
    try:
        out = sh("pm list packages")
    except AdbProbeError:
        return []
    found = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        pkg = line[len("package:"):].strip()
        if pkg == DEFAULT_IG_PACKAGE or pkg.startswith("com.instagram.andro"):
            found.append(pkg)
    # oryginał pierwszy, reszta alfabetycznie
    orig = [p for p in found if p == DEFAULT_IG_PACKAGE]
    clones = sorted(p for p in found if p != DEFAULT_IG_PACKAGE)
    return orig + clones


def hardware_gaps(doc: dict) -> list[str]:
    """Które pola hardware są puste — wtedy bot sam zczytuje z ADB."""
    missing = []
    for key in HARDWARE_FIELDS:
        val = doc.get(key)
        if val in (None, "", "REPLACE"):
            missing.append(key)
        elif key == "resolution" and not (
            isinstance(val, (list, tuple)) and len(val) == 2
            and all(isinstance(x, int) and x > 0 for x in val)
        ):
            missing.append(key)
    if not (doc.get("uiautodev") or {}).get("base_url"):
        missing.append("uiautodev.base_url")
    return missing


def next_scrcpy_port(config_root: Path, *, exclude_alias: str | None = None) -> int:
    """Pierwszy wolny port scrcpy — każdy telefon osobny, żeby nie kolidowały."""
    used: set[int] = set()
    devices_dir = Path(config_root) / "devices"
    if devices_dir.is_dir():
        for p in devices_dir.glob("*.json"):
            if exclude_alias and p.stem == exclude_alias:
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            port = (d.get("scrcpy") or {}).get("port")
            if port:
                try:
                    used.add(int(port))
                except (TypeError, ValueError):
                    pass
    port = DEFAULT_SCRCPY_PORT
    while port in used:
        port += 1
    return port


def _clones_from_packages(packages: list[str]) -> list[dict]:
    if not packages:
        packages = [DEFAULT_IG_PACKAGE]
    out = []
    for i, pkg in enumerate(packages, start=1):
        note = "oryginalna instalacja" if pkg == DEFAULT_IG_PACKAGE else "wykryty klon IG"
        out.append({"slot": i, "package": pkg, "note": note})
    return out


def apply_probe(
    doc: dict,
    probed: dict,
    *,
    config_root: Path | None = None,
    ig_packages: list[str] | None = None,
    force_hardware: bool = False,
) -> bool:
    """Wlej wynik probe do dokumentu urządzenia. Zwraca czy cokolwiek zmieniło."""
    changed = False
    for key in HARDWARE_FIELDS:
        new = probed.get(key)
        if new in (None, "", []):
            continue
        old = doc.get(key)
        empty = old in (None, "", "REPLACE")
        if key == "resolution":
            empty = empty or not (
                isinstance(old, (list, tuple)) and len(old) == 2)
        if empty or force_hardware:
            if old != new:
                doc[key] = new
                changed = True

    if not (doc.get("uiautodev") or {}).get("base_url"):
        doc["uiautodev"] = dict(DEFAULT_UIAUTO)
        changed = True

    scrcpy = doc.get("scrcpy")
    if not isinstance(scrcpy, dict) or not scrcpy.get("port"):
        port = DEFAULT_SCRCPY_PORT
        if config_root is not None:
            port = next_scrcpy_port(config_root, exclude_alias=doc.get("alias"))
        doc["scrcpy"] = {
            "port": port,
            "max_size": (scrcpy or {}).get("max_size", 900) if isinstance(scrcpy, dict) else 900,
            "max_fps": (scrcpy or {}).get("max_fps", 15) if isinstance(scrcpy, dict) else 15,
        }
        changed = True

    if ig_packages:
        existing = list(doc.get("clones") or [])
        have = {c.get("package") for c in existing if c.get("package")}
        if not have:
            doc["clones"] = _clones_from_packages(ig_packages)
            changed = True
        else:
            # dopisz nowo wykryte klony, nic nie kasuj
            next_slot = max((int(c.get("slot") or 0) for c in existing), default=0)
            for pkg in ig_packages:
                if pkg in have:
                    continue
                next_slot += 1
                existing.append({
                    "slot": next_slot,
                    "package": pkg,
                    "note": "wykryty klon IG",
                })
                changed = True
            doc["clones"] = existing
    elif not doc.get("clones"):
        doc["clones"] = _clones_from_packages([DEFAULT_IG_PACKAGE])
        changed = True

    return changed


def _write_device(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def hydrate_device_doc(
    doc: dict,
    *,
    persist_path: Path | None = None,
    config_root: Path | None = None,
    shell: AdbShell | None = None,
    force: bool = False,
) -> dict:
    """Uzupełnij hardware z ADB gdy brakuje pól (albo `force`).

    Zwraca `{hydrated, probed, error}`. Mutuje `doc`; zapisuje gdy persist_path.
    """
    serial = (doc.get("adb_id") or "").strip()
    result = {"hydrated": False, "probed": None, "error": None, "changed": False}
    if not serial:
        result["error"] = "brak adb_id"
        return result

    gaps = hardware_gaps(doc)
    if not force and not gaps:
        return result

    try:
        probed = probe_hardware(serial, shell=shell)
        packages = list_ig_packages(serial, shell=shell)
    except AdbProbeError as e:
        result["error"] = str(e)
        return result

    result["probed"] = probed
    # force albo luki: nadpisz hardware faktami z ADB (zły model z formularza)
    changed = apply_probe(
        doc, probed,
        config_root=config_root,
        ig_packages=packages,
        force_hardware=True,
    )
    result["changed"] = changed
    result["hydrated"] = True
    if changed and persist_path is not None:
        _write_device(persist_path, doc)
    return result


def hydrate_device_file(
    path: Path,
    *,
    config_root: Path | None = None,
    force: bool = False,
) -> dict:
    """Wczytaj JSON urządzenia, zhydratuj, zapisz jeśli coś się zmieniło."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not doc.get("alias"):
        doc["alias"] = Path(path).stem
    root = config_root or Path(path).parent.parent
    result = hydrate_device_doc(
        doc, persist_path=Path(path), config_root=root, force=force,
    )
    result["doc"] = doc
    return result
