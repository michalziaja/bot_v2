# Rzeczy do poprawy

Data weryfikacji w kodzie: **2026-08-08**.  
Język: PL. Zakres: rzeczy zbyt skomplikowane, kolizje, błędy oraz **rozjazdy dashboard ↔ workflow bota** — z konkretnymi ścieżkami.

**Powiązane dokumenty:**
- [`STAN_SYSTEMU.md`](STAN_SYSTEMU.md) — co realnie działa
- [`DRZEWO_PLIKOW.md`](DRZEWO_PLIKOW.md) — mapa katalogów / plików
- [`KOLEJNE_KROKI.md`](KOLEJNE_KROKI.md) — kolejność prac
- [`CLI_TESTOWANIE.md`](CLI_TESTOWANIE.md) — komendy CLI i testowanie
- Kontekst: [`AGENT_ONBOARDING.md`](AGENT_ONBOARDING.md), [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md), [`BACKLOG.md`](BACKLOG.md)

> **Uwaga:** ten plik **nie** wypisuje stanu kont ani danych logowania.  
> **Zamknięte (nie otwarte):** `BACKLOG.md` §3 (normalizacja pakietu klonów) — zrobione w `core/device.py` (`_normalize_resource_ids`); nie traktować jako długu.

Legenda rekomendacji (szczegóły w §6):
- **Ukryć UI** — przestać obiecywać w panelu / katalogu
- **Podłączyć** — egzekwować w runtime to, co już jest w configu
- **Naprawić** — bug / półimplementacja / rozjazd statusów

---

## 1. Wstęp

Panel (`panel/static/index.html` + `panel/server.py`) i szablon konta (`config/_template/account.json`) eksponują wiele przełączników „jak w produkcie”. Część z nich **zapisuje się do JSON i kończy życie na dysku** — workflow (`plugins/runner.py` / `plugins/impl/*` / `steps/*` / `core/navigator.py`) ich nie czyta.

Drugi problem to **podwójne ścieżki wykonania** (JobRunner vs FlowRunner, scheduler w panelu vs CLI) oraz **katalog deklaratywny vs rejestr implementacji** — UI sugeruje stan „planned”, a kod już działa (albo odwrotnie: UI sugeruje discovery, a API zwraca 501).

Cel tego pliku: lista faktów do uproszczenia albo dokończenia, żeby dashboard nie kłamał względem bota.

---

## 2. Dashboard ≠ workflow

Ustawienia w panelu / configu **nie egzekwowane** (lub tylko stub) w workflow.  
Źródło formularza konta: `panel/static/index.html` (SCHEMA) + wartości w `config/_template/account.json` / `config/accounts/*.json`.

| Ustawienie | Gdzie w UI | Brak / lukа w kodzie |
|---|---|---|
| **Proxy / sieć** (`network.proxy_url`, `proxy_credentials`, `expected_ip_country`, `ip_rotation_command`) | Ustawienia konta → „Sieć / proxy” | **Ukryte w UI** — zero odczytu w `.py` runtime. Pre-session check / rotator — backlog, nie kod produkcyjny. |
| **Telegram / alerty** (`recovery.alert_channel.kind/token/chat_id`) | „Recovery i alerty” | **Ukryte w UI** — brak klienta Telegram. Overlay BLOCKING w `core/navigator.py` tylko loguje `navigator.blocking_overlay`. |
| **`stop_on_verify`** | „Recovery i alerty” | **Ukryte w UI** — brak odczytu w `.py`. Sesja nie ma hooka „verify → abort wg flagi”. |
| **`screenshot_on_error`** | „Recovery i alerty” | **Ukryte w UI** — brak odczytu w `.py`. `JobRunner` przy `PluginResult.failed` nie robi dumpa ekranu. |
| **`max_cost_per_session_usd`** | „AI Vision” | **Ukryte w UI** — brak limitu kosztów w `ai/vision.py` (żaden licznik USD / abort sesji). |
| **`recovery_identify` (model + L6)** | „AI Vision” → `model_per_mode.recovery_identify` | **Ukryte w UI** — stub odpowiedzi w `ai/vision.py`. `core/navigator.py` kończy drabinkę na L5 / `navigator.exhausted` — **brak L6**. |
| **Gate `niche.accepted`** | „Nisza” → checkbox `accepted` | **Zrobione** — `niche_is_accepted` w `analyze_frames_ai` / `_analyze_grid`; event `niche_not_accepted`. |
| **Discovery niszy** | „Nisza” + przycisk → `POST /api/run-plugin` | **Zrobione** — `niche_discover`: posty batch / rolki 1:1 / tap-opis / tokeny / dokładne # keywords; sync fraz; `write_niche`. Patrz `NICHE_DISCOVER.md`. |
| **Engagement rolek** | Plugin `reels_niche` (ustawienia „Klatki” / „Analiza AI”) | **Aktualne** — `use_frames` + `frame_interval_s` (bez `watch_reel`); opis **tap** (shared z discovery); logi `reel_log`; AI score/decyzja/powod. Patrz `REELS_NICHE_WORKFLOW.md`. |

~~Rozjazd `source.kind=raw_profiles` (tylko candidates)~~ — **naprawione**: reader raw + `mark_outcome` (`interacted_at`/`outcome`); `unsupported_source` tylko przy nieznanym `kind`.

---

## 3. Kolizje / nadmierna złożoność

| Temat | Pliki | Co koliduje |
|---|---|---|
| **JobRunner vs FlowRunner** | `plugins/runner.py`, `runner/flow_runner.py`, `cli.py` (`run-queue` vs `start --flow`) | Dwa executory: produkcja = kolejka pluginów; YAML flows = legacy / kalibracja. Łatwo odpalić „złą” ścieżkę i porównywać jabłka do gruszek. |
| **Scheduler panel vs CLI** | `panel/server.py` (`_scheduler_loop`), `cli.py` `cmd_scheduler` | Ten sam `scheduler/tick.py`, dwa procesy. CLI ostrzega w docstringu: nie uruchamiać razem z `panel` — walka o telefon. |
| **Catalog `status` vs `registry.available()`** | `plugins/catalog.py` vs `plugins/registry.py` + `plugins/impl/*` | Katalog: m.in. `feed_browser`, `content_search`, `mass_stories`, `target_scraper` = **`planned`**, a klasy są `@register` w `impl/` i ładuje je `registry.load_all()`. UI / estymator patrzą na etykietę katalogu, runner — na `is_implemented()`. |
| **`niche_discover` w katalogu** | `plugins/catalog.py` + `impl/niche_discover.py` | **Zrobione** — `status: ready`, registry, one-shot z panelu. |

---

## 4. Błędy i dług techniczny

| Problem | Pliki | Fakt |
|---|---|---|
| **Checkpoint bez resume** | `plugins/runner.py` (`_checkpoint` → `data/accounts/<alias>/queue_state.json`) | Docstring obiecuje wznowienie od miejsca przerwania. Kod **tylko zapisuje** JSON po zadaniu; `run()` zawsze startuje kolejkę od zera (brak odczytu checkpointu). Test: `tests/test_plugins_runtime.py` (`test_checkpoint_written_after_each_task`). |
| **Brak `login_if_needed` w prologu JobRunner** | `plugins/runner.py` `PROLOGUE_STEPS` | Tylko `system.wake` → `system.unlock` → `system.ensure_ig_running`. Login jest w osobnym flow CLI (`cli.py` `cmd_login`) oraz w `steps/system/login_if_needed.py` — **nie** w prologu kolejki. Po wylogowaniu sesja pada. |
| ~~Like tiles = TODO~~ | `steps/profile/visit.py` | **Zrobione** — open kafelek → `post.like` → soft BACK na profil. Defaults: `like_tiles.enabled`, `gate.enabled`, `pick_strategy=random`. |
| **PIN unlock = TODO** | `steps/system/unlock.py`, UI: `device.lockscreen_pin` w SCHEMA | Tylko swipe up. PIN/wzór → `StepResult.failed` („PIN nieobsługiwany”). Pole PIN w panelu nie jest podpięte do kroku. |
| **Pipeline `target_candidates`** | `data/stores.py`, harvest → `raw_profiles.jsonl` | Interact **nie głoduje**: czyta raw + `mark_outcome`. Brak nadal: promocja raw→`target_candidates` (filtr jakościowy). |
| **`posts.skip_pinned`** | `target_scraper` / catalog | UI + default `True`, w kodzie **no-op** (`_ = bool(...)`). |
| **`cursor_persist`** | `plugins/blocks.py` (phrases/targets) | Pole w catalogu — **brak odczytu** w `plugins/impl/*`. |
| **`niche_discover`** | katalog + `impl/` | **Zrobione** — pipeline + panel Nisza; bez „Zapis zaawansowane” / `merge_exclusions`. |
| **Assert viewer przed engagement** | `target_scraper._viewer_engagement` | **Odroczone (ROI)** — live: start ze złego ekranu (`home_feed` / cudzy sheet). Brak assertu „jesteśmy na viewerze”. |
| **`type_query` spam dumpów** | `steps/search/type_query.py` | **Odroczone (ROI)** — prawie każdy helper `hierarchy(force=True)`; reuse flata na retry. |
| **Wspólny `back_until`** | `content_search` / `reels_niche` / `target_scraper` / `niche_discover` | **Odroczone (ROI)** — lokalne `_back_to*` z różną semantyką; naming/eventy niespójne. |
| **Martwe settings scrapera** | catalog + `target_scraper` | **Odroczone (ROI)** — `skip_pinned` no-op, `min_comment_likes` nieczytane, `harvest.collect` vs `collect.per_*`, dwa klucze dedup. |
| **Cache nicków w raw** | `RawProfilesStore.add_rows` / `mark_outcome` | **Odroczone (ROI)** — pełny read/rewrite JSONL przy każdym harvest; scratch/cache per sesja. |

### Zamknięte (scraper / raw / ROI — nie otwierać ponownie)

| Temat | Status |
|---|---|
| Expand sheet (likers/commenters) | **Zrobione** — open → `expand_sheet_confirmed` (drag_handle) → `full_screen`/`sheet_top` → harvest; trust payload `post.open_*` (drugi expand tylko bez flagi) |
| Followers/following bez expand | **Zrobione** — natywna `follow_list`; `expand_sheet_confirmed` tylko likers/comments |
| Warunkowy BACK (engagement retry) | **Zrobione** — BACK tylko gdy sheet wszedł; `no_entry`/`nothing_opened` = 0× BACK |
| Autor w harvestcie commenters | **Zrobione** — `COMMENT_USERNAME_IDS` bez `row_feed_photo_profile_name`; `list.harvest_usernames` param `exclude` |
| Wyciek `baseline_screen` (content_search) | **Zrobione** — `finally` pop `baseline_screen` + `content_search_active` |
| Wspólne `gate_params` + `max_likes` | **Zrobione** — `plugins/base.py`; wczesna bramka w `reels.watch`; docs `REELS_NICHE_WORKFLOW` |
| Hierarchy dump thrash (expand) | **Zrobione** — jedna geo na fazę (before / after_pull) |
| Martwy UI content_search | **Częściowo** — AI **podłączone** (Vision/opis/komentarze + runtime na rolkach). `like_comments` / `skip_authors` nadal **ukryte** (nieegzekwowane). |
| Komentarze bez resource-id | **Zrobione** — orphan TextView w `collect_rows` |
| Dedup `raw_profiles` | **Zrobione** — `RawProfilesStore.add_rows` + schemat w `_example/` |
| Podgląd raw w panelu | **Zrobione** — `GET /api/raw-profiles` + typ `raw_profiles_preview` |
| Flow `target_scraper` mid-search | **Zrobione** — search tylko na starcie targetu; między kafelkami BACK na profil |

### Świadomie nieotwarte

| Temat | Status |
|---|---|
| **BACKLOG §3 — normalizacja resource-id klonów** | **Zrobione** w `core/device.py` (`_normalize_resource_ids` przy hierarchy). Nie zgłaszać ponownie jako otwartego bloku. |

---

## 5. Bezpieczeństwo

| Ryzyko | Pliki | Fakt |
|---|---|---|
| **Plaintext secrets** | `config/secrets.py` → `config/secrets.json`; `config/credentials.py` → `config/credentials.json` | Klucz Anthropic i hasła IG w JSON na dysku (chmod 0600 best-effort). Panel maskuje w odpowiedzi HTTP, ale pliki są czytelne lokalnie / w backupach. |
| **Panel bez auth** | `panel/server.py` | FastAPI bez middleware auth / API key. Endpointy: start/stop sesji, sekrety, credentials, kod 2FA, scheduler. Przy wystawieniu poza localhost — pełna kontrola. |

(Pobocznie: `steps/system/_ui_helpers.py` `escape_input_text` — wąski escape przed `adb shell input text`; hasło z credentials może zawierać znaki problematyczne dla shella.)

---

## 6. Co uprościć vs dokończyć

Rekomendacja **per punkt** — wybrać jedną ścieżkę i nie zostawiać „ładnego UI bez kodu”.

| Punkt | Ukryć UI | Podłączyć | Naprawić |
|---|---|---|---|
| Proxy / network | ✓ **ukryte** do czasu `security/ip_rotator` (BACKLOG) | — | — |
| Telegram alerts | ✓ **ukryte** albo | prawdziwy sender + hook na BLOCKING | — |
| `stop_on_verify` | ✓ **ukryte** albo | abort sesji przy verify/2FA | — |
| `screenshot_on_error` | ✓ **ukryte** albo | dump przy `failed` w JobRunner | — |
| `max_cost_per_session_usd` | ✓ **ukryte** albo | licznik w `ai/vision.py` + abort | — |
| `recovery_identify` / L6 | ✓ **ukryte** w UI | L6 w `core/navigator.py` + prawdziwy mode | — |
| Gate `niche.accepted` | — | ✓ `niche_is_accepted` | — |
| Discovery one-shot | — | ✓ runtime + SessionManager | — |
| `raw_profiles` w `profile_interact` | — | ✓ reader + `mark_outcome` | — |
| Catalog `planned`/`partial` vs registry | — | — | ✓ sync `status` (m.in. `content_search` planned, `profile_interact` partial) |
| JobRunner vs FlowRunner | docs/UI: flows = debug only | — | — |
| Dual scheduler | jeden orkiestrator; drugi tylko failover | — | file-lock / refuse gdy drugi żyje |
| Checkpoint bez resume | uczciwy docstring („audit only”) | odczyt `queue_state.json` + skip done | — |
| Prolog bez login | — | dodać `system.login_if_needed` (+ opcjonalnie verify code) do `PROLOGUE_STEPS` | — |
| Like tiles | — | — | ✓ zrobione |
| PIN unlock | wymóg: brak PIN na telefonie; ukryć pole | — | wpisywanie PIN z `lockscreen_pin` |
| Promocja raw→candidates | — | pipeline filtrujący (raw już działa w interact) | — |
| `skip_pinned` / `cursor_persist` | ukryć albo badge „niedostępne” | — | realny filtr pinned / kursor fraz |
| content_search AI / like_comments / skip_authors | like_comments + skip_authors: ✓ ukryte | AI: ✓ Vision/opis/komentarze + `_one_item` | — |
| Plaintext secrets | — | vault / szyfrowanie at-rest | nie commitować plików |
| Panel bez auth | wymusić bind localhost | Basic/API token | — |

### Priorytet (skrót)

1. **Bezpieczeństwo** (localhost / auth, sekrety poza gitem) — zanim cokolwiek poza maszyną deweloperską.  
2. **Ukryć martwy UI** (proxy, telegram, recovery flags, max_cost, model L6) — mniej fałszywych oczekiwań.  
   *(content_search: AI podłączone; like_comments/skip_authors nadal ukryte)*  
3. **Prolog z loginem + sync catalog↔registry** (`planned`/`partial` ≠ registry).  
4. **Checkpoint: resume albo uczciwy opis.**  
5. Dopiero potem: L6, PIN, opcjonalny pipeline raw→candidates, `skip_pinned` / `cursor_persist`.

---

*Lista problemów i rekomendacji — bez implementacji w tym pliku. Stan „co działa” → [`STAN_SYSTEMU.md`](STAN_SYSTEMU.md); mapa plików → [`DRZEWO_PLIKOW.md`](DRZEWO_PLIKOW.md).*
