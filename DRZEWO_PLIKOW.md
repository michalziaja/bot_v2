# Drzewo plików — mapa projektu v2

## 1. Wstęp

Ten plik to **mapa katalogów i kluczowych plików** Instagrama-bota v2: gdzie leży logika, żeby kolejny agent szukał ścieżką, a nie grepem.

Opisy skrócone z docstringów / nagłówków modułów — nie inventarze stanu kont.

Powiązane dokumenty:

- [`STAN_SYSTEMU.md`](STAN_SYSTEMU.md) — aktualny stan systemu
- [`RZECZY_DO_POPRAWY.md`](RZECZY_DO_POPRAWY.md) — niespójności i dług techniczny
- [`KOLEJNE_KROKI.md`](KOLEJNE_KROKI.md) — kolejność prac
- [`CLI_TESTOWANIE.md`](CLI_TESTOWANIE.md) — jak testować przez CLI
- [`REELS_NICHE_WORKFLOW.md`](REELS_NICHE_WORKFLOW.md) — pętla `reels_niche` (klatki, opis tap, AI, pauzy)
- [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md) — discovery niszy + shared caption

Kontrakty warstw: [`ARCHITEKTURA.md`](ARCHITEKTURA.md). Onboarding: [`AGENT_ONBOARDING.md`](AGENT_ONBOARDING.md).

**Ścieżka produktowa:** kolejka pluginów (`run-queue` / `JobRunner`).  
**Legacy:** YAML w `flows/` + `FlowRunner` + `cli start` (patrz §5).

---

## 2. Drzewo katalogów (z opisami)

```
v2/
  cli.py              # jedno wejście CLI (start/run-queue/panel/scheduler/…)
  config/             # konta, urządzenia, sekrety, credentials, walidacja
  config_bin/         # binaria pomocnicze (scrcpy-server.jar)
  core/               # telefon, UI, humanizer, eventy, limity, scrcpy
  plugins/            # katalog ustawień, kolejka, JobRunner, impl/*
  steps/              # atomowe kroki UI (registry @register)
  panel/              # dashboard FastAPI + HTML
  scheduler/          # plan urządzenia, estymaty, tick egzekucji
  data/               # stores/stats + runtime per konto + jar scrcpy
  ai/                 # Vision API + prompts/*.yml
  runner/             # Context, Step, FlowRunner (YAML)
  flows/              # YAML — LEGACY / smoke / regresja
  calibration/        # mapy ekranu, recognizer, mini/full flow kalibracji
  humanizer/          # profile pauz YAML (profiles/*.yaml)
  tests/              # unittest (offline / samples)
```

---

## 3. Kluczowe pliki per katalog

### `cli.py` (root)

- **cli.py** — CLI: buduje `Context`, rejestruje kroki; komendy m.in. `start` (YAML/`FlowRunner`), `run-queue` (`JobRunner`), `login`, `stats`, `known`, `where`, `scheduler`, `scheduler-switch`, `panel`, `recover`.

### `config/`

- **schema.py** — ładuje `accounts/<alias>.json` + `devices/<alias>.json`, łączy i waliduje; brak pola = odmowa sesji.
- **credentials.py** — login/hasło IG poza configami kont (osobny plik, poza gitem).
- **secrets.py** — sekrety (klucz API) poza configami kont.
- **accounts/<alias>.json** — config konta (plugins, limits, schedule, …); kolejki: `accounts/<alias>.queues/<nazwa>.json`.
- **devices/<alias>.json** — sprzęt (adb, uiautodev, model, klony).
- **_template/account.json** — szablon nowego konta.

### `config_bin/`

- **scrcpy-server.jar** — serwer scrcpy pushowany na telefon (`ScrcpyClient`).

### `core/`

- **device.py** — `UiAuto2Device`: jedyny adapter REST uiautodev + adb.
- **uiauto_server.py** — autostart/graceful shutdown subprocessu `uiautodev`.
- **scrcpy_client.py** — start scrcpy-server, forward, zwraca `FrameBuffer`.
- **frame_buffer.py** — dekodowanie klatek (h264 / mjpeg / adb screencap).
- **screen_map.py** — mapa UI + rozpoznawanie ekranów (presence resource-id, `wins_over`).
- **navigator.py** — drabinka recovery L0–L6 przy złym ekranie / overlay / obcej appce.
- **human_touch.py** — tapy/swipe z jitterem i pauzami humanizera (nad device).
- **humanizer.py** — profile pauz z `humanizer/profiles/<name>.yaml`.
- **events.py** — append-only `events.jsonl` (ts, session, account, kind, payload).
- **limits.py** — limity dzienne/godzinowe z eventów.
- **chance.py** — `roll()` dla losowych decyzji (like/follow/…).
- **dwell.py** — czas zatrzymania zależny od długości treści do przeczytania.
- **composite.py** — siatka N klatek do AI (CPU/dysk, bez telefonu).
- **verification_code.py** — kanał panel↔bot: `CODE_REQUEST` / `CODE_RESPONSE` (2FA email/SMS).

### `plugins/`

- **\_\_init\_\_.py** — warstwa jobs: katalog, schematy, kolejka.
- **catalog.py** — źródło prawdy ustawień dla panelu (`GET /api/plugins`).
- **blocks.py** — wspólne bloki ustawień (`harvest`, `hot_interact`, …) — raz, wiele pluginów.
- **base.py** — kontrakt: `Budget`, `PluginResult`, protokół `Plugin` (+ ścieżka STOP);
  wspólne `gate_params` / `draw_range` dla `reels_niche` i `content_search`.
- **store.py** — odczyt/zapis `plugins` w configu konta oraz kolejek `.queues/`.
- **registry.py** — nazwa pluginu → klasa (jak steps).
- **runner.py** — `JobRunner`: prolog → pluginy → epilog, budżet, checkpoint, limity, `max_queue_duration_s`.
- **stepcall.py** — wywołanie kroku z pluginu (to, co FlowRunner robi wokół kroku).
- **reel_log.py** / **scraper_log.py** / **profile_log.py** / **content_search_log.py** — ludzkie logi stdout (prefix = id pluginu).

#### `plugins/impl/`

- **feed_browser.py** — home + tacka stories; ciepło konta, bez like/follow.
- **reels_warmup.py** — oglądanie rolek bez AI/harvest (rozgrzewka klonów).
- **reels_niche.py** — główny pozysk: pętla `reels.*`, budżet i decyzje z katalogu
  (klatki ∥ gate → composite ‖ opis tap; AI: `use_frames`/`read_caption`; logi `reel_log`).
- **niche_discover.py** — one-shot discovery niszy (panel); posty composite, rolki 1:1, opis tap-only
  (ta sama guarded ścieżka co engagement).
- **content_search.py** — search hashtag/keyword/location (`source` w ustawieniach);
  `#` w `search.type_query` w cudzysłowach (shell); `finally` pop `baseline_screen`;
  gate rolek: wspólne `gate_params` z `plugins/base.py` (AI / like_comments / skip_authors ukryte w catalogu).
- **target_scraper.py** — zbieranie profili (zero interakcji): search raz → posty ordered
  (likers→commenters: expand+confirm `full_screen`, trust payload `open_*`, warunkowy BACK)
  → zakładka Rolki → followers/following **bez** expand → HOME;
  commenters: `exclude` autora; zapis `raw_profiles.jsonl` + dedup; logi `scraper_log`.
- **mass_stories.py** — oglądanie relacji followersów targetów (kolejność/budżet w pluginie).
- **profile_interact.py** — domyślnie `raw_profiles` (+ podgląd w panelu); też `run_one()` dla hot.

### `steps/`

- **registry.py** — nazwa kroku → klasa (`@register`).
- **system/** — wake/unlock/ensure_ig/ensure_screen/login/kod 2FA/browse_feed/stats/close/sleep.
- **feed/** — scroll home, otwarcie relacji z tacki.
- **reels/** — open/watch (klatki∥gate → composite‖caption)/swipe/like/AI/harvest sheetów/profil z sheetu (+ `_guards`, `_sheet`, `_reel_meta`).
  `_sheet.expand_sheet_confirmed`: jedna geo na fazę (before/after); drag_handle → `full_screen` (`sheet_top`);
  `COMMENT_USERNAME_IDS` **bez** `row_feed_photo_profile_name` (autor nad wątkiem); `collect_rows` — orphan TextView.
- **search/** — open, type_query (`#` w cudzysłowach), pick_result.
- **post/** — open z siatki (ordered / `content_types`), like, listy zaangażowanych (`open_likers`/`open_comments` z expand+confirm + payload `full_screen`).
- **profile/** — visit, grid/header, followers, pick_from_store (`raw_profiles` | `target_candidates`), kolejka wizyt, exit.
- **lists/** — harvest username’ów (+ `exclude`, dedup/`from_target`/`content_kind`), scroll aż nowe wiersze.
- **stories/** — ring z listy, watch segments, next user, close (+ `_viewer`).

### `panel/`

- **server.py** — FastAPI + HTML: konta/urządzenia, kolejki, timeline, estymaty, start/stop sesji, SSE eventów, kod weryfikacyjny, `GET /api/raw-profiles/{acc}`. Start: `python -m v2.cli panel` → `:8765`.
- **static/index.html** — dashboard (formularze z katalogu; podgląd raw pod „Źródło” w `profile_interact`).

### `scheduler/`

- **state.py** — globalnie: master switch, `active_hours`, `cycle_days` → zwykle `data/scheduler.json`.
- **device_planner.py** — cykl dni na urządzenie + materializacja daty; slot = odcinek z czasem trwania.
- **planner.py** — wspólne prymitywy (historia: stary plan per-konto bez duration).
- **estimator.py** — górne ograniczenie czasu scenariusza (układanie bez kolizji).
- **action_estimator.py** — szacunek potencjalnych like/follow/comment (panel, nie gwarancja).
- **tick.py** — pętla: materializuje dzień i egzekwuje gotowy plan (bez losowania godzin).

### `data/`

- **stores.py** — JSONL stores (`RawProfilesStore` itd.); interfejs pod przyszły adapter.
  Schemat raw: `ts/source/username/has_ring/from_target/content_kind`; dedup przy zapisie;
  przykład: `data/accounts/_example/raw_profiles.example.jsonl`.
- **stats.py** — agregaty z `events.jsonl` / profilowych JSONL (bez dodatkowego zapisu).
- **accounts/<id>/** — runtime sesji (events, stores, frames, STOP, CODE_*, …).
- **scrcpy-server.jar** — kopia/jar używany przez klienta (obok `config_bin/`).

### `ai/`

- **vision.py** — Vision: reel_niche / profile_grid / niche_discover_* (+ stuby); usage tokenów; REAL (Anthropic) lub STUB.
- **prompts/*.yml** — prompty (`reel_niche`, `profile_grid`, `niche_discover_posts|reels|account|merge`).

### `runner/`

- **context.py** — obiekt sesji wstrzykiwany do kroków.
- **step.py** — protokół kroku + `StepResult`.
- **flow_runner.py** — **LEGACY:** ładuje flow YAML, iteruje kroki, loguje wyniki.

### `flows/` — **LEGACY**

YAML do smoke/regresji (`smoke_stats`, `warmup_reels`, `reels_niche_harvest`, `interact_from_target_list`). Nie jest głównym UX produktu.

### `calibration/`

- **recognize.py** — lekki recognizer + popup + szkielet recovery (fingerprints / resource-id).
- **mini_flow.py** / **test_full_flow.py** — żywe mini/pełne przebiegi kalibracyjne.
- **\*_draft.json** (mapa modelu) — używana przez `ScreenMap` przy starcie CLI.

### `humanizer/`

- **profiles/*.yaml** — zakresy pauz per typ akcji (ładowane przez `core/humanizer.py`).

### `tests/`

Unittesty warstw: plugins, scheduler, steps, core, config, AI — zwykle offline, bez telefonu.

---

## 4. Mapa „szukasz X → idź do Y”

| Szukasz | Idź do |
|--------|--------|
| Ustawienia UI pluginów / formularze panelu | `plugins/catalog.py`, `plugins/blocks.py` → panel czyta katalog |
| Egzekucja biznesowa (kolejność, budżet) | `plugins/impl/*` + `plugins/runner.py` (`JobRunner`) |
| Atomowy tap / dump UI | `steps/**` + `steps/registry.py` |
| Kolejka zadań / scenariusze | `plugins/store.py`, `config/accounts/<alias>.queues/` |
| Start kolejki z CLI | `cli.py` → `run-queue` |
| Urządzenie / adb / klon | `config/devices/`, `config/schema.py`, `core/device.py` |
| Runtime sesji (eventy, stores, STOP, kod) | `data/accounts/<id>/`, `data/stores.py`, `data/stats.py` |
| AI / vision / prompty | `ai/vision.py`, `ai/prompts/` |
| Harmonogram / timeline / kolizje | `scheduler/` (`device_planner`, `tick`, `state`, estymatory) |
| Dashboard HTTP | `panel/server.py`, `panel/static/` |
| Limity / losowość / pauzy | `core/limits.py`, `core/chance.py`, `core/humanizer.py`, `humanizer/profiles/` |
| Recovery ekranu | `core/navigator.py`, `core/screen_map.py`, `cli recover` |
| Login / 2FA kod | `steps/system/login_if_needed.py`, `enter_verification_code_if_needed.py`, `core/verification_code.py`, `config/credentials.py` |
| YAML flow (debug) | `flows/`, `runner/flow_runner.py`, `cli start` — **legacy** |

---

## 5. Legacy (nie mylić z produkcją)

Oznaczone świadomie jako ścieżka nieproduktowa:

| Element | Uwaga |
|--------|--------|
| **`flows/`** | YAML smoke/regresja — nie główny UX |
| **`runner/flow_runner.py` (`FlowRunner`)** | Executor YAML; silnik decyzyjny siedzi w pluginach |
| **`cli start --account=… --flow=…`** | Start YAML; produkcja = **`run-queue`** + `JobRunner` |
| Panel `POST /api/start` z `flow` | Odpowiednik legacy startu flow w subprocess |

**Produkcja:** `python -m v2.cli run-queue --account=…` (opcjonalnie `--queue=…`) albo panel → kolejka / harmonogram.

---

## 6. Triada: account JSON → device JSON → queues → data/ runtime

```
config/accounts/<alias>.json     # kto: plugins, limits, schedule.enabled (udział w planie), …
        │
        ├── schema.py ładuje + łączy ──► config/devices/<device_alias>.json
        │                                 # jak: adb_id, uiautodev, model, app_clone_package, …
        │
        ├── plugins (ustawienia) w tym samym account JSON
        │
        └── kolejki osobno:
              config/accounts/<alias>.queues/<nazwa>.json
              (default = scenariusz „default”)

Uruchomienie (JobRunner / scheduler tick):
        │
        ▼
data/accounts/<account_id>/      # runtime: events.jsonl, stores JSONL,
                                 # frames, STOP, CODE_REQUEST/RESPONSE, …
```

Sekundy poza triadą (instalacja): `config/secrets.json`, `config/credentials.json`, `data/scheduler.json` (master switch / active_hours / cycle).

---

*Mapa oparta o docstringi kluczowych modułów; bez listy stanu zalogowanych kont.*
