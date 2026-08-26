# Stan systemu — Instagram Bot v2

> Snapshot operacyjny dla kolejnego agenta (stan kodu na 2026-08-08).  
> **Źródło prawdy:** ten plik + kod. `AGENT_ONBOARDING.md` = mapa; `ROADMAP.md` / części `ARCHITEKTURA.md` / `BACKLOG.md` = historyczne, gdy kolidują z kodem.  
> **Bez stanu zalogowanych kont / sesji online.**

**Dokumenty towarzyszące (czytaj równolegle):**

| Plik | Rola |
|------|------|
| [`DRZEWO_PLIKOW.md`](DRZEWO_PLIKOW.md) | Opisy plików / katalogów |
| [`RZECZY_DO_POPRAWY.md`](RZECZY_DO_POPRAWY.md) | Znane długi i bugi |
| [`KOLEJNE_KROKI.md`](KOLEJNE_KROKI.md) | Proponowana kolejność prac |
| [`CLI_TESTOWANIE.md`](CLI_TESTOWANIE.md) | Komendy CLI i testowanie |
| [`AGENT_ONBOARDING.md`](AGENT_ONBOARDING.md) | Mapa + pułapki + code review |

---

## 1. Produkt

Automatyzacja **Instagrama na Androidzie** — nie bot Telegram/Discord, nie oficjalne API IG.

| Warstwa | Tech |
|---------|------|
| UI telefonu | **uiautodev** REST (`:20242`) → `UiAuto2Device` |
| Sterowanie | **ADB** (shell, forward, push) |
| Klatki wideo | **scrcpy-server** + PyAV / OpenCV; fallback screenshot HTTP |
| AI | Anthropic Vision (`ai/vision.py`) albo cichy **STUB** bez klucza |
| Panel | FastAPI + uvicorn + `panel/static/index.html` |
| Dane | JSON / JSONL lokalnie — **brak DB** |
| Cwd | katalog rodzica `...\bot\` → `python -m v2.cli …` |

**Brak w runtime (tylko config/docs/backlog):** Telegram alerts, proxy, Discord, płatności, Instagram API.

---

## 2. Wejścia

| Wejście | Komenda / URL | Orkiestrator |
|---------|---------------|--------------|
| CLI kolejka (**PROD**) | `python -m v2.cli run-queue --account=…` | `JobRunner` |
| CLI flow YAML | `python -m v2.cli start --account=… --flow=…` | `FlowRunner` (legacy / regresja) |
| Panel | `python -m v2.cli panel` → `:8765` | `SessionManager` → subprocess CLI |
| Scheduler | wątek w panelu **lub** `python -m v2.cli scheduler` | `tick_once` → `run-queue` |
| Login | `cli login` / `POST /api/login` | kroki systemowe |

**Nie uruchamiać jednocześnie** `panel` i `cli scheduler` — dwa orkiestratory na ten sam telefon.

---

## 3. Warstwy i kontrakty

```
CLI / Panel / Scheduler
        │
        ▼
 JobRunner (plugins)  ──lub──  FlowRunner (YAML)
        │
        ▼
 Plugins (impl/*) — pętle, gate/match/chance, budżety
        │
        ▼
 Steps (steps/*) — atomowe UI  (@register → StepResult)
        │
        ▼
 Core: device, navigator, screen_map, humanizer, events, limits, AI…
        │
        ▼
 Config (JSON) + Data (JSONL) + Calibration
```

| Kontrakt | Znaczenie |
|----------|-----------|
| **Context** | DI sesji (`runner/context.py`): device, ht, navigator, screen_map, humanizer, events, account, session_id, fb, scratch |
| **JobRunner** | Prolog → zadania → epilog; budżet; STOP; rama `max_queue_duration_s`; checkpoint zapis |
| **Plugin** | Pętla + budżet + decyzje; woła kroki przez `plugins/stepcall.call` |
| **Step** | Atom UI; zwraca `StepResult` |
| **Budget** | `max_items`, `max_duration_s`, `stop_path` (`plugins/base.py`); wspólne `gate_params` / `draw_range` |

**Merge ustawień:** `catalog.defaults` ← `account.plugins` ← `task.overrides` (`plugins/store.py`).

YAML flow ≠ produkt użytkownika.

---

## 4. Flow sesji (kolejka)

1. `cli.main` → `cmd_run_queue`
2. `steps.registry.load_all()` + `plugins.registry.load_all()`
3. `build_context(account)` — device, scrcpy/FrameBuffer, ScreenMap, Humanizer, Navigator, EventLogger → `Context`
4. Usunięcie zaległego `data/accounts/<alias>/STOP`
5. `JobRunner.run(account)`:
   - kolejka: `config/accounts/<alias>.queues/<nazwa>.json` (domyślnie `default`)
   - **PROLOG:** `system.wake` → `unlock` → `ensure_ig_running` → `navigator.ensure("home_feed")`  
     *(bez auto-loginu)*
   - **dla każdego zadania:** merge settings → Budget (przycięty do okna kolejki) → `ensure(entry)` → `plugin.run` → `finally ensure(exit)` → **checkpoint** → pauza `between_tasks` z profilu humanizera
   - **EPILOG:** `ensure(home_feed)` → `close_ig` → `sleep_screen`
6. Abort kolejki: STOP użytkownika, `limit_daily`, `on_task_error=abort`, koniec okna (`MIN_TASK_WINDOW_S=30`)

### STOP

Plik `data/accounts/<alias>/STOP`. Sprawdzany między zadaniami (`_user_stopped`) i w budżecie pluginu. CLI/panel kasują zaległy STOP przy starcie.

### Checkpoint — ZAPIS bez resume

`JobRunner._checkpoint` zapisuje `data/accounts/<alias>/queue_state.json` po każdym zadaniu (`session_id`, `queue_name`, `tasks[]`).

**W kodzie nie ma odczytu tego pliku ani pomijania wykonanych zadań.** Docstring w `runner.py` obiecuje wznawianie — to **aspiracja / dług**, nie zachowanie. Po STOP/crashu kolejka startuje od zera. Test sprawdza tylko zapis (`test_checkpoint_written_after_each_task`).

---

## 5. Siedem pluginów (runtime)

Źródło uruchomienia: `plugins.registry.available()` — **7 klas w `impl/`**.  
Katalog (`plugins/catalog.py`) ma też 8. wpis `niche_discover` (`ready`, `queueable=False`) — **zaimplementowany** w `plugins/impl/niche_discover.py` (one-shot z panelu Nisza). Patrz [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md): batch postów, rolki 1:1, tap-opis, tokeny, dokładne liczby keywords/#.

| id | Rola | Jak działa (skrót) | Znane luki |
|----|------|-------------------|------------|
| `feed_browser` | Warmup home | Najpierw tray stories, potem scroll (`feed.scroll` + distance/dwell). Długa przerwa `long_feed` z profilu humanizera. **Zero lajków/follow w kodzie.** | Catalog `status=planned` mimo implementacji |
| `reels_niche` | Główny harvest | `reels.open` → pętla: watch (klatki ∥ gate → composite ‖ opis tap) → AI (`use_frames`/`read_caption`; wynik przez `say_ai_result`) → like → harvest → hot; „Nastepna rolka…” → ⏸ `humanizer.pause("before_next_reel")` → swipe. Szczegóły: [`REELS_NICHE_WORKFLOW.md`](REELS_NICHE_WORKFLOW.md) | Catalog `partial`; sekcja „Klatki" (`frames`/`frame_interval_s`); scrcpy gdy `use_frames`; `watch_reel` w ustawieniach pluginu (dla trybu czasu oglądania) |
| `content_search` | Discovery # / fraza / lokalizacja | Fraza → siatka → open tile → analiza (gate/like/harvest/hot) → opcjonalnie `per_phrase.scrolls_in_viewer`× (swipe → analiza) → **BACK** do `search_results` (nigdy `ensure(home)`/`clips` między scrollami); `finally` czyści `baseline_screen` + `content_search_active`. Logi `content_search_log`. `search.type_query`: `#` w cudzysłowach | AI / `like_comments` / `skip_authors` **ukryte w UI** (runtime nie egzekwuje); catalog `planned`; posty bez gate; po utracie sheeta skip harvest_commenters |
| `profile_interact` | Interakcja z profilami | Domyślnie `raw_profiles` → search (reuse: BACK+clear nick, **bez home**) → `profile.visit` (sanity/gate → visited; story soft-return; grid 1×scroll+crop; AI reasons; **realne like tiles**; follow) + `mark_outcome`/`interacted_at`. Pauza między profilami: `humanizer.pause("between_profiles")`. Panel: podgląd raw (`GET /api/raw-profiles`) | Hot (`run_one`) **nie** mutuje raw; defaults: `gate.enabled`, `like_tiles`, `pick_strategy=random`; catalog `partial`; brak promocji raw→candidates |
| `mass_stories` | Relacje z list followers targetów | Target → followers list → ring → watch segments | Catalog `planned` |
| `target_scraper` | Zbieracz usernameów (**działa** w registry) | Per target: **search raz** → profil → siatka postów (ordered) likers→commenters → zakładka Rolki (to samo) → followers/following → HOME / kolejny target. Zero search mid-kafelki. Likers/comments: open → expand+`full_screen` (trust payload `post.open_*`; retry expand tylko bez flagi) → harvest; **BACK tylko gdy sheet wszedł** (`no_entry`/`nothing_opened` = 0× BACK). Followers/following: natywna lista, **bez** `expand_sheet`. Commenters: `exclude` autora (`from_target`); `COMMENT_USERNAME_IDS` bez `row_feed_photo_profile_name`. Logi `scraper_log`. → `raw_profiles.jsonl` | Catalog nadal `planned`; `skip_pinned` = **no-op**; zero interakcji (like/follow) |
| `reels_warmup` | Warmup rolek | Watch bez gate/AI; opcjonalny like ze szansą; skip ads | Catalog `partial` |

Wszystkie queueable: `entry_screen` / `exit_screen` = `home_feed` (katalog). JobRunner i tak woła `navigator.ensure` przed/po.

---

## 6. AI Vision

Plik: `ai/vision.py`. Tryby: `reel_niche` / `profile_grid` / `niche_discover_*` / stuby `comment_gen` / `recovery_identify`.  
Każdy call zbiera `usage` (`tokens_in` / `tokens_out`); discovery sumuje do `account.niche.last_ai_*`.

| Tryb | Prompt YAML | Użycie w runtime |
|------|-------------|------------------|
| `reel_niche` | `ai/prompts/reel_niche.yml` | `steps/reels/analyze_frames_ai.py` — stdout: score/decyzja/powod (bez pełnego promptu); #/keywords w opisie mają większą wagę niż 2–4 klatki |
| `profile_grid` | `ai/prompts/profile_grid.yml` | `steps/profile/visit.py` |
| `niche_discover_posts` | `ai/prompts/niche_discover_posts.yml` | `plugins/impl/niche_discover.py` |
| `niche_discover_reels` | `ai/prompts/niche_discover_reels.yml` | j.w. |
| `niche_discover_account` | `ai/prompts/niche_discover_account.yml` | j.w. |
| `niche_discover_merge` | `ai/prompts/niche_discover_merge.yml` | j.w. (≥2 profile) |
| `comment_gen` | **brak pliku** | tylko stub w `_stub_response`; **nie wołany** |
| `recovery_identify` | **brak pliku** | tylko stub; **nie wołany** (L6 AI nie istnieje) |

Bez `ANTHROPIC_API_KEY` / `config/secrets.json` albo `account.ai.stub=true` → STUB (heurystyki). Bot „działa”, decyzje nie są prawdziwym modelem.

---

## 7. Navigator

`core/navigator.py` — drabinka L0–L5 (+ L5b), potem `navigator.exhausted`.

Mimo docstringa „L0–L6”: **brak poziomu L6 z AI**. Po nieudanym L5/L5b → `False`.  
`BACK_TO_EXIT_SCREENS` obejmuje sheety i pełne ekrany bez bottom nav (`profile_other`, `story_viewer`, `search_results`, …).

**Normalizacja klonów:** w `UiAuto2Device.hierarchy()` — `_normalize_resource_ids` zamienia `ig_package:id/…` → `com.instagram.android:id/…` przed fingerprintami. BACKLOG §3 jest **przestarzały**.

---

## 8. Panel (`:8765`)

FastAPI, **bez auth**. `SessionManager`: jedna sesja naraz (subprocess `v2.cli`).

**Działa (grupy):**

- Konta / urządzenia / credentials (status bez hasła w GET) / secrets / humanizer profiles
- Config konta, katalog pluginów + ustawienia per konto (delta vs defaults)
- Kolejki / scenariusze (`queue`, presets, delete)
- Start: `run-queue`, `run-plugin`, flow YAML, login; stop; status
- Diagnostyka: `where`, `recover`; 2FA IPC (`CODE_REQUEST` / `CODE_RESPONSE`)
- Harmonogram: master switch, timeline per device, regenerate/move/queue/autospread,
  ręczne dodanie kolejki na oś (`locked`), `sessions_per_day` edytowane na osi (nie w siatce kont),
  estimate czasu i akcji
- SSE: `GET /api/events/stream`
- Podgląd raw: `GET /api/raw-profiles/{acc}?limit=&unique=` (ustawienia `profile_interact`)
- Wątek `_scheduler_loop` w procesie panelu (tick co ~60 s)

Przy włączonym master switch ręczne starty → **HTTP 423**.

---

## 9. Scheduler (E10 — **jest w kodzie**)

Plan **per urządzenie** (nie per konto): telefon = zasób wyłączny.

| Moduł | Rola |
|-------|------|
| `scheduler/estimator.py` | Górne ograniczenie czasu scenariusza |
| `scheduler/action_estimator.py` | Szacunek lajków / follow / … |
| `scheduler/device_planner.py` | `build_day_plan`, materialize, move, locked blocks, dropped |
| `scheduler/state.py` | Master switch + `active_hours` + `cycle_days` → `data/scheduler.json` |
| `scheduler/tick.py` | `tick_once` → grace 5 min → `start(account, scenario, max_duration_s)` |

Flow ticka: `is_enabled` → materialize dnia → `expire_missed` → `due_block` → jeśli busy: `device_busy` → inaczej start `run-queue` z `--max-duration-s` = reszta okna → status `running` / później `done` / `failed` / `missed`.

`queue_override` bloku → efemeryczna kolejka slot (`write_slot_queue` / `drop_slot_queue`).

CLI: `scheduler`, `scheduler-switch --on/--off`.

---

## 10. Ścieżki config / data

```
v2/
  config/
    accounts/<alias>.json
    accounts/<alias>.queues/<scenariusz>.json   # default = DEFAULT_SCENARIO
    devices/<device_alias>.json
    credentials.json          # gitignore — hasła IG
    secrets.json              # gitignore — ANTHROPIC_API_KEY
    _template/
  calibration/<model>_draft.json   # np. SM_A505FN_draft.json
  data/
    scheduler.json
    accounts/<id>/
      events.jsonl
      queue_state.json         # zapis checkpointu (bez resume)
      STOP
      CODE_REQUEST / CODE_RESPONSE
      sessions/*.log
      raw_profiles.jsonl          # harvest/scraper; dedup przy zapisie
      target_candidates.jsonl     # bot tylko czyta; promocja raw→tu jeszcze brak
      visited_profiles.jsonl
      frames/ …
    accounts/_example/raw_profiles.example.jsonl   # schemat wiersza
    devices/<alias>/schedule/YYYY-MM-DD.json
  ai/prompts/{reel_niche,profile_grid,niche_discover_*}.yml
  config_bin/scrcpy-server.jar
```

**`raw_profiles.jsonl` (wiersz):** `ts`, `source`, `username`, `has_ring`, `from_target`, `content_kind` (`reel`/`post`/`null`). Dedup po `username` wg `dedup_days` (None = forever). Przykład: `data/accounts/_example/raw_profiles.example.jsonl`.

`DATA_ROOT` / `CONFIG_ROOT` = `v2/data`, `v2/config` (względem pakietu).

---

## 11. Testy

- **~736** metod `test_*` w **42** plikach `v2/tests/test_*.py` (stan 2026-08-08, zliczenie statyczne).
- Offline na samples / fakeach — bez telefonu:  
  `python -m unittest discover -s v2/tests -v` (cwd: `...\bot`).
- Pokryte m.in.: screen_map, navigator, limits, reels, AI stub, humanizer, flow_runner, pluginy (`profile_interact`, `content_search`, hot/sheet), planner/tick, login/verification, credentials/secrets, stores (`mark_outcome`), events, estimator.
- **Brak / słabo:** panel/`SessionManager`, resume checkpoint, injection/path safety, dual-orchestrator, prolog+login.

---

## 12. Konflikty docs vs prawda

| Temat | Docs (historyczne) | Prawda w kodzie |
|-------|--------------------|-----------------|
| **Checkpoint** | ARCHITEKTURA / docstring runnera: „wznawia od miejsca przerwania” | Tylko **zapis** `queue_state.json`; **brak resume** |
| **E10** | ROADMAP: „czego nie ma: automatu (E10)” | **Jest:** `scheduler/*` + tick w panelu / `cli scheduler` → `run-queue` |
| **E9** | ROADMAP: brak statystyk | `data/stats.py` + `cli stats` istnieją; zakładka panelu — wg potrzeb UI |
| **Catalog status** | `planned` / `partial` w `catalog.py` | **Zsynchronizowane** — wszystkie 8 pluginów mają `status: ready` zgodnie z `registry` |
| **Klony (BACKLOG §3)** | „screen_map nie normalizuje prefiksu” | **Zrobione** w `device._normalize_resource_ids` przy `hierarchy()` |
| **L6 AI** | ARCHITEKTURA / docstring navigatora sugeruje L6 | Po L5/L5b → exhausted; **brak** `recovery_identify` w runtime |
| **comment_gen** | Wspomniany w vision | Brak promptu YAML; brak wywołań ze steps |
| **niche_discover** | Spec w catalog / `NICHE_DISCOVER.md` | **Zaimplementowany** — posty (batch) → rolki (1:1) → account → merge; firm_tap + retry; opis rolki tap-only; tokeny IN/OUT; dokładne `keywords_count`/`hashtags_count`; sync fraz append; one-shot panel / `--plugin=` |
| **Gate niche.accepted** | UI obiecuje blokadę AI | **Egzekwowane** w `analyze_frames_ai` + `_analyze_grid` (`niche_not_accepted`) |
| **raw_profiles „unsupported”** | starsze docs / RZECZY | **Obsługiwane** — default source + `mark_outcome` + podgląd API |
| **Like tiles TODO** | starsze onboarding | **Realne** open→like→soft back |
| **Dedup raw** | review: „deklarowana ≠ implementacja” | **Zrobione** w `RawProfilesStore.add_rows` |
| **ROI code-review (2026-08-08)** | ślepy BACK / expand na follow_list / wyciek baseline / autor w commenters / martwy UI content_search | **Zrobione** — warunkowy BACK; followers bez expand; trust `full_screen`; `gate_params` w `plugins/base.py`; `exclude` harvest; ukryty martwy catalog; mniej dumpów geo. Odroczone: assert viewer, type_query dumps, `back_until`, dead settings scrapera, cache raw |

---

## 13. Kluczowe parametry / flagi CLI

```text
run-queue --account=… [--queue=…] [--only=id1,id2] [--max-items=N]
          [--max-duration-s=S] [--speed=F] [--dry-run]
start --account=… --flow=…
login / where / recover --target=…
stats --account=… --days=N
scheduler [--interval=…]
scheduler-switch --on|--off
panel [--host …] [--port 8765]
```

Stop: utwórz `data/accounts/<alias>/STOP`.

---

## 14. Must-know (operacyjnie)

1. Produkt = kolejka pluginów, nie YAML.
2. Catalog status jest zsynchronizowany z `plugins.registry` — wszystkie 8 pluginów `ready`.
3. Bez klucza Anthropic = STUB.
4. Checkpoint bez resume.
5. Master switch ON blokuje ręczne starty (423).
6. Jedna sesja / jedno urządzenie naraz; plan pilnuje kolizji.
7. Prolog bez loginu — po wylogowaniu najpierw `login`.
8. Overlay/sheet: zawsze `try/finally` + powrót do bazowego ekranu.
9. `humanizer.speed` skraca pauzy ludzkie, nie waity UI. Wszystkie ludzkie pauzy pochodzą z `humanizer/profiles/*.yaml` (`default` / `aggressive` / `conservative`) i trafiają do `events.jsonl` jako `humanizer.pause`. Panel pokazuje zakresy z wybranego profilu gdy brak override. Czas oglądania rolki / relacji (`watch_reel`, `watch_story`) to parametry biznesowe pluginów, nie pauzy humanizera.
10. Panel bez auth + plaintext sekrety na dysku.

---

*Koniec snapshotu. Przy zmianach architektury aktualizuj ten plik (i konflikty w §12).*
