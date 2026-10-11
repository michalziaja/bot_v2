"""profile.set_bio — wpisz biogram z panelu na własnym profilu IG.

Źródło: `data/accounts/<alias>/profile.json` (`bio`). Brak tekstu → skip.
Wejście: zakładka profilu → „Dodaj biogram” albo Edytuj profil → Biogram.
"""
from __future__ import annotations

import time
from pathlib import Path

from v2.core.screen_map import flatten_hierarchy
from v2.data.account_profile import get_bio
from v2.runner.step import StepResult
from v2.steps.profile.set_avatar import (
    EDIT_HINTS,
    EDIT_RID,
    PROFILE_TAB,
    _tap_first,
    _tap_hints,
)
from v2.steps.registry import register
from v2.steps.system import login_log
from v2.steps.system._ui_helpers import center, escape_input_text, find_field, normalize_ws
from v2.steps.system.login_if_needed import _confirm_post_login_popups
from v2.steps.system.sweep_tabs import _tap_tab

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data"

BIO_HINTS = ("biogram", "biography", "bio")
BIO_OPEN_HINTS = ("dodaj biogram", "add bio", "edytuj biogram", "edit bio")
SAVE_HINTS = ("zapisz", "save", "gotowe", "done")
BIO_IDS = (
    "com.instagram.android:id/profile_edit_bio",
    "com.instagram.android:id/bio",
    "com.instagram.android:id/biography",
)


@register
class SetBio:
    name = "profile.set_bio"
    requires_screen = None

    def run(self, ctx, params):
        alias = ctx.account.account_id
        data_root = getattr(ctx, "data_root", None) or DATA_ROOT
        bio = (params.get("bio") or get_bio(data_root, alias) or "").strip()
        if not bio:
            login_log.say("Brak biogramu na PC — skip")
            return StepResult.skipped("no_bio_text")

        _tap_tab(ctx, PROFILE_TAB)
        time.sleep(0.8)
        _confirm_post_login_popups(ctx, max_steps=4)

        if not _open_bio_editor(ctx):
            login_log.say("Brak pola biogramu — skip")
            return StepResult.skipped("no_bio_field")

        time.sleep(0.5)
        field = _find_bio_field(flatten_hierarchy(ctx.device.hierarchy(force=True)))
        if not field or not field.get("bounds"):
            login_log.say("EditText biogramu nie wszedł — skip")
            return StepResult.skipped("no_bio_edittext")

        if not _type_bio(ctx, field, bio):
            return StepResult.failed("nie wpisało się bio", attempted=bio[:40])

        time.sleep(0.4)
        if not _tap_hints(ctx, SAVE_HINTS):
            login_log.say("Brak Zapisz po bio — skip (tekst może już być)")
            return StepResult.skipped("no_save_after_bio", typed=True)

        login_log.say("Biogram zapisany")
        ctx.log("profile.set_bio.ok", length=len(bio))
        return StepResult.ok(length=len(bio))


def _open_bio_editor(ctx) -> bool:
    """Skrót „Dodaj biogram” albo Edytuj profil → wiersz Biogram."""
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    if _find_bio_field(flat):
        return True
    if _tap_first(ctx, (), BIO_OPEN_HINTS):
        return True
    if not _tap_first(ctx, (EDIT_RID,), EDIT_HINTS):
        return False
    time.sleep(0.7)
    flat2 = flatten_hierarchy(ctx.device.hierarchy(force=True))
    if _find_bio_field(flat2):
        return True
    return _tap_first(ctx, (), BIO_HINTS) or _tap_first(ctx, (), BIO_OPEN_HINTS)


def _find_bio_field(flat: list[dict]) -> dict | None:
    """EditText biogramu — id, hint, albo jedyne pole poza imieniem."""
    field = find_field(flat, BIO_IDS, BIO_HINTS, "EditText")
    if field:
        return field
    edits = [e for e in flat if "EditText" in (e.get("class") or "")]
    if len(edits) == 1:
        return edits[0]
    return None


def _type_bio(ctx, field: dict, bio: str) -> bool:
    """Wyczyść pole i wpisz bio (spacje przez %s — ADB nie lubi enterów)."""
    text = " ".join(bio.split())
    login_log.say("Wpisuję biogram…")
    ctx.ht.tap(*center(field["bounds"]), pause_kind="tap")
    time.sleep(0.2)
    # Komentarz: jeden shell — 80× DEL bez 80 rund ADB
    ctx.device.shell(
        "sh -c 'input keyevent KEYCODE_MOVE_END; "
        "i=0; while [ $i -lt 80 ]; do input keyevent 67; i=$((i+1)); done'"
    )
    ctx.device.shell(f'input text "{escape_input_text(text)}"')
    time.sleep(0.5)
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    now = _find_bio_field(flat) or field
    val = normalize_ws((now.get("text") or "") + " " + (now.get("content-desc") or ""))
    ok = text.lower()[:20] in val or (val and not any(h in val for h in BIO_HINTS))
    if ok:
        login_log.say("Pole biogramu potwierdzone")
    return ok
