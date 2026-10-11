---
name: calibrate-device
description: >-
  Kalibracja nowego telefonu pod bota IG (ScreenMap, kotwice nav, zrzuty).
  Używaj gdy OnePlus/nowy model się gubi, identify() zwraca top=?, brak
  {MODEL}_draft.json, fallback SM_A505FN, albo user prosi o kalibrację /
  przejęcie kontroli nad urządzeniem i przejście znanych kroków.
---

# Kalibracja nowego urządzenia

OnePlus (KB2003, 1080×2400) **nie** może jechać na surowych kotwicach A50
(1080×2340) — dolny nav wpada w pasek systemowy, `identify()` daje `top=?`.

## Co zrobić

1. Weź alias konta na tym telefonie (`klon1` / `oneplus`).
2. Przejmij ADB (wake, `wm dismiss-keyguard`, nie restartuj `adbd` w sandboxie).
3. Odpal:

```bash
cd /home/michal && .venv-lub-bot_v2/.venv/bin/python -u -m v2.cli calibrate --account=klon1
```

4. Walker sam: wake → unlock → launch IG → zamyka GMS/help → dump co krok
   (jpg + slim JSON) → tap 5 tabów gdy `home_feed` → zapis
   `calibration/{MODEL}_draft.json`.
5. Oglądaj zrzuty w `calibration/{MODEL}/walk/` żeby wiedzieć, na którym
   ekranie stoi (login / signup / DatePicker / home).
6. Po zapisie mapy CLI ładuje ją zamiast A50 (`{model}_draft.json`).

## Zasady

- Screen co krok — nie zgaduj z samego dumpsys.
- Kotwice `nav.*` tylko z żywego drzewa; gdy wylogowany — fallback **nad**
  insetem systemowym (~140 px), nigdy na samym dole 2400.
- Fingerprinty resource-id zostają z A50 (klony normalizują ID).
- Ekrany bez ID (login, signup, kod, urodziny, DatePicker) — `must_have_text_any`.
- MAC/root na OnePlus bez Magisk: skip, nie blokuj kalibracji.
- Nie klep **Dalej** pod modalem DatePicker / „Potrzebujesz pomocy?”.

## Kod

- `v2.core.device_calibrate` — walker
- `v2.cli calibrate --account=…`
- mapa: `calibration/KB2003_draft.json`
