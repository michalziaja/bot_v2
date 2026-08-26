"""Tryb samolotowy przez ADB — lekki test Wi-Fi (bez SIM / bez pełnego flow).

Cel: sprawdzić, że airplane ON->OFF działa i Wi-Fi wraca.
Nie podpinać do schedulera / panelu / między sesjami.
"""
from __future__ import annotations

import subprocess
import time
from typing import Callable


# Runner ADB: (serial, shell_cmd) -> stdout+stderr. Testy mogą podmienić.
AdbShell = Callable[[str, str], str]


def default_adb_shell(serial: str, command: str) -> str:
    """Surowy `adb -s SERIAL shell ...` — bez uiautodev / scrcpy."""
    r = subprocess.run(
        ["adb", "-s", serial, "shell", command],
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = (r.stdout or "") + (r.stderr or "")
    return out.strip()


def airplane_flag(serial: str, shell: AdbShell = default_adb_shell) -> str:
    """0 = wył, 1 = wł (settings global). Puste = nie udało się odczytać."""
    return shell(serial, "settings get global airplane_mode_on").strip()


def wifi_on_flag(serial: str, shell: AdbShell = default_adb_shell) -> str:
    """0/1 z settings — przybliżenie, czy Wi-Fi wróciło po OFF."""
    return shell(serial, "settings get global wifi_on").strip()


def set_airplane(
    serial: str,
    enabled: bool,
    shell: AdbShell = default_adb_shell,
) -> None:
    """Włącz/wyłącz airplane na typowym Androidzie (Samsung A50 / API 30).

    Kolejność:
      1) `cmd connectivity airplane-mode` (Android 11+)
      2) fallback: settings + broadcast (klasyczny OEM / Magisk)
    """
    state = "enable" if enabled else "disable"
    flag = "1" if enabled else "0"
    ez = "true" if enabled else "false"

    # Preferowana ścieżka na nowszych API
    out = shell(serial, f"cmd connectivity airplane-mode {state}")
    # Jeśli cmd nie istnieje / permission denied — klasyczny fallback
    low = (out or "").lower()
    if "unknown" in low or "not found" in low or "denied" in low or "exception" in low:
        shell(serial, f"settings put global airplane_mode_on {flag}")
        # Broadcast wymusza zastosowanie bez rebootu (często wymaga shell/root)
        shell(
            serial,
            "am broadcast -a android.intent.action.AIRPLANE_MODE "
            f"--ez state {ez}",
        )
    else:
        # Dla spójności odczytu `settings get` i tak ustawiamy flagę
        shell(serial, f"settings put global airplane_mode_on {flag}")


def toggle_airplane(
    serial: str,
    pause_s: float = 3.0,
    shell: AdbShell = default_adb_shell,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict:
    """Airplane ON -> pauza -> OFF. Zwraca krótki status (flagi przed/po).

    `log` / `shell` / `sleep` wstrzykiwalne pod test mock — bez prawdziwego ADB.
    Printy bez znaków spoza cp1250 (konsola Windows).
    """
    before = airplane_flag(serial, shell)
    wifi_before = wifi_on_flag(serial, shell)
    log(f"[airplane] serial={serial}  przed: airplane={before}  wifi_on={wifi_before}")

    log("[airplane] WL — tryb samolotowy ON")
    set_airplane(serial, True, shell=shell)
    on_flag = airplane_flag(serial, shell)
    log(f"[airplane] status po ON: airplane={on_flag}")

    log(f"[airplane] czekam {pause_s:g}s...")
    sleep(float(pause_s))

    log("[airplane] WYL — tryb samolotowy OFF")
    set_airplane(serial, False, shell=shell)
    # Krótka chwila na podniesienie Wi-Fi
    sleep(1.5)
    after = airplane_flag(serial, shell)
    wifi_after = wifi_on_flag(serial, shell)
    log(f"[airplane] po OFF: airplane={after}  wifi_on={wifi_after}")
    log("[airplane] gotowe (bez SIM: sprawdzamy tylko Wi-Fi / airplane flag)")

    return {
        "serial": serial,
        "airplane_before": before,
        "airplane_on": on_flag,
        "airplane_after": after,
        "wifi_before": wifi_before,
        "wifi_after": wifi_after,
    }
