---
name: ""
overview: ""
todos: []
isProject: false
---

# Bot v2 — plan realizacji dashboardu `/app`

Kontrakt: `/home/michal/Downloads/bot-v2-dashboard-spec.md`.
Stan kodu (historia): [panel_v2_weryfikacja.md](panel_v2_weryfikacja.md).
Nie zaczynać trzeciego UI. Klasyczny `GET /` (`panel/static/index.html`) zostaje zapasem — nie rozbudowywać, nie przenosić tam nowych zakładek.
Nie commituj z tego pliku. Nie implementuj, dopóki operator nie każe kodować.

Panel: `cd /home/michal && python -m v2.cli panel` → `http://127.0.0.1:8765/app`.
Język UI: polski. Id pluginów w kodzie zostają angielskie.

---

## 0. Zasady (ze speca)

1. Telefon jest jednostką pracy. Konto jest ładunkiem na telefonie (klon App Cloner). Harmonogram układa bloki na urządzeniu. Runy są na koncie.
2. Dziś panel kłamie: jeden `Popen` w `SessionManager` (`panel/server.py`), `GET /api/status` globalne, scheduler `is_busy=lambda: SESSION.status()["running"]` (~linia 2661). **Bez rozcięcia sesji per serial UI floty nie ma sensu.** To krok 1, nie „później”.
3. Nie ruszać runtime pluginów (katalog, kroki, humanizer), poza tym co konieczne do równoległych sesji, stopu per telefon oraz merge global/override.
4. Nie klonować repo, nie dodawać bazy. Persistence: JSON/JSONL.
5. Nie implementować rzeczy ze SCHEMA, których runtime nie ma: PIN, Telegram, proxy na telefonie, `daily_dms`, `comment_gen`, cost-cap abort. W Ustawieniach: wyłączone + „w schemacie, nie w runtime”.
6. Visual: tokeny z `panel/static/app.css`. Lewy rail + canvas, jak na mockach JPG. Nie szkło, nie fiolet, nie 3D.
7. Mock jest poglądem. Funkcje klasyka niewidoczne na mocku **nie znikają** — idą do właściwej zakładki `/app`. Świadoma strata tylko gdy ten plan to nazywa.
8. Gdy mock vs spec vs klasyk się gryzą: **wybór = spec**, funkcja klasyka zostaje przeniesiona. Konflikty wypisane w §2.

### Sposób pracy (obowiązkowy)

Po **każdym** z 11 kroków: **stop kodowania**. Wrócić do tego pliku i:

1. Oznaczyć ukończony krok: co weszło (pliki, API, testy), co świadomie wypadło.
2. **Rozpisać następny krok dokładniej** niż jest teraz — konkretne funkcje, endpointy, stany UI, kryteria, ryzyko — zanim padnie pierwsza linia kodu tego kroku.
3. Nie skakać do N+2. UI floty przed krokiem 1 jest zakazane.

---

## 1. Decyzje operatora (2026-09-02)

Potwierdzone w czacie + kwestionariuszu. Nie zgadywać ponownie.

**Harmonogram (mock, wcześniej brakowało pliku):**
`/home/michal/.cursor/projects/home-michal-bot-v2/assets/harmonogram-195385d4-1b3e-4dd4-a8f1-72f3929ebc91.jpg`

- Wiersze = telefony (`phone_A` online / `galaxy` dim offline), nie jeden `<select>`.
- Kolumny = dni kalendarzowe tygodnia (Pn–Nd z datami, np. 31.08–06.09), oś godzin 08:00–20:00.
- Playhead „teraz” (pionowa linia).
- Toggle **scheduler per telefon** w toolbarze (`phone_A ON` / `galaxy OFF`).
- Bloki: kolor **konta** (legenda na dole), etykieta `konto · runa`, kłódka na locked.
- Inspector prawo: wybrana sesja, run, czas, duration, **Zablokowana**, limity konta, Przesuń / Zamień run / Usuń.
- Przycisk **Ułóż tydzień**. Selector „Ten tydzień”.
- Stare PNG (`mockup-harmonogram.png`) = pojedynczy telefon — **nie** źródło. JPG JobSaver `harmonogram-6fe06d0b-*.jpg` = obcy produkt.

**Nav / override (wcześniej):** pełny sidebar z mocka. **Konta** = per konto (CRUD, login, nisza, pauzy, limity). **Ustawienia** = globalne (humanizer, alerty, limity, kroki systemowe; proxy w przyszłości). Global like 100/dzień; konto może nadpisać na 50.

**Kwestionariusz (ponowiony):**

1. **Pulpit** — tylko sesja i wejścia (Start/Stop/Live/Plan). Hydrate, kalibracja, ADB raw **tylko na Flocie**.
2. **Runy / Pluginy** — defaultów konta (`POST /api/plugins`) **nie przenosimy**. Źródło prawdy = override w kroku runu + zapisane akcje. Defaulty katalogu wystarczą. Tab Pluginy zostaje w klasyku.
3. **Lejek** — hint: interact jedzie z `raw_profiles`; auto-godziny filtra **w panelu Lejka**; **nie** przełączamy `profile_interact` na candidates.
4. **Na żywo** — tylko gdy sesja jedzie. Klatka z FrameBuffera bota (JPEG na dysk), nie uiautodev. Bez sesji: „brak sesji”, bez screenshotu. **Zwijany Terminal (filtr regex)**. „Gdzie jestem” zostaje w klasyku.
5. **Statystyki** — native KPI w `/app` jak mock; link „pełne /stats”; **`/share` zostaje readonly**; Graph/Treści nie ruszamy.
6. **Konta** — mock + pauza konta + limity jako override globalnych + Discovery + 2FA na karcie. Kroki systemowe (wake/unlock) i JSON surowy — **Ustawienia globalne / klasyk**, nie karta.
7. **Flota** — jak mock: rejestracja, ADB, klony, hydrate, kalibracja jeśli jest plik, lock/flock. Diagnostyka ekranu **zostaje w klasyku**.
8. **Ustawienia** — nowy `config/bot.json` (limity, humanizer, kroki) + override w Koncie. Auth = wiersz „wkrótce”, **bez fake locka**.

Nadal ze speca (nie pytaliśmy, nie sprzeczne):

- Kolor bloku = konto. Biblioteka Runy = 7 queueable. Bateria na live — brak API, pominąć. „Odzyskaj” FAIL — poza zakresem. Kalibracja nie blokuje Floty. Chrome nie jest jedynym schedulerem. Udział konta w osi nie chować w klasyku. Istniejące `account.json` = już override. Filtr HTTP jeden job. Klasyczny `/` świadomie dalej globalnie `running`.

---

## 2. Konflikty mock vs spec vs klasyk

Wybór zawsze: spec + nie zgubić funkcji klasyka.

- **Nav.** Mock i spec się zgadzają (9 pozycji, Ustawienia na dole raila). Klasyk ma 7 tabów + link `/stats`. Mapowanie w §3. Stary plan IA (Treści, Konto, System) — nieważny.
- **Harmonogram.** Źródło layoutu = JPG `harmonogram-195385d4-*.jpg` (wiersze telefonów × tydzień z datami × godziny + inspector). Spec §4.2 to samo, plus cykl 1/3/7 z configu. `/app` dziś = jeden select + karty dni. Klasyk = jeden telefon, pion. JPG JobSaver `harmonogram-6fe06d0b` ignorować.
- **Runy.** Spec + `run-0ad61d9b-*.jpg` = lista run | kolejka | inspector | biblioteka 7 pluginów + zapisane akcje. `run2-*.jpg` ma zmyślone pluginy i modal „Nowa zapisana akcja” zamiast inspectora — **ignorować nazwy pluginów z run2**; modal zapisu akcji można zostawić jako wariant UX przy „Zapisz jako akcję”.
- **Pulpit chrome.** Mock ma globalne „Scheduler ON”. Spec: scheduler nie jest jednym toggle na całą flotę w canvasie. Chrome = stan + dzwonek + zegar; sterowanie = Harmonogram/Flota/Ustawienia.
- **Konta inspector.** Mock pokazuje humanizer speed i limity dziś; spec: Sesja / Limity / Nisza / Urządzenie. Humanizer pełny (pauzy) jest w klasyku — global w Ustawieniach, override w Konta (accordion).
- **Lejek sen.** Mock: zakres 4–9 s. Klasyk: `sleep_between_s` + `sleep_request_s`. Spec: „sen”. Zostawić dwa pola klasyka, nie wymyślać range w runtime.
- **Na żywo log.** Operator: log ludzki (spec). Dodatkowo **zwijany Terminal z filtrem regex** (decyzja). Dump JSONL nie jest home. „Gdzie jestem” zostaje w klasyku.
- **Preview Na żywo.** Klatki tylko przy jadącej sesji, ze **tego samego FrameBuffera** co Vision (bot zapisuje JPEG). Bez sesji nie ma podglądu. Nie drugi scrcpy, nie uiautodev na żywo. Szczegóły §4.1.
- **profile_interact źródło.** Spec: hint w Lejku, nie przełączać na candidates.

---

## 3. Mapowanie klasyk → `/app`

Każda sekcja `panel/static/index.html`. Funkcja idzie do zakładki, nie zostaje tylko pod `/`.

### Terminal (`data-tab="events"`)

- Log zdarzeń, filtr regex, wyczyść, autoscroll, etykieta konta.
- Sidebar: Uruchom kolejkę / Stop, badge IDLE/RUNNING.

Dokąd:

- Log ludzki + filtr tekstu + autoscroll → **Na żywo**.
- Start/Stop per telefon → **Pulpit** i **Na żywo** (po kroku 1).
- Regex + zwijany Terminal → **Na żywo** (decyzja). Surowy dump jako home — nie.

### Pluginy (`data-tab="plugins"`)

- Master/detail defaultów konta (`GET/POST /api/plugins/{acc}`).
- Testuj (3) dla queueable.
- `niche_discover` wyłączony z listy (uruchamiany z Ustawień konta).

Dokąd:

- Defaulty konta (`POST /api/plugins`) → **zostają w klasyku** (decyzja: nie przenosimy). Runy = override kroku + zapisane akcje.
- Testuj (3) → **Runy** (opcjonalnie przy bibliotece). Discovery → **Konta**.

### Planowanie → Scenariusze

- Lista run, `default` zarezerwowany, dodaj plugin wielokrotnie, `between_tasks_s`, `on_task_error`, dry-run, uruchom kolejkę.

Dokąd: **Runy** (model święty, layout z mocka `run-0ad61d9b`). Dry-run (`GET /api/queue/{acc}/resolved` + estimate) — przycisk w Runy, nie na mocku, nie gubić.

### Planowanie → Oś czasu

- Master switch, godziny, cykl 1/3/7, sesje/dzień, oś, drag, edytor, kłódka, dodaj slot, ułóż, wyczyść, autospread, paski limitów.

Dokąd: **Harmonogram**. Godziny i cykl mają też wejście w **Ustawieniach** (jedno źródło: `GET/POST /api/scheduler`). Wyczyść oś — akcja niebezpieczna w Harmonogramie (nie na mocku).

### Filtr profili (`data-tab="filter"`)

- Auto ON, godziny, limit, sen, proxy, followers, min postów, publiczne, arabskie/hindi, Odpal / Stop / log / status / preview candidates.

Dokąd: **Lejek**. Pending nie jest kolumną. Auto-godziny: panel filtra (decyzja).

### Ustawienia konta (`data-tab="settings"`, SCHEMA w `index.html` ~1714)

- Nisza + discovery + tag_variants + sync fraz → **Konta**.
- Kroki systemowe (wake/unlock/stats_snapshot/close_ig/sleep_screen) → **Ustawienia** (global) + override w **Konta**.
- Limity dzienne/godzinowe + cooldown → **Ustawienia** + override w **Konta**. `daily_dms` disabled.
- Humanizer (profil, speed, pauzy, long_pause) → **Ustawienia** + override w **Konta**.
- AI Vision modele i progi → **Konta** accordion. `comment_gen`, `recovery_identify`, cost-cap — disabled.
- Recovery / Telegram → **Ustawienia**, wiersze disabled.
- Sieć / proxy telefonu → **Ustawienia**, disabled.
- Urządzenie i klon (alias, device_alias, app_clone_*, spoof, PIN) → **Konta**. PIN disabled. Spoof — accordion, nie karta.
- Usuń konto → **Konta**.

### Konta i urządzenia (`data-tab="accounts"`)

- Rejestr urządzeń, ADB live, hydrate → **Flota**.
- Siatka kont na urządzeniu + udział w planie → **Konta** (odczyt) + **Harmonogram** (sesje/dzień, zapis).
- Nowe konto / login / hasło / 2FA / usuń → **Konta**.

### Bot (`data-tab="bot"`)

- Gdzie jestem + nawigacja ekranów → **klasyk** (decyzja Flota bez diagnostyki).
- Klucz Anthropic + workspace → **Ustawienia**.
- Lista znanych kroków (`GET /api/known_steps`) → **Ustawienia**, read-only, schowane.
- JSON surowy → klasyk (decyzja).

### Link Statystyki

- `/stats` → **Statystyki** w `/app` + link „pełne /stats”. Share bez zmian w tym tasku.

### Świadomie gubimy w `/app` (zostaje klasyk)

- Defaulty pluginów konta (tab Pluginy).
- Surowy edytor JSON konta.
- „Gdzie jestem” / skoki Home/Reels.
- Ewentualnie kalibracja, jeśli nie ma realnego UI poza draftem.
- Równoległy filtr HTTP na dwa konta naraz.
- Auth panelu (wiersz „wkrótce”).

Nie gubimy: 2FA na karcie, discovery, auto-godzin filtra, sesji/dzień, dry-run, Testuj (3) w Runach, Terminal regex na Na żywo, `/share` readonly.

---

## 4. Kolejność — 11 kroków (spec §5)

To są fazy sekwencyjne, nie równoległe PR-y. UI floty / pulpitu / live **przed** krokiem 1 będzie kłamać — nie robić.

1. SessionManager per device + status/stop/SSE + scheduler `is_busy(device)` + `scheduler_enabled` per device.
2. Shell: lewy rail, hashe speca, redirect `#konto`→`#konta`, `#system`→`#flota`.
3. Pulpit: Start/Stop per karta, KPI; miniaturka JPEG tylko gdy ten telefon jedzie (z FB).
4. Na żywo: focus device + Stop tego serialu + miniatury; klatka z FB gdy jedzie, inaczej „brak sesji”.
5. Harmonogram: wiersze = telefony, inspector, scheduler per device.
6. Runy: layout z mocka na istniejącym kreatorze (nie przepisywać queue/actions).
7. Lejek: nowy widok + `GET /api/visited-profiles/{acc}`.
8. Konta: karty + inspector + 2FA + override limitów/niszy.
9. Flota: karty urządzeń + register/hydrate/ADB + (opc.) diagnostyka.
10. Statystyki: KPI w shellu, iframe opcjonalny.
11. Ustawienia: secrets, `config/bot.json` UI, adnotacje, auth „wkrótce”.

Krok 11 zawiera **widoczny** edytor globali. **Merge w runtime** (`core/limits.py`, humanizer, `toggles.steps`) musi istnieć zanim Konta (krok 8) pokażą override — inaczej UI kłamie. Dlatego **szkielet `config/bot.json` + merge wczytywania** wchodzi w krok 1 albo jako twardy prefiks kroku 8 (nie później niż 8). Pełny ekran Ustawień zostaje na 11.

### 4.1 Na żywo — klatka tylko gdy bot jedzie (FrameBuffer)

**Po polsku.** Bot (CLI) i panel (przeglądarka) to dwa programy. Scrcpy leci **w bocie**. Klatki są w pamięci bota (`FrameBuffer`). Panel nie może „sięgnąć palcem” do tej pamięci. Nie odpalamy drugiego scrcpy — to zabija tego pierwszego.

**Tak: podczas sesji używamy FB.** Bot i tak dekoduje strumień dla Vision. Co ~0,5–1 s bierze `get_frame()`, robi mały JPEG (ok. 480 px) i zapisuje np. `data/devices/<alias>/preview.jpg` (atomowo: write tmp + rename). Panel `GET /api/devices/{alias}/preview` czyta **ten plik**, jeśli świeży. Zero dodatkowego screenshotu na telefonie.

**Bez sesji nie ma klatek — i nie trzeba.** Na żywo / miniatura na Pulpicie: „brak sesji” / pusty ekran. Nie wołamy uiautodev ani `screencap` „dla ciekawości”. Offline ADB też pusty ekran.

Dziś `panel/preview.py` robi uiautodev/screencap niezależnie od sesji — **to zmieniamy** (zapis JPEG w CLI przy scrcpy; panel czyta plik). Fallback uiautodev tylko gdy FB nie wystartował (rzadki codec=adb) — nie jako podgląd bez sesji.

Nie: drugi scrcpy z panelu. Nie: 15 fps w przeglądarce (wystarczy 1 fps z pliku).

---

## 5. Krok 1 — backend floty (warunek wstępny)

### 5.1 Stan dziś

- `panel/server.py`: `class SessionManager` + `SESSION = SessionManager()` — jeden `self.proc`.
- `status()` → `{running, account, flow, pid, since, stopping, deadline, stdout, returncode}`.
- Start: `POST /api/start`, `POST /api/run-queue`, `POST /api/run-plugin`, `POST /api/login` — wszystkie wołają `SESSION.start` i padają 409 gdy *jakikolwiek* proc żyje.
- Stop: `POST /api/stop` — bez body, gasi ten proc.
- SSE: `GET /api/events/stream?account=` — tail `events.jsonl` + **globalnego** `SESSION.stdout_path`.
- Scheduler: `tick_once(..., is_busy: Callable[[], bool])` w `scheduler/tick.py`. Panel wstrzykuje `lambda: SESSION.status()["running"]`.
- `_deny_if_scheduler_enabled()` — globalny 423 gdy `data/scheduler.json` → `enabled`.
- `_deny_if_session_running()` — diagnostyka 409 gdy cokolwiek jedzie.
- Lock procesu bota i tak jest per serial: `data/locks/device_<serial>.lock`.

### 5.2 Docelowy SessionManager

Klucz slotu: `device_alias` (to samo co `account.device.device_alias`). Serial jest w `config/devices/<alias>.json` → `adb_id`; lock zostaje per serial.

Mapa: `device_alias → slot` (Popen + meta jak dziś: account, flow, started_at, stdout/stderr, deadline, watchdog).

Zachowania:

- `start(account, ...)`: resolve `device_alias` z configu konta. 409 tylko gdy **ten** slot jedzie albo brak ADB (serial nie w `adb devices` state=device). Inny telefon jedzie → OK.
- `stop(device_alias=)` albo `stop(account=)`: STOP file + terminate **tylko tego** slotu. Stop bez body: slot „aktywny w UI Na żywo” (ostatni focus), nie cała flota.
- Watchdog (`WATCHDOG_GRACE_S`) per slot — nie wolno zabić sąsiada (dzisiejszy komentarz przy `_hard_kill_later` już o tym mówi; przy mapie to musi być twardy invariant).
- `force_stop` przy zejściu panelu: wszystkie sloty.
- Login / run-plugin / run-queue / start / scheduler `_start` — ten sam manager.

### 5.3 Kontrakt HTTP

`GET /api/status`:

```
{
  "sessions": [
    {
      "device_alias": "phone_A",
      "account": "jobsaver",
      "running": true,
      "phase": "kolejka [default]",
      "queue": "default",
      "started_at": 169...,
      "eta": null,
      "stopping": false,
      "pid": 1234
    }
  ],
  "running_any": true,
  "running": true
}
```

Pole `running` = `running_any` dla klasyka. **`app.js` nie używa `running` do disable Start na innych telefonach.** Start na karcie: disable gdy OFFLINE, gdy **ten** device ma sesję, gdy scheduler ON **na tym** device (423).

`POST /api/start` i `POST /api/run-queue`: payload jak dziś `{account, queue_name, ...}`. 409 tylko to samo urządzenie / brak ADB. 423 tylko gdy bezpiecznik globalny **i** `scheduler_enabled` tego device.

`POST /api/stop`: body opcjonalne `{device_alias}` albo `{account}`. Bez body = focus live.

`GET /api/events/stream?device=` albo `?account=`: tail events + stdout **tego** slotu. Dziś parametr `account` jest wymagany — zachować, dodać `device`.

Preview: `GET /api/devices/{alias}/preview` czyta JPEG z FB (plik sesji). 204 gdy brak sesji / stary plik. Nie uiautodev idle.

Opcjonalnie `GET /api/alerts`: 2FA waiting + ADB offline vs zarejestrowane + sesja fail. Albo złożyć w froncie z istniejących (`/api/verification-code/{acc}`, `/api/adb-devices`, status).

### 5.4 Scheduler per device

`scheduler/tick.py`:

- Zmiana sygnatury: `is_busy: Callable[[str], bool]` (argument = `device` z pętli `for device in dp.list_devices`).
- `if is_busy(device):` → `device_busy` tylko dla tego telefonu.
- `run_forever` ta sama zmiana.
- Panel: `is_busy=lambda device: SESSION.is_running_on(device)`.

`config/devices/<alias>.json` nowe pole `scheduler_enabled` (default `true` gdy brak klucza).

Tick: jeśli global `enabled` jest false → jak dziś, zero startów. Jeśli global true, a device `scheduler_enabled` false → pomiń to device (nie `device_busy`, tylko skip / wynik `scheduler_off`). Ręczny Start na tym telefonie dozwolony.

Gdy global ON i device ON: ręczny Start **tego** telefonu → 423. Inne telefony wolne.

`GET /api/devices` powinien zwracać `scheduler_enabled` (i lock — §9).

Testy istniejące w `tests/test_scheduler.py` wołają `is_busy` jako `lambda: False` — **zaktualizować** do `lambda device: False` / `lambda device: True`. Dodać test: dwa device, busy na A, due na B → B startuje.

### 5.5 Prefiks config globalnego (żeby krok 8 nie kłamał)

Nowy plik `config/bot.json` (nazwa ze speca / decyzji usera; nie `data/` — to config, nie stan).

Zawartość v1:

- `limits` — ten sam kształt co w `config/_template/account.json` (`daily_likes`, `daily_follows`, `daily_comments`, `daily_dms`, `per_hour_caps`, `cooldown_after_warning_hours`)
- `humanizer` — `profile_name`, `speed`, `after_each_step`, `overrides`
- `toggles.steps` — wake / unlock / stats_snapshot / close_ig / sleep_screen

Merge przy `config/schema.py` → `load_account`:

- `effective = deep_merge(bot.section, account.section)`
- klucz obecny w koncie **zastępuje** global (w tym `0` = brak limitu, jak dziś)
- brak klucza w koncie = wartość z bota
- `AccountConfig.limits` / `.humanizer` / `.toggles` = effective (runtime nie pyta osobno o plik)

`core/limits.py` `check()` zostaje na `ctx.account.limits` — po merge to już effective. Nie dublować odczytu w limiterze.

Humanizer: `Humanizer.load` dostaje już zmergowany `account.humanizer`.

`step_enabled` / `step_option` — to samo.

Nowy helper: `load_bot(config_root)` + `GET/POST /api/bot-config` (POST w kroku 11; GET może być wcześniej). Zapis konta (`POST /api/config/{acc}`) zapisuje **tylko override** albo pełną sekcję — decyzja implementacyjna: prościej zapisać pełną sekcję konta jak dziś (istniejące konta = override). UI Konta: checkbox „własne” vs „jak globalne”; „jak globalne” usuwa klucz z JSON konta.

Szablon `config/_template/account.json`: limity/humanizer/steps mogą zostać jako kopia (nowe konto i tak ma własne wartości) **albo** zostać wyczyszczone do `{}` żeby dziedziczyć. Preferowane: `{}` / brak sekcji = dziedziczenie. Migracja istniejących plików: **nie ruszać** (są override’ami).

Nie w `bot.json` w tym tasku: proxy telefonu, PIN, Telegram, plugin defaults, nisza, AI (zostają per konto).

### 5.6 Kryterium kroku 1 (bez UI floty)

- Dwa zarejestrowane telefony: `POST /api/run-queue` na `kasia` (galaxy) podczas jadącego `jobsaver` (phone_A) → 200, drugi Popen. Test jednostkowy z dwoma fake Popen / zmockowanym startem.
- `POST /api/stop {device_alias: phone_A}` nie rusza procesu galaxy.
- `tick_once` z `is_busy` true tylko dla phone_A: due blok na galaxy → `started`.
- Klasyczny `/` nadal wstaje. `status.running` nadal bool.
- Testy `tests/test_scheduler.py` zielone po zmianie sygnatury.

Pliki: `panel/server.py`, `scheduler/tick.py`, `config/schema.py`, `config/bot.json` (nowy), ewent. `core/limits.py` tylko gdy merge nie siedzi w `load_account`. Testy: nowy `tests/test_session_manager.py` (albo rozszerzenie panel/server testów), `tests/test_scheduler.py`, `tests/test_limits.py` (effective z bota + override), `tests/test_config.py`.

Nie ruszać: `plugins/impl/*`, kroki, katalog poza odczytem.

---

## 6. Krok 2 — shell

### Cel

Lewy rail jak na wszystkich mockach JPG. Hash routing zostaje.

### Layout vs mock

- Rail ~200 px: brand `bot v2` / `flota IG`; linki; na dole `Ustawienia`.
- Canvas: tytuł + kontekst (konto/telefon) + dzwonek + zegar.
- Link „Klasyczny panel” → `/` schowany (stopka raila albo Ustawienia). Nie w głównej liście.
- Tokeny bez zmian (`app.css` `:root`).

Hashe (zastępują `VIEWS` w `app.js`):

- `#pulpit` Pulpit
- `#harmonogram` Harmonogram
- `#runy` Runy
- `#lejek` Lejek (nowy section)
- `#na-zywo` Na żywo (alias `#nazywo`, `#sesja` zostaje)
- `#statystyki` Statystyki
- `#konta` Konta
- `#flota` Flota
- `#ustawienia` Ustawienia

Redirect: `#konto` → `#konta`, `#system` → `#flota` (w `VIEW_ALIASES` + `history.replaceState`).

Dzwonek (nie zakładka): 2FA (`GET /api/verification-code/{acc}` dla kont), ADB offline (zarejestrowane vs `/api/adb-devices`), sesja fail / stopping. Klik → Konta (2FA) / Flota (ADB) / Na żywo (sesja).

### API

Istniejące. Alerts złożone w JS albo opcjonalny `GET /api/alerts` z kroku 1.

### Z klasyka niewidoczne na mocku

Link do klasyka. Nic więcej w tym kroku.

### Kryterium

Rail na każdej zakładce. Stare hashe nie otwierają placeholderów „Wkrótce”. Placeholdery `#lejek` / `#konta` / `#flota` / `#ustawienia` mogą być puste do swoich kroków, ale hashe żyją.

Pliki: `panel/static/app.html`, `app.js` (`VIEWS`, `VIEW_ALIASES`, `applyView`), `app.css` (`.top` → `.rail` + `.canvas`).

---

## 7. Krok 3 — Pulpit

Mock: `image-6210200f-8cbe-4bf4-9840-d48fe207702f.jpg`.

### Cel

Flota na jeden rzut oka. Start/Stop **tego** telefonu. Wejście w live / plan.

### Layout vs mock

- KPI: Online `n/m`, Jedzie `n`, Dziś sesje zrobione/planowane, Lejek pending (suma lub konto aktywne), Alerty.
- Siatka kart = `GET /api/devices` (nie hardcodować `phone_A` / `galaxy`).
- Karta: alias, model, `ONLINE` / `OFFLINE` / `JEDZIE`, JPEG preview, chipy kont (`account.device_alias === alias`), konto w sesji wyróżnione, plugin/faza/%/ETA albo następny blok z timeline, kropki klonów, Start (wybór konta + runy), Stop, Na żywo, Plan (hash + preselect device).
- OFFLINE: Start disabled, banner `ADB offline · {serial}`. CTA Podłącz/Hydrate **nie na Pulpicie** (decyzja: Flota). Link „Flota” wystarczy.
- Pod spodem opcjonalnie pozioma oś **dzisiejsza** dla telefonu, który jedzie (albo wybranego) — z `GET /api/timeline/{alias}`.

Start **nie** disable bo inny telefon jedzie.

### API

Istniejące: `/api/devices`, `/api/accounts`, `/api/status` (nowe), `/api/timeline/{dev}`, preview, `/api/scheduler`, `POST /api/run-queue`, `POST /api/stop`.
Hydrate: `POST /api/devices/{alias}/hydrate`.
Pending lejka: `GET /api/profile-filter/{acc}/status` → `pending` (zsumować).

Nowych endpointów nie trzeba, o ile krok 1 oddał listę sesji.

### Z klasyka niewidoczne na mocku

Wybór konkretnej runy przy Starcie (nie tylko `default`) — select na karcie, jak dziś w `/app`. Flow `POST /api/start {flow}` zostaje w API, nie eksponować w UI (kolejka jest źródłem).

### Kryterium

Dwa telefony na pulpicie. Sesja na A nie gasi Start na B (gdy B online i jego scheduler off albo nie blokuje). Preview jak dziś. Klasyczny `/` działa.

Pliki: `app.html` (KPI markup), `app.js` (`refreshPulpit`, `sessionOnDevice`, `startQueue` — czytać `status.sessions`).

---

## 8. Krok 4 — Na żywo

Mock: `nazywo-9620c4ad-3d77-4d9f-8b90-48c0cf21a488.jpg`.

### Cel

Operator patrzy na **jeden** telefon. Stop gasi tylko ten serial. Reszta floty jako miniatury.

### Layout vs mock

- Duży JPEG z FB sesji (to samo API co pulpit). Caption: klatka z bota ~1 fps, nie drugi scrcpy.
- Meta: device, konto, runa, klatka/faza, %, ETA, Stop.
- Środek: postęp kroków runy (z `/api/stats/{acc}` + kolejka) — mock ma listę pluginów z JEDZIE; zbliżyć, nie wymyślać nowego silnika postępu.
- Prawo: log ludzki, autoscroll, filtr tekstu.
- Zwijany **Terminal** z filtrem regex (decyzja; nie home).
- Miniatury innych telefonów: thumbnail + ONLINE/OFFLINE/JEDZIE. Klik = focus (preview + SSE).

Dziś: `live-idle` gdy nic nie jedzie. Zostaje: bez sesji na tym telefonie — „brak sesji”, Stop disabled, bez screenshotu.

### API

Istniejące: preview, stats, stop.
Nowe z kroku 1: SSE `?device=`, status.sessions.
Hash: `#na-zywo` + query/state `device=` (np. `location.hash = #na-zywo` + `sessionStorage` / `?device=` — nie psuć hash-routera; preferować `#na-zywo` + zmienna JS ustawiana z Pulpitu).

### Z klasyka niewidoczne na mocku

Wyczyść log. Terminal regex zwijany. „Gdzie jestem” — klasyk.

### Kryterium

Live na phone_A + Stop nie gasi galaxy. Idle galaxy pokazuje klatkę albo „brak obrazu”, nie umiera cały widok. Miniatura przełącza SSE.

Pliki: `app.html` (`view-na-zywo`), `app.js` (`refreshLive`, `connectLiveStream`).

---

## 9. Krok 5 — Harmonogram

**Źródło layoutu:** `harmonogram-195385d4-1b3e-4dd4-a8f1-72f3929ebc91.jpg`. Spec §4.2. Nie stare PNG jednego telefonu, nie JPG JobSaver.

### Cel

Układanie tygodnia floty: wiersz = telefon, kolumna = dzień **tego tygodnia** (daty). Blok = konto × run × czas. Scheduler per telefon. Harmonogram **nie składa pluginów** — wiesza gotową runę (`queue` name).

### Dziś

Jeden select, karty dni cyklu, Ułóż tydzień, pigułki. Backend move/queue/block/regenerate/autospread **jest**. API cyklu to wzorzec 1/3/7 dni, nie kalendarz dat — UI i tak pokazuje **tydzień z datami** (mock); mapowanie bloku cyklu → dzień kalendarza jak klasyk / `today_index`.

### Docelowo (1:1 z mocka)

- Toolbar: tytuł + „Okno 08:00–20:00 • cykl N” (godziny z `active_hours`, nie hardcode jeśli config inny).
- Selector tygodnia (`Ten tydzień 31.08–06.09`).
- **Ułóż tydzień** (regen wybranego / per wiersz). Locked przeżywają regen.
- Toggles `phone_A scheduler ON` / `galaxy scheduler OFF` → `scheduler_enabled` na device.
- Siatka: kolumny Pn–Nd z datą; wiersze godzin 08…20; **osobny pas wierszy na każdy telefon** (nagłówek `phone_A (online)` / `galaxy (offline)` dim).
- Playhead pionowy „teraz” na dzisiejszej kolumnie.
- Bloki: kolor z legendy konta, tekst `jobsaver · default`, zakres godzin, ikona kłódki gdy `locked`.
- Legenda kolorów kont na dole.
- Klik bloku → inspector **Wybrana sesja**: konto, Run (select), dzień/godziny, duration, toggle **Zablokowana**, limity konta (used / cap / pozostało / reset — `estimate-actions` + `used_today`), Przesuń, Zamień run, Usuń.
- Drag na osi: wzorzec klasyka (snap 5 min, 409→force). `POST …/move`.
- Dodaj blok: `POST …/block` (od razu locked).
- Sesje/dzień + udział kont: `GET/POST /api/schedule-accounts/{dev}` — nie na mocku, nie gubić.
- Wyczyść oś: `DELETE /api/timeline/{dev}` z potwierdzeniem.
- Limity: max vs `daily_*`, `used+max(plan)` żółto. Do limitu liczymy **max** widełek.

Kłódka: osobnego `POST …/lock` nie ma. Toggle „Przypnij”: dodać cienki endpoint **albo** konwencję (move w miejscu / flaga w payloadzie queue). Nie blokować siatki na lock-toggle — pole już jest, inspector może pokazać kłódkę readonly w v1 siatki, toggle w v1.1 tego samego kroku.

### API

Istniejące — użyć, nie pisać drugiego plannera.
Nowe: zapis `scheduler_enabled` (PATCH device albo `POST /api/devices/{alias}` z subsetem). `GET /api/devices` rozszerzony.

### Z klasyka niewidoczne na mocku

Ramy godzin/cykl (też Ustawienia), sesje/dzień, wyczyść, autospread, confirm regen.

### Kryterium

Widać phone_A i galaxy na jednej siatce. Due blok na wolnym jedzie, gdy drugi pracuje (zależy od kroku 1). Inspector zmienia runę przez `…/queue`. Klasyczna oś nietknięta.

Pliki: `app.js` (`refreshHarmonogram`, `renderWeek` → siatka floty), `app.css` (siatka + playhead), ewent. `panel/server.py` (device patch). Nie ruszać `scheduler/device_planner.py` poza busy (krok 1).

---

## 10. Krok 6 — Runy

Mock: `run-0ad61d9b-*.jpg` (layout). `run2-*.jpg` tylko jako wariant zapisu akcji, **bez** zmyślonych pluginów.

### Cel

Playlista pluginów na konto. **Przestylizować, nie zmieniać modelu.**

### Model (święty)

- Runa = `config/accounts/<alias>.queues/<name>.json`: `queue[]` (`id`, `plugin`, `note`, `budget`, `overrides`, `enabled`), `between_tasks_s`, `on_task_error`.
- `default` zarezerwowany.
- Zapisana akcja ≠ plugin ≠ runa. Preset `{name, plugin, overrides}` w `.actions/`. Wstawienie **kopiuje** override. Edycja kroku nie nadpisuje presetu, dopóki „Zapisz jako akcję” / update.
- Dwa razy ten sam plugin = dwa `id`.

Biblioteka (tylko queueable):

- `feed_browser` — warmup — Przeglądaj feed
- `reels_warmup` — warmup — Reels warmup
- `reels_niche` — engagement — Reels nisza
- `content_search` — discovery — Szukaj treści
- `profile_interact` — engagement — Interakcja profili
- `mass_stories` — engagement — Relacje masowe
- `target_scraper` — discovery — Scraper

`niche_discover` nie w pickerze. Zero DM / comment / publish.

### Layout vs mock

- Lewa: lista run (kroki, ETA z `POST /api/estimate-actions`), `+ Nowa runa`, kłódka przy `default`.
- Środek: kolejka, drag, enabled, drop-zone, stopka `between_tasks_s` + `on_task_error`.
- Prawa: inspector = pola z `GET /api/plugins` (basic vs advanced accordion). Zapisz jako akcję, Usuń krok.
- Dół: biblioteka chipów + zapisane akcje.
- Góra: select konta, `~2h 28m`, Zapisz / Usuń.

Dziś kreator jest 3-kolumnowy, ale biblioteka jest z boku, nie na dole, a lista run to `<select>`. Przestylizować; logika `refreshRuny` zostaje.

### API

Już spięte: queue-presets, queue GET/POST/DELETE, actions CRUD, plugins, estimate-actions.

### Z klasyka niewidoczne na mocku

Dry-run / resolved. Testuj (3). Pigułki limitów max/limit (zostawić — warunek układania).

### Kryterium

`default` nieusuwalny. Zapisana akcja to kopia. 7 pluginów. `jobsaver` queues (`hashtag`, `interakcja`, `kluczowe`, `tok`, `rolki`, `default`) widać jako lista. Testy `tests/test_saved_actions.py` nadal zielone.

Pliki: `app.html`, `app.js` (layout), `app.css`. Nie pisać od zera.

---

## 11. Krok 7 — Lejek

Mock: `lejek-bae9205d-8241-4cf0-96ca-b4c9e4228ad6.jpg`. Nowy widok.

### Cel

Pipeline wzrostu na koncie. W klasyku jest tylko „Filtr profili”.

### Model

```
surowy → (filtr HTTP) → kandydaci
              ↑
       pending = kolejka filtra (licznik, nie kolumna)
wizyty: visited_profiles (osobna kolumna)
```

Kolumny:

- **Surowy** — `GET /api/raw-profiles/{acc}` ← `raw_profiles.jsonl` (unique, source, followers).
- **Kandydaci** — `GET /api/target-candidates/{acc}` ← `target_candidates.jsonl`.
- **Ostatnio obejrzani** — **nowe** `GET /api/visited-profiles/{acc}` ← `VisitedProfilesStore` / `visited_profiles.jsonl`. Sort newest first. Pola: `username`, `ts`, `action`, `reason`, `tiles_liked`, `followed`, `header`. Filtry: Wszystkie / Z interakcją / Bez.

Interakcja (zielone): `followed` albo `tiles_liked>0` albo `action` w `{liked, followed, like_only}` — zmapować z faktu zapisu w `steps/profile/visit.py` (`action=skip|like_only|…`, `followed`, `tiles_liked`, `reason=gate:…`).
Bez interakcji: `private`, `gate_failed` / `reason` gate, `match_failed`, `skipped`/`skip`, `open_failed`.
Sama wizyta nie jest interakcją.

Pending: `pending_usernames()` — surowy minus decided (`ok`/`remove`). `skip` wraca na ogon. Licznik przy filtrze + KPI. `GET /api/profile-filter/{acc}/status` już ma `pending`, `raw_unique`, `candidates`, `decided`.

Hint (nie ukrywać): „Interact je z raw; kandydaci to sitko HTTP”. Nie przełączać `profile_interact` w tym tasku.

### Layout vs mock

- Select konta, Odpal filtr, status IDLE/RUNNING, `pending filtra: N`.
- KPI: Surowy, Kandydaci, Obejrzani dziś, Interakcja `n/m`.
- 3 kolumny + prawa karta filtra: ON/OFF, proxy losowo/webshare/rayobyte, followers min/max, min postów, tylko publiczne, odrzut bio, limit/cykl, sen (pola klasyka), auto-godziny `times[]`, Odpal / Stop, log (`/log` albo `status.job.terminal_tail`), last run checked/saved/removed.

### API

Istniejące: raw-profiles, target-candidates, profile-filter GET/POST/status/log/run/stop.
Nowe: `GET /api/visited-profiles/{acc}?limit=` (1–500). Źródło: `data/stores.py` `VisitedProfilesStore.read_all()`, newest first, bez mutacji.

### Z klasyka niewidoczne na mocku

Auto-godziny. Preview candidates pod statusem — w kolumnie Kandydaci. Terminal pełny/ogon — w karcie filtra.

### Kryterium

Trzy kolumny; pending tylko licznik; outcome rozróżnia like/follow vs bez. Filtr HTTP bez ADB. Test: visited JSONL → endpoint zwraca posortowane + pola action/followed.

Pliki: `panel/server.py` (endpoint), `app.html` / `app.js` (nowy widok), `data/stores.py` tylko jeśli brakuje `preview`/limit (dziś jest `read_all`). Test: `tests/test_profile_stores.py` albo nowy `tests/test_visited_profiles_api.py`.

---

## 12. Krok 8 — Konta

Mock: `konta-0fbcd223-dcd1-4b49-8432-a37c66f6c114.jpg`. Zastępuje placeholder `#konto`.

### Cel

Zarządzanie kontami: dodawanie, usuwanie, logowanie, ustawienia per konto (nisza, pauzy, limity jako override). Karty, nie SCHEMA dump.

### Layout vs mock

- Siatka: alias, status logowania (`GET /api/accounts/{acc}/credentials` — bez hasła), telefon + clone package, nisza (keywords chipy), limity dziś (stats) albo cap, następny blok, „wyłączone w osi”.
- Filtry: Wszystkie / per device.
- `+ Nowe konto`: modal alias + IG login + `device_alias` + `app_clone_package` → `POST /api/accounts`. Hasło przez credentials, nie w logach.
- Przyciski karty: Edytuj, Login (`POST /api/login`), Discovery (`POST /api/run-plugin` `niche_discover`).
- OFFLINE / 2FA: input + `POST /api/verification-code/{acc}` **na karcie**.
- Inspector: Sesja / Limity (override globalnych, checkbox „własne”) / Nisza + Discovery / Urządzenie. Pauza konta. Accordion: humanizer override. **Nie** na karcie: kroki systemowe (Ustawienia globalne), JSON surowy (klasyk), defaulty pluginów (klasyk).
- Usuń: `DELETE /api/accounts/{acc}` z potwierdzeniem (trash `data/accounts` jak dziś).

Nie na karcie: Recovery, proxy telefonu, PIN.

### API

Istniejące: accounts CRUD, config GET/POST, credentials, login, verification-code, clone, plugins GET/POST, run-plugin, schedule-accounts, timeline, stats (used_today).
Nowe: `GET /api/bot-config` do etykiet „jak globalne: 100”.

### Z klasyka niewidoczne na mocku

Cały SCHEMA poza tym, co na mocku — w inspectorze / accordionie. tag_variants mapa. Sync fraz discovery. Usuń APPID z konta.

### Kryterium

Cztery konta (jobsaver, a50_clone2, technature699, kasia) z API, nie z mocka. 2FA na karcie bez klasyka. Override like 50 na jednym koncie nie zmienia drugiego; `limits.check` widzi 50. `#konto` redirect.

Pliki: `app.html` / `app.js` (duży nowy widok; można wzorować pola na SCHEMA, nie kopiować 6k linii). `POST /api/config` już waliduje.

---

## 13. Krok 9 — Flota

Mock: `flota-76fa98c1-9dcd-4f07-ac95-59111b0f837e.jpg`. Zastępuje placeholder `#system`.

### Cel

Rejestr urządzeń, ADB, klony, lock. Copy: jeden proces na serial (flock). Panel po kroku 1 jedzie dwa telefony.

### Layout vs mock

- Karty: alias, model, Android, rozdzielczość, serial, scrcpy port (info; preview i tak nie używa scrcpy), ONLINE = adb ∩ `adb_id`, lock / `claimed_by`, clone slots + które konto, Hydrate, Podgląd → Na żywo, Kalibracja jeśli jest plik (nie blokuje kroku).
- OFFLINE: „ADB nie widzi urządzenia”, Podłącz = hydrate/odśwież ADB.
- Panel: `+ Zarejestruj` alias + `adb_id` → `POST /api/devices`. Surowa lista `GET /api/adb-devices`.
- Toggle scheduler per karta (to samo pole co Harmonogram).

### API

Istniejące: devices, adb-devices, hydrate, preview, POST devices.
Dodać do `GET /api/devices`: `scheduler_enabled`, scrcpy.port, lock/claimed (odczyt `data/locks/device_<serial>.lock` jeśli jeszcze nie ma w payloadzie — dziś `claimed_by` jest na klonach, nie na flocku sesji).

Diagnostyka ekranu **nie** wchodzi na Flotę (decyzja) — zostaje w klasyku. `_deny_if_session_running` i tak per device po kroku 1 (dla hydrate / innych POST).

### Z klasyka niewidoczne na mocku

Formularz manufacturer/model opcjonalny (hydrate i tak zczyta). „Gdzie jestem” / Recover — klasyk.

### Kryterium

Karty phone_A i galaxy z API. Register + hydrate. `#system` redirect. Diagnostyka nie odpala się na cudzym serialu.

Pliki: `app.html` / `app.js`, `panel/server.py` (rozszerzenie list_devices, deny per device).

---

## 14. Krok 10 — Statystyki

Mock: `stats-990de30c-6e17-414d-acca-f1792ad4c13e.jpg`.

### Cel

KPI konta w shellu `/app`. Nie iframe-ować `/stats` na zawsze.

### Layout vs mock

- Select konta; segment `Ostatnia sesja | 24h | Zakres`. Auto-refresh ~4 s gdy ostatnia sesja.
- Karty: followers (+delta), sesje, polubienia, follow, harvest, AI cost/tokeny — z istniejących API.
- Limity dziś vs cap (effective po merge).
- Czas wg pluginu — paski, bez nowej biblioteki chartów.
- Lista ostatnich sesji OK/FAIL. Bez „Odzyskaj”.
- Pasek floty: które telefony jechały w zakresie.

Link „pełne /stats”. Iframe nie jest głównym widokiem. **`/share/schedule` i `/share/stats` zostają** readonly, bez dopieszczania w tym tasku poza spójnością osi gdy Harmonogram będzie siatką.

### API

`GET /api/stats/{acc}`, `…/sessions`, `…/sessions/{sid}`, `…/aggregate?range=24h`, `…/aggregate?from=&to=`. `stats-view.js` wchłonąć albo zostawić jako moduł KPI.

### Z klasyka / `/stats` niewidoczne na mocku

Pace, pełne wykresy sesji — link. Share — nie ruszać w tym kroku.

### Kryterium

Zakres i 24h bez iframe jako główny widok. Flota na dole. Klasyczny `/stats` żyje.

Pliki: `app.html`, `stats-view.js`, ewent. `app.js`.

---

## 15. Krok 11 — Ustawienia

Brak osobnego mocka — spec §4.9 + decyzja usera (global vs override).

### Cel

Rzeczy globalne. Nie ustawienia jednego konta.

### Layout

Sekcje:

- Anthropic key + workspace: `GET|POST /api/secrets` (nie echo raw key).
- Godziny okna + cykl 1/3/7: `GET|POST /api/scheduler` (`active_hours`, `cycle_days`) — to samo źródło co Harmonogram.
- Globalny bezpiecznik schedulera (`enabled`).
- **Globalne limity / humanizer / kroki systemowe** — edycja `config/bot.json` przez `GET|POST /api/bot-config`. Krótki tekst: „konto może nadpisać w Konta”.
- Auth: toggle „wkrótce”.
- Link do klasycznego panelu.
- Wiersze disabled: PIN, Telegram, proxy na telefonie, DM, comment_gen, cost-cap.
- Read-only: znane kroki (`/api/known_steps`).

### API

Istniejące: secrets, scheduler.
Nowe: `GET/POST /api/bot-config` (walidacja kształtu jak fragment account.json).

### Kryterium

Zmiana global `daily_likes` na 80 zmienia effective konta **bez** własnego klucza. Konto z `daily_likes: 50` zostaje na 50. Secrets bez echo. Auth nie udaje, że działa.

Pliki: `app.html` / `app.js`, `panel/server.py`, `config/bot.json`, `config/schema.py` (jeśli nie w kroku 1).

---

## 16. Poza zakresem tego tasku

- Treści / brand / generator / kalendarz postów / Graph API / Graph vs bot (dawne fazy 4–6).
- Resume checkpoint po STOP/crash (JobRunner zapisuje, nie wznawia). Przycisk „Odzyskaj” z mocka stats.
- Przełączenie `profile_interact` z `raw_profiles` na `target_candidates` — tylko hint w Lejku.
- Share view auth (`/share` bez tokena).
- Przepisywanie `panel/static/index.html`.
- Nowe platformy, posting, DM, PIN, Telegram, proxy na telefonie, `daily_dms`, `comment_gen`, cost-cap abort.
- Drugi proces scrcpy z panelu (FB sesji → plik JPEG jest w zakresie).
- Równoległy filtr HTTP (jeden `_filter_proc` zostaje).
- Trzeci UI, nowa baza, klon repo.
- Hardcode aliasów `phone_A` / `galaxy` w JS.

---

## 17. Pliki do ruszenia

Ze speca §1 plus to, czego wymaga override i busy:

- `panel/static/app.html` — shell, sekcje widoków
- `panel/static/app.js` — wszystkie widoki operatora
- `panel/static/app.css` — rail + siatki
- `panel/static/stats-view.js` — KPI (zostawić / wchłonąć)
- `panel/server.py` — SessionManager, status/stop/SSE, visited, bot-config, devices fields, deny per device
- `panel/preview.py` — czyta JPEG z FB sesji, nie uiautodev idle
- CLI / start scrcpy — zapis `preview.jpg` per alias (komentarz PL)
- `scheduler/tick.py` — `is_busy(device)`
- `scheduler/device_planner.py` — tylko jeśli busy/annotate tego wymaga; oś zostaje
- `scheduler/state.py` — bez zmiany sensu master switch; per-device nie tu
- `config/schema.py` — `load_bot` + merge
- `config/bot.json` — nowy
- `config/_template/account.json` — dziedziczenie (puste sekcje) ostrożnie
- `config/devices/<alias>.json` — pole `scheduler_enabled` (default w kodzie, nie trzeba ręcznie edytować istniejących)
- `core/limits.py` — tylko gdy effective nie idzie z AccountConfig
- `core/humanizer.py` — analogicznie, raczej bez zmian
- `data/stores.py` — visited preview/limit jeśli brak
- `profile_filter/pipeline.py` — odczyt pending (już jest); nie zmieniać promocji

Nie ruszać: `panel/static/index.html`, `plugins/impl/*`, kroki, katalog poza `GET /api/plugins`.

---

## 18. Testy

Obowiązkowe (spec + ten plan):

- **SessionManager dwa Popen.** Nowy `tests/test_session_manager.py` (albo test panelowy bez prawdziwego CLI): start konta na `phone_A` nie rzuca gdy slot `galaxy` już „jedzie”; `stop(phone_A)` nie czyści slotu galaxy; `status()["sessions"]` ma dwa wpisy; `status()["running"]` zostaje bool. Popen zmockować (`subprocess.Popen` / wstrzyknięcie slotu) — nie odpalać prawdziwego ADB.
- **Scheduler `is_busy(device)`.** `tests/test_scheduler.py`: istniejące lambdy `lambda: False/True` → `lambda device: …`. Nowy case: dwa device, busy tylko na A, due blok na B → `started` na B, A → `device_busy`. Device z `scheduler_enabled=false` nie startuje przy global ON.
- **Lejek visited.** Endpoint `GET /api/visited-profiles/{acc}`: newest first, `limit`, 404 gdy brak konta. Fixtures z `action`/`followed`/`tiles_liked`/`reason`. Można TestClient FastAPI albo czysty store + funkcja serializacji.
- **Actions.** `tests/test_saved_actions.py` — nie regresować. Runy tylko przestylizowane.
- **Override limitów.** `tests/test_limits.py` / `tests/test_config.py`: `bot.json` `daily_likes=100`, konto bez klucza → 100; konto `daily_likes=50` → 50; `0` na koncie = brak limitu (nie spadaj do global). Humanizer speed analogicznie jeśli merge siedzi w `load_account`.
- **Deny per device.** Ręczny start: global scheduler ON + device ON → 423 na tym serialu, 200 na drugim. Diagnostyka `_deny_if_session_running` tylko gdy ten serial jedzie.

Nie wymagane w tym tasku: testy DOM/Playwright na rail; testy share auth; testy plugin impl.

Uruchomienie: `python -m unittest tests.test_session_manager tests.test_scheduler tests.test_saved_actions tests.test_limits tests.test_config tests.test_profile_stores` (dostosować nazwy po dodaniu plików).

---

## 19. Kryteria gotowe (cały task)

Ze speca §6, uzupełnione:

- Dwa zarejestrowane telefony: sesja na `phone_A` nie blokuje Start na `galaxy` (gdy galaxy online i jego scheduler nie zamyka ręcznego startu).
- Stop na live `phone_A` nie gasi procesu `galaxy`.
- Scheduler: due blok na wolnym urządzeniu startuje, gdy drugie jedzie.
- Lejek: trzy kolumny; pending tylko licznik filtra; outcome wizyty rozróżnia like/follow vs bez interakcji.
- Runy: `default` zarezerwowany; zapisana akcja to kopia override; biblioteka = 7 pluginów.
- `#konto` / `#system` nie są martwymi placeholderami (redirect + żywe widoki).
- Klasyczny `/` nadal działa. Nie rozbudowany.
- Brak TikTok / X / DM / publish w UI.
- Preview: klatka z FB sesji (plik JPEG); bez sesji pusty ekran, nie uiautodev.
- Global like 100 + override 50 na jednym koncie działa w `limits.check`.
- Auth nie udaje, że jest.

---

## 20. Mocki referencyjne

Katalog: `/home/michal/.cursor/projects/home-michal-bot-v2/assets/`

- Pulpit: `image-6210200f-8cbe-4bf4-9840-d48fe207702f.jpg`
- Lejek: `lejek-bae9205d-8241-4cf0-96ca-b4c9e4228ad6.jpg`
- Konta: `konta-0fbcd223-dcd1-4b49-8432-a37c66f6c114.jpg`
- Statystyki: `stats-990de30c-6e17-414d-acca-f1792ad4c13e.jpg`
- Runy layout: `run-0ad61d9b-eb91-427a-a014-4c696aa9680d.jpg`
- Runy (odrzuć zmyślone pluginy): `run2-7b27793d-2196-49c8-a9c0-c08c9f6352cb.jpg`
- Na żywo: `nazywo-9620c4ad-3d77-4d9f-8b90-48c0cf21a488.jpg` (nazwa `nazywo-`, nie `na-zywo-`)
- Flota: `flota-76fa98c1-9dcd-4f07-ac95-59111b0f837e.jpg`
- Harmonogram (źródło): `harmonogram-195385d4-1b3e-4dd4-a8f1-72f3929ebc91.jpg`
- Harmonogram (stare PNG, jeden telefon — nie layout floty): `mockup-harmonogram.png`, `panel-03-harmonogram-runy.png`
- Nie używać: `harmonogram-6fe06d0b-f271-49ae-8726-b3799b592fe8.jpg` (obcy produkt / Sesja JobSaver)

Stan floty (fakt, nie hardcode w UI): `phone_A` SM-A505FN serial `R58M74B5FRN` (4 klony, konta jobsaver / a50_clone2 / technature699); `galaxy` SM-A520F serial `52108b3459fe2457` (1 klon, kasia). Czytać z API.

---

## 21. Co nie jest tym planem

`panel_v2_weryfikacja.md` = historia „mamy” z 2026-09-02 + słownik. Dawne fazy 4–6 Graph/Treści są tam oznaczone jako poza zakresem. Ten plik = jedyny backlog realizacji `/app`.