---
name: Search Tags variants helper
overview: Flaga w content_search (ew. niche_discover) — po wyszukaniu frazy/# przełącza zakładkę na Znaczniki, zbiera warianty do niche.tag_variants, pokazuje osobną listę w UI; w sesji osobne limity budżetu na frazy vs znaczniki; tryb po collect = tylko zapis albo wejście w siatkę.
todos:
  - id: dump-tags-tab
    content: Live dump hierarchy zakładki Znaczniki (tabs + wiersze listy) na A50 PL i zapis sample
    status: pending
  - id: step-switch-tab
    content: Nowy krok search.switch_results_tab (aliasy PL/EN + swipe HorizontalScrollView)
    status: pending
  - id: step-collect-variants
    content: Nowy krok search.collect_tag_variants (scrape listy, normalizacja #, dedup, limit, sort po postach jeśli UI daje)
    status: pending
  - id: wire-output
    content: Scratch + persist do niche.tag_variants[expression] (merge/dedup); helper zapisu w store
    status: pending
  - id: catalog-mvp
    content: "Catalog content_search: flaga discover_tag_variants + mode collect|grid + limity fraz vs znaczników + max_variants; UI osobna lista wariantów"
    status: pending
  - id: tests-unit
    content: Testy aliasów zakładek, normalizacji #, dedupu i merge mapy tag_variants bez urządzenia
    status: pending
isProject: false
---

# Helper wariantów hashtagów — zakładka Znaczniki / Tags

## Cel

Po wyszukaniu słowa kluczowego lub `#hashtag` bot ma:

1. Wylądować na ekranie wyników (`search_results`).
2. Przełączyć pasek zakładek na **Znaczniki** (EN: **Tags**) — zakładka często poza ekranem, wymaga przesunięcia w lewo.
3. Zescrapować listę powiązanych / wariantowych hashtagów.
4. Zapisać je w **`niche.tag_variants[expression]`** oraz pokazać w **osobnej liście w ustawieniach** (panel).
5. W sesji `content_search` umożliwić **osobną gałąź budżetu** na znaczniki (obok zwykłych fraz) — np. 2 frazy × 2 kafelki × 2 scroll **oraz** 2 znaczniki × 2 kafelki × 2 scroll.

To **nie** jest przełączanie Popularne/Najnowsze wewnątrz siatki `#` (`per_phrase.grid_tab`) — to osobny pasek zakładek wyników wyszukiwania.

---

## Decyzje użytkownika (rozstrzygnięte)

| # | Temat | Decyzja |
|---|--------|---------|
| 1 | Persist MVP | **`niche.tag_variants[expression]`** w JSON konta; scratch = bufor sesji; promocja do flat `hashtags_discovered` / `phrases` opcjonalna (flagi, default off) |
| 2 | Osobny plugin vs flaga | **Flaga** w `content_search` (ew. też `niche_discover`) — **bez** osobnego pluginu one-shot |
| 3 | Seed wejściowy | **Dowolny** — zarówno `#hashtag`, jak i zwykły keyword |
| 4 | Po collect vs siatka | **Do wyboru w ustawieniach:** tryb `collect` (tylko zbierz + zapisz) **albo** `grid` (po collect wejście w siatkę wybranego `#`) — nie zawsze jedno |
| 5 | Model użycia / budżet | W jednej sesji możliwe **dwie gałęzie**: limity na zwykłe frazy **oraz** osobne limity na discovered tags / variants (np. 2×2×2 fraz + 2×2×2 znaczników) |
| 6 | Po collect — UI | Zostawić w JSON **i** wyświetlić w ustawieniach w **osobnej liście** (przy wyrażeniu lub globalnie) |
| 7 | Limit wariantów | **Tak** — `max_variants` per wyrażenie; sort po liczbie postów z subtitle **jeśli UI to daje** |

---

## Stan wyjściowy (analiza kodu / kalibracji)

### Kroki search (`steps/search/*`)

| Krok | Rola | Luka względem celu |
|------|------|--------------------|
| `search.open` | Tap `nav.search_tab` → `search_top` / `search_input_active` | OK jako wejście |
| `search.type_query` | Focus + wpisanie (fold PL→ASCII, escape `#`) + opcjonalnie `submit`→`search_results` | Kończy na domyślnej zakładce (siatka „Dla Ciebie”) |
| `search.pick_result` | Tap w **profil** z sugestii (`row_search_user_*`) | Tylko konta; brak hashtagów / Tags |

**Brak** kroku przełączania zakładek wyników search i **brak** scrapera listy Znaczniki.

### Struktura fraz w `account.json` (dziś)

Żywe konta (np. `config/accounts/a50_clone3.json`) i `plugins/store.py` trzymają frazy jako **płaskie `list[str]`**:

| Pole | Rola |
|------|------|
| `niche.keywords` / `niche.hashtags` | Scalona lista (manual + discovered) — UI / prompty |
| `niche.keywords_manual` / `hashtags_manual` | Ręczne źródła |
| `niche.keywords_discovered` / `hashtags_discovered` | Odkryte (AI / discovery) |
| `plugins.content_search.phrases.list` (+ osobne listy hashtags/keywords w UI) | Frazy do `content_search` — też płaskie stringi |

Template `config/_template/account.json` ma uproszczoną niszę (`keywords` / `hashtags` bez `*_manual` / `*_discovered`) — runtime i tak normalizuje przez `ensure_niche_phrase_sources`.

**Wniosek:** nie zamieniać flat list na obiekty `{tag, variants}` — złamałoby `_dedup_phrases`, `merge_niche_phrases`, `apply_discovered_phrases`, sync do `content_search` i panel. Warianty Tags to **osobna mapa obok** list fraz.

### Pluginy

- **`content_search`** (`plugins/impl/content_search.py`): `_search` = `search.open` → `type_query(submit=True)` → siatka na `search_results`. Domyślnie zostaje na **Dla Ciebie**. Ustawienie catalog `per_phrase.grid_tab` (`top`/`recent` = Popularne/Najnowsze) **nie jest podłączone w kodzie pluginu** i dotyczy innego UI (podzakładki siatki `#`), nie paska Znaczniki.
- **`niche_discover`**: hashtagi z Vision / captionów (`extract_hashtags_from_texts` + AI) → `apply_discovered_phrases` → `niche.hashtags_discovered`. **Nie** czyta zakładki Tags z wyszukiwarki. Ma wzorzec przełączania zakładek **profilu** (`_select_profile_grid_tab` po `content-desc` + indeks) — do przeniesienia idei, nie kodu 1:1 (inne ID / Button z `text`).

### Kalibracja

- Fingerprint `search_results` (`calibration/SM_A505FN_draft.json`):
  - `search_tab_bar_layout`
  - `scrollable_tab_layout` (**HorizontalScrollView**)
  - `tabbed_pager`
  - `view_switcher_container`
- Sample `calibration/samples/44_search_results_grid.flat.json` (wyniki po frazie, locale **PL**):

| Widoczny tekst Button | Uwagi |
|----------------------|--------|
| `Dla Ciebie` | Domyślna; `class=android.widget.Button`, **bez resource-id** |
| `Konta` | Odpowiednik EN Accounts |
| `Bez personalizacji` | Osobna zakładka (nie „Profile”) |
| `Audio` | Widoczna przy prawej krawędzi |
| *(ucięty fragment)* | Ostatni `LinearLayout` `[1053…1080]` — kolejne zakładki **poza ekranem** |

**Znaczniki nie widać w sample** — trzeba je dociągnąć swipe’em po `scrollable_tab_layout`. Brak sample hierarchy z otwartą listą Tags → **pierwszy krok wdrożenia: live dump**.

### Sugestie (przed Enterem)

Sample `43_search_suggestions.flat.json`: wiersze fraz mają m.in. `row_search_keyword_title` / `search_keyword_title` (tekst np. `gym` bez `#`). To **alternatywne** źródło wariantów (typeahead), nie zakładka Znaczniki — można rozważyć później jako fallback, nie MVP.

### Mapowanie locale zakładek (wyniki search)

| Klucz wewnętrzny | PL (A50, sample) | EN (oczekiwane) |
|------------------|------------------|-----------------|
| `for_you` | Dla Ciebie | For you |
| `accounts` | Konta | Accounts |
| `unpersonalized` | Bez personalizacji | Unpersonalized / Without personalization* |
| `audio` | Audio | Audio |
| `tags` | Znaczniki | Tags |
| `places` | Miejsca* | Places |
| `reels` | Rolki* | Reels |

\*Teksty oznaczone `*` — potwierdzić dumpem (nie w sample 44). Identyfikacja po **`text` Buttona**, nie po resource-id.

### Czy jest już przełączanie zakładek search?

**Nie.** Jedyny podobny mechanizm to zakładki siatki **profilu** w `niche_discover`. Brak swipe poziomego po `scrollable_tab_layout` w kodzie search.

---

## Gdzie w architekturze

**Rekomendacja: dwa nowe kroki w `steps/search/`** + flaga/orkiestracja w `content_search` (ew. `niche_discover`) + helper zapisu + UI listy.

| Element | Ścieżka | Rola |
|---------|---------|------|
| `search.switch_results_tab` | `steps/search/switch_results_tab.py` | Znajdź i tapnij zakładkę po aliasach; swipe HSV gdy poza ekranem |
| `search.collect_tag_variants` | `steps/search/collect_tag_variants.py` | Scrape listy Tags → znormalizowane `#…` (limit + opcjonalny sort) |
| helper zapisu | np. `plugins/store.py` → `merge_tag_variants` / `write_niche` | Persist mapy przy wyrażeniu |
| orkiestracja | flaga w `content_search` (+ opcjonalnie `niche_discover`) | Seed z fraz → Tags → collect → zapis → mode collect\|grid |
| UI panelu | ustawienia content_search / niche | Osobna lista znaczników/wariantów |

**Dlaczego nie rozszerzać `type_query` / `pick_result`:** inna odpowiedzialność (wpis vs wybór profilu vs nawigacja zakładek vs harvest listy). Kroki zostają reuse’owalne z `stepcall`.

**Nie mieszać** z `per_phrase.grid_tab` (Popularne/Najnowsze) — osobny concern; ewentualnie osobny ticket na dociągnięcie istniejącego ustawienia catalog.

---

## Flow UI (MVP)

```text
HOME / dowolny ekran z bottom nav
  → search.open
  → search.type_query({ query: "#fitness" | "fitness", clear_first: true, submit: true })
  → identify: top == search_results
  → search.switch_results_tab({ tab: "tags" })
       • dump hierarchy
       • szukaj Button text ∈ aliasy tags (znaczniki / tags)
       • jeśli brak: swipe w lewo w pasie y≈ tab_bar (bounds scrollable_tab_layout), max N prób
       • tap Button; settle; weryfikacja (selected / lista tagów widoczna)
  → search.collect_tag_variants({ max_items, max_scrolls, sort_by_post_count? })
       • zbierz widoczne wiersze hashtagów (+ subtitle postów jeśli dostępne)
       • opcjonalnie scroll listy w dół
       • normalizacja: zawsze z #, casefold dedup, bez query seed jeśli include_seed=false
       • obcięcie do max_variants (po sort jeśli włączony)
  → scratch (bufor sesji) + persist do niche.tag_variants[expression]
  → wg ustawienia after_collect_mode:
       • collect → BACK / ensure home (tylko discovery)
       • grid    → tap wybrany # z listy / wariant → siatka → harvest jak frazy
  → (sesja content_search) osobne liczniki budżetu: phrases vs tag_variants
```

**Uwaga o wejściu:** `submit=True` jest spójne z `content_search` (siatka wyników). Seed = dowolny keyword lub `#`. Dump Tags potwierdzi, czy po `#` Enter vs tap sugestii zmienia zestaw zakładek.

### Diagram sesji z dwiema gałęziami budżetu

```text
content_search session
├── Gałąź A — frazy (keywords / hashtags z phrases.list)
│     limity: max_phrases × tiles_per_phrase × scrolls
│     flow: search → (opcjonalnie Tags collect jeśli flaga+mode) → siatka Dla Ciebie / #
│
└── Gałąź B — znaczniki / warianty (z niche.tag_variants lub live collect)
      limity: max_tag_variants_phrases × tiles_per_tag × scrolls_tag
      (niezależne od limitu fraz — obie gałęzie w jednej sesji)
```

Przykład docelowy użytkownika: **2 frazy × 2 kafelki × 2× scroll** + **2 znaczniki × 2 kafelki × 2× scroll** w jednej sesji.

---

## Identyfikatory UI / fingerprinty do kalibracji

### Już mamy (wystarczy reuse)

- Ekran: fingerprint `search_results` + kotwica `search.input`.
- Kontener zakładek: `com.instagram.android:id/scrollable_tab_layout`, `…/search_tab_bar_layout`.

### Do dodania po live dumpie Tags

1. **Sample** np. `calibration/samples/45_search_tags_tab.flat.json` (+ hierarchy) — pełny pasek po swipe + lista wierszy.
2. **Aliasy tekstowe** w kodzie kroku (niekoniecznie w JSON anchors), np.:
   - `tags`: `("znaczniki", "tags", "tagi")`
   - pozostałe zakładki jak w tabeli locale (na przyszłe reuse).
3. **Resource-id wierszy listy Tags** — TBD z dumpu. Kandydaci do sprawdzenia (nie potwierdzone w sample):
   - `row_hashtag_*` / `row_search_hashtag_*` / `row_search_keyword_*`
   - tekst z `#` w `TextView` + subtitle z liczbą postów
4. Opcjonalnie kotwica `search.results_tab_bar` → `scrollable_tab_layout` (ułatwi swipe po bounds).
5. **Nie** dodawać fingerprintu osobnego ekranu, dopóki Tags nadal matchuje `search_results` (ten sam activity + tab bar) — wystarczy stan „aktywna zakładka tags” w logice kroku.

---

## Format wyjścia i persist (rozstrzygnięte)

### Decyzja użytkownika

Jeśli keywords i `#` są w JSON konta, **wynik scrapera Znaczniki zapisujemy w JSON przy danym wyrażeniu** (mapowanie expression → lista wariantów). Scratch zostaje buforem sesji / payloadem kroku, ale **nie jest jedynym miejscem prawdy**. Panel pokazuje **osobną listę** wariantów.

### Kształt danych (rekomendowany)

Nowa mapa obok istniejących flat list — **bez** zamiany `keywords` / `hashtags` na obiekty:

```json
{
  "niche": {
    "keywords_manual": ["technologia", "fitness training"],
    "hashtags_manual": ["#technologia", "#fitness"],
    "keywords_discovered": [],
    "hashtags_discovered": [],
    "keywords": ["technologia", "fitness training"],
    "hashtags": ["#technologia", "#fitness"],
    "tag_variants": {
      "#technologia": [
        "#technologiajutra",
        "#techgear",
        "#gadzety"
      ],
      "fitness training": [
        "#fitnesstraining",
        "#workout",
        "#gym"
      ]
    }
  }
}
```

#### Dlaczego mapa `niche.tag_variants`, a nie obiekty w listach

1. **Spójność z runtime:** `store._dedup_phrases` / `merge_niche_phrases` / sync `content_search` oczekują `list[str]` — zmiana na obiekty to duży breaking change.
2. **Semantyka:** warianty Tags to **powiązania przy seedzie**, nie nowe „źródło” jak `*_discovered` (AI). Osobne pole czytelnie oddziela „frazy do użycia” od „sugestie IG dla frazy”.
3. **Klucz = wyrażenie wejściowe** (to, co wpisano w search / co jest w listach niche), wartość = znormalizowane `#…` z zakładki Znaczniki.
4. **`plugins.content_search.phrases`** zostaje płaskie dla gałęzi A; gałąź B czyta z `tag_variants` (lub live collect) bez mieszania w jedną listę bez limitu.

#### Normalizacja kluczy (`expression`)

| Seed wejściowy | Klucz w `tag_variants` |
|----------------|------------------------|
| hashtag (`#Foo`, `foo` gdy query było `#`) | `#` + `casefold` formy kanonicznej z `#` (jak `_as_hashtag_list`) |
| keyword (bez `#`) | `strip` + zachowana czytelna forma; lookup/dedup kluczy po `casefold` |

Wartości zawsze: `#` + strip, dedup po `casefold` (jak `_dedup_phrases(..., force_hash=True)`).

Opcjonalnie później: osobne `tag_variants_meta[expression] = { "updated_at", "source": "search_tags" }` — nie wymagane w MVP.

### Scratch (zawsze w MVP — bufor sesji)

```python
ctx.scratch["tag_variants"] = ["#fitnessmotivation", "#fitnessgirl", ...]
ctx.scratch["tag_variants_query"] = "#fitness"   # fraza wejściowa (= klucz mapy)
ctx.scratch["tag_variants_tab"] = "tags"
```

Payload kroku `collect_tag_variants`:

```json
{
  "query": "#fitness",
  "hashtags": ["#fitnessmotivation", "#fitnessgirl"],
  "count": 2,
  "scrolled": 1
}
```

### Persist (główne miejsce zapisu)

| Warstwa | Gdzie | Zachowanie |
|---------|-------|------------|
| **Główna** | `config/accounts/<id>.json` → `niche.tag_variants[expression]` | Merge append + dedup (patrz niżej) |
| Sesja | `ctx.scratch` | Bufor pod caller / logi |
| UI | panel ustawień — **osobna lista** | Odczyt mapy; nie mieszać z `*_discovered` |
| Opcjonalnie (flaga) | `niche.hashtags_discovered` i/lub `plugins.content_search.phrases` | **Promocja** wariantów do płaskich list — nie zastępuje mapy |

Helper w `store` (szkic API):

```python
def merge_tag_variants(
    niche: dict,
    expression: str,
    variants: list[str],
    *,
    max_variants: int | None = None,
    include_seed: bool = False,
) -> dict:
    """Zwraca zaktualizowane niche['tag_variants'] (merge + dedup)."""
```

Zapis przez istniejący zapis niszy (`write_niche` / reconcile panelu) — nowe pole ignorowane przez stary kod aż panel/runtime je odczyta.

### Merge / dedup przy kolejnych discovery

1. Znormalizuj `expression` → klucz mapy.
2. Weź istniejącą listę `tag_variants.get(key, [])` (match kluczy casefold, zachowaj pierwszy zapisany klucz).
3. Dopisz nowe warianty z collect; **dedup casefold**; zachowaj kolejność: najpierw stare, potem nowe (chyba że sort po postach przy świeżym scrape — wtedy kolejność z UI/sortu, merge i tak dedupuje).
4. Szanuj `max_variants` (po merge, keep prefix do limitu; jeśli limit mniejszy niż już zapisane — nie kasować w MVP bez jawnej flagi `trim=true`).
5. `include_seed=false` (default): nie wstawiaj samego seeda do listy wartości.
6. Nie usuwaj wpisów dla innych wyrażeń.
7. Idempotencja: ponowny scrape tej samej frazy nie duplikuje `#`.

### Migracja starych kont

- Brak `niche.tag_variants` → traktuj jako `{}` (lazy create przy pierwszym zapisie).
- Flat `keywords` / `hashtags` / `*_manual` / `*_discovered` **bez zmian**.
- Template: dodać `"tag_variants": {}` przy okazji wdrożenia (opcjonalnie); runtime i tak robi `dict.get`.
- Stare konta z wariantami już „wklejonymi” tylko do `hashtags` / `phrases.list` (bez mapy) — **nie zgadujemy** parent expression; mapa wypełnia się od pierwszego uruchomienia helpera.

### UI panelu (MVP przy integracji)

- **Osobna lista** znaczników/wariantów — przy wyrażeniu (rozwijane „Znaczniki powiązane (IG)” = `tag_variants[expression]`) **i/lub** widok globalny całej mapy.
- Puste = jeszcze nie scrapowane.
- Nie mieszać wizualnie z `*_discovered` (AI) — inna etykieta / sekcja.
- Badge „ma warianty” przy frazie w `content_search` — opcjonalnie.

---

## Integracja — flaga w `content_search` (rozstrzygnięte: bez osobnego pluginu)

### `content_search` (główne miejsce)

- Sekcja catalog `discover_tag_variants.*` (patrz niżej).
- Po udanym `_search` (seed = keyword lub `#`):
  1. switch Tags → collect → zapis `niche.tag_variants[query]`
  2. wg `after_collect_mode`:
     - **`collect`** — nie otwieraj siatki z Tags; wróć / idź dalej wg gałęzi A
     - **`grid`** — wejdź w wybrany `#` z wariantów i harvestuj kafelki w **gałęzi B** (osobne limity)
- Osobna flaga promocji do `phrases.list` — domyślnie **off**.

**Ryzyko:** po switchu na Tags siatka mediów znika — w mode `collect` nie harvestować z tej zakładki; w mode `grid` wejść w konkretny `#` z listy, nie lajkować „pustej” listy Tags.

### Budżet sesji — dwie gałęzie

| Gałąź | Źródło seedów | Limity (przykład catalog) |
|-------|---------------|---------------------------|
| A — frazy | `phrases.list` / keywords+hashtags | istniejące `max_phrases` / tiles / scrolls |
| B — znaczniki | wpisy z `tag_variants` (lub świeżo zebrane) | `tag_variants.max_phrases`, `tiles_per_phrase`, `scrolls` (osobne) |

Obie gałęzie mogą działać w **jednej sesji** — liczniki niezależne.

### `niche_discover` (opcjonalnie, ta sama flaga-koncept)

- Opcjonalny seed: dla top N keywords/`#` z niszy zbierz warianty Tags → `tag_variants[expression]`.
- Nie zastępuje Vision; promocja do `hashtags_discovered` tylko przy jawnej fladze (default off).

---

## Ustawienia catalog (MVP — doprecyzowane)

Sekcja pod `content_search` (nie osobny plugin). Nazwy robocze — dopiąć do konwencji catalog przy implementacji.

### Discovery / collect

| Klucz | Typ | Default | Opis |
|-------|-----|---------|------|
| `discover_tag_variants.enabled` | bool | false | Włącz zbieranie z zakładki Znaczniki |
| `discover_tag_variants.after_collect_mode` | enum | `collect` | `collect` = tylko zapis listy; `grid` = po collect wejdź w siatkę wybranego `#` |
| `discover_tag_variants.max_variants` | num | 15 | Max unikalnych `#` na wyrażenie (po merge / scrape) |
| `discover_tag_variants.max_scrolls` | num | 2 | Scrolle listy Tags przy collect |
| `discover_tag_variants.sort_by_post_count` | bool | true | Jeśli subtitle z liczbą postów dostępny — sort malejąco przed obcięciem limitu; inaczej kolejność UI |
| `discover_tag_variants.include_seed` | bool | false | Czy dodać samą frazę wejściową do listy wariantów |
| `discover_tag_variants.write_to_account` | bool | true | Persist `niche.tag_variants[expression]` |
| `discover_tag_variants.promote_to_discovered` | bool | false | Dodatkowo append do `hashtags_discovered` |
| `discover_tag_variants.sync_content_search` | bool | false | Dodatkowo append do płaskiej `phrases.list` |

### Budżet gałęzi B (znaczniki) — osobno od fraz

| Klucz | Typ | Default | Opis |
|-------|-----|---------|------|
| `tag_variants_budget.enabled` | bool | false | Czy sesja ma osobną gałąź harvestu na znaczniki/warianty |
| `tag_variants_budget.max_phrases` | num | 2 | Ile znaczników/wariantów otworzyć w sesji (obok limitu zwykłych fraz) |
| `tag_variants_budget.tiles_per_phrase` | num | 2 | Kafelki na znacznik |
| `tag_variants_budget.scrolls` | num | 2 | Scrolle siatki na znacznik |
| `tag_variants_budget.source` | enum | `from_map` | `from_map` = bierz z `niche.tag_variants`; `live_collect` = najpierw collect z seeda fraz, potem harvest; `both` = mapa + dociągnięcie |

Seed discovery (które frazy odpalić pod Tags): z listy `phrases` / niche — **dowolny** keyword lub `#` (bez osobnego ograniczenia typu seeda).

### UI catalog / panel

- Sekcja „Znaczniki (warianty IG)” — **osobna lista** (globalna mapa + per expression).
- Kontrolki: enable, mode collect|grid, limity wariantów, limity budżetu gałęzi B (max / tiles / scrolls).
- Nie mylić z `per_phrase.grid_tab` (Popularne/Najnowsze).

Na start kalibracji wystarczą kroki wywoływane ręcznie / z testu; catalog + UI przy PR integracji.

---

## Ryzyka

| Ryzyko | Mitigacja |
|--------|-----------|
| Locale PL/EN (i inne) | Aliasy wielojęzyczne; log widocznych tab `text` przy failu |
| Znaczniki poza ekranem | Swipe po bounds `scrollable_tab_layout`, max próby, fail czytelny |
| Brak zakładki Tags dla frazy | Soft-fail / skip frazy; nie crash pluginu; nie kasuj istniejącej mapy |
| Brak resource-id na Buttonach | Match po `text` + `class=Button` w obszarze tab bara |
| Personalizacja („Dla Ciebie” vs Tags) | Tags zwykle mniej spersonalizowane — celowo; nie mylić z „Bez personalizacji” |
| Dump listy Tags nieznany | Blokuje scrape — **najpierw kalibracja live** |
| `grid_tab` top/recent w catalog | Nie mylić w UI/docs z tym helperem |
| Diakrytyki w `#` | `type_query` już folduje do ASCII — warianty z UI mogą mieć ogonki; normalizować przy dedupie jak w type_query |
| Po collect UI na Tags | Mode `collect` nie harvestuje z listy; mode `grid` wchodzi w konkretny `#` |
| Dwie gałęzie budżetu | Jasne liczniki phrases vs tag_variants; nie zużywać limitu A na B |
| Panel/reconcile nadpisze mapę | `reconcile_niche_phrases` / zapis panelu musi **przepuszczać** `tag_variants` (merge, nie drop unknown keys) |

---

## Kolejność wdrożenia MVP

1. **Kalibracja live (A50 PL):** search → fraza/`#` → Enter → swipe tabs → dump Znaczniki (flat + hierarchy). Opcjonalnie EN.
2. **`search.switch_results_tab`:** aliasy + swipe HSV + tap + log.
3. **`search.collect_tag_variants`:** scrape + scroll + normalizacja `#` + dedup + `max_variants` (+ sort po postach jeśli UI) → scratch/payload.
4. **Persist:** `merge_tag_variants` w `store` + zapis `niche.tag_variants` (z uwzględnieniem reconcile panelu).
5. **Testy jednostkowe** aliasów / normalizacji / merge mapy (bez device).
6. **Smoke live:** jedna fraza, assert `count > 0` + klucz w `account.json`.
7. **Integracja (osobny PR):** flaga `content_search` + catalog (mode collect\|grid, limity A/B) + UI osobna lista wariantów.
8. (Opcjonalnie) ta sama flaga-koncept w `niche_discover`; fallback typeahead gdy brak zakładki Tags.

---

## Otwarte pytania

1. ~~Persist docelowy MVP~~ → **ROZSTRZYGNIĘTE** (`niche.tag_variants` + scratch + opcjonalna promocja).
2. ~~Osobny plugin vs flaga~~ → **ROZSTRZYGNIĘTE** (flaga w `content_search` / ew. `niche_discover`).
3. ~~Seed wejściowy~~ → **ROZSTRZYGNIĘTE** (dowolny: `#` i keyword).
4. ~~Po collect vs siatka~~ → **ROZSTRZYGNIĘTE** (ustawienie `collect` \| `grid`).
5. ~~Model użycia / osobne limity~~ → **ROZSTRZYGNIĘTE** (dwie gałęzie budżetu w sesji).
6. ~~UI listy~~ → **ROZSTRZYGNIĘTE** (JSON + osobna lista w ustawieniach).
7. ~~Limit wariantów~~ → **ROZSTRZYGNIĘTE** (`max_variants` + sort po postach jeśli UI daje).

**Jedyna otwarta (nie blokuje kalibracji):**

- **Locale produkcyjne kont:** tylko PL, czy też EN od dnia 1? (aliasy i tak warto mieć obu zestawów w kodzie kroku — pytanie głównie o priorytet live dumpu EN.)

**Status:** plan gotowy do **kalibracji live** + implementacji kroków; catalog/UI w PR integracji.

---

## Pliki kluczowe (odniesienia)

- `steps/search/open.py`, `type_query.py`, `pick_result.py`
- `plugins/impl/content_search.py` (`_search`, `_query_for`, budżet sesji)
- `plugins/impl/niche_discover.py` (`_select_profile_grid_tab`, `apply_discovered_phrases` / sync phrases)
- `plugins/store.py` (`apply_discovered_phrases`, przyszłe `merge_tag_variants`)
- `plugins/catalog.py` (sekcja content_search — nowe `discover_tag_variants.*` / `tag_variants_budget.*`; nie mylić z `grid_tab`)
- `config/_template/account.json` (dodać `tag_variants` przy wdrożeniu)
- `config/accounts/*.json` (żywe flat lists — bez breaking change)
- `calibration/SM_A505FN_draft.json` (`search_results`)
- `calibration/samples/44_search_results_grid.flat.json` (tabs PL)
- `calibration/samples/43_search_suggestions.flat.json` (`row_search_keyword_title`)
