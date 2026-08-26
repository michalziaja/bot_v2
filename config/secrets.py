"""Sekrety (klucz API) — trzymane POZA configami kont.

Dlaczego osobny plik, a nie pole w `accounts/<alias>.json`:
  - klucz jest jeden na całą instalację, nie per konto — kopiowanie go do
    każdego configu to n kopii do rotacji przy wycieku,
  - configi kont są edytowalne z panelu w trybie surowego JSON-a i lądują
    w backupach `.json.bak` — nie chcemy tam sekretu,
  - łatwiej wykluczyć jeden plik z ewentualnego repo.

Pierwszeństwo ma zmienna środowiskowa: jeśli `ANTHROPIC_API_KEY` jest ustawiona,
wygrywa z plikiem. Dzięki temu uruchomienie z CI/innego środowiska nie wymaga
dotykania plików.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SECRETS_PATH = Path(__file__).parent / "secrets.json"

ENV_VAR = "ANTHROPIC_API_KEY"


def _read_file() -> dict:
    if not SECRETS_PATH.exists():
        return {}
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_api_key() -> str | None:
    """Klucz do Anthropic API: env → plik → None."""
    env = os.environ.get(ENV_VAR)
    if env:
        return env
    key = (_read_file().get("anthropic_api_key") or "").strip()
    return key or None


def set_api_key(key: str | None) -> None:
    """Zapisz (lub wyczyść, gdy puste) klucz w pliku sekretów."""
    data = _read_file()
    key = (key or "").strip()
    if key:
        data["anthropic_api_key"] = key
    else:
        data.pop("anthropic_api_key", None)
    SECRETS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    try:                       # 0600 tam, gdzie system to respektuje
        os.chmod(SECRETS_PATH, 0o600)
    except OSError:
        pass


def mask(key: str | None) -> str:
    """Maska do pokazania w UI — pełny klucz nigdy nie wraca do przeglądarki."""
    if not key:
        return ""
    if len(key) <= 12:
        return "•" * len(key)
    return f"{key[:7]}…{key[-4:]}"


def status() -> dict:
    """Skąd pochodzi klucz i jak wygląda — bez ujawniania wartości."""
    env = os.environ.get(ENV_VAR)
    file_key = (_read_file().get("anthropic_api_key") or "").strip()
    key = env or file_key or None
    return {
        "configured": bool(key),
        "source": "env" if env else ("file" if file_key else None),
        "masked": mask(key),
        "env_overrides_file": bool(env and file_key),
        "path": str(SECRETS_PATH),
    }


def get_filter_proxies() -> dict[str, str | list[str]]:
    """Pula URL-i proxy dla filtra profili (`filter_proxies` w secrets.json).

    Format (string lub lista sticky — jak PROXY_URLs w proxy1)::
        "filter_proxies": {
          "webshare": "http://user:pass@host:80/",
          "rayobyte": "http://user:pass@host:8000",
          "mars": [
            "http://user:pass_session-aaa@host:44443",
            "http://user:pass_session-bbb@host:44443"
          ]
        }
    """
    raw = _read_file().get("filter_proxies") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str | list[str]] = {}
    for name, url in raw.items():
        if not name:
            continue
        if isinstance(url, list):
            cleaned = [str(u).strip() for u in url if str(u).strip()]
            if cleaned:
                out[str(name)] = cleaned
        elif isinstance(url, str):
            u = url.strip()
            if u:
                out[str(name)] = u
    return out


def set_filter_proxies(proxies: dict[str, str | list[str]] | None) -> None:
    """Zapisz (lub wyczyść) mapę proxy filtra w secrets.json."""
    data = _read_file()
    if not proxies:
        data.pop("filter_proxies", None)
    else:
        cleaned: dict[str, str | list[str]] = {}
        for k, v in proxies.items():
            if not k:
                continue
            if isinstance(v, list):
                urls = [str(u).strip() for u in v if str(u).strip()]
                if urls:
                    cleaned[str(k)] = urls
            else:
                s = str(v).strip()
                if s:
                    cleaned[str(k)] = s
        data["filter_proxies"] = cleaned
    SECRETS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    try:
        os.chmod(SECRETS_PATH, 0o600)
    except OSError:
        pass


def filter_proxies_status() -> dict:
    """Nazwy skonfigurowanych proxy — bez ujawniania URL/haseł."""
    proxies = get_filter_proxies()
    # Ile URL-i w puli (lista sticky liczy się jako wiele)
    url_count = 0
    for v in proxies.values():
        url_count += len(v) if isinstance(v, list) else 1
    return {
        "configured": bool(proxies),
        "names": sorted(proxies.keys()),
        "url_count": url_count,
        "path": str(SECRETS_PATH),
    }
