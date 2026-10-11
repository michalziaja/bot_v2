# PLAYBOOK logowania `kasia21sum`

Pakiet klona: `com.instagram.androib` (NIE stock `com.instagram.android`).
Wynik: **tak** — konto na `home_feed` / `InstagramMainActivity` klona.
Captcha rozwiązał człowiek. Kod email/SMS: nie było.
Sesje: pass1 `20260906-145741-664390ca` → pass3 login+hasło → pass4 `20260906-150833-a2c4b6cc`.

## Sekwencja na przyszłość

1. **wake** — `wake_up_key` (AOD/keyguard).
2. **unlock** — swipe w górę, bez PIN.
3. **apply_clone_identity** — na tym przebiegu skip (`nie odczytano MAC wlan0`). Nie blokuje.
4. **ensure_ig_running** — klon wstaje, ale focus często przejmuje **GMS Passkey**
   (`AssistedSignInActivity`). `pidof` to NIE sukces. Splash `#0x7f082060` **nie** padł.
5. **Zamknij GMS** — X `com.google.android.gms:id/cancel` (desc `Anuluj`).
   NIE tapaj „Użyj klucza z innego urządzenia”.
   Wpięte w `system.login_if_needed` (2026-09-06).
6. Pola IG: hint „nazwa użytkownika” / „hasło” (EditText, często bez resource-id)
   + „Zaloguj się”.
7. **CAPTCHA** „Wpisz widoczne znaki” / *real human behind this login*
   = **STOP, człowiek**. NIE tapaj „Kontynuuj” (puste pole → czerwony błąd).
   Wpięte: `_mentions_captcha` przerywa popupy; krok = `failed("captcha_requires_human")`.
8. Po captchy (pass4): ekran **„Zapisać dane logowania?”** → **Zapisz**
   (nigdy „Nie teraz”).
9. Tip **„Rozumiem”** → feed (`home_feed`, `InstagramMainActivity`).
10. Kod weryfikacyjny: nie pojawił się.
11. Avatar: skip (brak `data/accounts/kasia21sum/avatar.*`).
12. Restore MAC: skip gdy nie było spoofa.

## Popupy — co klikać

| Ekran | Selektor / tekst | Akcja |
|---|---|---|
| GMS passkey | `gms:id/cancel` „Anuluj” | zamknąć |
| GMS passkey | „Użyj klucza z innego urządzenia” | NIE |
| Login IG | pola + „Zaloguj się” | tap + `input text` |
| Captcha | „Wpisz widoczne znaki” + „Kontynuuj” | STOP — człowiek |
| Zapisz dane | „Zapisz” (`save_login_prompt`) | TAK |
| Tip | „Rozumiem” | TAK |
| Lokalizacja / kontakty / push | Nie zezwalaj / Pomiń / Nie teraz | jak dotychczas |

## Bug `sweep_tabs` (pass4)

Na `search_tab` `_confirm_post_login_popups` uznał kafelek rolki
(„autor rolki: kasia markiewicz…”) i potem **Zapisz** na reel za popup.
Skończyło na `reel_comments_sheet`. Konto już było zalogowane —
to nie rollback logowania, ale **nie powtarzać** ślepego CONFIRM na explore.

Na przyszłość: popupy po loginie tylko gdy widać znane intro/permission/save-login,
nie gdy `search_top` / siatka rolek.

## Sukces launchu / logowania

- Focus: `com.instagram.androib/` w `mCurrentFocus` / `mResumedActivity`.
- Po loginie: `identify().top == home_feed` (albo profil).
- Nie `pidof`. Nie stock `com.instagram.android`.

Dumpy: `calibration/recon_login_kasia21sum/`
STEP_MAP: `STEP_MAP.json`
Reguły w kodzie: `steps/system/login_if_needed.py`
