"""Account config loader + validator.

Ładuje account.json + odpowiadający devices/<alias>.json,
łączy w jeden obiekt, waliduje obecność krytycznych pól.
Twarde błędy przy starcie — brakujące pole = odmowa uruchomienia sesji.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_ROOT = Path(__file__).parent

class ConfigError(Exception):
    """Raised when config is missing required fields or references broken."""


@dataclass
class AccountConfig:
    account_id: str
    ig_username: str
    device: dict
    humanizer: dict
    limits: dict
    flows: list
    toggles: dict
    niche: dict
    ai: dict
    recovery: dict
    network: dict
    meta: dict
    plugins: dict
    raw: dict = field(repr=False)

    def step_enabled(self, step_name: str) -> bool:
        s = self.toggles.get("steps", {}).get(step_name, {})
        return bool(s.get("enabled", True))

    def step_option(self, step_name: str, key: str, default=None):
        return self.toggles.get("steps", {}).get(step_name, {}).get(key, default)

    def plugin_settings(self, plugin_id: str) -> dict:
        """Ustawienia pluginu z configu (bez defaultów katalogu — te dokłada
        `v2.plugins.store.read_settings`, żeby schema.py nie zależało od katalogu)."""
        return self.plugins.get(plugin_id) or {}

    def plugin_enabled(self, plugin_id: str) -> bool:
        # nowa lokalizacja: plugins.<id>.enabled; stara (toggles.plugins) jako fallback
        p = self.plugins.get(plugin_id)
        if isinstance(p, dict) and "enabled" in p:
            return bool(p["enabled"])
        legacy = self.toggles.get("plugins", {}).get(plugin_id, {})
        return bool(legacy.get("enabled", False))


REQUIRED_ACCOUNT_PATHS = [
    ("meta", "account_id"),
    ("meta", "ig_username"),
    ("device", "device_alias"),
    # app_clone_package opcjonalne — panel może wyczyścić APPID (zwolnienie klona);
    # runtime i tak wymaga niepustego pakietu przy starcie IG.
    ("humanizer", "profile_name"),
]

REQUIRED_DEVICE_PATHS = [
    ("adb_id",),
    ("resolution",),
    ("uiautodev", "base_url"),
]


def _get(d: dict, path: tuple) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _validate(cfg: dict, required, label: str):
    missing = [".".join(p) for p in required if _get(cfg, p) in (None, "", "REPLACE")]
    if missing:
        raise ConfigError(f"{label}: brakujące/niewypełnione pola: {missing}")


def load_account(account_id: str, config_root: Path | None = None) -> AccountConfig:
    root = config_root or CONFIG_ROOT
    account_path = root / "accounts" / f"{account_id}.json"
    if not account_path.exists():
        raise ConfigError(f"config konta nie istnieje: {account_path}")
    acc = json.loads(account_path.read_text(encoding="utf-8"))
    _validate(acc, REQUIRED_ACCOUNT_PATHS, f"account:{account_id}")

    dev_alias = acc["device"]["device_alias"]
    dev_path = root / "devices" / f"{dev_alias}.json"
    if not dev_path.exists():
        raise ConfigError(f"account {account_id} odsyła do device '{dev_alias}' — brak {dev_path}")
    dev = json.loads(dev_path.read_text(encoding="utf-8"))
    _validate(dev, REQUIRED_DEVICE_PATHS, f"device:{dev_alias}")

    # scalanie danych sprzętowych do sekcji device konta (referencja przez alias)
    merged_device = {**acc["device"], "_hardware": dev}

    return AccountConfig(
        account_id  = acc["meta"]["account_id"],
        ig_username = acc["meta"]["ig_username"],
        device      = merged_device,
        humanizer   = acc.get("humanizer", {}),
        limits      = acc.get("limits", {}),
        flows       = acc.get("flows", []),
        toggles     = acc.get("toggles", {}),
        niche       = acc.get("niche", {}),
        ai          = acc.get("ai", {}),
        recovery    = acc.get("recovery", {}),
        network     = acc.get("network", {}),
        meta        = acc.get("meta", {}),
        plugins     = acc.get("plugins", {}),
        raw         = acc,
    )


def new_account_from_template(alias: str, ig_username: str, device_alias: str,
                              app_clone_package: str | None = None,
                              config_root: Path | None = None) -> Path:
    """Kopiuje szablon i wypełnia minimalne pola. Zwraca ścieżkę do nowego pliku.

    `app_clone_package` — który klon IG na urządzeniu ma sterować to konto;
    domyślnie zostaje `com.instagram.android` z szablonu (oryginalna instalacja).
    """
    root = config_root or CONFIG_ROOT
    tmpl_path = root / "_template" / "account.json"
    dst = root / "accounts" / f"{alias}.json"
    if dst.exists():
        raise ConfigError(f"account '{alias}' już istnieje: {dst}")
    tmpl = json.loads(tmpl_path.read_text(encoding="utf-8"))
    tmpl["meta"]["account_id"]  = alias
    tmpl["meta"]["alias"]       = alias
    tmpl["meta"]["ig_username"] = ig_username
    tmpl["device"]["device_alias"] = device_alias
    if app_clone_package:
        tmpl["device"]["app_clone_package"] = app_clone_package
        # Komentarz: tożsamość klona z devices/<alias>.clones[].identity
        ident = _identity_from_device(root, device_alias, app_clone_package)
        if ident:
            if ident.get("mac"):
                tmpl["device"]["spoofed_mac"] = ident["mac"]
            if ident.get("imei"):
                tmpl["device"]["spoofed_imei"] = ident["imei"]
            if ident.get("android_id"):
                tmpl["device"]["spoofed_android_id"] = ident["android_id"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(tmpl, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def _identity_from_device(config_root: Path, device_alias: str, package: str) -> dict | None:
    """identity klona z devices/<alias>.json → clones[]."""
    path = config_root / "devices" / f"{device_alias}.json"
    if not path.exists():
        return None
    try:
        dev = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for c in dev.get("clones") or []:
        if c.get("package") == package and isinstance(c.get("identity"), dict):
            return c["identity"]
    return None
