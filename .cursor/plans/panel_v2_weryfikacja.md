# Panel v2 — historia stanu (nie plan realizacji)

Branch: `zmiana_dashboardu`.

**Plan realizacji dashboardu `/app`:** [bot_v2_dashboard_realizacja.md](bot_v2_dashboard_realizacja.md).
Ten plik zostaje jako słownik + zweryfikowany stan kodu z 2026-09-02. Nie implementować stąd.

Stary UI (`panel/static/index.html` + `panel/static/stats.html`) zostaje pod **Klasyczny panel**. Nie rozbudowujemy go. Backend (`panel/server.py`) w starych fazach 1–3 nie przepisujemy w całości — dokładamy endpointy i rozcinamy SessionManager.

**Źródło prawdy stanu `/app`:** sekcja [Stan `/app` (kod, 2026-09-02)](#stan-app-kod-2026-09-02).
**Źródło prawdy produktu:** `/home/michal/Downloads/bot-v2-dashboard-spec.md`.

---

## Słownik

Żeby nie mylić trzech „sesji”:

- **Run** — szablon kolejki (`config/accounts/<alias>.queues/<nazwa>.json`), np. Hashtagi, Interakcja, Grzanie
- **Blok** — wpis na osi telefonu: konto × run × godzina
- **Na żywo** — aktualny przebieg + klatka preview (uiautodev/adb JPEG, nie scrcpy FB)
- **Post** — treść do publikacji Graph API — **POZA ZAKRESEM** tego tasku

Nav operatora (potwierdzony, mock + spec):

**Pulpit · Harmonogram · Runy · Lejek · Na żywo · Statystyki · Konta · Flota · Ustawienia**

Stare hashe: `#konto` → `#konta`, `#system` → `#flota`.
`Treści` / Graph nie wchodzą do nava.

---

## Fazy 1–3 (historyczne — częściowo zrobione)

Fazy 1–3 z dawnego planu IA (shell, pulpit, preview, runy, harmonogram v1, stats+share) są **częściowo w kodzie**. Luki vs mock/spec opisuje plan realizacji. Nie traktować todos z tego pliku jako backlogu.

---

## Fazy 4–6 Graph / Treści — POZA ZAKRESEM tego tasku

Dawna faza 4 (studio treści / brand / generator), faza 5 (kalendarz postów + Graph API) i faza 6 (Graph vs bot / dual stats) **nie są częścią realizacji `/app`**.

Nie dodawać do nava: Treści, kalendarz postów, Graph Insights, publikacji.
Nie ruszać: resume checkpoint, switch `profile_interact` → candidates, share auth, przepisywania `index.html`.

Szczegóły: spec §8 oraz plan realizacji, sekcja „Poza zakresem”.

---

## Świadomie później / nie w tym planie (zostaje)

- Auth właściciela (magic link / rola viewer) — w `/app` tylko flaga „wkrótce”
- Przepisanie JobRunnera i formatu queue
- Aplikacja mobilna
- Zamiana całego `index.html` w jednym PR
- Nieoficjalne postowanie z telefonu
- PIN unlock, Telegram alerts, proxy na telefonie, `daily_dms`, `comment_gen`, cost-cap abort
- TikTok, X, DM, publish

---

## Stan `/app` (kod, 2026-09-02)

Weryfikacja plików `panel/static/app.html`, `app.js`, `app.css`, `stats-view.js`, `share.html`, `share.js` oraz endpointów w `panel/server.py`. Mocki referencyjne (JPG 2026-09-02): pulpit, lejek, konta, stats, run, run2, nazywo, flota; plus starsze PNG Harmonogramu. Spec: `bot-v2-dashboard-spec.md`.

**Werdykt Harmonogramu z 1.09 nadal aktualny.** v1 = lista dni cyklu + pigułki limitów + **Ułóż tydzień**. Brak siatki, inspectora, draga, kłódki/LIVE na osi. Backend move / queue / POST block / regenerate już jest; osobnego `POST …/lock` nie ma (kłódka to pole `locked`, ustawiane przy add/move).

Klasyczny panel (`index.html` → Planowanie / oś) ma pionową oś godzin + drag + edytor bloku + now-line + 🔒 — **nie ruszamy go**. `/app` tego nie odziedziczył.

---

### Mamy

**Shell** — `GET /app` (`panel/server.py`), **top-nav** (nie lewy rail). Nav: Pulpit · Harmonogram · Runy · Na żywo · Statystyki · Konto · System + link Klasyczny panel. Hash-router w `app.js` (`VIEWS`, aliasy `#nazywo` / `#sesja`). Brak: `#lejek`, `#konta`, `#flota`, `#ustawienia`.

**Pulpit** — karty z `/api/devices`, `/api/adb-devices`, `/api/accounts`, `/api/status` (globalne `{running}`), `/api/timeline/{dev}` (następny blok), `/api/scheduler` (banner 423). Start: `POST /api/run-queue`. Stop: `POST /api/stop` (gasi jedyny Popen). Miniatura: `GET /api/devices/{alias}/preview` (`panel/preview.py`, **uiautodev/adb JPEG**, nie scrcpy FB). Klik klatki → `#na-zywo`. Start disable gdy `status.running` — kłamie przy flocie.

**Harmonogram v1** — `refreshHarmonogram` / `renderWeek` w `app.js`:
- selektor **jednego** urządzenia (`GET /api/timeline-devices`)
- karty dni cyklu (`GET /api/timeline/{dev}` + `GET /api/schedule-accounts/{dev}`) — stacked `article.tl-day`, nie siatka
- blok: `start–koniec · duration` + `konto × scenario` (bez `locked`, bez `runtime.status`)
- pigułki max/limit + donut (`POST /api/estimate-actions`); dziś pokazuje `used_today`
- **Ułóż tydzień** → `POST /api/timeline/{dev}/regenerate` (confirm: „kłódka zostaje”)
- banner gdy `GET /api/scheduler` → `enabled` (tylko odczyt — brak toggle)

**Runy** — kreator w `/app`: biblioteka z `/api/plugins`, kroki z drag, inspector per `task.id`, limity jak na osi (`used_today`, żółto przy `over_remaining`). CRUD: `GET/POST /api/queue/{acc}?name=`, `GET /api/queue-presets/{acc}`, `DELETE /api/queue/{acc}/{name}`. `default` zarezerwowany. **Zapisane akcje** (presety kroku, nie nowy typ runu): `config/accounts/<alias>.actions/<slug>.json`, CRUD `GET/POST /api/accounts/{acc}/actions` + `GET/PUT/DELETE …/actions/{id}`. UI: zakładki „Akcje bazowe” / „Zapisane akcje”; inspector „Zapisz jako akcję” / „Zaktualizuj zapisaną”; wstawienie do runu kopiuje `overrides` (pole `from_action` na tasku to wskaźnik, nie live-link). Katalog: 7 queueable + `niche_discover` (queueable=false, ui_hidden).

**Na żywo** — split telefon | postęp | log. Preview ~1 s, SSE `/api/events/stream`, postęp z `/api/stats/{acc}` (faza / kafelek / fraza / chipy), Stop. Log ludzki (stdout + session start/end), nie dump JSONL. **Umiera przy braku sesji** (globalny `status.running`).

**Statystyki** — `stats-view.js`: KPI 24h (`/api/stats/{acc}`, `/api/stats/{acc}/aggregate?range=24h`) + iframe pełnego `/stats`.

**Share (właściciel, bez auth)** — `GET /share/schedule`, `/share/stats`; `GET /app?view=owner` → 302 na share. Ta sama lista dni co operator + KPI 24h. Zero Start/Stop / haseł / ADB / edycji. Share pokazuje `· przypięty` przy `locked` (operator na Harmonogramie — nie). Auth share **poza zakresem**.

**Konto / System** — placeholdery w `app.html` („Wkrótce”).

**Preview** — `panel/preview.py`: uiautodev `:20242` / `adb exec-out screencap`, JPEG ~480 px, ~1 fps. Zgodnie ze specem. Nie przełączać na scrcpy FB.

**Backend osi (gotowy, `/app` go nie woła poza GET + regenerate):**
- `POST /api/timeline/{dev}/day/{i}/block` — ręczny slot, od razu `locked=True`
- `POST …/block/{id}/move` — `{start, force}`
- `POST …/block/{id}/queue` — `{scenario, queue_override, account}`
- `DELETE …/block/{id}`
- `POST …/autospread`
- `DELETE /api/timeline/{dev}` — wyczyść oś
- `GET/POST /api/scheduler` — master switch + `active_hours` + `cycle_days` (`data/scheduler.json`)
- `GET/POST /api/schedule-accounts/{dev}` — udział kont + sesje/dzień
- `POST /api/estimate` + `/api/estimate-actions` — ETA, min–max, `utilization`, `used_today`, `remaining`, `over` / `over_remaining`
- `GET /api/timeline/{dev}` zwraca `runtime[block_id].status` (`running` / `done` / `missed` / `failed`) — pod LIVE i stany na osi

**SessionManager (bloker floty)** — `panel/server.py` klasa + global `SESSION`. Jeden `Popen`. `GET /api/status` → `{running, account, flow, …}`. `POST /api/stop` bez body gasi ten jedyny proces. Scheduler tick (~linia 2661): `is_busy=lambda: SESSION.status()["running"]` — blok na `galaxy` dostaje `device_busy`, gdy jedzie `phone_A`. `_deny_if_scheduler_enabled()` jest globalne (423 na cały panel).

**Global vs override** — nie istnieje. Każde `config/accounts/<alias>.json` ma własne `limits` / `humanizer` / `toggles.steps`. Brak `config/bot.json`.

---

### Musimy mieć (historyczny gap vs mock z 1.09 — supersedowane planem realizacji)

Poniższe tabele zostają jako audyt z 1–2.09. Backlog wykonawczy = 11 kroków w `bot_v2_dashboard_realizacja.md`.

#### Harmonogram (P0)

- Siatka Pn–Nd × godziny (oś 8–22) — **brak**. Dziś: lista kart dnia. Cykl API to 1/3/7 dni wzorca.
- Bloki na osi (konto × run × czas) — **częściowo**. Dane są; brak pozycji, kolorów, selected.
- Linia „teraz” — **brak**. API ma `today` / `today_index`.
- Badge LIVE / done / missed — **brak w UI**. `runtime.status` już w GET.
- Kłódka na bloku — **brak w UI**. Pole `b.locked` jest.
- Drag bloku (snap, 409→force) — **brak w UI**. `POST …/move` gotowy.
- Inspector bloku — **brak**.
- Dodaj / usuń blok — **brak w UI**. API gotowe.
- Ułóż tydzień — **jest**.
- Pigułki limitów dnia — **częściowo**.
- Master switch „plan działa” — **tylko banner**.
- Ramy (godziny, cykl, sesje/dzień) — **brak w `/app`**.

#### Reszta `/app` vs mock

- **Pulpit** — karty, preview, Start/Stop, banner. Brak paska KPI floty, Start kłamie przy drugim telefonie.
- **Runy** — kreator + zapisane akcje. Layout nie jest trójkolumną z mocka (`run-0ad61d9b`).
- **Na żywo** — split + preview + log. Umiera bez sesji. Brak miniatur floty.
- **Statystyki** — KPI 24h + iframe. Brak native zakresu / ostatniej sesji.
- **Share** — stub readonly. Auth poza zakresem.
- **Lejek / Konta / Flota / Ustawienia** — nie istnieją w `/app`.

---

## Do potwierdzenia — ANULOWANE

Kwestionariusz o zakładkach został anulowany (2026-09-02). Jedyna potwierdzona decyzja: pełny sidebar z mocka + Konta vs Ustawienia (per konto vs global + override). Reszta = założenia w planie realizacji, do korekty.
