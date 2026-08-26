# Kolejne kroki — do idealnie działającego narzędzia

Data: 2026-08-08  
Źródła: [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md), [`BACKLOG.md`](BACKLOG.md), luki z kodu + [`RZECZY_DO_POPRAWY.md`](RZECZY_DO_POPRAWY.md).

**Powiązane:**
- [`STAN_SYSTEMU.md`](STAN_SYSTEMU.md) — co już działa
- [`DRZEWO_PLIKOW.md`](DRZEWO_PLIKOW.md) — mapa katalogów / plików
- [`RZECZY_DO_POPRAWY.md`](RZECZY_DO_POPRAWY.md) — UI ≠ runtime, dług techniczny
- [`CLI_TESTOWANIE.md`](CLI_TESTOWANIE.md) — komendy do szybkiego testu
- [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md) — plan gate + plugin discovery
- [`REELS_NICHE_WORKFLOW.md`](REELS_NICHE_WORKFLOW.md) — engagement rolek (`use_frames`, opis tap, logi)

---

## 1. Cel docelowy

Idealne narzędzie to bot, którego **panel i config nie kłamią**: każde widoczne ustawienie ma efekt w runtime albo jest ukryte; siedem pluginów produkcyjnych działa przewidywalnie z kolejki i harmonogramu; nisza jest odkrywana i **zatwierdzana** zanim AI engagement z niej korzysta; po przerwie sesja wznawia się z checkpointu; prolog sam loguje gdy trzeba; w panelu widać te same liczby co w CLI (`data/stats.py`); a tematy z BACKLOG (Telegram, proxy, multi-device, SQLite…) wchodzą dopiero gdy rdzeń jest wiarygodny.

---

## 2. Priorytet P0 — blokery / fałszywe UI

### P0.1 Gate `niche.accepted` + runtime `niche_discover` — **ZROBIONE**

Plugin `plugins/impl/niche_discover.py`, prompty warstwowe, one-shot
`POST /api/run-plugin` + `cli run-queue --plugin=niche_discover`,
gate `niche_is_accepted` w Vision engagement. Po discovery `accepted=false`.

Aktualny stan (2026-08-08): **najpierw zbieranie** (own + konkurencja → JSON
frames/opisy), **potem AI** per profil i merge; posty zbiorczo (`batch_size`),
rolki 1:1; opis rolki **tap**; `firm_tap`+retry; tokeny IN/OUT; dokładne
`keywords_count` / `hashtags_count`; sync fraz append.  
Szczegóły: [`NICHE_DISCOVER.md`](NICHE_DISCOVER.md).

### P0.2 Martwe ustawienia panelu — podłączyć albo usunąć / ukryć

Z [`RZECZY_DO_POPRAWY.md`](RZECZY_DO_POPRAWY.md) §2 — operator zmienia przełącznik, nic się nie dzieje:

| Ustawienie | Rekomendacja P0 |
|---|---|
| proxy / network | **Ukryć** do BACKLOG §9 |
| Telegram `alert_channel` | **Ukryć** albo prawdziwy sender (→ P3) |
| `stop_on_verify`, `screenshot_on_error` | **Ukryć** albo podpiąć |
| `max_cost_per_session_usd` | **Ukryć** albo licznik w Vision |
| model `recovery_identify` w UI | **Ukryć** do L6 (P2) albo badge „niedostępne” |
| `source.kind=raw_profiles` | **Zrobione** — reader + `mark_outcome`; nie ukrywać |
| `keywords` / `hashtags` w niszy | **Wpięte** w discovery + opcjonalny sync do `content_search`; edytowalne w panelu Nisza |
| statusy `planned`/`partial` w catalog mimo `impl/` | **Zrobione** — wszystkie pluginy w `catalog.py` mają `status: ready` |
| content_search AI / like_comments / skip_authors | **Zrobione** — ukryte w UI (ROI); runtime nadal nie egzekwuje |

**DoD P0.2:** każde pole w panelu ma efekt w `.py` **albo** jest ukryte / oznaczone jako niedostępne; catalog nie kłamie względem registry.

---

## 3. P1 — domknięcie operacyjne

### P1.1 E9 — statystyki w panelu

`data/stats.py` + CLI `stats` (`full_report`) **już istnieją**. Panel nie ma zakładki / endpointu z tymi samymi liczbami (ROADMAP wciąż mówi „brak E9” — kod CLI wyprzedza docs).

- Endpoint API = ten sam kontrakt co `cli stats --json`
- Zakładka: dziś / N dni / per plugin / limity
- Semantyka akcji = `limits.classify_action` (panel ≠ inny limier)

### P1.2 Resume checkpointu

`JobRunner._checkpoint` zapisuje `queue_state.json`, ale **nie wznawia** — docstring kłamie. Po STOP/crash kolejka od zera.

- Odczyt stanu przed pętlą; skip tasków już `ok`
- Jasna reguła resetu (nowa nazwa kolejki / świeży start)
- Test bez telefonu w `tests/test_plugins_runtime.py`

### P1.3 Login w prologu

`PROLOGUE_STEPS` = wake → unlock → ensure_ig. Brak `system.login_if_needed` (krok i CLI `login` już są). Po wylogowaniu sesja pada.

- Dodać login (+ opcjonalnie `enter_verification_code_if_needed`) do prologu
- Idempotentny skip gdy już zalogowany
- 2FA/captcha nadal kontrolowany fail (pełna warstwa → P3 / BACKLOG §10)

---

## 4. P2 — lukę produktowe (po P0/P1)

| # | Temat | Fakt w kodzie | Cel |
|---|---|---|---|
| **P2.1** | Like tiles | **ZROBIONE** — `_like_grid_tiles` (open→`post.like`→soft back); defaults ON | — |
| **P2.2** | Zasilanie kolejki profili | **ZROBIONE (raw)** — `source.kind=raw_profiles` + `mark_outcome`; candidates nadal bez promocji | Opcjonalnie pipeline raw→candidates |
| **P2.3** | Kalibracja E7 | Brak fingerprintów `hashtag_page` / `post_detail` / `post_likers_list`… (świadomie — zły FP psuje home) | Sesja kalibracyjna + kotwica `reels.comments_icon`; `RealScreensTests` pilnuje regresji |
| **P2.4** | L6 `recovery_identify` | Navigator kończy na L5 → `exhausted`; stub w Vision | Po L5: screenshot → AI → akcja; STUB = kontrolowany fallback |
| **P2.5** | `skip_pinned` / `cursor_persist` / martwe settings scrapera | UI w catalogu; scraper: skip_pinned = no-op; cursor_persist nieużywane w `impl/` | Albo podpiąć, albo ukryć w UI (ROI odroczone) |

### ROI code-review (2026-08-08) — **ZROBIONE** (rdzeń)

Warunkowy BACK; followers bez expand; trust `full_screen`; `gate_params`; exclude autora;
mniej dumpów geo; ukryty martwy catalog content_search.  
**Odroczone:** assert viewer, type_query dumps, `back_until`, dead settings scrapera, cache raw
→ [`RZECZY_DO_POPRAWY.md`](RZECZY_DO_POPRAWY.md) §4.

---

## 5. P3 — BACKLOG (świadomie później)

Szczegóły i „czemu nie teraz” w [`BACKLOG.md`](BACKLOG.md):

| Temat | BACKLOG | Kiedy |
|---|---|---|
| Telegram alerts | §10 / docs | Przy automacie bez ciągłego patrzenia w panel |
| Proxy / security module | §9 | Przy wielu klonach |
| SQLite limity + Supabase | §2, §7 | Gdy `events.jsonl` boli / panel 24/7 |
| Multi-device `slot_manager` | §4 (+ §3 normalizacja pakietu) | Po stabilnym E10 na 1 koncie |
| Humanizacja Bézier + telemetria | §5–6 | Po ostrzeżeniach IG / własne dane gestów |
| Onboard CLI / auto-kalibracja | §8 | 3. urządzenie lub update IG |
| Watchdog stucka (krok bez timeoutu) | §1 | Razem z / tuż po automacie |

Dodatkowo z review (security envelope): plaintext secrets, auth panelu, hardening `input text` — **przed** wystawieniem panelu poza localhost (patrz `RZECZY_DO_POPRAWY.md` §5).

---

## 6. Aktualizacja docs

Docs muszą dogonić kod (nie odwrotnie):

| Dokument | Co poprawić |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | E9/E10: CLI stats + scheduler już częściowo są; dopisać lukę „stats w panelu”; statusy pluginów |
| [`ARCHITEKTURA.md`](ARCHITEKTURA.md) | Checkpoint „wznawia” → „tylko zapis”; L6 jako plan, nie fakt; Telegram jako placeholder |
| [`BACKLOG.md`](BACKLOG.md) | Zostawić uzasadnienia; odhaczyć to, co weszło do P0–P2 tutaj |
| [`plugins/catalog.py`](plugins/catalog.py) | `status` = `ready` tam, gdzie jest `impl/` |
| Ten plik + STAN / RZECZY / CLI / DRZEWO | Trzymać spójność przy każdej większej zmianie (`target_scraper` / raw / expand / ROI) |

---

## 7. Definition of Done — „idealne narzędzie”

- [ ] **UI = runtime** — brak pól/przycisków, które nic nie robią lub kończą się 501
- [x] **Gate niszy** — `accepted=false` → AI engagement skip; discovery działa z panelu
- [ ] **7 pluginów** — kolejka PROD (`run-queue`), dry-run OK, live bez fałszywych ekranów
- [ ] **E9 w panelu** — te same agregaty co `cli stats` / `data/stats.py`
- [ ] **Resume** — przerwana kolejka wznawia się z `queue_state.json`
- [ ] **Prolog + login** — wylogowanie nie zabija sesji „w ciemno”
- [ ] **Like tiles / candidates** — like tiles ✓; raw w interact ✓; opcjonalna promocja candidates (P2.2)
- [ ] **Kalibracja E7** — dumpy/kotwice dla post.* bez psucia home (P2)
- [ ] **L6** — nieznany ekran ma ścieżkę AI albo uczciwy komunikat „brak L6” (P2)
- [ ] **Jeden orkiestrator** — panel *albo* `cli scheduler`, nie oba naraz
- [ ] **Docs zgodne z kodem** — ROADMAP / ARCHITEKTURA / BACKLOG / catalog
- [ ] **Security minimum** — panel tylko localhost (lub auth); sekrety nie w git; bezpieczne `input text`
- [ ] **P3** — dopiero gdy P0–P2 nie blokują codziennej pracy

---

*Kolejność: P0 → P1 → P2 → docs → P3. Nie listować tu statusu kont — to [`STAN_SYSTEMU.md`](STAN_SYSTEMU.md).*
