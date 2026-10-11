# Agent: kalibracja utknęła

Urządzenie: `oneplus`
Krok: `04_landing`
Oczekiwano: `['date_picker', 'home_feed', 'ig_map_intro', 'login_screen', 'search_top', 'signup_birthday', 'signup_code', 'signup_contact']`
Widzę (identify): `reels_feed`

Krok `04_landing` nie przeszedł. Oczekiwałem ['date_picker', 'home_feed', 'ig_map_intro', 'login_screen', 'search_top', 'signup_birthday', 'signup_code', 'signup_contact'], identify dał `reels_feed`. Obejrzyj screen i powiedz: OK (to ten ekran), Pomiń, Ponów dump albo Przerwij.

Screenshot: `/home/michal/bot_v2/data/devices/oneplus/calibrate.jpg`

Odpowiedź w panelu **Flota** (OK / Pomiń / Ponów / Przerwij)
albo POST `/api/devices/{alias}/calibrate/reply`.
