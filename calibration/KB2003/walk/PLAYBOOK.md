# Kalibracja KB2003

Rozdzielczość: 1080×2400. Zrzuty: `KB2003/walk/`.
Mapa: `KB2003_draft.json`. Harvest kotwic: 7.
Start: `2026-09-09T20:49:30Z`. Przerwane: False.

## Kroki

- `00_start` — stan wejściowy — zanim ruszamy gestami → top=? [ok]
- `01_wake` — po wake → top=? [ok]
- `02_unlock` — po unlock / dismiss-keyguard → top=? [ok]
- `03_ig` — po launch klona → top=reels_feed [ok]
- `04_landing` — landing: login / signup / home / picker → top=reels_feed [ok]
- `05_tab_feed` — po tap feed_tab → top=home_feed [ok]
- `06_tab_search` — po tap search_tab → top=search_top [ok]
- `07_tab_reels` — po tap clips_tab → top=reels_feed [ok]
- `08_tab_direct` — po tap direct_tab → top=direct_inbox [ok]
- `09_tab_profile` — po tap profile_tab → top=profile_self [ok]
