"""Zdjęcie profilowe konta — plik na PC (`data/accounts/<alias>/avatar.*`)."""
from __future__ import annotations

from pathlib import Path

# Komentarz: te same rozszerzenia co galeria IG przyjmuje jako awatar
AVATAR_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def account_dir(data_root: Path, alias: str) -> Path:
    """Katalog danych konta."""
    return Path(data_root) / "accounts" / alias


def avatar_path(data_root: Path, alias: str) -> Path | None:
    """Istniejący plik awatara albo None."""
    d = account_dir(data_root, alias)
    if not d.is_dir():
        return None
    for ext in AVATAR_EXTS:
        p = d / f"avatar{ext}"
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def save_avatar(data_root: Path, alias: str, blob: bytes, filename: str = "") -> Path:
    """Zapisz upload jako `avatar.<ext>` — jeden plik na konto."""
    if not blob:
        raise ValueError("pusty plik awatara")
    raw_name = (filename or "avatar.jpg").strip()
    ext = Path(raw_name).suffix.lower()
    if ext not in AVATAR_EXTS:
        ext = ".jpg"
    d = account_dir(data_root, alias)
    d.mkdir(parents=True, exist_ok=True)
    for old in AVATAR_EXTS:
        prev = d / f"avatar{old}"
        if prev.is_file():
            prev.unlink()
    dest = d / f"avatar{ext}"
    dest.write_bytes(blob)
    return dest


def phone_avatar_dir(alias: str) -> str:
    """Folder na telefonie (galeria) — bez Recents, po aliasie konta."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (alias or "acc"))
    return f"/sdcard/Pictures/bot_v2/{safe}"
