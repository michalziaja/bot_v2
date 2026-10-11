"""Profil IG konta na PC — bio + status zdjęcia (`data/accounts/<alias>/`)."""
from __future__ import annotations

import json
from pathlib import Path

from v2.data.account_avatar import account_dir, avatar_path

# Komentarz: limit IG na biogram — dłuższe obcinamy przy zapisie
BIO_MAX_LEN = 150
PROFILE_NAME = "profile.json"


def _profile_file(data_root: Path, alias: str) -> Path:
    return account_dir(data_root, alias) / PROFILE_NAME


def _read_doc(data_root: Path, alias: str) -> dict:
    path = _profile_file(data_root, alias)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def get_bio(data_root: Path, alias: str) -> str:
    """Zapisany biogram albo pusty string."""
    return str(_read_doc(data_root, alias).get("bio") or "").strip()


def set_bio(data_root: Path, alias: str, bio: str) -> str:
    """Zapisz biogram (obcięty do limitu IG). Pusty kasuje treść."""
    text = " ".join(str(bio or "").split())
    if len(text) > BIO_MAX_LEN:
        text = text[:BIO_MAX_LEN].rstrip()
    d = account_dir(data_root, alias)
    d.mkdir(parents=True, exist_ok=True)
    doc = _read_doc(data_root, alias)
    if text:
        doc["bio"] = text
    else:
        doc.pop("bio", None)
    path = _profile_file(data_root, alias)
    if doc:
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    elif path.exists():
        path.unlink()
    return text


def profile_status(data_root: Path, alias: str) -> dict:
    """Stan do panelu: bio + czy leży avatar.*."""
    pic = avatar_path(data_root, alias)
    return {
        "ok": True,
        "bio": get_bio(data_root, alias),
        "has_avatar": bool(pic),
        "filename": pic.name if pic else None,
    }
