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

from v2.core.adb_probe import hardware_gaps, hydrate_device_doc

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
    # humanizer.profile_name sprawdzamy po merge z bot.json (konto może dziedziczyć).
]

# Pola, które muszą być w effective (bot + override konta), niekoniecznie w pliku konta.
REQUIRED_EFFECTIVE_PATHS = [
    ("humanizer", "profile_name"),
]

# Domyślne globalne — gdy brak config/bot.json albo brakuje klucza.
DEFAULT_BOT = {
    "limits": {
        "daily_likes": 100,
        "daily_follows": 30,
        "daily_comments": 15,
        "daily_dms": 0,
        "per_hour_caps": {
            "likes": 20,
            "follows": 8,
            "comments": 4,
        },
        "cooldown_after_warning_hours": 24,
    },
    "humanizer": {
        "profile_name": "default",
        "speed": 1.0,
        "after_each_step": False,
        "overrides": {},
    },
    "toggles": {
        "steps": {
            "stats_snapshot": {
                "enabled": True,
                "on_session_start": True,
                "on_session_end": False,
            },
            "wake": {"enabled": True},
            "unlock": {"enabled": True},
            "close_ig": {"enabled": True},
            "sleep_screen": {"enabled": True},
        },
    },
}

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


def deep_merge(base: dict, override: dict) -> dict:
    """Złącz słowniki: klucz z override wygrywa, w tym `0` i `False`.

    Zagnieżdżone dict-y mergujemy rekurencyjnie — `limits.daily_likes` na
    koncie nie kasuje reszty limitów z `bot.json`. Brak klucza w override
    = wartość z bazy.
    """
    if not isinstance(base, dict):
        base = {}
    if not isinstance(override, dict):
        return dict(base)
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_bot(config_root: Path | None = None) -> dict:
    """Globalne limity / humanizer / kroki z `config/bot.json`.

    Plik jest źródłem dziedziczenia dla kont bez własnego klucza. Brak pliku
    albo dziura w JSON → `DEFAULT_BOT`. Zwracamy kopię, żeby wołający nie
    mutował stałej.
    """
    root = config_root or CONFIG_ROOT
    path = root / "bot.json"
    raw: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            raw = {}
    return {
        "limits": deep_merge(DEFAULT_BOT["limits"], raw.get("limits") or {}),
        "humanizer": deep_merge(DEFAULT_BOT["humanizer"], raw.get("humanizer") or {}),
        "toggles": deep_merge(DEFAULT_BOT["toggles"], raw.get("toggles") or {}),
    }


# Klucze, które panel może zapisać w bot.json (kształt jak fragment account.json).
_BOT_LIMIT_INTS = (
    "daily_likes", "daily_follows", "daily_comments", "daily_dms",
    "cooldown_after_warning_hours",
)
_BOT_HOUR_CAP_KEYS = ("likes", "follows", "comments")
_BOT_STEP_NAMES = ("wake", "unlock", "stats_snapshot", "close_ig", "sleep_screen")


def _nonneg_int(value, label: str) -> int:
    """Liczba całkowita ≥ 0 — bool odrzucamy (True == 1 w Pythonie)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"bot.json: {label} musi być liczbą ≥ 0")
    n = int(value)
    if n < 0:
        raise ConfigError(f"bot.json: {label} musi być liczbą ≥ 0")
    return n


def _validate_bot_section(key: str, section: Any) -> dict:
    """Sprawdź jedną sekcję payloadu POST /api/bot-config."""
    if not isinstance(section, dict):
        raise ConfigError(f"bot.json: {key} musi być obiektem")
    if key == "limits":
        for name in _BOT_LIMIT_INTS:
            if name in section:
                section[name] = _nonneg_int(section[name], f"limits.{name}")
        caps = section.get("per_hour_caps")
        if caps is not None:
            if not isinstance(caps, dict):
                raise ConfigError("bot.json: limits.per_hour_caps musi być obiektem")
            for cap_key in _BOT_HOUR_CAP_KEYS:
                if cap_key in caps:
                    caps[cap_key] = _nonneg_int(
                        caps[cap_key], f"limits.per_hour_caps.{cap_key}")
    elif key == "humanizer":
        if "profile_name" in section:
            name = section["profile_name"]
            if not isinstance(name, str) or not name.strip():
                raise ConfigError("bot.json: humanizer.profile_name nie może być puste")
            section["profile_name"] = name.strip()
        if "speed" in section:
            speed = section["speed"]
            if isinstance(speed, bool) or not isinstance(speed, (int, float)) or float(speed) <= 0:
                raise ConfigError("bot.json: humanizer.speed musi być liczbą > 0")
            section["speed"] = float(speed)
        if "after_each_step" in section and not isinstance(section["after_each_step"], bool):
            raise ConfigError("bot.json: humanizer.after_each_step musi być bool")
        if "overrides" in section and not isinstance(section["overrides"], dict):
            raise ConfigError("bot.json: humanizer.overrides musi być obiektem")
    elif key == "toggles":
        steps = section.get("steps")
        if steps is not None:
            if not isinstance(steps, dict):
                raise ConfigError("bot.json: toggles.steps musi być obiektem")
            for step_name, step in steps.items():
                if step_name not in _BOT_STEP_NAMES:
                    raise ConfigError(f"bot.json: nieznany krok systemowy: {step_name}")
                if not isinstance(step, dict):
                    raise ConfigError(f"bot.json: toggles.steps.{step_name} musi być obiektem")
                if "enabled" in step and not isinstance(step["enabled"], bool):
                    raise ConfigError(
                        f"bot.json: toggles.steps.{step_name}.enabled musi być bool")
                if step_name == "stats_snapshot":
                    for flag in ("on_session_start", "on_session_end"):
                        if flag in step and not isinstance(step[flag], bool):
                            raise ConfigError(
                                f"bot.json: toggles.steps.stats_snapshot.{flag} musi być bool")
    return section


def save_bot(payload: dict, config_root: Path | None = None) -> dict:
    """Zapisz globalne limits / humanizer / kroki do `config/bot.json`.

    Payload to fragment (jedna lub więcej sekcji). Klucz obecny w payloadzie
    merguje się z plikiem — brak klucza zostawia dotychczasową wartość.
    Zwraca effective `load_bot` (DEFAULT + plik), ten sam kształt co GET.
    """
    if not isinstance(payload, dict):
        raise ConfigError("bot.json: oczekiwany obiekt JSON")
    known = ("limits", "humanizer", "toggles")
    incoming = {k: payload[k] for k in known if k in payload}
    if not incoming:
        raise ConfigError(f"bot.json: podaj przynajmniej jedno z: {list(known)}")
    for key, section in incoming.items():
        _validate_bot_section(key, section)

    root = config_root or CONFIG_ROOT
    path = root / "bot.json"
    current: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except Exception:
            current = {}

    # Tylko trzy sekcje — PIN / Telegram / proxy nie lądują w bot.json.
    # Brak klucza w payloadzie = zostaw surową wartość z pliku.
    next_doc = {
        "limits": deep_merge(current.get("limits") or {}, incoming.get("limits") or {}),
        "humanizer": deep_merge(
            current.get("humanizer") or {}, incoming.get("humanizer") or {}),
        "toggles": deep_merge(current.get("toggles") or {}, incoming.get("toggles") or {}),
    }

    root.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(".json.bak").write_bytes(path.read_bytes())
    path.write_text(
        json.dumps(next_doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return load_bot(root)


def device_scheduler_enabled(device_alias: str, config_root: Path | None = None) -> bool:
    """Czy scheduler ma ruszać to urządzenie. Brak klucza = włączony."""
    root = config_root or CONFIG_ROOT
    path = root / "devices" / f"{device_alias}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if not isinstance(doc, dict) or "scheduler_enabled" not in doc:
        return True
    return bool(doc.get("scheduler_enabled"))


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
    # Nowe / nieuzupełnione telefony: zczytaj resolution/model/dpi z ADB i zapisz.
    if hardware_gaps(dev):
        if not dev.get("alias"):
            dev["alias"] = dev_alias
        probe = hydrate_device_doc(dev, persist_path=dev_path, config_root=root)
        still = hardware_gaps(dev)
        if still:
            hint = probe.get("error") or "podłącz telefon kablem i włącz debugowanie USB"
            raise ConfigError(
                f"device:{dev_alias}: brakujące/niewypełnione pola: {still} "
                f"— bot zczyta je sam z ADB ({hint})"
            )
    _validate(dev, REQUIRED_DEVICE_PATHS, f"device:{dev_alias}")

    # scalanie danych sprzętowych do sekcji device konta (referencja przez alias)
    merged_device = {**acc["device"], "_hardware": dev}

    # Global z bot.json, konto nadpisuje obecne klucze (0 = brak limitu).
    bot = load_bot(root)
    humanizer = deep_merge(bot["humanizer"], acc.get("humanizer") or {})
    limits = deep_merge(bot["limits"], acc.get("limits") or {})
    toggles = deep_merge(bot["toggles"], acc.get("toggles") or {})
    _validate(
        {"humanizer": humanizer, "limits": limits, "toggles": toggles},
        REQUIRED_EFFECTIVE_PATHS,
        f"account:{account_id}+bot",
    )

    return AccountConfig(
        account_id  = acc["meta"]["account_id"],
        ig_username = acc["meta"]["ig_username"],
        device      = merged_device,
        humanizer   = humanizer,
        limits      = limits,
        flows       = acc.get("flows", []),
        toggles     = toggles,
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
