"""Dane logowania IG (username + hasło) — trzymane POZA configami kont.

Ten sam powód co `config/secrets.py`: configi kont są edytowalne z panelu w
trybie surowego JSON-a i lądują w backupach `.json.bak` — nie chcemy tam
hasła. Osobny plik, kluczowany aliasem konta, poza gitem.

Hasło nigdy nie wraca do przeglądarki w jawnej postaci — endpointy panelu
zwracają tylko `configured: bool` + zamaskowany username.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"


def _read_file() -> dict:
    if not CREDENTIALS_PATH.exists():
        return {}
    try:
        return json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_file(data: dict) -> None:
    CREDENTIALS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    try:                       # 0600 tam, gdzie system to respektuje
        os.chmod(CREDENTIALS_PATH, 0o600)
    except OSError:
        pass


def get(account: str) -> dict | None:
    """{'ig_username', 'ig_password'} dla konta, albo None gdy nieustawione."""
    entry = _read_file().get(account)
    if not entry or not entry.get("ig_username") or not entry.get("ig_password"):
        return None
    return {"ig_username": entry["ig_username"], "ig_password": entry["ig_password"]}


def set(account: str, ig_username: str | None, ig_password: str | None) -> None:
    """Zapisz (lub wyczyść, gdy oba puste) dane logowania konta."""
    data = _read_file()
    ig_username = (ig_username or "").strip()
    ig_password = (ig_password or "").strip()
    if ig_username and ig_password:
        data[account] = {"ig_username": ig_username, "ig_password": ig_password}
    else:
        data.pop(account, None)
    _write_file(data)


def mask_username(username: str | None) -> str:
    if not username:
        return ""
    if len(username) <= 3:
        return "•" * len(username)
    return f"{username[:2]}…{username[-1]}"


def status(account: str) -> dict:
    """Skąd/co jest ustawione dla konta — bez ujawniania hasła."""
    entry = get(account)
    return {
        "configured": bool(entry),
        "username_masked": mask_username(entry["ig_username"]) if entry else "",
    }
