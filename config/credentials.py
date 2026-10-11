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


def normalize_signup(raw) -> dict | None:
    """Profil zakładania konta IG — imię, data urodzenia, kontakt (email/tel)."""
    if not isinstance(raw, dict):
        return None
    full_name = str(raw.get("full_name") or "").strip()
    birthday = str(raw.get("birthday") or "").strip()
    contact = str(raw.get("contact") or "").strip()
    if not full_name or not birthday or not contact:
        return None
    return {"full_name": full_name, "birthday": birthday, "contact": contact}


def get_signup(account: str) -> dict | None:
    """Login + hasło + pola signup, albo None gdy brakuje czegokolwiek."""
    creds = get(account)
    if not creds:
        return None
    raw = _read_file().get(account) or {}
    signup = normalize_signup(raw.get("signup"))
    if not signup:
        return None
    return {**creds, **signup}


def set(account: str, ig_username: str | None, ig_password: str | None,
        signup=None) -> None:
    """Zapisz (lub wyczyść, gdy oba puste) dane logowania konta.

    `signup=None` zostawia istniejący profil; dict / {} nadpisuje.
    """
    data = _read_file()
    ig_username = (ig_username or "").strip()
    ig_password = (ig_password or "").strip()
    existing = data.get(account) if isinstance(data.get(account), dict) else {}
    if ig_username and ig_password:
        entry = {"ig_username": ig_username, "ig_password": ig_password}
        if signup is None:
            kept = normalize_signup((existing or {}).get("signup"))
            if kept:
                entry["signup"] = kept
        else:
            cleaned = normalize_signup(signup)
            if cleaned:
                entry["signup"] = cleaned
        data[account] = entry
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
    signup = get_signup(account)
    return {
        "configured": bool(entry),
        "username_masked": mask_username(entry["ig_username"]) if entry else "",
        # Komentarz: contact maskujemy jak login; imię i data nie są sekretem
        "signup_configured": bool(signup),
        "signup": {
            "full_name": signup["full_name"] if signup else "",
            "birthday": signup["birthday"] if signup else "",
            "contact_masked": mask_username(signup["contact"]) if signup else "",
        },
    }
