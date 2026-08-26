# Instagram Bot v2 — mapa projektu dla agenta

> Plik onboardingowy: po przeczytaniu kolejny agent powinien wiedzieć **co to jest**, **jak działa**, **gdzie szukać**, **jakie ryzyka**.  
> Złożone z przeglądu struktury, logiki, integracji i code review (2026-08-08).  
> Nie zastępuje `ARCHITEKTURA.md` — to skrót operacyjny + findings.

---

## 0. TL;DR (30 sekund)

- To **nie jest bot Telegram/Discord**. To **automatyzacja Instagrama na telefonie Android** (ADB + uiautodev + scrcpy).
- Produktowa ścieżka: **kolejka pluginów** (`run-queue`), nie YAML flow.
- Wejścia: `cli.py` / panel FastAPI (`:8765`) / scheduler.
- Dane: **JSON/JSONL** (brak DB). AI: **Anthropic Vision** (albo cichy STUB bez klucza).
- Cwd uruchomienia: katalog rodzica `...\bot\` → `python -m v2.cli …`
- Najważniejszy docs w repo: `ARCHITEKTURA.md`. Ten plik = mapa + review.
- Workflow engagement: [`REELS_NICHE_WORKFLOW.md`](REELS_NICHE_WORKFLOW.md); discovery: [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md).

---

## 1. Stack i technologie

| Warstwa | Tech |
|--------|------|
| Język | Python 3.9+ |
| UI telefonu | **uiautodev** REST (`:20242`) → `UiAuto2Device` |
| Sterowanie | **ADB** (shell, forward, push) |
| Klatki wideo | **scrcpy-server** + PyAV / OpenCV / fallback screenshot |
| AI | **Anthropic** SDK (`ai/vision.py`) |
| Panel | **FastAPI** + uvicorn + `panel/static/index.html` |
| Config | JSON + YAML (PyYAML) |
| Persystencja | JSONL/JSON lokalnie — **brak SQLite/Supabase** (backlog) |
| Testy | `unittest` (~42 pliki w `tests/`) |

**Zależności:** brak własnego `requirements.txt` w `v2/` — używany jest  
`c:\Users\mz\Desktop\prog\bot\bot-czerwiec\bot\instagram_bot\requirements.txt`  
(+ zwykle Pillow, którego może brakować w tym pliku).

**Brak w kodzie (tylko config/docs/backlog):** Telegram alerts, proxy, Discord, płatności, oficjalne API IG.

---

## 2. Drzewo katalogów (gdzie co leży)

```
v2/
  cli.py                 # wejście CLI (main + build_context)
  ARCHITEKTURA.md        # kontrakty warstw — MUST READ
  ROADMAP.md / BACKLOG.md
  AGENT_ONBOARDING.md    # TEN PLIK

  ai/                    # Vision + prompts/*.yml
  calibration/           # ScreenMap (SM_A505FN_draft.json) + samples/
  config/                # accounts, devices, secrets, credentials, schema
  config_bin/            # scrcpy-server.jar
  core/                  # device, navigator, humanizer, events, limits, scrcpy…
  data/                  # stores.py, stats.py + runtime accounts/<id>/
  flows/                 # YAML (regresja / legacy — NIE główny UX)
  humanizer/profiles/    # pauzy ludzkie (default / aggressive / conservative)
  panel/                 # FastAPI dashboard + SessionManager
  plugins/               # JobRunner, catalog, store, impl/* (biznes)
  runner/                # Context, FlowRunner, Step
  scheduler/             # planner + tick
  steps/                 # atomowe operacje UI (registry)
  tests/                 # unittest (offline na samples)
```

**Runtime per konto:** `data/accounts/<account_id>/`  
(events, stores JSONL, schedule, sessions, frames, STOP, CODE_*).

**Konta obecne:** `technature699`, `a50_clone2`, `a50_clone3`.

---

## 3. Schemat działania (E2E)

### Ścieżki startu

| Wejście | Komenda | Orkiestrator |
|---------|---------|--------------|
| CLI kolejka (PROD) | `python -m v2.cli run-queue --account=…` | `JobRunner` |
| CLI flow YAML | `python -m v2.cli start --account=… --flow=…` | `FlowRunner` |
| Panel | `python -m v2.cli panel` → `:8765` | `SessionManager` → subprocess CLI |
| Scheduler | `python -m v2.cli scheduler` lub pętla w panelu | `tick_once` → `run-queue` |
| Login | `cli login` / `POST /api/login` | kroki systemowe |

### Flow produkcyjny (kolejka) — krok po kroku

1. `cli.main` → `cmd_run_queue`
2. `steps.registry.load_all()` + `plugins.registry.load_all()`
3. `build_context(account)`:
   - `load_account` → device + humanizer + limits…
   - `ensure_uiauto` → `UiAuto2Device`
   - `ScrcpyClient` → `FrameBuffer` (fallback: screenshot)
   - `ScreenMap.load(calibration/<model>_draft.json)`
   - `Humanizer`, `HumanTouch`, `EventLogger`, `Navigator`
   - składanie `runner.context.Context`
4. Usunięcie zaległego `STOP`
5. `JobRunner.run(account)`:
   - czyta kolejkę `config/accounts/<alias>.queues/<nazwa>.json`
   - **PROLOG:** wake → unlock → ensure_ig_running → `navigator.ensure("home_feed")`
   - **dla każdego zadania:** merge settings → Budget → ensure(entry) → `plugin.run` → ensure(exit) → checkpoint → pauza
   - **EPILOG:** home_feed → close_ig → sleep_screen
6. Wewnątrz pluginu: `plugins.stepcall.call` → kroki → device/AI/stores/limits/chance
7. Efekty: gesty na telefonie, Anthropic (opcjonalnie), JSONL, pliki STOP/CODE_*, logi sesji

### Scheduler (model blokowy, od 2026-08)

Plan powstaje **raz na urządzenie**, nie na konto — telefon jest zasobem wyłącznym,
więc kolizja musi być wykryta przy układaniu, a nie dopiero przy odpalaniu.

1. `estimator.estimate_queue` liczy GÓRNE ograniczenie czasu scenariusza:
   Σ `budget.max_duration_min` zadań + przerwy + prolog/epilog + bufor.
2. `device_planner.build_day_plan` układa wszystkie sesje wszystkich kont
   urządzenia na jednej osi (greedy first-fit z jitterem, odstęp 5–20 min).
   Bloki `locked` (ruszone ręcznie) przeżywają regenerację; sesje, które się
   nie mieszczą, lądują w `dropped` z powodem.
3. `state.is_enabled` — master switch (`data/scheduler.json`). Wyłączony =
   plan jest tylko podglądem. Włączony = ręczne uruchomienia dostają **HTTP 423**.
4. `tick.tick_once` odpala blok, gdy `now >= start` (okno łaski 5 min), przekazując
   pozostały czas jako `--max-duration-s`. Przegapiony blok → status `missed`.
5. `JobRunner.max_queue_duration_s` przycina budżet każdego zadania do reszty
   okna, a `SessionManager` ma watchdoga na wypadek przeciągnięcia.

---

## 4. Warstwy (architektura)

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
 Steps (steps/*) — atomowe UI
        │
        ▼
 Core: device, navigator, screen_map, humanizer, events, limits, AI…
        │
        ▼
 Config (JSON) + Data (JSONL) + Calibration
```

**Kontrakt:** Step = atom; Plugin = pętla + budżet + decyzje.  
YAML flow ≠ produkt użytkownika (testy/prolog/legacy).

---

## 5. Najważniejsze symbole (plik → nazwa → rola)

### Wejście / orkiestracja

| Plik | Symbol | Rola |
|------|--------|------|
| `cli.py` | `build_context` | Składa pełny Context sesji |
| `cli.py` | `cmd_run_queue` | Start produkcyjny |
| `cli.py` | `cmd_start` / `cmd_login` / `main` | Flow YAML / login / argparse |
| `plugins/runner.py` | `JobRunner` | Prolog → zadania → epilog |
| `runner/flow_runner.py` | `FlowRunner` | Executor YAML |
| `runner/context.py` | `Context` | DI sesji (`scratch` = cache per iterację) |
| `runner/step.py` | `Step` / `StepResult` | Protokół kroku |
| `plugins/base.py` | `Budget`, `Plugin`, `PluginResult`, `gate_params`, `draw_range` | Budżet + wynik + wspólny gate → `reels.watch` |
| `plugins/stepcall.py` | `call` | Wywołanie kroku z pluginu |

### Pluginy (7 zaimplementowanych)

| Plik | id | Rola |
|------|-----|------|
| `plugins/impl/reels_niche.py` | `reels_niche` | Główny harvest (klatki∥gate → opis tap → AI `use_frames`/`read_caption` → like → harvest; `humanizer.pause("before_next_reel")` z profilu) |
| `plugins/reel_log.py` | — | Ludzkie logi stdout z prefixem `reels_niche` (`say_ai_result`, tryby wejść) |
| `plugins/impl/feed_browser.py` | `feed_browser` | Feed / stories |
| `plugins/impl/content_search.py` | `content_search` | Szukaj → grid → harvest/hot; **bez** `ensure(reels_feed)`/`clips_tab`; `finally` pop `baseline_screen`; gate rolek przez wspólne `gate_params` |
| `plugins/content_search_log.py` | — | Ludzkie logi stdout (prefix `content_search`) |
| `plugins/impl/profile_interact.py` | `profile_interact` | raw_profiles → search reuse → visit; `run_one` dla hot |
| `plugins/profile_log.py` | — | Ludzkie logi wizyty (sanity/gate/grid AI/tiles/follow) |
| `plugins/impl/mass_stories.py` | `mass_stories` | Masowe stories |
| `plugins/impl/target_scraper.py` | `target_scraper` | Search raz → posty/Rolki (likers→commenters, expand+confirm) → followers/following **bez** expand → HOME; warunkowy BACK; exclude autora z commenters; → `raw_profiles` |
| `plugins/scraper_log.py` | — | Ludzkie logi stdout (prefix `target_scraper`, wzorzec `reel_log`) |
| `plugins/impl/niche_discover.py` | `niche_discover` | Discovery niszy (one-shot): posty batch, rolki 1:1, tap-opis, tokeny, dokładne # keywords |
| `plugins/impl/reels_warmup.py` | `reels_warmup` | Warmup bez AI |
| `plugins/catalog.py` | `PLUGINS` | Schema UI + defaults |
| `plugins/store.py` | `resolve`, `read_queue`, `write_niche`, `niche_is_accepted` | Merge + kolejki + nisza |
| `plugins/registry.py` | `load_all` | Rejestr klas z kodem |

> **Pułapka:** statusy w `catalog.py` były niezsynchronizowane z `registry` — naprawione.  
> Źródło prawdy uruchomienia: `plugins.registry.available()` — **8 pluginów** (7 queueable + `niche_discover`). Wszystkie mają teraz `status: ready`.

### Core

| Plik | Symbol | Rola |
|------|--------|------|
| `core/device.py` | `UiAuto2Device` | Adapter uiautodev + ADB |
| `core/navigator.py` | `Navigator.ensure` | Recovery L0–L5 do ekranu |
| `core/screen_map.py` | `ScreenMap` | Fingerprinty resource-id |
| `core/humanizer.py` | `Humanizer` | Pauzy ludzkie + speed + event `humanizer.pause` (bez `watch_reel`/`watch_story` — te są w pluginach) |
| `core/human_touch.py` | `HumanTouch` | Humanizowane gesty |
| `core/events.py` | `EventLogger` | Append-only events.jsonl |
| `core/limits.py` | `check`, `note_action` | Limity daily/hourly |
| `core/chance.py` | `roll` | Losowość akcji |
| `core/scrcpy_client.py` | `ScrcpyClient` | Stream H.264 |
| `core/frame_buffer.py` | `FrameBuffer` | Najświeższa klatka |
| `core/verification_code.py` | `request_code` / `wait_for_code` | IPC 2FA panel↔bot |
| `ai/vision.py` | `AiVision.run` | Anthropic lub STUB |

### Config / data / panel / scheduler

| Plik | Symbol | Rola |
|------|--------|------|
| `config/schema.py` | `AccountConfig`, `load_account` | Walidacja + merge |
| `config/credentials.py` | `get`/`set` | Hasła IG |
| `config/secrets.py` | — | Klucz Anthropic (env > plik) |
| `data/stores.py` | `*Store` | JSONL profili |
| `data/stats.py` | `full_report` | Agregaty |
| `panel/server.py` | `SessionManager`, `app` | Dashboard |
| `scheduler/estimator.py` | `estimate_queue` | Górne ograniczenie czasu scenariusza |
| `scheduler/device_planner.py` | `build_day_plan`, `move_block` | Plan dnia dla urządzenia |
| `scheduler/state.py` | `is_enabled` | Master switch START/STOP |
| `scheduler/tick.py` | `tick_once` | Egzekucja bloków |

---

## 6. Mapa „gdzie szukać X”

| Potrzeba | Gdzie |
|----------|-------|
| Komendy CLI | `cli.py` (`cmd_*`) |
| API panelu | `panel/server.py` |
| Logika biznesowa | `plugins/impl/*.py`, `plugins/blocks.py` |
| Atomowe UI | `steps/**` |
| Telefon / ADB | `core/device.py` |
| Rozpoznawanie ekranu | `core/screen_map.py` + `calibration/*_draft.json` |
| Recovery nawigacji | `core/navigator.py` |
| AI | `ai/vision.py` + `ai/prompts/` |
| Limity | `core/limits.py` + `account.limits` |
| Kolejki | `config/accounts/<a>.queues/*.json` |
| Credentials | `config/credentials.json` (gitignore) |
| API key | env `ANTHROPIC_API_KEY` lub `config/secrets.json` |
| Eventy / stats | `data/accounts/<id>/events.jsonl`, `data/stats.py` |
| Miękki stop | plik `data/accounts/<id>/STOP` |
| 2FA IPC | `CODE_REQUEST` / `CODE_RESPONSE` |
| Harmonogram | `scheduler/*` + `data/devices/<alias>/schedule/YYYY-MM-DD.json` |
| Master switch | `data/scheduler.json` (`cli scheduler-switch --on/--off`) |

---

## 7. Modele danych (bez Pydantic)

| Typ | Plik | Kluczowe pola |
|-----|------|---------------|
| `AccountConfig` | `config/schema.py` | device, humanizer, limits, plugins, niche, ai, recovery, toggles |
| `Context` | `runner/context.py` | device, ht, navigator, screen_map, humanizer, events, account, scratch, fb |
| `StepResult` | `runner/step.py` | status, payload, error, duration_ms |
| `Budget` | `plugins/base.py` | max_items, max_duration_s, stop_path |
| Queue JSON | `*.queues/*.json` | `{queue:[{id,plugin,enabled,overrides,budget}], between_tasks_s, on_task_error}` |
| Event | `events.jsonl` | `{ts, session_id, account_id, kind, payload}` |
| Plan urządzenia | `data/devices/<alias>/schedule/YYYY-MM-DD.json` | `{version:2, date, device, blocks:[{id, account, scenario, start, duration_s, est, queue_override, status, locked}], dropped}` |

**Merge ustawień pluginu:** `catalog.defaults` ← `account.plugins` ← `task.overrides`.

---

## 8. Integracje zewnętrzne

| Serwis | Klient | Auth | Uwagi |
|--------|--------|------|-------|
| uiautodev | `core/device.py` | brak (localhost) | hierarchy, tap, shell, screenshot |
| scrcpy | `core/scrcpy_client.py` | ADB | port ~27183; fallback screenshot |
| Anthropic | `ai/vision.py` | env / secrets.json | bez klucza → **cichy STUB** |
| Panel | `panel/server.py` | **brak** | pełna kontrola lokalna |
| Telegram | — | — | **tylko placeholder w configu** |
| Proxy | config `network.*` | — | **nieużywane w kodzie** |
| Instagram API | — | — | **brak** — tylko UI aplikacji |

---

## 9. Jak uruchomić

```text
cd c:\Users\mz\Desktop\prog\bot\bot-czerwiec\bot
```

Wymagania: Python + deps, `adb`, telefon USB, mapa `calibration\SM_A505FN_draft.json`, opcjonalnie klucz Anthropic.

```bash
python -m v2.cli panel                                          # dashboard :8765
python -m v2.cli run-queue --account=technature699
python -m v2.cli run-queue --account=technature699 --dry-run
python -m v2.cli run-queue --account=technature699 --only=<id> --max-items=3 --speed=4
python -m v2.cli login --account=technature699
python -m v2.cli where --account=technature699
python -m v2.cli recover --account=technature699 --target=home_feed
python -m v2.cli start --account=technature699 --flow=smoke_stats
python -m v2.cli run-queue --account=technature699 --max-duration-s=1800   # twarda rama okna
python -m v2.cli scheduler                                      # bez UI (nie razem z panelem)
python -m v2.cli scheduler-switch --on                          # master switch z terminala
python -m unittest discover -s v2/tests -v                      # bez telefonu
```

Stop: utwórz `data\accounts\<alias>\STOP`.

---

## 10. Must-read (kolejność dla nowego agenta)

1. `ARCHITEKTURA.md` — kontrakty, drabinka recovery, lekcje z live  
2. `cli.py` — Context, komendy  
3. `config/schema.py` + `config/_template/account.json`  
4. `core/device.py`  
5. `core/navigator.py`  
6. `core/screen_map.py` + `calibration/SM_A505FN_draft.json`  
7. `plugins/catalog.py` + `plugins/store.py`  
8. `plugins/base.py` + `plugins/runner.py`  
9. `plugins/impl/reels_niche.py` + [`REELS_NICHE_WORKFLOW.md`](REELS_NICHE_WORKFLOW.md)  
10. `steps/reels/watch.py` + `plugins/reel_log.py`  
11. [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md) (shared caption tap z engagement)  
12. `ai/vision.py` + `config/secrets.py`  
13. `data/stores.py` + `core/limits.py` + `data/stats.py`  
14. `panel/server.py`  
15. `scheduler/estimator.py` + `scheduler/device_planner.py` + `scheduler/tick.py`  
16. `BACKLOG.md` — co świadomie nie robić „przy okazji”

---

## 11. Pułapki / non-obvious (OBOWIĄZKOWO znać)

1. **Bez klucza Anthropic bot „działa” na STUB** — wygląda OK, decyzje są heurystyczne.
2. **Catalog status zsynchronizowany** — wszystkie 8 pluginów `ready` zgodnie z registry.
3. **Telegram w docs ≠ w kodzie** — BLOCKING tylko loguje event.
4. **L6 AI w ARCHITEKTURA nie istnieje w `navigator.py`** — po L5 jest exhausted.
5. ~~Lajki kafelków = no-op~~ — **naprawione**: open kafelek → `post.like` → soft BACK (bez `ensure` poza profil).
6. **Domyślne źródło = `raw_profiles`** (+ `mark_outcome`/`interacted_at`). `target_candidates` nadal bez pipeline promocji — nie głodzi interact przy raw.
7. **Checkpoint zapisuje, ale NIE wznawia** — po STOP/crashu kolejka od zera.
8. **Jedna sesja naraz** — plan pilnuje kolizji; zajęte urządzenie → `missed`.
9. **Nie uruchamiać jednocześnie `panel` i `cli scheduler`** — dwa orkiestratory.
9a. **Włączony master switch blokuje ręczne uruchomienia** (HTTP 423).
9b. **Estymata bez `max_duration_min` w zadaniu jest założeniem** — panel oznacza ⚠.
10. **Prolog JobRunnera bez logowania** — po wylogowaniu najpierw `login`.
11. **Zaległy `STOP` zabija nowy start** — CLI/panel zwykle kasują przy starcie.
12. **`humanizer.speed` nie skraca waitów UI** — tylko pauzy ludzkie. Wszystkie ludzkie pauzy są centralnie logowane do `events.jsonl` jako `humanizer.pause`. Czas oglądania treści (`watch_reel`, `watch_story`) to parametry biznesowe pluginów, nie pauzy humanizera.
13. **Overlay bez `try/finally` = klik w sheet** — klasyczny bug.
14. **Nowy ekran bez bottom nav → `BACK_TO_EXIT_SCREENS`**, inaczej oscylacja.
15. **Mapa UI pod model** — brak `calibration/{model}_draft.json` = exit.
16. **Proxy/network w JSON nic nie robi.**
17. **`reels_niche` używa klatek, nie `watch_reel`** — klatki: `ai.use_frames` + `frames` + `frame_interval_s`; `watch_reel` jest w ustawieniach pluginu (dla trybu czasu oglądania). Przed swipe `before_next_reel` 0.3–0.6 s.
18. **`content_search` NIGDY `ensure(reels_feed)` / clips** — rolka z # nie jest main feedem; `stepcall` + `_dismiss_sheet_stack` tylko BACK. Po lost sheet → skip harvest_commenters. `finally` **musi** `pop("baseline_screen")` (wyciek psuje `profile.exit` w kolejce).
19. **Hot open z sheetu: tap w nick (`nick_tap_xy`), nie awatar** — ring → story. Debounce `reels_feed` 3×; `opened_then_lost` ≠ „nie otwarto”; restore sheet + retry.
20. **`target_scraper`:** search tylko na starcie targetu; likers/comments: expand → trust `full_screen` z `post.open_*`; followers/following **bez** expand; BACK na retry tylko gdy sheet wszedł (`no_entry`/`nothing_opened` = 0× BACK); commenters `exclude` autora; `skip_pinned` = no-op.
21. **`#` w search:** zawsze w cudzysłowach (`input text "…"`) — inaczej shell zjada hashtag.
22. **Story / like tiles: soft return (BACK), nie `ensure(profile_other)`** — ensure wspina się poza profil (likers→search).
23. **Między profilami w `profile_interact`: reuse search** (clear + nick) — bez powrotu na `home_feed`.
24. **Sanity/gate fail też → `visited_profiles`** (+ raw `mark_outcome`) — inaczej nick wraca w kółko.
25. **Followers parse:** `tys.` / `mln` / `K`/`M` w `_header.parse_count` (PL+EN).
26. **`gate_params`:** jeden helper w `plugins/base.py` (`reels_niche` + `content_search` → `reels.watch`); `max_likes=0` = brak limitu.
27. **Sekrety plaintext** na dysku — nie commitować, nie wklejać do czatu.

---

## 12. Code review — findings (severity ↓)

### CRITICAL

1. **Plaintext sekrety** (`config/credentials.json`, `config/secrets.json`) — hasła IG + klucz API; chmod na Windows często no-op.  
2. **Panel bez auth** — pełna kontrola (credentials, secrets, start/stop, 2FA); `--host 0.0.0.0` = przejęcie w LAN.  
3. **Shell injection przez `input text`** — `escape_input_text` escape’uje za mało; hasło z panelu może egzekwować shell na urządzeniu (`steps/system/_ui_helpers.py`).

### HIGH

4. **Checkpoint bez resume** — `queue_state.json` tylko zapis (`plugins/runner.py`).  
5. ~~Slot oznaczany po `Popen`, nie po sukcesie~~ — blok ma teraz statusy `running`/`done`/`missed`/`failed`.  
6. ~~Okno 3 min + device_busy = utracone sesje~~ — plan zapobiega kolizjom, a przegapiony blok jest raportowany.  
7. **Brak auto-loginu w prologu kolejki.**  
8. **Path traversal** w nazwach account/queue (brak whitelist + resolve).  
9. **Zero testów panelu / SessionManager.**

### MEDIUM

10. Race w `SessionManager.stop` (late-binding `self.proc`).  
11. Dual orchestrator panel+scheduler.  
12. `where`/`recover` podczas aktywnej sesji bez locka.  
13. Niesatomowe RMW credentials/secrets/config.  
14. ~~`RawProfilesStore` dedup~~ — **zrobione** (`add_rows` + `dedup_days`; schemat `ts/source/username/has_ring/from_target/content_kind`).  
15. `CONFIRM_HINTS` z `"ok"` jako substring.  
16. Uchwyty logów sesji nie zamykane.  
17. Martwe pole `ig_password_encrypted` w template.  
18. `posts.skip_pinned` w `target_scraper` = **no-op**; `cursor_persist` w catalogu bez użycia w `impl/`; martwy UI content_search (AI/like_comments/skip_authors) **ukryty**.
19. Odroczone (ROI review): assert viewer przed engagement; mniej force-dumpów w `type_query`; wspólny `back_until`; cache nicków w `RawProfilesStore`.

### LOW

19. Szerokie `except` połykające błędy.  
20. PIN unlock nieobsługiwany.  
21. Brak testów escape/path/dual-scheduler.

### Ocena jakości

- **Rdzeń bota (kroki, pluginy, navigator):** solidny, dobre kontrakty i komentarze „dlaczego”.  
- **Ops/security envelope (panel, sekrety, scheduler semantics):** słaby — tu priorytet napraw.

### Priorytet napraw (z review)

1. Rotacja sekretów → 2. Auth panelu → 3. Hardening `input text` →  
4. Realne resume checkpointu → 5. Semantyka `mark_slot_executed` / missed slots →  
6. Login w prologu → 7. Testy panelu.

### Pokrycie testami

**Jest:** screen_map, navigator, limits, reels, AI stub, humanizer, flow_runner, plugins, planner/tick, login/verification, credentials/secrets, stores, events.  
**Brak:** panel/SessionManager, resume checkpoint, injection/path safety, dual orchestrator, prologue+login.

---

## 13. TODO w kodzie (świadome)

| Plik | Treść |
|------|-------|
| ~~`steps/profile/visit.py` like tiles~~ | **Zrobione** |
| `steps/system/unlock.py` | PIN nieobsługiwany |
| `target_scraper` / catalog | `skip_pinned` no-op; catalog status **zsynchronizowany**; martwe settings scrapera (ROI odroczone) |
| `plugins/blocks.py` | `cursor_persist` w UI — brak użycia w `impl/` |
| `BACKLOG.md` | Watchdog, SQLite, multi-device, Bézier, Supabase, L6 AI, security/IP, popupy |
| `ARCHITEKTURA.md` | Alert Telegram, retention frames/events (plik poza v2 / historyczny) |
| `plugins/catalog.py` | Statusy **zsynchronizowane** z registry; martwy AI/like_comments content_search = **ukryty** |

---

## 14. Konwencje projektu

- Import pakietu: `v2.*`; `cli.py` dokłada parent do `sys.path`
- Kroki: `domena.akcja` + `@register` → `StepResult`
- Pluginy: `id` snake_case w `impl/` + wpis w `catalog.py`
- Helpers: `_ui_helpers.py`, `_grid.py`, `_sheet.py`
- Dane: append-only JSONL; interfejs stores pod przyszły Supabase
- IPC: pliki (`STOP`, `CODE_*`), nie sockety
- Screen ID: tylko `resource-id`
- Overlay cleanup: `try/finally` + powrót do bazowego ekranu
- Komentarze w kodzie: bogate docstringi „czemu”

---

## 15. Szybkie komendy diagnostyczne

```bash
python -m v2.cli where --account=<alias>     # co widzi ScreenMap
python -m v2.cli recover --account=<alias> --target=home_feed
python -m v2.cli stats --account=<alias> --days=1
python -m v2.cli run-queue --account=<alias> --dry-run
```

Logi: `data/accounts/<id>/sessions/*` + `events.jsonl`.  
SSE panelu: `GET /api/events/stream`.

---

*Koniec mapy. Przy zmianach architektury zaktualizuj ten plik oraz `ARCHITEKTURA.md`.*
