"""Domknięcie recon: dump ekranu captcha + poprawka STEP_MAP/PLAYBOOK.

Komentarze PL. Nie rozwiązuje captchy. Nie commituje.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.core.device import UiAuto2Device
from v2.core.device_lock import DeviceLock
from v2.core.events import new_session_id
from v2.core.uiauto_server import ensure_running as ensure_uiauto
from v2.steps.registry import get as get_step
from v2.steps.registry import load_all

from _recon import BASE, OUT, PKG, SERIAL, dump, dump_activity, redact
from run_login_trace import (
    PLAYBOOK_PATH,
    STEP_MAP_PATH,
    _build_playbook,
    _entry,
    _write_step_map,
)


def main() -> int:
    ensure_uiauto(BASE)
    session_id = new_session_id()
    lock = DeviceLock(Path("/home/michal/bot_v2/data"), SERIAL)
    lock.acquire(owner="kasia21sum", session_id=session_id)
    try:
        dev = UiAuto2Device(serial=SERIAL, base_url=BASE, ig_package=PKG)
        meta = dump(
            dev, "29_captcha_human_stop",
            note="ZATRZYMANO: captcha — człowiek na telefonie, nie omijać",
            extra={"want_fatal": False, "human_required": True},
        )
        final = dump_activity(dev)
        load_all()
        # Context-less restore — MAC i tak nie był zapisany; skip OK
        try:
            from v2.cli import build_context
            # nie wołamy build_context (drugi lock). restore skip bez ctx.
            print("[fin] restore pominięty tu — brak zapisanego MAC w pass1",
                  flush=True)
        except Exception:
            pass
    finally:
        lock.release()

    doc = json.loads(STEP_MAP_PATH.read_text(encoding="utf-8"))
    doc["login_result"] = "częściowo"
    doc["stopped_at_step"] = "captcha_after_login_if_needed"
    doc["verification"] = False
    doc["captcha_or_suspicious"] = True
    doc["splash_crash"] = False
    doc["final_focused_package"] = final.get("focused_package")
    doc["final_focused_activity"] = final.get("focused_activity")
    doc["final_clone_owns_focus"] = final.get("clone_owns_focus")
    doc["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    doc["manual_todo"] = (
        "- CAPTCHA „Wpisz widoczne znaki” na klonie androib — "
        "wpisz znaki RĘCZNIE na telefonie, NIE omijać botem\n"
        "- `_confirm_post_login_popups` tapnął „Kontynuuj” na captchy "
        "(puste pole → błąd czerwony) — przyszły login musi to BLOKOWAĆ\n"
        "- po captchy mogą być save-login / nowy telefon / kod email"
    )
    # oznacz kroki 24/25 jako captcha
    for st in doc.get("steps") or []:
        texts = " ".join(st.get("visible_texts") or []).lower()
        if "wpisz widoczne znaki" in texts or "real human behind this login" in texts:
            st.setdefault("special", {})["captcha"] = True
    doc["steps"].append(_entry(
        "26", "dismiss_aborted_captcha",
        "dismiss_notifications PRZERWANE — captcha",
        "aborted_by_gate",
        {"reason": "captcha_human_required"},
        "zabito proces żeby nie tapnąć kolejnych Kontynuuj",
        meta, [],
        ["ZATRZYMANO: captcha, człowiek na telefonie"],
    ))
    doc["steps"].append(_entry(
        "29", "captcha_human_stop",
        "Captcha — stop, człowiek",
        "aborted_by_gate",
        {"human_required": True},
        None, meta, [],
    ))
    _write_step_map(doc)
    PLAYBOOK_PATH.write_text(_hand_playbook(doc), encoding="utf-8")
    print(
        f"[fin] captcha={doc['captcha_or_suspicious']} "
        f"focus={doc['final_focused_package']}/{doc['final_focused_activity']} "
        f"clone_focus={doc['final_clone_owns_focus']}",
        flush=True,
    )
    return 0


def _hand_playbook(doc: dict) -> str:
    """PLAYBOOK pod przyszłe logowania — obserwacje z live 2026-09-06."""
    return """# PLAYBOOK logowania `kasia21sum`

Pakiet klona: `com.instagram.androib` (NIE stock `com.instagram.android`).
Wynik: **częściowo** — login/hasło wpisane, stanęło na **CAPTCHA**.
Sesja CLI: `20260906-145741-664390ca`  pass3: `20260906-150159-bd14f779`.

## Obserwowana sekwencja

1. **wake** — ekran OFF (AOD/keyguard). `wake_up_key` działa.
2. **unlock** — swipe w górę, bez PIN.
3. **apply_clone_identity** — skip: `mac_spoof_failed:nie odczytano MAC wlan0`.
   Restore też skip (brak zapisanego MAC). Nie blokuje logowania.
4. **ensure_ig_running** — `current_pkg` wraca `com.instagram.androib` (OK),
   ale **focused/resumed** od razu przejmuje Google:
   `com.google.android.gms/.../AssistedSignInActivity`.
   `pidof` klona żyje — to NIE jest sukces launchu.
   **Brak** crashu splash `Resources$NotFoundException #0x7f082060`.
5. Ekran IG pod overlayem: login PL (logo, pola, „Zaloguj się”).
   Overlay GMS: „Zaloguj się w inny sposób” / „Użyj klucza z innego urządzenia”.
6. Oficjalny `login_if_needed` **skip** — hierarchia widzi tylko GMS,
   brak EditText username/hasło.
7. **Klucz:** zamknąć GMS tapem X
   `com.google.android.gms:id/cancel` (desc=`Anuluj`, bounds ~`[936,1866][1062,1992]`)
   albo `id/touch_outside`. BACK też zamyka sheet.
8. Po zgasnięciu ekranu (timeout) hierarchia = AOD. Zawsze **wake+unlock**
   zanim szukasz pól.
9. Pass3 (ekran on, GMS zniknął): pola widoczne, `dominant=com.instagram.androib`,
   activity `com.instagram.modal.ModalActivity`.
10. Wpis login + hasło + tap „Zaloguj się” — **działa**.
11. IG pokazał **CAPTCHA** „Wpisz widoczne znaki” (EN: real human behind this login).
    `_confirm_post_login_popups` uznał „Kontynuuj” za popup i tapnął 5+ razy
    na pustym polu → czerwony błąd „Wpisz litery i cyfry, aby kontynuować.”
12. Agent **zatrzymał** dalsze tapy. Captcha = człowiek na telefonie.

## Popupy i co kliknięto

| Ekran | Selektor / tekst | Akcja |
|---|---|---|
| Lockscreen | swipe up | unlock OK |
| GMS passkey | `gms:id/cancel` „Anuluj” / X | **zamknąć** zanim login |
| GMS passkey | „Użyj klucza z innego urządzenia” | **NIE** tapować |
| Login IG | content-desc pola (bez resource-id) | tap + `input text` |
| Login IG | „Zaloguj się” | tap — działa |
| Captcha | „Wpisz widoczne znaki” + „Kontynuuj” | **STOP — człowiek** |
| Captcha | „Wygeneruj nowy kod” / dźwięk | tylko ręcznie |

## Co działa / co pada

- Launch klona: **tak** (screenshot login IG, potem ModalActivity klona).
- Splash crash `#0x7f082060`: **nie**.
- Oficjalny `cmd_login` bez dismiss GMS: **pada** (`login_screen_not_detected`).
- Login/hasło po dismiss GMS: **tak**.
- Weryfikacja email/SMS: **nie pojawiła się** (jeszcze).
- Captcha: **tak** — bot nie omija.
- Avatar: brak `data/accounts/kasia21sum/avatar.*` → skip.
- MAC spoof wlan0: nie działa na tym przebiegu (pusty odczyt).

## Selektory na przyszłość

- GMS X: `com.google.android.gms:id/cancel` / content-desc `Anuluj`.
- Pola logowania: hint „nazwa użytkownika” / „hasło” (EditText, często bez id).
- Captcha BLOKUJ gdy flat zawiera: `wpisz widoczne znaki`,
  `real human behind this login`, `wpisz znaki`, `wygeneruj nowy kod`.
  Wtedy NIE wolno tapnąć CONFIRM „Kontynuuj”.
- Sukces launchu: linia `mCurrentFocus`/`mResumedActivity` z `com.instagram.androib/`.
  Nie `pidof`. Nie stock `com.instagram.android`.

## Ręcznie dokończyć na telefonie

- Wpisać captcha na `com.instagram.androib` (ModalActivity).
- Po captchy: ewentualny kod email/SMS w panelu
  `POST /api/verification-code/kasia21sum`.
- Nie ruszać stock `com.instagram.android`.

Dumpy: `/home/michal/bot_v2/calibration/recon_login_kasia21sum`
STEP_MAP: `STEP_MAP.json`
"""


if __name__ == "__main__":
    raise SystemExit(main())
