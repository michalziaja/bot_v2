# Workflow: Rolki — nisza (`reels_niche`)

Źródło prawdy: `plugins/impl/reels_niche.py` + kroki `steps/reels/*` + podprocedura `profile_interact.run_one`.
Live logi: `plugins/reel_log.py` (zsynchronizowane z gestami). Pauzy: `humanizer` + stałe settle UI.

Legenda na diagramach:
- romb = decyzja
- prostokąt = akcja / krok
- węzeł `⏸` = pauza
- `stop` = koniec sesji / przerwanie pętli

**Architektura jednej rolki:** klatki ‖ gate → composite ‖ caption → AI → like/harvest → swipe  
Opis przez **tap** (`_read_reel_caption_guarded`), **nie** swipe-caption.

---

## 1. Pętla sesji (plugin `run`)

```mermaid
flowchart TD
  START([Start reels_niche]) --> OPEN["reels.open<br/>home_feed → reels_feed"]
  OPEN -->|status=failed| FAIL([PluginResult.failed])
  OPEN -->|ok| LOOP{budget.stop_reason?}

  LOOP -->|items / minutes / limit| DONE([PluginResult.done<br/>stopped_by])
  LOOP -->|brak| CLEAR["Wyczyść scratch:<br/>reel, reel_ai, reel_caption,<br/>reel_top_comments, …"]
  CLEAR --> ONE["_one_reel()"]
  ONE --> TICK["budget.tick()"]
  TICK --> OUT{outcome?}

  OUT -->|limit_daily| DONE
  OUT -->|feed_lost| DONE_ERR([done · stopped_by=error])
  OUT -->|ok / skip| BUDGET2{budget.stop_reason?}

  BUDGET2 -->|tak| DONE
  BUDGET2 -->|nie| NEXT["say «Nastepna rolka…»"]
  NEXT --> PAUSE["⏸ humanizer.pause('before_next_reel')"]
  PAUSE --> SWIPE["reels.swipe_next"]
  SWIPE -->|ok| RESET_SW["swipe_failures = 0"]
  RESET_SW --> LOOP

  SWIPE -->|failed| INC["swipe_failures += 1"]
  INC --> SW2{swipe_failures ≥ 2?}
  SW2 -->|tak| DONE_ERR
  SW2 -->|nie| ENS["navigator.ensure(reels_feed)<br/>max_steps=3"]
  ENS --> LOOP
```

**Powody `stopped_by`:** `items` · `minutes` · `limit_daily` · `error` (feed_lost / 2× swipe fail).

---

## 2. Jedna rolka (`_one_reel`)

```mermaid
flowchart TD
  A([Nowa rolka]) --> MODE{ai.enabled?}
  MODE -->|tak| MODESAY["opcjonalnie: say tryb AI<br/>tylko klatki / tylko opis / brak"]
  MODE -->|nie| W
  MODESAY --> W["reels.watch<br/>use_frames · read_caption · gate"]

  W --> STAT["stats.watched++"]
  STAT --> MARK{marker == last_marker?}
  MARK -->|tak| REP["stats.repeat_skipped++"]
  REP --> SKIP1([return skip])

  MARK -->|nie| WSTAT{watch.status == ok?}
  WSTAT -->|nie · ad / gate / skip| GS1["stats.gate_skipped++"]
  GS1 --> SKIP1

  WSTAT -->|ok| AUTH{author ∈ skip_authors?}
  AUTH -->|tak| GS2["stats.gate_skipped++"]
  GS2 --> SKIP1

  AUTH -->|nie| SLIP{reel_slipped?}
  SLIP -->|tak| GS3["stats.gate_skipped++"]
  GS3 --> SKIP1

  SLIP -->|nie| CAPFB{want_caption AND<br/>brak caption z watch?}
  CAPFB -->|tak| READ["reels.read_caption_comments<br/>open_comments = read_top_comments"]
  CAPFB -->|nie| AIEN
  READ --> AIEN{ai.enabled?}

  AIEN -->|nie| LIKE
  AIEN -->|tak| ANALYZE["reels.analyze_frames_ai"]
  ANALYZE -->|skipped no_ai_inputs / inne| AIR["stats.ai_rejected++"]
  AIR --> SKIP1
  ANALYZE -->|ok| EVAL["_eval_ai(reel_ai, ai_cfg)"]
  EVAL -->|pass=false| AIR
  EVAL -->|pass=true| LIKE

  LIKE{like_reel.enabled?} -->|nie| LIKERS
  LIKE -->|tak| LIKESTEP["reels.like<br/>chance z configu"]
  LIKESTEP -->|ok| LOK["stats.liked++"]
  LIKESTEP -->|daily limit| LDL([return limit_daily])
  LIKESTEP -->|skipped / inny| LIKERS
  LOK --> LIKERS

  LIKERS{harvest.likers.enabled<br/>AND score ≥ min_niche_score?}
  LIKERS -->|tak| SL["_sheet_block(likers)"]
  LIKERS -->|nie| COMM
  SL -->|limit_daily / feed_lost| EARLY([return outcome])
  SL -->|None| COMM

  COMM{comments: harvest<br/>OR like_comments<br/>OR hot_interact?}
  COMM -->|tak| SC["_sheet_block(comments)"]
  COMM -->|nie| OK([return ok])
  SC -->|limit_daily / feed_lost| EARLY
  SC -->|None| OK
```

Uwagi:
- Harvest lajkujących: brak progu / brak score (AI off) → `_score_ok` = true.
- Wynik AI w terminalu tylko przez `vision.say_ai_result` (bez duplikatu `ai_ok_line` w pluginie).
- `read_top_comments` domyślnie **false** (kosztowne); na ścieżce analizy zwykle wyłączone.

---

## 2a. Tryby wejść AI (`use_frames` / `read_caption`)

Niezależne flagi w `ai` (katalog: sekcje „Klatki” / „Analiza AI”):

| `use_frames` | `read_caption` | Zachowanie |
|---|---|---|
| true (default) | true (default) | klatki + composite + opis → Vision |
| true | false | tylko obraz (bez caption) |
| false | true | text-only: Vision z `frames=[]`, opis z tap |
| false | false | przy AI ON → skip `no_ai_inputs` |

- `use_frames=false` → `reels.watch` **nie** startuje capture/composite.
- `read_caption` w pluginie jest brane tylko gdy `ai.enabled` (przy AI off opis nie jest czytany).
- `use_frames` działa też przy AI off (zbieranie klatek jak wcześniej).

---

## 3. `reels.watch` — klatki, bramka, opis (równolegle)

W `reels_niche`: `use_watch_duration=false` — N klatek co `frame_interval_s`. `watch_reel` jest dostępny w ustawieniach pluginu, ale używany tylko gdy `reels.watch` pracuje w trybie czasu oglądania.

```mermaid
flowchart TD
  S([reels.watch]) --> UF{use_frames?}
  UF -->|tak| CAP["Wątek: capture N klatek<br/>co frame_interval_s"]
  UF -->|nie| META
  CAP --> META["Główny: read_current_reel<br/>say «Klatki…» / «bez klatek»"]

  META --> AD{is_ad?}
  AD -->|tak| CANCEL["cancel capture"]
  AD -->|nie| GATE{gate skonfigurowany?}

  GATE -->|nie| OKG
  GATE -->|tak| G1{likes ≥ min_likes?<br/>comments ≥ min_comments?}
  G1 -->|unknown + on_unknown=skip| FAILG
  G1 -->|poniżej progu| FAILG["gate_failed"]
  G1 -->|ok| OKG["say «Filtr OK»"]

  FAILG --> CANCEL
  CANCEL --> SKIP([StepResult.skipped<br/>ad_detected / gate_failed])

  OKG --> JOIN{use_frames?}
  JOIN -->|tak| WAIT["join capture → composite thread"]
  JOIN -->|nie| CAPQ
  WAIT --> CAPQ{read_caption?}
  CAPQ -->|tak| READ["capture_reel_caption<br/>‖ composite · say «Czytam opis…»"]
  CAPQ -->|nie| DONE
  READ --> DONE([StepResult.ok + scratch.reel<br/>+ reel_composite? + caption?])
```

**Decyzje bramki (`_eval_early_gate`):**
| Warunek | Wynik |
|---|---|
| `is_ad` | skip `ad_detected` |
| `likes < min_likes` | skip `gate_failed` |
| `likes > max_likes` (gdy ustawione) | skip `gate_failed` |
| `comments < min_comments` | skip `gate_failed` |
| wartość `None` + `on_unknown=skip` | skip |
| wartość `None` + `on_unknown=pass` | OK dla tego pola |

`max_likes` jest oceniane we wczesnej bramce `reels.watch` (`_eval_early_gate`),
gdy plugin przekaże je przez wspólne `gate_params` z `plugins/base.py`
(`reels_niche` + `content_search`; 0 / brak = bez górnego limitu).

---

## 4. Opis (+ opcjonalnie top komentarze)

Opis: **ten sam flow co discovery** — `_read_reel_caption_guarded` (TAP w caption / „…więcej”), **bez swipe** po feedzie.

Ścieżki wywołania:
1. **Preferowana:** w `reels.watch` równolegle ze sklejaniem composite (`capture_reel_caption` → `reel_caption_from_watch`).
2. **Fallback:** krok `reels.read_caption_comments`, gdy caption nie wyszedł z watch.

```mermaid
flowchart TD
  R([capture_reel_caption]) --> AD{reklama?}
  AD -->|tak| SKIPAD([skipped])
  AD -->|nie| SAY["say «Czytam opis…»"]
  SAY --> TAP["_read_reel_caption_guarded<br/>TAP + expand «więcej»"]
  TAP --> SAYC["say «Opis: …»"]
  SAYC --> SLIP{autor się zmienił?}
  SLIP -->|tak| FLAG["reel_caption_slipped"]
  SLIP -->|nie| OC
  FLAG --> OC{open_comments?<br/>domyślnie false}

  OC -->|tak| OPENC["say «Czytam top komentarze…»<br/>expand_sheet + top-K"]
  OC -->|nie| LAND
  OPENC --> LAND{na reels_feed?}
  LAND -->|nie| ENS["navigator.ensure(reels_feed)"]
  LAND -->|tak| OK([ok])
  ENS -->|ok| OK
  ENS -->|fail| FAIL([failed])
```

> Discovery i engagement używają **tej samej** ścieżki tap — patrz [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md) §4 / §17.

---

## 5. Live logi (kolejność ≈ gesty)

Typowa ścieżka z AI (oba wejścia ON):

1. `Klatki @autor…` (start watch / capture)
2. `Filtr OK - @autor…`
3. `Czytam opis...` → `Opis: …`
4. `Analiza AI...`
5. wynik przez `say_ai_result`: `score` / `decyzja` / `powod`
6. opcjonalnie `Polubiono @…` / harvest
7. `Nastepna rolka...` → ⏸ `humanizer.pause("before_next_reel")` → swipe

Przy odrzuceniu: pusta linia + `ODRZUT: …` przed „Nastepna rolka…”.

---

## 6. Ocena AI (`analyze_frames_ai` + `_eval_ai`)

Prompt: `ai/prompts/reel_niche.yml` (`max_tokens` ~180).

**Waga sygnałów:** #hashtagi i keywords z opisu mają **większą wagę** niż 2–4 klatki
(próbka z całej rolki). Przy pokryciu tekstowym niszy nie stawiaj `off` tylko na podstawie obrazu.
Stdout: `score` / `decyzja` / `powod` przez `say_ai_result` (bez pełnego promptu).

```mermaid
flowchart TD
  A([analyze_frames_ai]) --> IN{use_frames OR read_caption<br/>i jest composite/caption?}
  IN -->|nie| SKIP([skipped no_ai_inputs])
  IN -->|tak| VIS["AiVision.run reel_niche<br/>frames = composite lub []"]
  VIS --> SAY["say_ai_result"]
  SAY --> SCR["scratch.reel_ai"]

  E([_eval_ai]) --> HAS{jest reel_ai?}
  HAS -->|nie| NP["pass=false · why=no_ai_result"]
  HAS -->|tak| MODE{ai.mode?}

  MODE -->|exclude_only| EX{verdict ∈ off/excluded?}
  EX -->|tak| FAIL1["pass=false · excluded"]
  EX -->|nie| PASS1["pass=true"]

  MODE -->|niche_match domyślnie| AV{allow_verdict ustawione<br/>i verdict ∉ allow?}
  AV -->|tak| FAIL2["pass=false · verdict=…"]
  AV -->|nie| SC{niche_score ≥ min_niche_score?}
  SC -->|nie| FAIL3["pass=false · score<min"]
  SC -->|tak| PASS2["pass=true"]
```

Domyślnie katalog: `mode=niche_match`, `min_niche_score=55`.

---

## 7. Lajk rolki (`reels.like`) — warstwy w kroku

Plugin woła like z `match={}` (puste) — match AI jest już w `_eval_ai`. W kroku nadal działa szansa:

```mermaid
flowchart TD
  L([reels.like]) --> AD{reklama?}
  AD -->|tak| SKIPAD
  AD -->|nie| G{params.gate?}
  G -->|fail| AG([aborted_by_gate])
  G -->|ok / brak| M{params.match?}
  M -->|fail| SM([skipped match_failed])
  M -->|ok / brak| C["_eval_chance(chance, score)"]
  C -->|miss| SC([skipped chance_missed])
  C -->|hit| TAP["double-tap środek wideo<br/>⏸ pause(tap) 0.15–0.4 s"]
  TAP --> OK([ok · liked])
```

`chance`: stałe `{p}` albo formuła `{formula: score/100}` (domyślne w katalogu).

---

## 8. Blok sheetu — lajkujący / komentarze (`_sheet_block`)

Jedno otwarcie listy na harvest + like comments + hot. Zawsze `finally` → powrót na `reels_feed`.

```mermaid
flowchart TD
  SB(["_sheet_block(where)"]) --> KO{want_hot OR want_likes?}
  KO -->|tak| KEEP["keep_sheet_open=true"]
  KO -->|nie| CLOSE["keep_sheet_open=false"]
  KEEP --> HARV
  CLOSE --> HARV

  HARV["HARVEST_STEP[where]<br/>limit = draw_range(collect)<br/>⏸ settle 0.35–0.4 s przy otwarciu"]
  HARV --> SAVED["stats.*_saved += saved"]

  SAVED --> KEEPFAIL{keep_open AND status≠ok?}
  KEEPFAIL -->|tak| LOG["sheet_not_open → return None"]
  KEEPFAIL -->|nie| LC{want_likes?}

  LC -->|tak| LIKEC["_like_comments()"]
  LIKEC -->|limit_daily| RETLDL([return limit_daily])
  LIKEC -->|n| STATL["stats.comments_liked += n"]
  LC -->|nie| HOT
  STATL --> HOT{want_hot?}

  HOT -->|tak| HI["_hot_interact()"]
  HOT -->|nie| FIN
  HI --> FIN

  FIN[["finally: _back_on_feed()<br/>navigator.ensure(reels_feed, max_steps=4)"]]
  FIN -->|ok| RET["return stop"]
  FIN -->|fail| FL(["return feed_lost"])
```

---

## 9. Lajkowanie komentarzy (`_like_comments` → `reels.like_comment`)

```mermaid
flowchart TD
  LC([_like_comments]) --> WANT["want = draw_range(count)"]
  WANT --> LOOP{i < want?}
  LOOP -->|nie| RETN([return liked])
  LOOP -->|tak| STEP["reels.like_comment"]

  STEP --> LIM{limits.like allowed?}
  LIM -->|nie daily| LDL([return limit_daily])
  LIM -->|tak| ROWS{są wiersze?}
  ROWS -->|nie| BREAK([break · no_candidates])

  ROWS -->|tak| FILT{counts_readable AND min_comment_likes?}
  FILT -->|tak| POOL1["pool = likes ≥ min"]
  FILT -->|nieczytelne + min>0| POOL2["górna połowa panelu"]
  FILT -->|min=0| POOL3["wszystkie nielajknięte"]

  POOL1 --> CH
  POOL2 --> CH
  POOL3 --> CH{extra_chance_percent roll?}
  CH -->|miss| SKIPCH([skipped chance_missed · next])
  CH -->|hit| TAP["tap serduszko<br/>⏸ pause_kind=tap"]
  TAP --> INC["liked++"]
  INC --> LOOP
  SKIPCH --> LOOP
```

---

## 10. Hot interact (`_hot_loop` + `profile.visit` short)

```mermaid
flowchart TD
  H([_hot_interact]) --> PREP["settings_for(profile_interact_override, hot.overrides)<br/>short = NOT full_procedure"]
  PREP --> N["want = draw_range(hot.count)"]
  N --> LOOP{i < want?}
  LOOP -->|nie| NONE([return None])

  LOOP -->|tak| OPEN["OPEN_PROFILE_STEP[where]<br/>prefer_ring / require_ring<br/>⏸ wait_for_stable_ui ≤1.5 s<br/>⏸ pause_kind=tap"]

  OPEN -->|open_failed| CONT["continue · inny wiersz<br/>log: why / landed / opened_then_lost"]
  CONT --> LOOP
  OPEN -->|no_candidates / failed| BREAK([break])
  OPEN -->|ok na profile_other| VISIT["ProfileInteract.run_one(short)"]

  VISIT --> VIS
  subgraph VIS["profile.visit — short"]
    direction TB
    H1["header"] --> SAN{sanity<br/>private / already following?}
    SAN -->|abort| SK1[skipped]
    SAN -->|ok| G2{gate.enabled?}
    G2 -->|fail| SK2[skipped gate]
    G2 -->|ok / off| DEC["_decide_action(scoring, follow)<br/>min_scoring · chance"]
    DEC -->|follow| EXF["execute follow<br/>⏸ pause(between_steps) 0.6–1.4 s"]
    DEC -->|skip| SAVE
    EXF --> SAVE["save visit"]
  end

  VIS --> STAT["stats.hot_visited++ · hot_followed?"]
  STAT --> EXIT["profile.exit → baseline sheet"]
  EXIT --> DL{stop == limit_daily?}
  DL -->|tak| LDL([return limit_daily])
  DL -->|nie| SHEET{nadal na sheet_screen?}
  SHEET -->|nie| LOST["log hot_interact_lost_sheet · break"]
  SHEET -->|tak| LOOP
```

Przy `full_procedure=true` short=false → dodatkowo: story (⏸ `watch_story` 3–8 s z ustawień pluginu), grid AI, like tiles.

**Hot open (kod 2026-08-08):** `steps/reels/open_profile_from_sheet.py` + `_sheet.nick_tap_xy` — tap w nick (bias w prawo od ringu), nie w awatar. Poll: `reels_feed` dopiero po **3** kolejnych hitach (mignięcie pod animacją ≠ fail); late `profile_other` = OK; przy zgubionym sheetcie restore (reopen likers/comments) + 1 retry; `why=opened_then_lost` vs brak otwarcia. Hot **nie** mutuje `raw_profiles` (`hot_from_reels`).

---

## 11. Mapa pauz w ścieżce `reels_niche`

| Miejsce | Rodzaj | Domyślny zakres | Uwagi |
|---|---|---|---|
| między klatkami capture | `frame_interval_s` | 0.5 s | gdy `use_frames`; `watch_reel` w ustawieniach pluginu (dla trybu czasu oglądania) |
| opis (tap / expand) | gesty guarded | — | dwell humanizera opcjonalnie w discovery helpers |
| po double-tap lajku | `tap` | 0.15–0.4 s | |
| harvest open / scroll | `tap` + sleep 0.35–0.4 s | | settle listy |
| open profile z sheetu | `wait_for_stable_ui` | ≤1.5 s | + `tap` |
| follow execute | `between_steps` | 0.6–1.4 s | |
| story (tylko full) | `watch_story` | 3–8 s | z ustawień pluginu / `profile_interact` |
| **przed następną rolką** | `before_next_reel` | **0.3–0.6 s** | po komunikacie „Nastepna rolka…”, `humanizer.pause("before_next_reel")` z profilu; przed `swipe_next` (`announce=false`) |
| long_pause globalny | co N akcji humanizera | n=200 · 60–180 s | profil, nie plugin |

**W niszy:** `watch_reel` dostępny w ustawieniach pluginu, ale domyślnie używana jest metoda klatek; `caption_dwell` wyłączony.

---

## 11a. Logi panel / terminal

| Źródło | Co widać |
|---|---|
| `plugins/reel_log.say` | Prefix `reels_niche` — w panelu niebieski (CSS `.ev-plugin`, bez ANSI) |
| AI `reel_niche` | `score` / `decyzja` / `powod` — bez pełnego promptu |
| Hot open fail | `why` / `landed` / `saw_profile` (ludzki log z pluginu) |
| `niche_discover.reel_caption.*` | W `events.jsonl`; **wyciszone w panelu** (nawet gdy engagement używa guarded) |
| Udane kroki `reels.*` | Milczą na stdout (status ludzki z `reel_log`) |
| Navigator L* / identify | Często filtrowane w panelu SSE (mniej szumu przy content_search) |

---

## 12. Przepływ danych (scratch)

```mermaid
flowchart LR
  W[reels.watch] -->|reel, reel_composite?| S[(scratch)]
  W -->|reel_caption? równolegle| S
  R[read_caption_comments] -->|reel_caption, reel_top_comments| S
  A[analyze_frames_ai] -->|reel_ai, reel_caption_source| S
  L[reels.like] -.->|czyta reel, reel_ai| S
  H[harvest_*] -->|saved do store| DB[(targets DB)]
  P[open_*_profile] -->|hot_used, baseline_screen| S
  V[profile.visit] -->|profile_decision, followed| S
```

Na starcie każdej rolki plugin **czyści** `reel*` ze scratch — żeby opis/AI nie przeciekły do następnej treści.

---

*Źródło prawdy dla `reels_niche` (2026-08-08): klatki∥gate → composite‖opis tap;
`use_frames` / `frame_interval_s` / `read_caption`; `watch_reel` w ustawieniach pluginu;
logi `reel_log`; AI: score/decyzja/powod; #/keywords > sparse frames. Patrz też [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md).*
