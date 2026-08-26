"""Notatka kalibracji Tags (probe-tags-tab) — 2026-08-09.

Live dump: OK na `com.instagram.android` (account technature699).
Sample: `calibration/samples/45_search_tags_tab.{flat,hierarchy}.json`

Wynik hierarchy (PL):
- Zakładki wyników: Button BEZ resource-id, text m.in. Dla Ciebie / Konta /
  Bez personalizacji / Audio / Znaczniki (po swipe w lewo po
  `scrollable_tab_layout`).
- Lista Znaczniki:
  - `com.instagram.android:id/row_hashtag_container`
  - `…/row_hashtag_textview_tag_name`  (tekst `#fitnessmotivation`)
  - `…/row_hashtag_textview_tag_subtitle` (np. `174 mln postów`, `922 tys. postów`)

Wymaga live / nie pokryte tym przebiegiem:
- Klon `com.instagram.androjb` (a50_clone3): `am start` wraca na launcher —
  nie da się dumpować Tags na tym klonie bez naprawy launch/FG.
- Locale EN (Tags / posts) — aliasy są w kodzie, dump EN nie zrobiony.
- `uiautomator dump` na urządzeniu bywa Killed (używać uiautodev hierarchy).
- Pełny flow `search.open` + `type_query(submit)` przez `build_context`/
  scrcpy nie był użyty w tym dumpie (surowy uiautodev + BACK z clips viewer).
"""
