# CLI — szybkie testowanie

Wejście zawsze z katalogu rodzica pakietu (`bot/`), nie z `v2/`:

```bash
cd c:\Users\mz\Desktop\prog\bot\bot-czerwiec\bot
python -m v2.cli <komenda> …
```

Źródło prawdy flag: `v2/cli.py` (zweryfikowane 2026-08-08).

**Powiązane:**
- [`DRZEWO_PLIKOW.md`](DRZEWO_PLIKOW.md) — gdzie leżą configi, kolejki, data
- [`STAN_SYSTEMU.md`](STAN_SYSTEMU.md) — co działa w systemie
- [`RZECZY_DO_POPRAWY.md`](RZECZY_DO_POPRAWY.md) — znane długi i rozjazdy UI↔runtime
- [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md) — discovery niszy (pipeline, tap-opis, tokeny)

---

## Konto + urządzenie + plugin

| Co | Skąd | Flaga CLI? |
|---|---|---|
| **Konto** | `config/accounts/<alias>.json` | `--account=<alias>` |
| **Urządzenie** | `device.device_alias` → `config/devices/<alias>.json` (ADB, uiautodev, scrcpy, model mapy) | **NIE** — nie ma `--device` |
| **Plugin** | zadanie w kolejce (`plugin` + `id`) albo filtr `--only` | pośrednio przez kolejkę / `--only` |

Mapa UI: `calibration/<model>_draft.json` (z hardware urządzenia). Brak mapy = exit przy starcie sesji z telefonem.

Kolejki: `config/accounts/<alias>.queues/<queue_name>.json` (domyślna = scenariusz `default`). Legacy: `<alias>.queue.json` nadal czytany przy migracji.

---

## Komendy (przegląd)

| Komenda | Telefon? | Opis |
|---|---|---|
| `run-queue` | tak (chyba że `--dry-run`) | **PROD** — kolejka pluginów |
| `start` | tak | Legacy flow YAML (`--flow`) |
| `login` | tak | wake → unlock → IG → login → kod 2FA jeśli trzeba |
| `where` | tak | diagnostyka ekranu |
| `recover` | tak | doprowadź do `--target` (np. `home_feed`) |
| `known` | nie | lista zarejestrowanych kroków |
| `stats` | nie | agregaty z JSONL (`data/stats.py`) |
| `panel` | nie* | dashboard (domyślnie `:8765`) |
| `scheduler` | tak (subprocess) | tick harmonogramu bez UI |
| `scheduler-switch` | nie | master switch `--on` / `--off` |

\*Panel sam nie rusza telefonu; start sesji z UI odpala `run-queue`.

---

## `run-queue` (PROD)

```text
python -m v2.cli run-queue --account=<alias>
    [--queue=<queue_name>]
    [--only=<task_id>]
    [--max-items=N]
    [--max-duration-s=S]
    [--dry-run]
    [--speed=N]
```

| Flaga | Znaczenie |
|---|---|
| `--account` | **wymagane** — alias konta |
| `--queue` | nazwany scenariusz w `<alias>.queues/<nazwa>.json`; bez flagi = domyślna |
| `--only` | tylko zadanie o tym `id` z kolejki |
| `--max-items` | nadpisz budżet jednostek we **wszystkich** taskach (testy) |
| `--max-duration-s` | twarda rama całej kolejki (używa tego harmonogram blokowy) |
| `--dry-run` | wypisz rozwiązane settings (catalog ← account ← override) i wyjdź — **bez telefonu** |
| `--speed` | globalna prędkość na ten przebieg (`2` = pauzy ~2× krótsze) |

### Przykłady

```bash
# podgląd merge ustawień — bez ADB
python -m v2.cli run-queue --account=<alias> --dry-run

# pełna domyślna kolejka
python -m v2.cli run-queue --account=<alias>

# nazwany scenariusz
python -m v2.cli run-queue --account=<alias> --queue=<queue_name>

# JEDEN plugin / jedno zadanie: --only=task_id
python -m v2.cli run-queue --account=<alias> --only=<task_id>

# krótki smoke: jedno zadanie + mały budżet + szybciej
python -m v2.cli run-queue --account=<alias> --only=<task_id> --max-items=3 --speed=2

# okno czasowe (jak blok schedulera)
python -m v2.cli run-queue --account=<alias> --max-duration-s=900
```

**Jak odpalić dokładnie jeden plugin:** w kolejce musi być task z tym `plugin` i znanym `id` — potem `--only=<task_id>`. Alternatywa: osobny plik w `<alias>.queues/` z jedną pozycją i `--queue=<queue_name>`.

### Discovery niszy (one-shot, bez kolejki)

```bash
python -m v2.cli run-queue --account=<alias> --plugin=niche_discover --speed=3
```

Albo Panel → Konto → **Nisza** → „Uruchom discovery”.  
Po zakończeniu: `niche.description` / keywords / #, `accepted=false`, tokeny IN/OUT.  
Dok.: [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md).

### Ludzkie logi (bez zmiany flag CLI)

Stdout pluginów ma własne prefixy (panel koloruje `.ev-plugin`):

| Plugin | Moduł logu |
|---|---|
| `reels_niche` | `plugins/reel_log.py` |
| `profile_interact` | `plugins/profile_log.py` (grid AI: Kafelek N — reason) |
| `content_search` | `plugins/content_search_log.py` (hot fail: why/landed) |
| `target_scraper` | `plugins/scraper_log.py` |

Udane kroki UI często milczą (`stepcall` quiet) — patrz status ludzki, nie `ok: search.open`.

### Smoke `target_scraper`

Kolejka: `config/accounts/<alias>.queues/smoke_target_scraper.json`
(search raz → posty/Rolki + expand confirmed na likers/comments;
followers/following bez expand; warunkowy BACK; bez search między kafelkami).

```bash
python -m v2.cli run-queue --account=<alias> --queue=smoke_target_scraper --dry-run
python -m v2.cli run-queue --account=<alias> --queue=smoke_target_scraper --speed=2
```

Zapis → `data/accounts/<alias>/raw_profiles.jsonl` (`from_target`, `content_kind`;
dedup; commenters bez autora/`exclude`).

### `content_search` / hashtagi

`search.type_query` pisze `input text "#tag"` (cudzysłowy) — bez tego `#` = komentarz shella na telefonie.
Gate rolek: wspólne `gate_params` (`max_likes` w `reels.watch`); AI / like_comments / skip_authors ukryte w UI.

---

## `start` (legacy YAML)

```bash
python -m v2.cli start --account=<alias> --flow=<flow_name>
```

Do smoke-testów kroków / starych flowów. Ścieżka produkcyjna = `run-queue`.

---

## Login, diagnostyka, recovery

```bash
python -m v2.cli login --account=<alias>

python -m v2.cli where --account=<alias>

python -m v2.cli recover --account=<alias> --target=home_feed
python -m v2.cli recover --account=<alias> --target=reels_feed --max-steps=6

python -m v2.cli known
```

`login` nie idzie w captcha / suspicious login — to ręczne na telefonie.  
`recover --target`: np. `home_feed`, `reels_feed`, `profile_self` (domyślnie `--max-steps=6`).

---

## Stats (bez telefonu)

```bash
python -m v2.cli stats --account=<alias>
python -m v2.cli stats --account=<alias> --days=14
python -m v2.cli stats --account=<alias> --json
```

`--json` = ten sam kształt co docelowy endpoint panelu (`full_report`).

---

## Panel

```bash
python -m v2.cli panel
python -m v2.cli panel --host=127.0.0.1 --port=8765
python -m v2.cli panel --no-browser
```

Domyślnie: **http://127.0.0.1:8765**. Nie wystawiaj `--host=0.0.0.0` bez auth (panel bez logowania).

**Zatrzymanie:** `Ctrl+C` w terminalu panelu — zwalnia port 8765 i ubija child sesji. Przy `--no-browser` nie zamykaj okna bez Ctrl+C (orphan python zostaje na porcie).

---

## Scheduler

```bash
# master switch
python -m v2.cli scheduler-switch --on
python -m v2.cli scheduler-switch --off
python -m v2.cli scheduler-switch          # status

# pętla tick (domyślnie co 60 s)
python -m v2.cli scheduler
python -m v2.cli scheduler --interval=30
```

**Nie** uruchamiaj `scheduler` jednocześnie z `panel` — oba mają orkiestrator i walczą o telefon.

---

## Soft STOP

Miękkie zatrzymanie sesji konta — plik (nie flaga CLI):

```text
v2/data/accounts/<alias>/STOP
```

Plugin kończy bieżącą jednostkę budżetu i wychodzi. CLI / panel przy **nowym** starcie zwykle kasują zaległy `STOP`, żeby nie ubić świeżej sesji od razu.

PowerShell (utwórz STOP):

```powershell
New-Item -ItemType File -Force -Path "v2\data\accounts\<alias>\STOP"
```

---

## Typowy przebieg testu jednego pluginu

1. `run-queue --account=<alias> --only=<task_id> --dry-run` — sprawdź settings  
2. Upewnij się, że telefon = `device_alias` z configu konta, uiautodev/ADB OK  
3. W razie potrzeby: `login --account=<alias>`  
4. `run-queue --account=<alias> --only=<task_id> --max-items=3 --speed=2`  
5. Przy zawieszeniu: utwórz `data/accounts/<alias>/STOP`  
6. Po sesji: `stats --account=<alias>` (bez telefonu)

Placeholdery: `<alias>`, `<task_id>`, `<queue_name>`, `<flow_name>` — bez wypisywania live statusu kont.
