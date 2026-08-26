---
name: ""
overview: ""
todos: []
isProject: false
---

# Plan: Podstawowe / Zaawansowane w ustawieniach pluginów

**Status:** zaakceptowany i zaimplementowany (katalog + renderer panelu + `scroll.distance`).  
**Źródła:** `plugins/catalog.py`, `plugins/blocks.py`, renderer w `panel/static/index.html` (`renderPluginDetail` / `mountCatalogSection`).  
**Cel:** przejrzysty układ: pola codzienne zawsze widoczne; rzadkie / eksperckie za accordionem „Zaawansowane”; etykiety PL zamiast surowych kluczy.

### Decyzje zamknięte

- **`feed_browser` / scroll:** ❌ **nie** dodajemy `scroll.enabled` — scroll jest zawsze aktywny, gdy plugin działa. ✅ Zamiast ON/OFF: **zmienna długość / dystans scrolla** (min ≈ cały post w dół, czasem więcej — humanizacja). Szczegóły w sekcji 1.

---

## Zasady wspólne (dla wszystkich pluginów)

### Model UI

1. Każdy plugin ma **sekcje grupujące** (jak dziś `sections` w katalogu), ale z nowym podziałem **Podstawowe vs Zaawansowane na poziomie pól / podsekcji**, nie tylko całych sekcji `advanced=True`.
2. Accordion **„Zaawansowane”** per grupa (Scroll, Relacje, Filtr, Lajk, AI, Lista lajkujących, Komentarze…) — zgodnie z przykładem użytkownika; nie jeden globalny accordion na cały plugin (obecny renderer zbiera wszystkie `section.advanced` do jednego `<details>`).
3. Pole **Włączenie pluginu** (`enabled`) — zawsze na górze, Podstawowe, etykieta: **„Plugin włączony”**.
4. Etykiety w UI: polskie; w nawiasie klucz katalogu tylko w docs / hover, nie jako główny label.

### Pola złożone (merge / nowe kontrole)

| Potrzeba UX | Stan katalogu | Propozycja |
|-------------|---------------|------------|
| Zakres lajków min–max jako **jedno** ustawienie | `gate.min_likes` + `gate.max_likes` osobno | Kontrolka `range` / para min–max: **„Lajki (min–max)”** → mapowanie na oba klucze |
| Szansa % vs AI | `ai.enabled` + `like_*.chance` (mode p / formula) osobno | Patrz rekomendacja przy `reels_niche` |
| Lista lajkujących ON/OFF (+ zaawansowane) | `likers.harvest.enabled` istnieje, ale cała sekcja często `advanced=True` | **Podstawowe:** tylko ON/OFF (+ ewentualnie ile zbierać); reszta w accordionie |
| Lista komentarzy ON/OFF (+ zaawansowane) | j.w. dla `comments.harvest` | Jak wyżej |

### Renderer (przyszła zmiana, nie teraz)

- Dziś: sekcje z `advanced: true` → jeden wspólny `<details>Zaawansowane</details>`.
- Docelowo: **per-grupa** accordion „Zaawansowane”; sekcja może mieć pola `basic` i `advanced` (albo flagę na polu / podsekcji).
- Etykiety PL: albo `label` w katalogu po polsku, albo mapa labeli w panelu — do decyzji przy implementacji.

---

## 1. `feed_browser` — Przeglądanie strony głównej

**Grupa katalogu:** `warmup` · **status:** planned

**Decyzja zamknięta (scroll):** ❌ bez `scroll.enabled` — scroll zawsze ON przy działającym pluginie. ✅ UX i przyszły runtime: **zmienna długość scrolla** (min = cały post, czasem ekstra).

### Grupy UI

1. **Włączenie**
2. **Scroll** (scrollowanie feedu)
3. **Relacje** (tacka na home)

### Podstawowe

**Włączenie**
- Plugin włączony (`enabled`)

**Scroll**
- Scroll feedu — **zawsze aktywny** gdy plugin włączony (brak ON/OFF; `scroll.enabled` **nie** dodajemy).
- **Długość / dystans scrolla** (propozycja UX + nota implementacyjna — dziś brak w katalogu):
  - min ≈ przewinięcie **całego posta** w dół (pełna wysokość posta)
  - max / czasem więcej — losowy ekstra scroll (humanizacja)
  - Docelowa kontrolka: zakres min–max (np. `scroll.distance` / `scroll.swipe_distance` — nazwa do ustalenia przy implementacji)
  - **Stan dziś:** w katalogu są tylko `scroll.dwell_*` i `scroll.long_pause_*` (czas postoju / przerwy, **nie** dystans). W `feed.scroll` swipe jest **hardcoded** (`~0.75→0.35` wysokości ekranu) — to nie jest „pełny post + czasem więcej”. Przy implementacji: dodać **zakres dystansu scrolla** do katalogu + przekazać do `feed.scroll` (plan only — bez zmian w `catalog.py` teraz).
- Max postów (`budget.max_items`) — zakres
- Max czas (min) (`budget.max_duration_min`)

**Relacje**
- Oglądaj relacje z tacki (`tray_stories.enabled`)
- Max osób (`tray_stories.max_users`) — zakres
- Segmenty relacji (`tray_stories.watch.segments`) — zakres  
  *(użytkownik wymienił „segments” w podstawowych)*

### Zaawansowane

**Scroll → Zaawansowane** *(czas postoju — osobno od dystansu scrolla)*
- Tryb patrzenia (`scroll.dwell_mode`: content_aware / fixed)
- Fallback czasu patrzenia (`scroll.dwell_fallback_s`)
- Długa przerwa co N postów (`scroll.long_pause_every_n`)
- Długość długiej przerwy (`scroll.long_pause_s`)

**Relacje → Zaawansowane**
- Czas na segment (`tray_stories.watch.segment_view_s`)
- Pomijaj sponsorowane (`tray_stories.watch.skip_sponsored`)  
  *(lajk relacji wyłączony w bloku — `allow_like=False`)*

### Notatki

- Dziś cała sekcja Relacje jest `advanced=True` w katalogu — **docelowo odwrotnie**: Relacje podstawowe + dwell w advanced.
- **Dystans scrolla** (Podstawowe) ≠ **dwell** (Zaawansowane): dwell = ile sekund patrzeć; distance = jak daleko przewinąć.
- Brak lajkowania w feedzie (świadomie, na stałe w kodzie).

---

## 2. `reels_niche` — Rolki — nisza

**Grupa:** `engagement` · **status:** partial  
**Główny plugin pozyskiwania.**

### Grupy UI

1. Włączenie  
2. Budżet  
3. Filtr (bramka bez AI)  
4. Lajkowanie  
5. Analiza AI  
6. Lista lajkujących  
7. Lista komentarzy  

### Podstawowe

**Włączenie**
- Plugin włączony (`enabled`)

**Budżet**
- Max rolek (`budget.max_items`)
- Max czas min (`budget.max_duration_min`)

**Filtr**
- Lajki min–max — **jedna kontrolka** → (`gate.min_likes`, `gate.max_likes`)
- Min. komentarzy (`gate.min_comments`)

**Lajkowanie**
- Lajkuj rolki (`like_reel.enabled`)
- Tryb decyzji lajka — patrz rekomendacja poniżej (`ai` + `like_reel.chance`)

**Analiza AI**
- Analiza AI ON/OFF (`ai.enabled`) — widoczne w podstawowych gdy tryb tego wymaga

**Lista lajkujących**
- Zbieraj lajkujących ON/OFF (`likers.harvest.enabled`)

**Lista komentarzy**
- Zbieraj komentujących ON/OFF (`comments.harvest.enabled`)

### Zaawansowane

**Filtr → Zaawansowane**
- Gdy liczniki nieczytelne (`gate.on_unknown`: skip / pass)
- Pomijaj reklamy (`gate.skip_ads`)
- Pomijani autorzy (`gate.skip_authors`)

**Analiza AI → Zaawansowane**
- Liczba klatek (`ai.frames`)
- Czytaj opis (`ai.read_caption`)
- Czytaj top komentarze (`ai.read_top_comments`)
- Zapisuj pojedyncze klatki (`ai.save_frames`)
- Tryb AI (`ai.mode`: niche_match / exclude_only)
- Sztywność (`ai.strictness`)
- Min. wynik niszy (`ai.min_niche_score`)
- Akceptowane werdykty (`ai.allow_verdict`)
- Max udział wykluczeń (`ai.max_excluded_ratio`)
- Strategia wyboru (`ai.pick_strategy`)
- Własny prompt (`ai.prompt_override`)

**Lajkowanie → Zaawansowane**
- Szansa / formuła (`like_reel.chance`) — szczegóły trybu chance (gdy nie schowane w selektorze trybu)

**Lista lajkujących → Zaawansowane**
- Ile zbierać (`likers.harvest.collect`)
- Wykrywaj ring relacji (`likers.harvest.detect_story_ring`)
- Dedup dni (`likers.harvest.dedup_days`)
- Etykieta źródła (`likers.harvest.source_tag`)
- Min. wynik AI do harvestu (`likers.min_niche_score_to_harvest`)
- Hot interact: ON + count + prefer/require ring + skip private + full procedure + overrides (`likers.hot_interact.*`)

**Lista komentarzy → Zaawansowane**
- Harvest: collect, ring, dedup, source_tag (`comments.harvest.*` poza enabled)
- Lajkuj komentarze ON + count + min likes + extra % (`comments.like_comments.*`)
- Hot interact (`comments.hot_interact.*`)

---

### Rekomendacja UX: % chance vs AI (`reels_niche`)

**Jedna konkretna rekomendacja: tryb „AI → potem %” (opcja C), z selektorem 3-stanowym.**

Kontrolka podstawowa: **„Decyzja o lajku”** (`like_decision.mode` — **nowe pole UX**, mapowane na istniejące):

| Opcja UI | Znaczenie | Mapowanie katalogu |
|----------|-----------|-------------------|
| **Tylko AI** | Lajk tylko gdy AI przepuści; szansa = 100% po pass | `ai.enabled=true`, `like_reel.chance = {mode:p, value:1.0}` (lub ukryć chance) |
| **Tylko % (bez AI)** | Brak Vision; losowa szansa stała | `ai.enabled=false`, `like_reel.chance = {mode:p, value:X}` |
| **AI, potem %** ← **domyślna / rekomendowana** | Najpierw bramka AI (score/werdykt); jeśli pass — dodatkowo rzut `chance` (stałe % lub formuła `score/100`) | `ai.enabled=true` + istniejące `like_reel.chance` |

**Dlaczego ta opcja:**
- Obecny katalog już tak działa (AI filtruje, potem `chance` / formula) — UX tylko to wyjaśnia.
- „Tylko AI” i „Tylko %” to uproszczenia tego samego modelu, bez nowych ścieżek runtime.
- Formuła `score/100` ma sens **tylko** przy AI; przy „Tylko %” UI pokazuje suwak 0–100%, przy „AI potem %” — suwak **lub** przełącznik „proporcjonalnie do wyniku AI”.

**Nie rekomendujemy** osobnego równoległego „% zamiast AI” bez selektora — myli użytkowników (dziś `chance` + wyłączone AI = stałe p; z AI = często formula).

---

## 3. `content_search` — Wyszukiwanie: # / słowa / lokalizacja

**Grupa:** `discovery` · **status:** planned

### Grupy UI

1. Włączenie  
2. Źródło i frazy  
3. Siatka wyników  
4. Budżet  
5. Filtr  
6. Analiza AI  
7. Lajkowanie  
8. Lista lajkujących  
9. Komentarze  

### Podstawowe

- Plugin włączony (`enabled`)
- Typ źródła (`source`: hashtag / keyword / location)
- Lista fraz (`phrases.list`)
- Ile fraz na sesję (`phrases.per_session`)
- Ile treści na frazę (`per_phrase.items`)
- Typy treści (`content_types`: reels / posts)
- Max treści łącznie (`budget.max_items`)
- Max czas (`budget.max_duration_min`)
- Lajki min–max (jedna kontrolka → `gate.min_likes` / `gate.max_likes`)
- Min. komentarzy (`gate.min_comments`)
- Analiza AI ON/OFF (`ai.enabled`) — domyślnie OFF
- Lajkuj treści (`like_content.enabled`)
- Decyzja lajka — ten sam wzorzec 3-stanowy co w reels_niche (`ai` + `like_content.chance`; domyślnie „Tylko %” bo AI off)
- Zbieraj lajkujących (`likers.harvest.enabled`)
- Zbieraj komentujących (`comments.harvest.enabled`)

### Zaawansowane

**Frazy**
- Kolejność (`phrases.order`)
- Zapamiętuj kursor (`phrases.cursor_persist`)

**Siatka**
- Pula kafelków do losowania (`per_phrase.grid_pool_size`)
- Zakładka siatki (`per_phrase.grid_tab`: top / recent)

**Filtr**
- on_unknown, skip_ads, skip_authors (`gate.*`)

**AI**
- frames, mode, strictness, min_niche_score, allow_verdict, max_excluded_ratio, pick_strategy, prompt_override (`ai.*` poza enabled)

**Lajk**
- Szczegóły `like_content.chance`

**Lajkujący / komentarze**
- Jak w reels_niche (harvest poza enabled, hot_interact, like_comments)

### Notatki

- Dziś sekcje likers/comments **nie** są `advanced` — docelowo tylko ON/OFF w basic.
- AI domyślnie wyłączona — UI powinno to podkreślać („fraza celuje w niszę”).

---

## 4. `profile_interact` — Interakcja z profilami

**Grupa:** `engagement` · **status:** partial  
*(użytkownik pisał „profile_engage” — w katalogu id = `profile_interact`)*

### Grupy UI

1. Włączenie  
2. Budżet  
3. Źródło profili  
4. Sanity  
5. Bramka profilu  
6. Relacja  
7. AI siatki  
8. Lajki kafelków  
9. Follow  
10. Tempo  

### Podstawowe

- Plugin włączony (`enabled`)
- Max profili (`budget.max_items`)
- Max czas (`budget.max_duration_min`)
- Źródło listy (`source.kind`: target_candidates / raw_profiles)
- Abort jeśli prywatny (`sanity.abort_if_private`)
- Abort jeśli już following (`sanity.abort_if_already_following`)
- Bramka liczników ON/OFF (`gate.enabled`)
- Min. postów / min–max followers / min ratio — podstawowe progi gdy bramka ON  
  (`gate.min_posts`, `gate.min_followers`, `gate.max_followers`, `gate.min_ratio`)
- Oglądaj relację (`story.enabled`)
- Analiza siatki AI ON/OFF (`grid_ai.enabled`)
- Lajkuj kafelki niszy (`like_tiles.enabled`) + ile (`like_tiles.count`)
- Pozwalaj na follow (`follow.allow`)
- Min. scoring do follow (`follow.min_scoring`)
- Szansa follow (`follow.chance`) — suwak / chance

### Zaawansowane

**Źródło**
- Dedup dni (`source.dedup_days`)
- Min. wiek wpisu min (`source.min_age_minutes`)

**Bramka**
- Wykluczenia w bio (`gate.bio_exclusions`)

**Relacja → Zaawansowane**
- segments, segment_view_s, skip_sponsored, like_chance_percent (`story.watch.*`)

**AI siatki → Zaawansowane**
- mode, strictness, min_niche_tiles, min_niche_ratio, min_valid_tiles, tiles_count, scrolls_to_grid, max_excluded_ratio, pick_strategy, prompt_override (`grid_ai.*`)

**Follow**
- Szczegóły trybu chance (formula vs p) jeśli potrzeba

**Tempo**
- Pauza między profilami (`pause_between_profiles_s`) — można trzymać w advanced (nie codziennie zmieniane)

### Notatki

- Brak list lajkujących/komentarzy (to nie treść — to profil).
- Używany też jako podprocedura hot_interact z innych pluginów.

---

## 5. `mass_stories` — Mass stories

**Grupa:** `engagement` · **status:** planned  
*(użytkownik: „stories”)*

### Grupy UI

1. Włączenie  
2. Budżet  
3. Konta-targety  
4. Przeglądanie listy  
5. Oglądanie relacji  
6. Zbieranie profili  

### Podstawowe

- Plugin włączony (`enabled`)
- Max targetów (`budget.max_items`)
- Max czas (`budget.max_duration_min`)
- Lista kont (`targets.list`)
- Ile kont / sesja (`targets.per_session`)
- Lista: followers vs following (`list_source`)
- Max otwarć ringów / target (`per_target.max_ring_opens`)
- Zbieraj obejrzanych ON/OFF (`harvest.enabled`)

### Zaawansowane

**Targety**
- order, cursor_persist (`targets.*`)

**Lista**
- max_scrolls, recent_cap (`per_target.*`)

**Oglądanie**
- segments, segment_view_s, skip_sponsored, like_chance_percent (`story_watch.*`)

**Harvest**
- collect, detect_story_ring, dedup_days, source_tag (`harvest.*`)
- Osobny dedup oglądania (`dedup_days` na rootcie pluginu)

---

## 6. `target_scraper` — Scraper targetów

**Grupa:** `discovery` · **status:** planned  
*(zbliżone do „harvest” w nomenklaturze użytkownika)*

### Grupy UI

1. Włączenie  
2. Budżet  
3. Konta-targety  
4. Źródła danych  
5. Posty / rolki  
6. Ile zbierać  
7. Filtry zapisu  
8. Zapis (harvest meta)  

### Podstawowe

- Plugin włączony (`enabled`)
- Max targetów (`budget.max_items`)
- Max czas (`budget.max_duration_min`)
- Lista kont (`targets.list`)
- Ile kont / sesja (`targets.per_session`)
- Źródła (`sources` multiselect: post_likers, post_commenters, reel_likers, reel_commenters, followers, following)
- Ile ostatnich postów/rolek (`posts.last_posts`)
- Pomijaj przypięte (`posts.skip_pinned`)

### Zaawansowane

- phrases order / cursor (`targets.*`)
- Limity collect: per_post_likers, per_post_commenters, per_list, max_scrolls_per_list (`collect.*`)
- Filtry: min_comment_likes, only_with_story_ring, skip_already_scraped_days (`filters.*`)
- Harvest meta: collect cap, detect_story_ring, dedup, source_tag (`harvest.*`)  
  *(harvest.enabled jest zawsze True w defaultach — rozważyć ukrycie lub wymuszenie)*

### Notatki

- Zero interakcji (tylko zbieranie) — UI powinno to komunikować w opisie grupy.

---

## 7. `reels_warmup` — Rolki — rozgrzewka

**Grupa:** `warmup` · **status:** partial  
*(użytkownik: „warmup”)*

### Grupy UI

1. Włączenie  
2. Budżet  
3. Zachowanie  

### Podstawowe

- Plugin włączony (`enabled`)
- Max rolek (`budget.max_items`)
- Max czas (`budget.max_duration_min`)
- Lajkuj podczas rozgrzewki (`like.enabled`)
- Szansa lajka % (`like.chance`) — tylko stałe p (bez AI)
- Przewijaj reklamy szybciej (`skip_ads`)

### Zaawansowane

- (opcjonalnie puste / brak) — plugin celowo prosty  
- Ewentualnie szczegóły formatu `chance` (mode/formula) — w praktyce tylko `p`

### Notatki

- Brak AI, brak harvestu — nie dokładać zbędnych accordionów.

---

## 8. `niche_discover` — Discovery niszy

**Grupa:** `config` · **status:** planned  
**`queueable: false`, `ui_hidden: true`** — **nie na liście Pluginy**; konfiguracja + start z **Ustawienia konta → Nisza**.

### Grupy UI (w formularzu konta / ukrytym panelu)

1. Budżet  
2. Źródła  
3. Zbieranie  
4. Batch / AI  
5. Zapis  

### Podstawowe

- Max treści (`budget.max_items`)
- Max czas (`budget.max_duration_min`)
- Seed tekstowy (`seed`)
- Własny profil ON (`sources.own_profile`)
- Konkurencja ON (`sources.competitors`)
- Lista konkurencji (`competitors.list`)
- Max profili konkurencji (`competitors.max`)

### Zaawansowane

**Zbieranie** (dziś już `advanced=True`)
- Ile z własnego (`collect.count`)
- Typy treści (`collect.content_types`)
- skip_pinned, read_caption, capture_media (`collect.*`)
- Ile z konkurenta (`competitors.collect_count`)

**Batch / AI**
- batch_size, finalize, strictness (`ai.*`)

**Zapis**
- merge_exclusions, sync_content_search_phrases (`write.*`)

### Notatki

- Brak klasycznego `enabled` w sensie kolejki — uruchomienie one-shot z ustawień konta.
- Przy restrukturyzacji katalogu: ten sam wzorzec basic/advanced, ale renderer w zakładce Ustawienia (nie Pluginy).

---

## Podsumowanie zmian katalogu (dopiero po akceptacji)

1. **Nowe / scalane pola UX (bez zmiany semantyki runtime, jeśli da się zmapować):**
   - `feed_browser`: zakres **dystansu scrolla** (min≈pełny post, max=ekstra) — dziś brak w katalogu; `scroll.enabled` **odrzucamy**
   - UI „lajki min–max” → `gate.min_likes` + `gate.max_likes`
   - UI „Decyzja o lajku” (3 stany) → `ai.enabled` + `*.chance` w `reels_niche` / `content_search`
2. **Przełożenie `advanced` z całych sekcji na podział pól** (Relacje, Likers, Comments, AI…).
3. **Polskie `label`** w katalogu lub warstwa tłumaczeń w panelu.
4. **Renderer:** accordion zaawansowanych **per grupa**, nie jeden na plugin.
5. **Nie ruszać** runtime / domyślnych wartości bez migracji kont.

---

## Checklist akceptacji użytkownika

- [x] Układ grup i list basic/advanced dla każdego pluginu OK  
- [x] Rekomendacja **AI → potem %** (selektor 3-stanowy) OK  
- [x] Merge likes min–max OK  
- [x] Likers/Comments: tylko ON/OFF w basic OK  
- [x] `feed_browser` scroll: ❌ nie dodajemy `scroll.enabled`; ✅ zmienna długość scrolla (min cały post, czasem więcej)  

- [x] `niche_discover`: ten sam wzorzec w Ustawieniach konta OK  
- [x] Po akceptacji — katalog + renderer zaimplementowane (basic/advanced per grupa)

---

*Dokument: plan docelowy. Implementacja: katalog + renderer + scroll.distance w runtime.*