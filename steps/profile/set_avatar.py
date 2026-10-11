"""profile.set_avatar — wrzuć zdjęcie z panelu na telefon i ustaw jako awatar.

Źródło: `data/accounts/<alias>/avatar.jpg|png|webp`. Brak pliku → skip.
UI IG bywa inny per wersję — gdy nie znajdziemy „Edytuj profil" / galerii,
logujemy i skip (logowanie i tak się udało).
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from v2.core.screen_map import flatten_hierarchy
from v2.data.account_avatar import avatar_path, phone_avatar_dir
from v2.runner.step import StepResult
from v2.steps.registry import register
from v2.steps.system import login_log
from v2.steps.system._ui_helpers import BUTTON_CLASSES, center, find_field, normalize_ws
from v2.steps.system.login_if_needed import _confirm_post_login_popups, _find_label_button
from v2.steps.system.sweep_tabs import _tap_tab

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data"

EDIT_HINTS = ("edytuj profil", "edit profile")
CHANGE_PHOTO_HINTS = (
    "zmień zdjęcie", "change photo", "dodaj zdjęcie", "add photo",
    "nowe zdjęcie profilowe", "new profile photo",
    # Komentarz: live OnePlus klon1 — „Edytuj zdjęcie profilowe” / „…lub awatara”
    "edytuj zdjęcie", "edit photo", "edytuj zdjęcie profilowe",
    "edytuj zdjęcie lub awatara",
)
PHOTO_PERM_ALLOW_HINTS = (
    "zezwalaj na wszystko", "allow all", "allow access to all",
)
PHOTO_PERM_BLOB = ("zdję", "photo", "film", "media", "obraz")
GALLERY_HINTS = (
    "wybierz z galerii", "choose from gallery", "wybierz z biblioteki",
    "choose from library", "galeria", "gallery", "biblioteka",
)
DONE_HINTS = ("gotowe", "done", "dalej", "next", "zapisz", "save")
THUMB_RID = "com.instagram.android:id/gallery_grid_item_thumbnail"
EDIT_RID = "com.instagram.android:id/profile_header_edit_profile"
PROFILE_TAB = "com.instagram.android:id/profile_tab"


@register
class SetAvatar:
    name = "profile.set_avatar"
    requires_screen = None

    def run(self, ctx, params):
        alias = ctx.account.account_id
        data_root = getattr(ctx, "data_root", None) or DATA_ROOT
        src = avatar_path(data_root, alias)
        if not src:
            login_log.say("Brak zdjęcia profilowego na PC — skip")
            return StepResult.skipped("no_avatar_file")

        login_log.say(f"Wrzucam awatar {src.name} na telefon…")
        remote = _push_avatar(ctx, src, alias)
        if not remote:
            return StepResult.failed("adb push awatara nieudany", path=str(src))

        _tap_tab(ctx, PROFILE_TAB)
        time.sleep(0.8)
        _confirm_post_login_popups(ctx, max_steps=4)

        if not _tap_first(ctx, (EDIT_RID,), EDIT_HINTS):
            login_log.say("Brak „Edytuj profil” — skip awatara")
            return StepResult.skipped("no_edit_profile", remote=remote)

        time.sleep(0.7)
        if not _tap_hints(ctx, CHANGE_PHOTO_HINTS):
            login_log.say("Brak „Zmień zdjęcie” — skip")
            return StepResult.skipped("no_change_photo", remote=remote)

        time.sleep(0.6)
        # Komentarz: sheet „Nowe zdjęcie / Galeria" — weź galerię gdy jest
        _tap_hints(ctx, GALLERY_HINTS)
        time.sleep(0.5)
        _allow_photo_access(ctx)
        time.sleep(0.8)
        if not _tap_first_thumb(ctx):
            login_log.say("Brak miniatury w galerii — skip")
            return StepResult.skipped("no_gallery_thumb", remote=remote)

        time.sleep(0.8)
        _tap_hints(ctx, DONE_HINTS)
        time.sleep(0.6)
        _tap_hints(ctx, DONE_HINTS)
        login_log.say("Awatar ustawiony (albo crop potwierdzony)")
        ctx.log("profile.set_avatar.ok", remote=remote, src=str(src))
        return StepResult.ok(remote=remote, src=str(src))


def _push_avatar(ctx, src: Path, alias: str) -> str | None:
    """adb push + skan MediaStore — ten sam serial co Device."""
    serial = getattr(ctx.device, "serial", "") or ""
    dest_dir = phone_avatar_dir(alias)
    dest = f"{dest_dir}/{src.name}"
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "mkdir", "-p", dest_dir],
            check=False, capture_output=True, timeout=15,
        )
        pushed = subprocess.run(
            ["adb", "-s", serial, "push", str(src), dest],
            check=False, capture_output=True, timeout=60,
        )
        if pushed.returncode not in (0, None):
            login_log.say(f"adb push: {(pushed.stderr or b'').decode(errors='replace')}")
            return None
        subprocess.run(
            ["adb", "-s", serial, "shell", "am", "broadcast",
             "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
             "-d", f"file://{dest}"],
            check=False, capture_output=True, timeout=15,
        )
        return dest
    except Exception as e:
        login_log.say(f"push awatara: {e}")
        return None


def _allow_photo_access(ctx) -> bool:
    """Dialog Androida o zdjęcia — bez zgody galeria jest pusta."""
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    blob = normalize_ws(" ".join(
        (e.get("text") or "") + " " + (e.get("content-desc") or "")
        for e in flat))
    if not any(h in blob for h in PHOTO_PERM_BLOB):
        return False
    if _tap_hints(ctx, PHOTO_PERM_ALLOW_HINTS):
        login_log.say("Zezwoliłem na dostęp do zdjęć")
        return True
    return False


def _tap_first(ctx, ids: tuple, hints: tuple) -> bool:
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    node = find_field(flat, ids, hints, BUTTON_CLASSES)
    if not node:
        node = next(
            (e for e in flat
             if any(h in normalize_ws((e.get("text") or "") + " " + (e.get("content-desc") or ""))
                    for h in hints)),
            None,
        )
    if not node or not node.get("bounds"):
        return False
    ctx.ht.tap(*center(node["bounds"]), pause_kind="tap")
    return True


def _tap_hints(ctx, hints: tuple) -> bool:
    if _tap_first(ctx, (), hints):
        return True
    btn = _find_label_button(
        flatten_hierarchy(ctx.device.hierarchy(force=True)), hints,
        allow_not_now=True)
    if not btn or not btn.get("bounds"):
        return False
    ctx.ht.tap(*center(btn["bounds"]), pause_kind="tap")
    return True


def _tap_first_thumb(ctx) -> bool:
    """Pierwsza miniatura w siatce galerii IG."""
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    thumbs = [e for e in flat if (e.get("resource-id") or "") == THUMB_RID]
    node = thumbs[0] if thumbs else None
    if not node or not node.get("bounds"):
        return False
    ctx.ht.tap(*center(node["bounds"]), pause_kind="tap")
    return True
