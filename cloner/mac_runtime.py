"""Apply / restore spoofowanego MAC na wlan0 (Magisk su) na czas sesji."""
from __future__ import annotations

import atexit
import re
import time
from pathlib import Path
from typing import Callable

# Komentarz: serial → oryginalny MAC do restore po crashu / DeviceLock.release
_PENDING: dict[str, str] = {}
_DEVICES: dict[str, object] = {}
_ATEXIT_DONE = False
MAC_RE = re.compile(r"(?i)([0-9a-f]{2}(?::[0-9a-f]{2}){5})")


class MacSpoofError(RuntimeError):
    """Nie udało się odczytać / ustawić MAC (brak roota, zły iface)."""


def parse_mac(text: str) -> str | None:
    """Wyciągnij pierwszy MAC z wyjścia `cat .../address` / ip link."""
    m = MAC_RE.search(text or "")
    return m.group(1).lower() if m else None


def persist_path(data_root: Path, account_alias: str) -> Path:
    """data/accounts/<alias>/last_real_mac.txt — restore po crashu procesu."""
    return Path(data_root) / "accounts" / account_alias / "last_real_mac.txt"


def _ensure_atexit() -> None:
    global _ATEXIT_DONE
    if _ATEXIT_DONE:
        return
    atexit.register(_restore_all_best_effort)
    _ATEXIT_DONE = True


def _restore_all_best_effort() -> None:
    for serial, original in list(_PENDING.items()):
        dev = _DEVICES.get(serial)
        if dev is None or not original:
            continue
        try:
            set_iface_mac(dev, original)
        except Exception:
            pass
    _PENDING.clear()
    _DEVICES.clear()


def read_iface_mac(device, iface: str = "wlan0") -> str:
    """Aktualny MAC iface przez su (Magisk)."""
    out = device.su_shell(f"cat /sys/class/net/{iface}/address")
    mac = parse_mac(out)
    if not mac:
        raise MacSpoofError(f"nie odczytano MAC {iface}: {out!r}")
    return mac


def set_iface_mac(device, mac: str, iface: str = "wlan0") -> None:
    """ip link set address — wymaga root."""
    mac_n = parse_mac(mac)
    if not mac_n:
        raise MacSpoofError(f"zły MAC: {mac}")
    device.su_shell(f"ip link set {iface} down")
    device.su_shell(f"ip link set {iface} address {mac_n}")
    device.su_shell(f"ip link set {iface} up")


def wifi_is_on(device) -> bool:
    """settings global wifi_on == 1."""
    out = (device.shell("settings get global wifi_on") or "").strip()
    return out == "1"


def wait_wifi(device, timeout_s: float = 20.0, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Czekaj aż Wi-Fi wróci po zmianie MAC."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if wifi_is_on(device):
            return True
        sleep(0.5)
    return False


def apply_wifi_mac(
    device,
    spoofed_mac: str,
    persist: Path,
    iface: str = "wlan0",
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Zapamiętaj oryginał, ustaw spoof, czekaj na Wi-Fi.

    Zwraca {original, spoofed, wifi_ok}."""
    spoof = parse_mac(spoofed_mac)
    if not spoof:
        raise MacSpoofError("puste spoofed_mac")
    original = read_iface_mac(device, iface=iface)
    persist.parent.mkdir(parents=True, exist_ok=True)
    persist.write_text(original + "\n", encoding="utf-8")
    serial = getattr(device, "serial", "") or ""
    if serial:
        _PENDING[serial] = original
        _DEVICES[serial] = device
    _ensure_atexit()
    set_iface_mac(device, spoof, iface=iface)
    ok = wait_wifi(device, sleep=sleep)
    return {"original": original, "spoofed": spoof, "wifi_ok": ok}


def restore_wifi_mac(
    device,
    persist: Path,
    iface: str = "wlan0",
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Przywróć MAC z pliku / _PENDING. Idempotentne gdy brak pliku."""
    serial = getattr(device, "serial", "") or ""
    original = None
    if persist.exists():
        original = parse_mac(persist.read_text(encoding="utf-8"))
    if not original and serial:
        original = parse_mac(_PENDING.get(serial) or "")
    if not original:
        return {"restored": False, "reason": "brak zapisanego MAC"}
    set_iface_mac(device, original, iface=iface)
    wait_wifi(device, timeout_s=12.0, sleep=sleep)
    try:
        persist.unlink(missing_ok=True)
    except OSError:
        pass
    if serial:
        _PENDING.pop(serial, None)
        _DEVICES.pop(serial, None)
    return {"restored": True, "mac": original}


def restore_for_serial(serial: str, device=None) -> None:
    """Hak DeviceLock.release — przywróć MAC gdy znamy device."""
    if not serial:
        return
    original = _PENDING.get(serial)
    dev = device or _DEVICES.get(serial)
    if dev is not None and original:
        try:
            set_iface_mac(dev, original)
        except Exception:
            pass
    _PENDING.pop(serial, None)
    _DEVICES.pop(serial, None)


def _on_device_lock_release(serial: str) -> None:
    """DeviceLock.release — przywróć MAC zanim lock spadnie."""
    restore_for_serial(serial)


# Komentarz: rejestracja przy imporcie kroku / joba — release sesji sprząta wlan0
from v2.core.device_lock import add_release_callback
add_release_callback(_on_device_lock_release)
