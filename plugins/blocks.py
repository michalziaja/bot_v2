"""Wspólne bloki ustawień — definiowane RAZ, referowane przez wiele pluginów.

`harvest`, `hot_interact`, `story_watch`, `comment_likes` powtarzają się w 4-5
pluginach. Gdyby każdy plugin miał własną kopię, pierwsza zmiana (np. dodanie
pola) rozjechałaby katalog. Tutaj są raz — plugin woła funkcję z prefiksem
i dostaje (fields, defaults).

Każda funkcja zwraca krotkę:
  fields   — lista pól do formularza (klucze to PEŁNE ścieżki od korzenia
             ustawień pluginu, żeby renderer nie musiał niczego składać)
  defaults — poddrzewo wartości domyślnych do wstawienia pod `prefix`

Typy pól rozumiane przez dashboard:
  bool, num, text, textarea, select, range, percent, tags, lines,
  multiselect, chance, likes_range, like_decision, json,
  tag_variants_map (mapa expression → list[#] w niche)

Flaga `advanced=True` na polu — panel chowa je w accordionie „Zaawansowane”
wewnątrz danej grupy (sekcji), nie w jednym globalnym bloku.
"""
from __future__ import annotations
from typing import Any


def _f(prefix: list[str], key: list[str], **kw) -> dict:
    """Pole formularza z pełną ścieżką klucza."""
    return {"key": [*prefix, *key], **kw}


def _nest(prefix: list[str], subtree: dict) -> dict:
    """Zawiń poddrzewo defaultów w ścieżkę `prefix`."""
    out: dict[str, Any] = {}
    cur = out
    for p in prefix[:-1]:
        cur[p] = {}
        cur = cur[p]
    if prefix:
        cur[prefix[-1]] = subtree
        return out
    return subtree


def mark_advanced(fields: list[dict], *suffixes: str) -> list[dict]:
    """Oznacz pola jako zaawansowane po ostatnim segmencie klucza."""
    want = set(suffixes)
    for f in fields:
        if f.get("key") and f["key"][-1] in want:
            f["advanced"] = True
    return fields


# ============================================================
# BUDGET — ile jednostek / ile czasu
# ============================================================
def budget(prefix=("budget",), *, unit_label="jednostek",
           unit_desc="Ile jednostek pracy wykonać w tym zadaniu.",
           items=(10, 25), minutes=20,
           items_label="Max jednostek", duration_label="Max czas (min)"):
    p = list(prefix)
    fields = [
        # Licznik jednostek — tylko int (step=1)
        _f(p, ["max_items"], type="range", label=items_label, unit=unit_label,
           step=1,
           desc=unit_desc + " Losowane z zakresu przy starcie zadania — "
                "stała liczba co sesję jest wykrywalnym wzorcem."),
        _f(p, ["max_duration_min"], type="num", label=duration_label, min=1,
           desc="Twardy limit czasu zadania w minutach. Zadanie kończy się "
                "czysto (powrót na ekran wyjściowy) nawet jeśli nie wyrobiło max_items."),
    ]
    return fields, _nest(p, {"max_items": {"min": items[0], "max": items[1]},
                             "max_duration_min": minutes})


# ============================================================
# GATE_CONTENT — bramka na licznikach, PRZED analizą AI
# ============================================================
def gate_content(prefix=("gate",), *, min_likes=100, max_likes=50000, min_comments=35,
                 hide_skip_authors: bool = False):
    """Bramka liczników. Lajki min–max to jedna kontrolka UX (`likes_range`).

    hide_skip_authors — pole zostaje w defaults (save), panel go nie rysuje
    (np. content_search: runtime nie egzekwuje skip_authors).
    """
    p = list(prefix)
    fields = [
        _f(p, ["min_likes"], type="likes_range", label="Lajki (min–max)",
           max_key=[*p, "max_likes"],
           desc="Zakres lajków treści. Poniżej min — pomijamy bez AI; powyżej max — "
                "odsiew viral/celebrity (tam lajkujący to przypadkowy ruch). "
                "Max = 0 oznacza brak górnego limitu."),
        _f(p, ["min_comments"], type="num", label="Min. komentarzy", min=0,
           desc="Minimalna liczba komentarzy. Niska liczba = mała szansa na sensowną "
                "listę komentujących do zebrania."),
        _f(p, ["on_unknown"], type="select", label="Gdy liczniki nieczytelne",
           options=["skip", "pass"],
           option_labels={"skip": "Pomiń treść", "pass": "Przepuść mimo braku danych"},
           advanced=True,
           desc="Co zrobić gdy liczniki są nieczytelne z dumpu."),
        _f(p, ["skip_ads"], type="bool", label="Pomijaj reklamy",
           advanced=True,
           desc="Pomijaj treści sponsorowane (wykrywane po markerze 'Sponsorowane')."),
        _f(p, ["skip_authors"], type="tags", label="Pomijani autorzy",
           advanced=True,
           hidden=hide_skip_authors,
           desc="Autorzy zawsze pomijani (konkurencja, konta własne, blacklist)."),
    ]
    return fields, _nest(p, {
        "min_likes": min_likes, "max_likes": max_likes, "min_comments": min_comments,
        "on_unknown": "skip", "skip_ads": True, "skip_authors": [],
    })


# ============================================================
# AI_MATCH — jak AI ma oceniać treść/profil
# ============================================================
# Dwa modele pracy, bo konta dzielą się na dwa typy:
#
#   niche_match  — konto ma OSTRO zdefiniowaną niszę. Szukamy pozytywnego
#                  dopasowania: treść musi trafić w niszę, żeby wejść
#                  w interakcję. Wykluczenia odrzucają pojedyncze pozycje.
#
#   exclude_only — konto ogólne. Nie wymagamy trafienia w niszę; odrzucamy
#                  wyłącznie to, co pasuje do wykluczeń, a z reszty wybieramy
#                  wg `pick_strategy` (najlepsze albo losowo) — TYLKO siatka.
#
# W OBU trybach wykluczenie NIE dyskwalifikuje całego profilu — wyrzuca tylko
# tę jedną pozycję. Profil odpada dopiero gdy wykluczenia zajmują zbyt dużą
# część widocznej treści (`max_excluded_ratio`).
STRICTNESS = ["loose", "balanced", "strict"]
STRICTNESS_LABELS = {
    "loose": "Luźna", "balanced": "Zrównoważona", "strict": "Ścisła",
}


def ai_match(prefix=("ai",), *, kind="content", mode="niche_match",
             min_score=55, tiles=False, enabled_label="Analiza AI",
             hide_enabled: bool = False, hide_all: bool = False):
    """kind: 'content' (rolka/post — jeden wynik) albo 'grid' (siatka kafelków).

    hide_enabled=True — pole enabled zostaje w katalogu (defaults/save), ale
    panel go nie rysuje (np. gdy steruje nim `like_decision`).
    hide_all=True — ukryj cały blok AI w UI (martwy kontrakt bez runtime).
    """
    p = list(prefix)
    hid = hide_all
    fields = [
        _f(p, ["enabled"], type="bool", label=enabled_label,
           hidden=hide_enabled or hide_all,
           desc="Wyłączenie pomija ocenę AI — wtedy decydują same liczniki "
                "i ustawienia szansy."),
        _f(p, ["mode"], type="select", label="Tryb AI",
           options=["niche_match", "exclude_only"],
           option_labels={
               "niche_match": "Dopasowanie do grupy docelowej" if kind == "grid"
               else "Dopasowanie do niszy",
               "exclude_only": "Tylko wykluczenia",
           },
           advanced=True, hidden=hid,
           desc=("niche_match = profil MUSI wyglądać na grupę docelową "
                 "(nie musi tworzyć w niszy). exclude_only = tylko wykluczenia."
                 if kind == "grid" else
                 "niche_match = treść MUSI trafić w niszę. exclude_only = odrzucamy "
                 "tylko wykluczenia (konto ogólne)."),
        ),
        _f(p, ["strictness"], type="select", label="Sztywność",
           options=STRICTNESS, option_labels=STRICTNESS_LABELS,
           advanced=True, hidden=hid,
           desc="Jak sztywno AI ma trzymać się definicji niszy i wykluczeń."),
    ]
    d = {"enabled": True, "mode": mode, "strictness": "balanced"}

    if kind == "content":
        fields += [
            _f(p, ["min_niche_score"], type="num", label="Min. wynik niszy",
               min=0, max=100, advanced=True, hidden=hid,
               desc="TYLKO w trybie niche_match: minimalny wynik zgodności (0-100)."),
            _f(p, ["allow_verdict"], type="multiselect", label="Akceptowane werdykty",
               options=["match", "partial", "off"],
               option_labels={"match": "match", "partial": "partial", "off": "off"},
               advanced=True, hidden=hid,
               desc="TYLKO w trybie niche_match: które werdykty akceptujemy."),
        ]
        d.update({"min_niche_score": min_score, "allow_verdict": ["match", "partial"]})
    else:
        # siatka profilu: audience_fit, nie liczba kafelków niszowych
        fields += [
            _f(p, ["allow_verdict"], type="multiselect",
               label="Akceptowane audience_fit",
               options=["match", "partial", "off"],
               option_labels={"match": "match", "partial": "partial", "off": "off"},
               advanced=True, hidden=hid,
               desc="Które werdykty grupy docelowej przepuszczają profil "
                    "(domyślnie match + partial)."),
            _f(p, ["min_niche_tiles"], type="num", label="Min. kafelków niszy", min=0,
               advanced=True, hidden=True,
               desc="Legacy: tylko gdy AI nie zwróci audience_fit."),
            _f(p, ["min_niche_ratio"], type="num", label="Min. udział niszy",
               min=0, max=1, step=0.05, advanced=True, hidden=True,
               desc="Legacy: tylko gdy AI nie zwróci audience_fit."),
            _f(p, ["min_valid_tiles"], type="num", label="Min. kafelków po filtrze", min=0,
               advanced=True, hidden=hid,
               desc="TYLKO exclude_only: ile kafelków musi przejść wykluczenia."),
            _f(p, ["tiles_count"], type="num", label="Ile kafelków do AI", min=3, max=18,
               advanced=True, hidden=hid,
               desc="Ile kafelków siatki wysłać do oceny (domyślnie 9 = pierwsze 3×3)."),
            _f(p, ["scrolls_to_grid"], type="num", label="Przewinięcia przed zrzutem",
               min=0, max=5, advanced=True, hidden=hid,
               desc="Ile razy ZAWSZE przewinąć profil w dół przed dumpem siatki "
                    "(jak stary max_scroll_for_grid=1). Chowa avatar/bio/highlighty; "
                    "0 tylko do diagnostyki — bez scrolla AI myli highlighty z postami."),
        ]
        d.update({"allow_verdict": ["match", "partial"],
                  "min_niche_tiles": 0, "min_niche_ratio": 0.0, "min_valid_tiles": 3,
                  "tiles_count": 9, "scrolls_to_grid": 1})

    # Osobne listy: rolki vs kafelki profilu — discovery NIE wypełnia
    # niche.exclusions; pusta lista pluginu = brak wykluczeń (albo ręczne
    # niche.exclusions jako rzadki fallback).
    if kind == "grid":
        excl_label = "Wykluczenia postów"
        excl_desc = (
            "Motywy dyskwalifikujące KONKRETNY kafelek (np. dzieci, rodzina, "
            "para, ślub) — nie cały profil. Discovery niszy ich nie generuje. "
            "Pusta lista = fallback do ręcznych niche.exclusions (jeśli są)."
        )
    else:
        excl_label = "Wykluczenia rolek"
        excl_desc = (
            "Motywy dyskwalifikujące rolkę w feedzie. Osobna lista od wykluczeń "
            "na profilu. Discovery niszy ich nie generuje. "
            "Pusta lista = fallback do ręcznych niche.exclusions (jeśli są)."
        )
    fields += [
        _f(p, ["exclusions"], type="tags", label=excl_label,
           hidden=hid, desc=excl_desc),
        _f(p, ["max_excluded_ratio"], type="num", label="Max udział wykluczeń",
           min=0, max=1, step=0.05, advanced=True, hidden=hid,
           desc="Odpada, gdy wykluczenia zajmują WIĘCEJ niż taki udział. "
                "1.0 = nigdy nie odrzucaj z tego powodu."),
    ]
    d.update({"exclusions": [], "max_excluded_ratio": 0.5})

    # Strategia wyboru ma sens tylko przy SIATCE (wiele kafelków). Przy rolce
    # jest jedna treść — pass/fail, nie ma puli do sortowania.
    if kind == "grid":
        fields += [
            _f(p, ["pick_strategy"], type="select", label="Strategia wyboru",
               options=["best", "random"],
               option_labels={"best": "Najlepsze", "random": "Losowo"},
               advanced=True, hidden=hid,
               desc="Jak wybierać kafelki do polubienia spośród tych, które przeszły."),
        ]
        # Losowo z puli should_like — zgodnie z pipeline profile_interact
        d["pick_strategy"] = "random"

    fields += [
        _f(p, ["prompt_override"], type="textarea", label="Własny prompt", rows=8,
           advanced=True, hidden=hid,
           desc="Własna treść promptu. Puste = domyślny z v2/ai/prompts/."),
    ]
    d["prompt_override"] = ""
    return fields, _nest(p, d)


# ============================================================
# AI_INPUTS — źródła do Vision (klatki / opis / komentarze)
# ============================================================
def ai_inputs(prefix=("ai",), *, frames=2, frame_interval_s=0.5,
              use_frames=True, read_caption=True, read_top_comments=False,
              save_frames=False):
    """Niezależne flagi wejść AI — spójne z reels.watch / analyze_frames_ai.

    use_frames + read_caption mogą być ON/OFF osobno (tylko Vision / tylko opis /
    oba). Przy włączonym AI i obu OFF analiza jest pomijana (brak wejść).
    """
    p = list(prefix)
    fields = [
        _f(p, ["use_frames"], type="bool", label="Analizuj klatki (Vision)",
           desc="Zbieraj klatki i wyślij composite do AI. Wyłącz = ocena bez "
                "obrazu (np. tylko opis). Razem z „Czytaj opis”: tylko obraz / "
                "tylko opis / oba. Oba OFF przy włączonym AI = pominięcie analizy."),
        _f(p, ["frames"], type="num", label="Liczba klatek",
           min=1, max=12,
           desc="Ile klatek pobrać z rolki do sklejenia w obraz dla AI "
                "(tylko gdy „Analizuj klatki” jest włączone)."),
        _f(p, ["frame_interval_s"], type="num",
           label="Odstęp między klatkami (s)", min=0, max=5, step=0.1,
           desc="Pauza między kolejnymi klatkami (np. 0.5 = pół sekundy)."),
        _f(p, ["save_frames"], type="bool",
           label="Zapisuj pojedyncze klatki", advanced=True,
           desc="Zapisz też pojedyncze klatki (dużo miejsca) — domyślnie wyłączone."),
        _f(p, ["read_caption"], type="bool", label="Czytaj opis",
           desc="Rozwiń i odczytaj opis rolki — trafia do promptu AI. "
                "Możesz włączyć sam opis (bez klatek) albo razem z klatkami."),
        _f(p, ["read_top_comments"], type="bool",
           label="Czytaj top komentarze", advanced=True,
           desc="Otwiera sheet komentarzy przed AI (wolne). Domyślnie wyłączone — "
                "opis i klatki zwykle wystarczą."),
    ]
    return fields, _nest(p, {
        "use_frames": use_frames,
        "frames": frames,
        "frame_interval_s": frame_interval_s,
        "save_frames": save_frames,
        "read_caption": read_caption,
        "read_top_comments": read_top_comments,
    })


# ============================================================
# HARVEST — zbieranie username-ów do raw_profiles.jsonl
# ============================================================
def harvest(prefix=("harvest",), *, enabled=True, collect=(10, 20),
            source_tag="auto", label_what="profili",
            enabled_label: str | None = None):
    p = list(prefix)
    en_lbl = enabled_label or f"Zbieraj {label_what}"
    fields = [
        _f(p, ["enabled"], type="bool", label=en_lbl,
           desc=f"Zbieraj username-y {label_what} do raw_profiles.jsonl "
                "(pula dla późniejszego profile_interact)."),
        # Licznik sztuk — int
        _f(p, ["collect"], type="range", label="Ile zbierać", unit="szt.",
           step=1, advanced=True,
           desc="Ile profili zebrać z jednej listy. Losowane z zakresu."),
        _f(p, ["detect_story_ring"], type="bool", label="Wykrywaj ring relacji",
           advanced=True,
           desc="Wykrywaj aktywną relację (ring wokół awatara) przy każdym profilu."),
        _f(p, ["dedup_days"], type="num", label="Dedup (dni)", min=0,
           advanced=True,
           desc="Nie zapisuj profili widzianych w ciągu ostatnich N dni. 0 = bez dedup."),
        _f(p, ["source_tag"], type="text", label="Etykieta źródła",
           advanced=True,
           desc="Etykieta źródła w raw_profiles.jsonl. 'auto' = plugin ustawia sam."),
    ]
    return fields, _nest(p, {
        "enabled": enabled,
        "collect": {"min": collect[0], "max": collect[1]},
        "detect_story_ring": True,
        "dedup_days": 30,
        "source_tag": source_tag,
    })


# ============================================================
# HOT_INTERACT — interakcja "na gorąco" z listy lajkujących/komentujących
# ============================================================
def hot_interact(prefix=("hot_interact",), *, enabled=False, count=(1, 3)):
    p = list(prefix)
    fields = [
        _f(p, ["enabled"], type="bool", label="Hot interact",
           advanced=True,
           desc="Wchodź na profile bezpośrednio z tej listy, bez czekania na "
                "osobne zadanie profile_interact."),
        # Licznik profili — int
        _f(p, ["count"], type="range", label="Ile profili (hot)", unit="profili",
           step=1, advanced=True,
           desc="Z iloma profilami z listy wejść w interakcję."),
        _f(p, ["prefer_ring"], type="bool", label="Preferuj ring",
           advanced=True,
           desc="Profile z aktywną relacją pierwsze."),
        _f(p, ["require_ring"], type="bool", label="Wymagaj ring",
           advanced=True,
           desc="Pomijaj wszystkich bez aktywnej relacji."),
        _f(p, ["skip_private"], type="bool", label="Pomijaj prywatne",
           advanced=True,
           desc="Wyjdź z profilu natychmiast gdy okaże się prywatny."),
        _f(p, ["full_procedure"], type="bool", label="Pełna procedura",
           advanced=True,
           desc="WYŁ = skrócona (~20 s). WŁ = pełna profile_interact (~75 s)."),
        _f(p, ["overrides"], type="json", label="Overrides profile_interact", rows=3,
           advanced=True,
           desc='Punktowe nadpisanie ustawień, np. {"follow": {"chance": {"mode":"p","value":0.5}}}.'),
    ]
    return fields, _nest(p, {
        "enabled": enabled,
        "count": {"min": count[0], "max": count[1]},
        "prefer_ring": True, "require_ring": False, "skip_private": True,
        "full_procedure": False, "overrides": {},
    })


# ============================================================
# STORY_WATCH — oglądanie relacji
# ============================================================
def story_watch(prefix=("story_watch",), *, segments=(2, 4), watch_story=(3, 7),
                allow_like=True, advanced_all: bool = False,
                basic_keys: frozenset | set | None = None):
    """Domyślnie: segments w basic, reszta advanced.

    advanced_all=True — wszystko advanced (chyba że podasz basic_keys).
    basic_keys — jawne wyjątki trzymane w podstawowych.
    """
    p = list(prefix)
    basic = set(basic_keys) if basic_keys is not None else (
        set() if advanced_all else {"segments"}
    )

    def _adv(name: str) -> bool:
        return name not in basic

    fields = [
        # Segmenty = sztuki (int); czas na segment = sekundy (float OK)
        _f(p, ["segments"], type="range", label="Segmenty relacji", unit="szt.",
           step=1, advanced=_adv("segments"),
           desc="Ile segmentów relacji obejrzeć u jednej osoby. Losowane per osoba."),
        _f(p, ["watch_story"], type="range", label="Czas na segment", unit="s",
           step=0.1, advanced=_adv("watch_story"),
           desc="Ile sekund na segment relacji przed przejściem dalej."),
        _f(p, ["skip_sponsored"], type="bool", label="Pomijaj sponsorowane",
           advanced=_adv("skip_sponsored"),
           desc="Wykrywaj segmenty sponsorowane i przechodź je swipe'em w bok."),
    ]
    d = {"segments": {"min": segments[0], "max": segments[1]},
         "watch_story": {"min": watch_story[0], "max": watch_story[1]},
         "skip_sponsored": True}
    if allow_like:
        fields.append(
            _f(p, ["like_chance_percent"], type="percent",
               label="Szansa lajka relacji %",
               advanced=_adv("like_chance_percent"),
               desc="Szansa na polubienie segmentu (double-tap). 0 = nigdy.")
        )
        d["like_chance_percent"] = 0
    return fields, _nest(p, d)


# ============================================================
# COMMENT_LIKES — lajkowanie komentarzy pod treścią
# ============================================================
def comment_likes(prefix=("like_comments",), *, hide: bool = False):
    """hide=True — defaults zostają (save), panel nie rysuje (martwy kontrakt)."""
    p = list(prefix)
    fields = [
        _f(p, ["enabled"], type="bool", label="Lajkuj komentarze",
           advanced=True, hidden=hide,
           desc="Lajkuj komentarze pod treścią — tani sygnał zainteresowania."),
        # Licznik komentarzy — int
        _f(p, ["count"], type="range", label="Ile komentarzy lajkować", unit="szt.",
           step=1, advanced=True, hidden=hide,
           desc="Ile komentarzy polubić pod jedną treścią."),
        _f(p, ["min_comment_likes"], type="num", label="Min. lajków komentarza", min=0,
           advanced=True, hidden=hide,
           desc="Lajkuj tylko komentarze mające już co najmniej tyle lajków."),
        _f(p, ["extra_chance_percent"], type="percent", label="Dodatkowa szansa %",
           advanced=True, hidden=hide,
           desc="Dodatkowa szansa na każdy kwalifikujący się komentarz."),
    ]
    return fields, _nest(p, {
        "enabled": False, "count": {"min": 1, "max": 3},
        "min_comment_likes": 5, "extra_chance_percent": 40,
    })


# ============================================================
# PHRASE_LIST — lista fraz / targetów z kursorem
# ============================================================
def phrase_list(prefix=("phrases",), *, label="Frazy", what="fraz",
                per_session=(1, 3), placeholder="",
                list_label: str | None = None,
                per_session_label: str | None = None,
                source_aware: bool = False):
    """Lista fraz. `source_aware=True` → trzy listy + kolejność least_recent."""
    p = list(prefix)
    if source_aware:
        list_field = {
            "key": list(p),
            "type": "source_phrases",
            "source_key": ["source"],
            "label": list_label or f"Lista {what}",
            "placeholder": placeholder,
            "desc": (
                "Trzy listy zawsze widoczne (hashtagi / słowa / lokalizacje). "
                "Źródło na sesję wybierasz na karcie zadania w kolejce."
            ),
        }
    else:
        list_field = _f(p, ["list"], type="lines",
                        label=list_label or f"Lista {what}",
                        placeholder=placeholder,
                        desc=f"Lista {what} — jedna pozycja na linię.")
    order_opts = ["random", "sequential"]
    order_labels = {"random": "Losowa", "sequential": "Po kolei"}
    order_desc = "random = tasuj; sequential = od góry listy."
    if source_aware:
        order_opts.append("least_recent")
        order_labels["least_recent"] = "Najdawniej sprawdzane"
        order_desc = ("random = tasuj; sequential = od góry listy; "
                      "least_recent = nigdy / najstarsze last_checked pierwsze.")
    fields = [
        list_field,
        _f(p, ["order"], type="select", label="Kolejność",
           options=order_opts,
           option_labels=order_labels,
           advanced=True,
           desc=order_desc),
        # Licznik pozycji na sesję — int
        _f(p, ["per_session"], type="range",
           label=per_session_label or f"Ile {what} / sesja", unit="szt.",
           step=1,
           desc=f"Ile {what} przerobić w jednym uruchomieniu pluginu."),
        _f(p, ["cursor_persist"], type="bool", label="Zapamiętuj kursor",
           advanced=True,
           desc="Zapamiętuj pozycję na liście między sesjami."),
    ]
    return fields, _nest(p, {
        "list": [], "hashtags": [], "keywords": [], "locations": [],
        "order": "random",
        "per_session": {"min": per_session[0], "max": per_session[1]},
        "cursor_persist": True,
    })



# ============================================================
# CHANCE — prawdopodobieństwo akcji (stałe albo z formuły)
# ============================================================
# Format: {"mode": "p"|"formula", "value": <float|str>}
#
# Tryb jest JAWNYM polem, a nie wyborem klucza ({p:...} vs {formula:...}).
# Przy tamtym wariancie zmiana trybu w panelu zostawiała po scaleniu OBA
# klucze naraz i nie dało się stwierdzić, który obowiązuje — merge głęboki
# scala słowniki, a nie podmienia je w całości.
def chance(mode="p", value=1.0) -> dict:
    return {"mode": mode, "value": value}


def chance_field(prefix: list, key: str, *, label: str, desc: str,
                 advanced: bool = False, allow_formula: bool = True):
    """Szansa na akcję. `allow_formula=False` = tylko suwak % (rozgrzewka, bez AI)."""
    return _f(list(prefix), [key], type="chance", label=label, desc=desc,
              advanced=advanced, allow_formula=allow_formula)


def like_decision_field(*, ai_key: list, chance_key: list,
                        label: str = "Decyzja o lajku",
                        default_mode: str = "ai_then_p",
                        desc: str | None = None) -> dict:
    """Pole UX mapowane na ai.enabled + *.chance — bez wpisu w defaults.

    default_mode: 'ai_only' | 'chance_only' | 'ai_then_p'
    Klucz `__ui.like_decision` jest tylko kotwicą w formularzu (ux_only).
    """
    return {
        "key": ["__ui", "like_decision"],
        "type": "like_decision",
        "label": label,
        "ai_key": list(ai_key),
        "chance_key": list(chance_key),
        "default_mode": default_mode,
        "ux_only": True,
        "desc": desc or (
            "Tylko AI = lajk po pass AI (szansa 100%). "
            "Tylko % = bez Vision, stała szansa. "
            "AI, potem % = bramka AI, potem rzut chance / formuła."
        ),
    }
