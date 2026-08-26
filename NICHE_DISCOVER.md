# `niche_discover` — discovery niszy z treści IG

**Status:** zaimplementowane (pipeline warstwowy).  
**Język:** odpowiedzi i komentarze w kodzie po polsku.  
**Runtime:** `plugins/impl/niche_discover.py`  
**Start:** Panel → Konto → **Nisza** → „Uruchom discovery”  
albo `python -m v2.cli run-queue --account=<alias> --plugin=niche_discover`

---

## 1. Cel

Z treści profilu (i opcjonalnie konkurencji) wypełnia `account.niche`:

| Pole | Zachowanie |
|------|------------|
| `description` / `audience` | **Zawsze nadpisane** nową analizą |
| `keywords` / `hashtags` | Z analizy; **dokładna liczba** z panelu (`ai.keywords_count` / `ai.hashtags_count`, default **8**). **keywords ≠ hashtags**: frazy bez `#` vs tagi IG z `#`; zakaz lustra „słowo” ↔ „#słowo”. Hashtagi z captionów (`observed`) mają priorytet przy limicie. |
| `exclusions` | **Zachowane** z istniejącej niszy (AI ich nie generuje) |
| `accepted` | Po discovery = `false` — zatwierdź zanim AI engagement korzysta z niszy |
| `last_ai_model` / `last_ai_tokens_in` / `last_ai_tokens_out` | Usage z całego przebiegu (panel + log) |

**Sync fraz** (checkbox obok `accepted`): append `#` i keywords do `content_search.phrases` bez duplikatów.

**Kadry:** źródło = żywy **scrcpy** (`ctx.fb`), fallback screenshot HTTP; log `niche_discover.frame_source`. Na dysk tylko crop / fit JPEG w pudełku **900×1950** (`fit_jpeg_box`, poprzednie scrcpy 720 × 1.25). Scrcpy: `max_size` default **900**. Composite rolek: `thumb_w=400` (320 × 1.25).

---

## 2. Pipeline

```
FAZA A (telefon) — bez AI:
  own → zbierz posty/rolki → JSON
  → HOME → search → competitor1 → zbierz → JSON
  → HOME → search → competitor2 → …
  → index.json

FAZA B (AI, telefon na home):
  dla każdego packa: posts → reels → account
  → merge (≥2 profile; own > competitor)
  → write_niche
```

JSON per konto: `data/accounts/<id>/frames/<session>/collect/{own|competitor_*}.json`
(frames paths + captions + bio tekstowe z hierarchii).

- **Posty (siatka):** od 1. kafelka, **bez filtra** post/film — każdy: **crop mediów** (bez oryginału full-screen na dysku) + opis jako tekst.
- **Rolki:** tylko zakładka Rolki; opis **tap-only** (dump tekstu / JSON caption) — **bez** screena rozwiniętego panelu opisu.
- **AI batch:** posty zbiorczo (`ai.batch_size`, default 4); rolki zawsze osobno.
- Modele w `account.ai_models`: `niche_discover_posts|reels|account|merge`.

---

## 3. Otwarcie kafelka (`post.open_from_grid`)

- `firm_tap` (~150 ms) — pewniejsze otwarcie niż krótki tap.
- Check: wyszliśmy z profilu (`still_on_profile_grid=false` + viewer).
- Jeśli nie → **powtórz TEN SAM kafelek** (`open_retries=2`).

---

## 4. Opis rolki (tap, nie swipe)

**Wspólna ścieżka** dla discovery i engagement (`reels_niche`):
`capture_reel_caption` → `_read_reel_caption_guarded` — **tylko TAP**
(jak stary `reels_core._tap_more_button`). Swipe po feedzie psuje rolkę.

Engagement woła to z `reels.watch` równolegle z budową composite
(`open_comments` domyślnie off — swipe/expand sheet tylko przy komentarzach).

1. Viewer (bez screena diagnostycznego panelu)  
2. `find_reel_caption_tap_target` → CAPTION_IDS / „…więcej” / strefa ~42%×83%  
3. Tap → dump tylko `CAPTION_IDS` (odrzuć like_count / UFI)  
4. `expand_more` w tekście opisu (**nie** `clips_ufi_more`)  
5. Checki po każdym geście; fail → `caption=""` + log `reason`

Eventy `niche_discover.reel_caption.*` trafiają do `events.jsonl`, ale są
**wyciszone w panelu** (żeby nie szumieć podczas `reels_niche`).

Szczegóły: §17 poniżej / docstring modułu.
Workflow engagement (flagi AI, live logi, pauzy):
[`REELS_NICHE_WORKFLOW.md`](REELS_NICHE_WORKFLOW.md).

---

## 5. Panel (Nisza)

- Pola edycji niszy + `accepted` + **Sync fraz**
- `Ile keywords` / `Ile hashtagów` (dokładna liczba)
- Readonly: `discovered_at`, model, tokeny IN/OUT
- Usunięte: sekcja „Zapis” / `merge_exclusions` (zaawansowane)

---

## 6. Pliki

| Plik | Rola |
|------|------|
| `plugins/impl/niche_discover.py` | Runtime |
| `plugins/catalog.py` | Ustawienia / defaults |
| `plugins/store.py` | `write_niche` |
| `core/composite.py` | `fit_jpeg_box`, stitch, siatki |
| `ai/prompts/niche_discover_{posts,reels,account,merge}.yml` | Prompty |
| `ai/vision.py` | Call + usage tokenów |
| `steps/post/open_from_grid.py` | firm_tap + retry |
| `steps/_expand_more.py` | „…więcej” (bez UFI) |
| `panel/static/index.html` | UI Nisza |

Testy: `tests/test_niche_discover.py`, `tests/test_expand_more.py`, `tests/test_ai_vision.py`

```bash
python -m unittest v2.tests.test_niche_discover -q
```

---

## 17. Odczyt opisu rolki (guardy)

1. **Viewer** — `clips_*`; opis tylko jako **dump tekstu** (bez JPG panelu).  
2. **Kandydat** — CAPTION_IDS → „…więcej” → fallback strefa.  
3. **TAP** (nie swipe) → check viewer + autor.  
4. **Expand** tekstowy + dump CAPTION_IDS.  
5. Truncated → drugi expand; BACK tylko gdy sami otworzyliśmy panel.

`comment_composer` na UFI **nie** oznacza otwartego panelu (marker = thread / bottom_sheet).

---

*Źródło prawdy dla `niche_discover` (2026-08-08; caption shared z reels_niche).*
