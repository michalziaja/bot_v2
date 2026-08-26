"""Katalog pluginów — pełna deklaracja: co istnieje i co da się ustawić.

To jest ŹRÓDŁO PRAWDY dla dashboardu. Panel czyta ten katalog przez
`GET /api/plugins` i generuje z niego formularze — nie ma ręcznego HTML-a
per plugin.

Na tym etapie katalog jest deklaratywny: brak `run()`, brak wykonania.
Chodzi o to, by zobaczyć KOMPLET ustawień w jednym miejscu, zanim
zacznie powstawać logika.

Pole `status`:
  ready   — plugin zaimplementowany i przetestowany
  partial — kroki istnieją, brakuje pętli/plugin wrappera
  planned — nic jeszcze nie ma

Układ UI (basic / advanced):
  Accordion „Zaawansowane” jest **per grupa (sekcja)**, nie globalnie.
  Flaga `advanced=True` na polu (lub na całej sekcji → dziedziczą pola).
  Etykiety `label` są po polsku; klucz katalogu zostaje w `key` / hover.
"""
from __future__ import annotations

from v2.plugins import blocks as B


def _merge(base: dict, over: dict) -> dict:
    """Głęboki merge — dla składania defaultów z bloków."""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class _Builder:
    """Zbiera sekcje i defaults, żeby definicja pluginu czytała się liniowo."""

    def __init__(self):
        self.sections: list[dict] = []
        self.defaults: dict = {}

    def section(self, title: str, desc: str, *parts, advanced: bool = False):
        """parts: albo lista pól, albo krotka (fields, defaults) z bloku.

        `advanced=True` — wszystkie pola sekcji dostają advanced (per-grupa
        accordion w panelu). Pojedyncze pola mogą mieć własną flagę.
        """
        fields: list = []
        for p in parts:
            if isinstance(p, tuple):
                f, d = p
                fields.extend(f)
                self.defaults = _merge(self.defaults, d)
            else:
                fields.extend(p)
        if advanced:
            for f in fields:
                f.setdefault("advanced", True)
        self.sections.append({
            "title": title, "desc": desc, "fields": fields,
            # sekcja.advanced zostawione dla kompatybilności; renderer patrzy na pola
            "advanced": False,
        })
        return self

    def vals(self, d: dict):
        """Defaults dla pól zadeklarowanych ręcznie (nie z bloku)."""
        self.defaults = _merge(self.defaults, d)
        return self


def _plugin(id, label, group, status, entry, exit, desc, b: _Builder, *,
            queueable: bool = True, ui_hidden: bool = False) -> dict:
    """Deklaracja pluginu.

    queueable=False — nie pojawia się w pickerze scenariuszy / harmonogramie.
    ui_hidden=True  — ukryty na liście operacyjnej Pluginy (konfiguracja
    tylko z innego miejsca, np. Ustawienia konta → Nisza).
    """
    return {
        "id": id, "label": label, "group": group, "status": status,
        "entry_screen": entry, "exit_screen": exit, "desc": desc,
        "queueable": queueable,
        "ui_hidden": ui_hidden,
        "sections": [{"title": "Włączenie", "desc": "", "advanced": False, "fields": [
            {"key": ["enabled"], "type": "bool", "label": "Plugin włączony",
             "desc": "Czy plugin jest dostępny dla tego konta. Wyłączony nie da się "
                     "dodać do kolejki."},
        ]}] + b.sections,
        "defaults": _merge({"enabled": False}, b.defaults),
    }


# ============================================================
# P1 — feed_browser
# ============================================================
_b = _Builder()
_b.section(
    "Scrollowanie feedu",
    "Przewijanie strony głównej bez żadnej interakcji. Scroll jest zawsze aktywny "
    "gdy plugin działa (brak ON/OFF). Lajkowanie jest wyłączone NA STAŁE w kodzie.",
    B.budget(("budget",), unit_label="postów",
             unit_desc="Ile postów feedu przewinąć.", items=(10, 25), minutes=8,
             items_label="Max postów", duration_label="Max czas (min)"),
    [
        # Dystans swipe'a (ułamek wysokości ekranu). Runtime: feed.scroll + feed_browser.
        {"key": ["scroll", "distance"], "type": "range", "label": "Długość scrolla",
         "unit": "ekranu", "step": 0.01,
         "desc": "Jak daleko przewinąć (ułamek wysokości ekranu). Min ≈ cały post w dół; "
                 "max = czasem więcej (humanizacja). Dziś ~0.40 to stary hardcode 0.75→0.35."},
        {"key": ["scroll", "dwell_mode"], "type": "select", "label": "Tryb patrzenia",
         "options": ["content_aware", "fixed"],
         "option_labels": {"content_aware": "Wg treści opisu", "fixed": "Stały zakres"},
         "advanced": True,
         "desc": "content_aware = czas patrzenia zależy od długości opisu; fixed = zakres poniżej."},
        {"key": ["scroll", "dwell_fallback_s"], "type": "range",
         "label": "Fallback czasu patrzenia", "unit": "s", "step": 0.1, "advanced": True,
         "desc": "Zakres używany w trybie fixed oraz gdy nie udało się odczytać opisu."},
        {"key": ["scroll", "long_pause_every_n"], "type": "num",
         "label": "Długa przerwa co N postów", "min": 0, "advanced": True,
         "desc": "Co ile postów zrobić dłuższą przerwę. 0 = bez długich przerw. "
                 "Długość przerwy pochodzi z profilu humanizera (kind `long_feed`)."},
    ],
).vals({"scroll": {
    "distance": {"min": 0.40, "max": 0.55},
    "dwell_mode": "content_aware",
    "dwell_fallback_s": {"min": 2.0, "max": 7.0},
    "long_pause_every_n": 8,
}})
_b.section(
    "Relacje z tacki",
    "Po scrollowaniu bot może obejrzeć relacje z paska na górze home. "
    "Przechodzi między osobami swipe'em w lewo. Własne konto jest pomijane.",
    [
        {"key": ["tray_stories", "enabled"], "type": "bool", "label": "Oglądaj relacje z tacki",
         "desc": "Czy oglądać relacje po zakończeniu scrollowania."},
        {"key": ["tray_stories", "max_users"], "type": "range", "label": "Max osób",
         "unit": "osób", "step": 1,
         "desc": "Ilu kolejnych użytkowników obejrzeć w jednym podejściu."},
    ],
    B.story_watch(("tray_stories", "watch"), segments=(2, 4), watch_story=(3, 7),
                  allow_like=False),
).vals({"tray_stories": {"enabled": True, "max_users": {"min": 3, "max": 8}}})

FEED_BROWSER = _plugin(
    "feed_browser", "Przeglądanie strony głównej", "warmup", "ready",
    "home_feed", "home_feed",
    "Scrollowanie feedu + oglądanie relacji z tacki. Zero interakcji: bez lajków, "
    "bez follow-ów, bez wchodzenia na profile. Najbezpieczniejszy plugin — do "
    "rozgrzewania konta i przerywania sesji między akcjami.",
    _b,
)


# ============================================================
# P2 — reels_niche
# ============================================================
_b = _Builder()
_b.section(
    "Budżet", "",
    B.budget(("budget",), unit_label="rolek",
             unit_desc="Ile rolek obejrzeć.", items=(15, 30), minutes=25,
             items_label="Max rolek", duration_label="Max czas (min)"),
)
_b.section(
    "Filtr (bramka bez AI)",
    "Odsiew na samych licznikach, ZANIM uruchomimy analizę AI.",
    B.gate_content(("gate",)),
)
_b.section(
    "Lajkowanie", "",
    [
        {"key": ["like_reel", "enabled"], "type": "bool", "label": "Lajkuj rolki",
         "desc": "Czy lajkować rolki, które przeszły bramkę i ocenę AI."},
        B.like_decision_field(
            ai_key=["ai", "enabled"], chance_key=["like_reel", "chance"],
            default_mode="ai_then_p",
        ),
        B.chance_field(["like_reel"], "chance", label="Szansa / formuła lajka",
                       desc="Szczegóły szansy (stała albo formuła z wyniku AI).",
                       advanced=True),
    ],
).vals({"like_reel": {"enabled": True, "chance": B.chance("formula", "score/100")}})
_b.section(
    "Klatki",
    "Zrzuty z rolki do AI — bez osobnego czasu oglądania. Po ostatniej klatce "
    "od razu opis i analiza. Możesz wyłączyć klatki i oceniać tylko opis "
    "(albo odwrotnie) w sekcji Analiza AI.",
    [
        # use_frames: zbieranie + composite → Vision z obrazem (niezależnie od opisu)
        {"key": ["ai", "use_frames"], "type": "bool", "label": "Analizuj klatki",
         "desc": "Zbieraj klatki i wyślij composite do AI. Wyłącz = ocena tylko "
                 "z opisu (text-only). Razem z „Czytaj opis” możesz wybrać: "
                 "tylko obraz / tylko opis / oba. Oba OFF przy włączonym AI = "
                 "pominięcie analizy (brak wejść)."},
        {"key": ["ai", "frames"], "type": "num", "label": "Liczba klatek",
         "min": 1, "max": 12,
         "desc": "Ile klatek pobrać z rolki do sklejenia w obraz dla AI "
                 "(tylko gdy „Analizuj klatki” jest włączone)."},
        {"key": ["ai", "frame_interval_s"], "type": "num",
         "label": "Odstęp między klatkami (s)", "min": 0, "max": 5, "step": 0.1,
         "desc": "Pauza między kolejnymi klatkami (np. 0.5 = pół sekundy)."},
        {"key": ["ai", "save_frames"], "type": "bool",
         "label": "Zapisuj pojedyncze klatki", "advanced": True,
         "desc": "Zapisz też pojedyncze klatki (dużo miejsca) — domyślnie wyłączone."},
    ],
).vals({"ai": {"use_frames": True, "frames": 2, "frame_interval_s": 0.5,
               "save_frames": False}})
_b.section(
    "Oglądanie",
    "Czas oglądania rolki — biznesowy parametr pluginu, nie ogólna pauza ludzka. "
    "Używany przez tryb `use_watch_duration` kroku `reels.watch` (np. flow YAML / rozgrzewka).",
    [
        {"key": ["watch_reel"], "type": "range", "label": "Czas oglądania rolki",
         "unit": "s", "step": 0.1, "advanced": True,
         "desc": "Ile sekund bot 'ogląda' jedną rolkę w trybie czasu oglądania. "
                  "Dziś reels_niche używa klatek; to ustawienie dla legacy / innych pluginów."},
    ],
).vals({"watch_reel": {"min": 2, "max": 3}})
_b.section(
    "Analiza AI",
    "Klatki i/lub opis idą do modelu względem niszy konta "
    "(Ustawienia konta → Nisza). Włączenie AI steruje też „Decyzja o lajku”.",
    [
        # read_caption: tekst do promptu — niezależnie od use_frames
        {"key": ["ai", "read_caption"], "type": "bool", "label": "Czytaj opis",
         "desc": "Rozwiń i odczytaj opis rolki — trafia do promptu AI. "
                 "Możesz włączyć sam opis (bez klatek) albo razem z klatkami. "
                 "Gdy AI włączone, a klatki i opis wyłączone — analiza jest "
                 "pomijana (brak wejść do modelu)."},
        {"key": ["ai", "read_top_comments"], "type": "bool",
         "label": "Czytaj top komentarze", "advanced": True,
         "desc": "Otwiera sheet komentarzy przed AI (wolne). Domyślnie wyłączone — opis i # wystarczą."},
    ],
    B.ai_match(("ai",), kind="content", mode="niche_match", min_score=55,
               hide_enabled=True),
).vals({"ai": {"read_caption": True, "read_top_comments": False}})
_b.section(
    "Lista lajkujących",
    "Otwarcie listy osób, które polubiły rolkę — najlepsze źródło targetów.",
    B.harvest(("likers", "harvest"), collect=(10, 20),
              source_tag="reel_likers", label_what="lajkujących",
              enabled_label="Zbieraj lajkujących"),
    [
        {"key": ["likers", "min_niche_score_to_harvest"], "type": "num",
         "label": "Min. wynik AI do harvestu", "min": 0, "max": 100,
         "advanced": True,
         "desc": "Zbieraj lajkujących tylko z rolek o wyniku AI co najmniej tyle."},
    ],
    B.hot_interact(("likers", "hot_interact")),
).vals({"likers": {"min_niche_score_to_harvest": 70}})
_b.section(
    "Komentarze",
    "Panel komentarzy: zbieranie autorów, lajkowanie komentarzy i hot interact.",
    B.harvest(("comments", "harvest"), collect=(5, 15),
              source_tag="reel_commenters", label_what="komentujących",
              enabled_label="Zbieraj komentujących"),
    B.comment_likes(("comments", "like_comments")),
    B.hot_interact(("comments", "hot_interact"), count=(1, 2)),
)

REELS_NICHE = _plugin(
    "reels_niche", "Rolki — nisza", "engagement", "ready",
    "home_feed", "home_feed",
    "Główny plugin pozyskiwania. Ogląda rolki, ocenia zgodność z niszą przez AI, "
    "lajkuje, zbiera lajkujących i komentujących do bazy targetów i opcjonalnie "
    "wchodzi z częścią z nich w interakcję od razu.",
    _b,
)


# ============================================================
# P3 — content_search
# ============================================================
_b = _Builder()
_b.section(
    "Źródło i frazy",
    "Wyszukiwarka IG: hashtag / słowo / lokalizacja. AI domyślnie wyłączona — "
    "fraza celuje w niszę.",
    [
        {"key": ["source"], "type": "select", "label": "Typ źródła",
         "options": ["hashtag", "keyword", "location"],
         "option_labels": {
             "hashtag": "Hashtag", "keyword": "Słowo kluczowe", "location": "Lokalizacja",
         },
         "desc": "hashtag → Tagi; keyword → wyniki po Enterze; location → Miejsca."},
    ],
    B.phrase_list(("phrases",), what="fraz", per_session=(1, 3),
                  placeholder="#fitnessmotivation",
                  list_label="Lista fraz",
                  per_session_label="Ile fraz na sesję",
                  source_aware=True),
).vals({"source": "hashtag"})
_b.section(
    "Siatka wyników",
    "Z siatki wyników wybieramy kafelek losowo z pierwszych N. "
    "Opcjonalnie po otwarciu scrollujemy w viewerze (analiza → swipe → analiza).",
    [
        # Liczniki sztuk — step=1 (int); scrolle w viewerze też tylko całe
        {"key": ["per_phrase", "items"], "type": "range",
         "label": "Ile treści na frazę", "unit": "szt.", "step": 1,
         "desc": "Ile kafelków otworzyć z jednej frazy (każdy = wejście z siatki). "
                 "Przy scrollach > 0 ustaw 1 — kolejne kafelki często już widziane w swipe."},
        {"key": ["per_phrase", "scrolls_in_viewer"], "type": "range",
         "label": "Ile scrolli w viewerze po otwarciu kafelka", "unit": "szt.", "step": 1,
         "desc": "Po otwarciu: analiza → X× swipe → analiza; na końcu BACK. "
                 "Łącznie treści ≈ 1 + scrolle. 0 = jedna treść i BACK."},
        {"key": ["per_phrase", "grid_pool_size"], "type": "num",
         "label": "Pula kafelków do losowania", "min": 1, "max": 12, "advanced": True,
         "desc": "Z ilu pierwszych kafelków siatki losujemy (przy items=1)."},
        {"key": ["per_phrase", "grid_tab"], "type": "select",
         "label": "Zakładka siatki", "options": ["top", "recent"],
         "option_labels": {"top": "Popularne", "recent": "Najnowsze"},
         "advanced": True,
         "desc": "Popularne czy Najnowsze."},
        {"key": ["content_types"], "type": "multiselect", "label": "Typy treści",
         "options": ["reels", "posts"],
         "option_labels": {"reels": "Rolki", "posts": "Posty"},
         "desc": "Z jakim typem treści wchodzimy w interakcję."},
    ],
).vals({"per_phrase": {"items": {"min": 3, "max": 8},
                       "scrolls_in_viewer": {"min": 0, "max": 0},
                       "grid_pool_size": 4, "grid_tab": "top"},
        "content_types": ["reels", "posts"]})
_b.section(
    "Budżet", "",
    B.budget(("budget",), unit_label="treści",
             unit_desc="Łączna liczba treści na całe zadanie (suma ze wszystkich fraz).",
             items=(10, 20), minutes=25,
             items_label="Max treści łącznie", duration_label="Max czas (min)"),
)
_b.section("Filtr", "Ta sama bramka co w reels_niche (rolki przez reels.watch).",
           B.gate_content(("gate",), min_likes=50, min_comments=10,
                          hide_skip_authors=True))
_b.section(
    "Lajkowanie", "",
    [
        {"key": ["like_content", "enabled"], "type": "bool", "label": "Lajkuj treści",
         "desc": "Czy lajkować znalezione rolki/posty."},
        B.like_decision_field(
            ai_key=["ai", "enabled"], chance_key=["like_content", "chance"],
            default_mode="chance_only",
            desc="Tylko AI / AI potem % = analiza AI na rolkach (Vision/opis/komentarze "
                 "w sekcji niżej). Tylko % = bez Vision, stała szansa. "
                 "Posty z siatki zawsze bez AI (tylko %).",
        ),
        B.chance_field(["like_content"], "chance", label="Szansa / formuła lajka",
                       desc="Przy wyłączonym AI użyj stałej wartości (nie formuły score/100).",
                       advanced=True),
    ],
).vals({"like_content": {"enabled": True, "chance": B.chance("p", 0.5)}})
_b.section(
    "Analiza AI",
    "Wejścia do modelu przy decyzji lajku obejmującej AI (Tylko AI / AI, potem %). "
    "Dotyczy tylko rolek z siatki — posty nie idą przez Vision. "
    "Wybierz: klatki (Vision), opis i/lub top komentarze.",
    B.ai_inputs(("ai",), frames=2, frame_interval_s=0.5,
                use_frames=True, read_caption=True, read_top_comments=False),
    B.ai_match(("ai",), kind="content", mode="exclude_only", min_score=55,
               hide_enabled=True),
).vals({"ai": {"enabled": False}})

_b.section(
    "Lista lajkujących", "",
    B.harvest(("likers", "harvest"), collect=(10, 20),
              source_tag="search_likers", label_what="lajkujących",
              enabled_label="Zbieraj lajkujących"),
    B.hot_interact(("likers", "hot_interact")),
)
_b.section(
    "Komentarze", "",
    B.harvest(("comments", "harvest"), collect=(5, 15),
              source_tag="search_commenters", label_what="komentujących",
              enabled_label="Zbieraj komentujących"),
    # like_comments — ukryte: runtime content_search nie lajkuje komentarzy
    B.comment_likes(("comments", "like_comments"), hide=True),
    B.hot_interact(("comments", "hot_interact"), count=(1, 2)),
)
# Jedna sekcja Znaczniki — discover (lista) + harvest (ile otworzyć).
# Runtime: normalize_tag_variants_settings() spina stare mode/grid + budget.enabled.
_b.section(
    "Znaczniki",
    "Po harvestcie głównych wyników frazy: zakładka Tags → zbierz powiązane #, "
    "opcjonalnie otwórz K z nich (osobny harvest). Nie mylić z Popularne/Najnowsze "
    "(grid_tab). Przy scrollach > 0 trzymaj 1 kafelek — kolejne kafelki często "
    "pokrywają się z już przewiniętymi rolkami.",
    [
        {"key": ["discover_tag_variants", "enabled"], "type": "bool",
         "label": "Używaj znaczników",
         "desc": "Włącza ścieżkę Tags: zbieranie listy i/lub otwieranie powiązanych #."},
        {"key": ["discover_tag_variants", "max_variants"], "type": "num",
         "label": "Ile # zbierać z listy Tags", "min": 1, "max": 50,
         "desc": "Limit unikalnych # zapisanych do mapy (per fraza). To nie jest "
                 "liczba otwarć — otwarcia ustawia „Ile # otworzyć w sesji”."},
        {"key": ["tag_variants_budget", "max_phrases"], "type": "num",
         "label": "Ile # otworzyć w sesji", "min": 0, "max": 20,
         "desc": "0 = tylko zbierz i zapisz listę; ≥1 = potem harvest tylu znaczników."},
        {"key": ["tag_variants_budget", "tiles_per_phrase"], "type": "num",
         "label": "Kafelki na #", "min": 1, "max": 20,
         "desc": "Ile wejść z siatki jednego znacznika. Przy scrollach > 0 zalecane 1."},
        {"key": ["tag_variants_budget", "scrolls"], "type": "num",
         "label": "Scrolle na #", "min": 0, "max": 20,
         "desc": "Ile swipe'ów w viewerze po otwarciu kafelka znacznika "
                 "(łącznie treści ≈ 1 + scrolle)."},
        {"key": ["tag_variants_budget", "source"], "type": "select",
         "label": "Źródło znaczników",
         "options": ["live_collect", "from_map", "both"],
         "option_labels": {
             "live_collect": "Live z listy Tags (ta sesja)",
             "from_map": "Z zapisanej mapy niche.tag_variants",
             "both": "Mapa + live collect",
         },
         "desc": "live = zbierz Tags w sesji i z tego otwieraj; mapa = tylko zapisane; "
                 "oba = mapa + świeży collect."},
        {"key": ["discover_tag_variants", "write_to_account"], "type": "bool",
         "label": "Zapisz mapę (niche.tag_variants)",
         "desc": "Persist zebrane # w JSON konta."},
        # --- zaawansowane / legacy ---
        {"key": ["discover_tag_variants", "max_scrolls"], "type": "num",
         "label": "Scrolle listy Tags", "min": 0, "max": 10, "advanced": True,
         "desc": "Ile razy przewinąć listę Znaczniki przy zbieraniu (nie viewer)."},
        {"key": ["discover_tag_variants", "sort_by_post_count"], "type": "bool",
         "label": "Sortuj po liczbie postów", "advanced": True,
         "desc": "Jeśli UI pokazuje subtitle z liczbą postów — sort malejąco przed limitem."},
        {"key": ["discover_tag_variants", "include_seed"], "type": "bool",
         "label": "Dołącz seed do listy wariantów", "advanced": True,
         "desc": "Czy sama fraza wejściowa ma trafić do tag_variants[expression]."},
        {"key": ["discover_tag_variants", "promote_to_discovered"], "type": "bool",
         "label": "Promuj do hashtags_discovered", "advanced": True,
         "desc": "Append do flat hashtags_discovered — domyślnie wyłączone."},
        {"key": ["discover_tag_variants", "sync_content_search"], "type": "bool",
         "label": "Sync do phrases.list", "advanced": True,
         "desc": "Append do fraz content_search — zwykle wyłącz (puchnie listę)."},
        # ukryte: runtime normalize spina legacy mode/grid + budget.enabled
        {"key": ["discover_tag_variants", "mode"], "type": "select",
         "label": "Legacy: po collect", "options": ["collect", "grid"],
         "hidden": True,
         "desc": "Przestarzałe — zamiast grid ustaw „Ile # otworzyć” ≥ 1."},
        {"key": ["tag_variants_budget", "enabled"], "type": "bool",
         "label": "Legacy: gałąź B", "hidden": True,
         "desc": "Przestarzałe — włączane automatycznie gdy „Ile # otworzyć” > 0."},
    ],
).vals({
    "discover_tag_variants": {
        "enabled": False,
        "mode": "collect",
        "max_variants": 4,
        "max_scrolls": 2,
        "sort_by_post_count": True,
        "include_seed": False,
        "write_to_account": True,
        "promote_to_discovered": False,
        "sync_content_search": False,
    },
    "tag_variants_budget": {
        "enabled": False,
        "max_phrases": 0,
        "tiles_per_phrase": 1,
        "scrolls": 4,
        "source": "live_collect",
    },
})

CONTENT_SEARCH = _plugin(
    "content_search", "Wyszukiwanie: # / słowa / lokalizacja", "discovery", "ready",
    "home_feed", "home_feed",
    "Wchodzi w wyszukiwarkę, wpisuje frazę z listy i przerabia treści z siatki. "
    "Analiza AI (rolki) domyślnie wyłączona — fraza sama celuje w niszę; "
    "włącz przez Decyzję o lajku i ustaw Vision/opis/komentarze.",
    _b,
)


# ============================================================
# P4 — profile_interact
# ============================================================
_b = _Builder()
_b.section(
    "Budżet", "",
    B.budget(("budget",), unit_label="profili",
             unit_desc="Ile profili odwiedzić.", items=(5, 15), minutes=20,
             items_label="Max profili", duration_label="Max czas (min)"),
)
_b.section(
    "Źródło profili",
    "Domyślnie target_candidates (po filtrze HTTP). "
    "raw_profiles — surowa pula bez promocji.",
    [
        {"key": ["source", "kind"], "type": "select", "label": "Źródło listy",
         "options": ["target_candidates", "raw_profiles"],
         "option_labels": {
             "raw_profiles": "Surowe profile (raw_profiles.jsonl)",
             "target_candidates": "Kandydaci (po filtrze HTTP)",
         },
         "desc": "Skąd czytać kolejkę profili do wizyt."},
        # Pod selectem — widoczne gdy kind=raw_profiles
        {"key": ["_ui", "raw_preview"], "type": "raw_profiles_preview",
         "label": "Podgląd raw_profiles",
         "desc": "Unikalne username (najnowsze pierwsze): źródło, from_target, "
                 "rodzaj treści, czas, ring.",
         "limit": 80,
         "show_when": {"key": ["source", "kind"], "equals": "raw_profiles"}},
        # Podgląd kandydatów po filtrze
        {"key": ["_ui", "candidates_preview"], "type": "target_candidates_preview",
         "label": "Podgląd target_candidates",
         "desc": "Profile po filtrze HTTP (proxy1) — gotowe do interakcji.",
         "limit": 80,
         "show_when": {"key": ["source", "kind"], "equals": "target_candidates"}},
        {"key": ["source", "dedup_days"], "type": "num", "label": "Dedup (dni)",
         "min": 0, "advanced": True,
         "desc": "Pomijaj profile odwiedzone w ciągu ostatnich N dni."},
        {"key": ["source", "min_age_minutes"], "type": "num",
         "label": "Min. wiek wpisu (min)", "min": 0, "advanced": True,
         "desc": "Pomijaj wpisy dodane w ostatnich N minutach."},
    ],
).vals({"source": {"kind": "target_candidates", "dedup_days": 30, "min_age_minutes": 5}})
_b.section(
    "Sanity",
    "Natychmiastowe wyjście z profilu, bez czytania dalej.",
    [
        {"key": ["sanity", "abort_if_private"], "type": "bool",
         "label": "Abort jeśli prywatny",
         "desc": "Wyjdź z profilu prywatnego."},
        {"key": ["sanity", "abort_if_already_following"], "type": "bool",
         "label": "Abort jeśli już following",
         "desc": "Wyjdź, jeśli już obserwujemy ten profil."},
    ],
).vals({"sanity": {"abort_if_private": True, "abort_if_already_following": True}})
_b.section(
    "Bramka profilu",
    "Odsiew po nagłówku, przed kosztowną analizą siatki.",
    [
        {"key": ["gate", "enabled"], "type": "bool", "label": "Bramka liczników",
         "desc": "Czy stosować bramkę (przy liście już przefiltrowanej — zbędna)."},
        {"key": ["gate", "min_posts"], "type": "num", "label": "Min. postów", "min": 0,
         "desc": "Minimalna liczba postów."},
        {"key": ["gate", "min_followers"], "type": "num", "label": "Min. followers", "min": 0,
         "desc": "Minimalna liczba obserwujących."},
        {"key": ["gate", "max_followers"], "type": "num", "label": "Max followers", "min": 0,
         "desc": "Maksymalna liczba obserwujących."},
        {"key": ["gate", "min_ratio"], "type": "num", "label": "Min. ratio",
         "min": 0, "step": 0.05,
         "desc": "Minimalny stosunek obserwujący/obserwowani."},
        {"key": ["gate", "bio_exclusions"], "type": "tags", "label": "Wykluczenia w bio",
         "advanced": True,
         "desc": "Słowa w bio dyskwalifikujące profil."},
    ],
).vals({"gate": {"enabled": True, "min_posts": 6, "min_followers": 100,
                 "max_followers": 20000, "min_ratio": 0.3, "bio_exclusions": []}})
_b.section(
    "Relacja",
    "Obejrzenie relacji przed decyzją — sygnał zainteresowania.",
    [
        {"key": ["story", "enabled"], "type": "bool", "label": "Oglądaj relację",
         "desc": "Obejrzyj relację, jeśli profil ma aktywną."},
    ],
    B.story_watch(("story", "watch"), segments=(1, 3), watch_story=(3, 6),
                  advanced_all=True),
).vals({"story": {"enabled": True}})
_b.section(
    "Lajkowanie kafelków",
    "Jak w rolkach: Tylko AI / Tylko % / AI, potem %. "
    "Wykluczony kafelek (AI) nie dyskwalifikuje profilu — wypada z puli lajków.",
    [
        {"key": ["like_tiles", "enabled"], "type": "bool",
         "label": "Lajkuj kafelki",
         "desc": "Czy lajkować posty/rolki z siatki profilu."},
        B.like_decision_field(
            ai_key=["grid_ai", "enabled"], chance_key=["like_tiles", "chance"],
            default_mode="ai_only",
            desc="Tylko AI = kategoryzacja siatki, lajk niszowych (szansa 100%). "
                 "Tylko % = bez Vision, losowe kafelki ze stałą szansą. "
                 "AI, potem % = bramka AI, potem rzut chance na kandydatach.",
        ),
        {"key": ["like_tiles", "count"], "type": "range", "label": "Ile kafelków",
         "unit": "szt.", "step": 1,
         "desc": "Górny limit polubień na profil (po filtrze AI / losowaniu)."},
        B.chance_field(["like_tiles"], "chance", label="Szansa / formuła lajka",
                       desc="Przy Tylko % i AI potem % — szansa na lajk kafelka. "
                            "Formuła score/100 używa wyniku niszy profilu gdy jest.",
                       advanced=True),
    ],
    # Parametry match AI — enabled steruje like_decision (ukryte tu)
    B.ai_match(("grid_ai",), kind="grid", mode="niche_match",
               enabled_label="Analiza siatki AI", hide_enabled=True),
).vals({
    "like_tiles": {
        "enabled": True,
        "count": {"min": 1, "max": 3},
        "chance": B.chance("p", 1.0),
    },
    "grid_ai": {"enabled": True},
})
_b.section(
    "Follow", "",
    [
        {"key": ["follow", "allow"], "type": "bool", "label": "Pozwalaj na follow",
         "desc": "Wyłączone = tylko lajki i zapis do bazy."},
        {"key": ["follow", "min_scoring"], "type": "num", "label": "Min. scoring do follow",
         "min": 0, "max": 1, "step": 0.05,
         "desc": "Minimalny łączny wynik profilu (0–1) do follow-a."},
        B.chance_field(["follow"], "chance", label="Szansa follow",
                       desc="Szansa na follow przy spełnionym progu."),
    ],
).vals({"follow": {"allow": True, "min_scoring": 0.5, "chance": B.chance("p", 0.8)}})
PROFILE_INTERACT = _plugin(
    "profile_interact", "Interakcja z profilami", "engagement", "ready",
    "home_feed", "home_feed",
    "Pełna procedura na profilu: nagłówek → sanity → bramka → relacja → "
    "lajki kafelków (AI i/lub %) → decyzja follow → zapis. "
    "Działa samodzielnie ORAZ jako hot_interact.",
    _b,
)


# ============================================================
# P5 — mass_stories
# ============================================================
_b = _Builder()
_b.section(
    "Budżet", "",
    B.budget(("budget",), unit_label="targetów",
             unit_desc="Ilu targetów przerobić.", items=(1, 3), minutes=20,
             items_label="Max targetów", duration_label="Max czas (min)"),
)
_b.section(
    "Konta-targety",
    "Konta konkurencji / z niszy, których obserwujących będziemy przeglądać.",
    B.phrase_list(("targets",), what="kont", per_session=(1, 3),
                  placeholder="nazwa_konta_bez_malpy",
                  list_label="Lista kont",
                  per_session_label="Ile kont / sesja"),
    [
        {"key": ["list_source"], "type": "select", "label": "Lista: followers vs following",
         "options": ["followers", "following"],
         "option_labels": {"followers": "Obserwujący", "following": "Obserwowani"},
         "desc": "Którą listę targetu otworzyć."},
    ],
).vals({"list_source": "followers"})
_b.section(
    "Przeglądanie listy",
    "Bot scrolluje listę i szuka osób z aktywną relacją (ring).",
    [
        {"key": ["per_target", "max_ring_opens"], "type": "range",
         "label": "Max otwarć ringów / target", "unit": "osób", "step": 1,
         "desc": "Ile relacji otworzyć u jednego targetu."},
        {"key": ["per_target", "max_scrolls"], "type": "num",
         "label": "Max przewinięć listy", "min": 1, "advanced": True,
         "desc": "Limit przewinięć listy."},
        {"key": ["per_target", "recent_cap"], "type": "num",
         "label": "Pamięć niedawno odwiedzonych", "min": 0, "advanced": True,
         "desc": "Ile ostatnio odwiedzonych pamiętać w tym przejściu."},
    ],
).vals({"per_target": {"max_ring_opens": {"min": 6, "max": 12},
                       "max_scrolls": 15, "recent_cap": 3}})
_b.section(
    "Oglądanie relacji", "",
    B.story_watch(("story_watch",), segments=(2, 5), watch_story=(3, 6),
                  advanced_all=True),
)
_b.section(
    "Zbieranie profili", "",
    B.harvest(("harvest",), enabled=True, collect=(10, 30),
              source_tag="mass_stories", label_what="obejrzanych",
              enabled_label="Zbieraj obejrzanych"),
    [
        {"key": ["dedup_days"], "type": "num", "label": "Dedup oglądania (dni)",
         "min": 0, "advanced": True,
         "desc": "Nie oglądaj ponownie relacji osoby widzianej w ciągu N dni."},
    ],
).vals({"dedup_days": 14})

MASS_STORIES = _plugin(
    "mass_stories", "Mass stories", "engagement", "ready",
    "home_feed", "home_feed",
    "Wchodzi na konto-target, otwiera listę obserwujących i ogląda relacje osób "
    "z aktywnym ringiem. Najtańszy sygnał uwagi w niszy.",
    _b,
)


# ============================================================
# P6 — target_scraper
# ============================================================
_b = _Builder()
_b.section(
    "Budżet", "",
    B.budget(("budget",), unit_label="targetów",
             unit_desc="Ilu targetów przerobić.", items=(1, 3), minutes=30,
             items_label="Max targetów", duration_label="Max czas (min)"),
)
_b.section(
    "Konta-targety", "Konta, z których zbieramy odbiorców.",
    B.phrase_list(("targets",), what="kont", per_session=(1, 3),
                  placeholder="nazwa_konta_bez_malpy",
                  list_label="Lista kont",
                  per_session_label="Ile kont / sesja"),
)
_b.section(
    "Źródła danych",
    "TYLKO ZBIERANIE — zero lajków / follow / relacji. Interakcje: profile_interact.",
    [
        {"key": ["sources"], "type": "multiselect", "label": "Źródła",
         "options": ["post_likers", "post_commenters", "reel_likers",
                     "reel_commenters", "followers", "following"],
         "option_labels": {
             "post_likers": "Lajkujący posty",
             "post_commenters": "Komentujący posty",
             "reel_likers": "Lajkujący rolki",
             "reel_commenters": "Komentujący rolki",
             "followers": "Obserwujący",
             "following": "Obserwowani",
         },
         "desc": "Co zebrać z targetu — można łączyć."},
    ],
).vals({"sources": ["post_likers", "post_commenters"]})
_b.section(
    "Posty / rolki",
    "Dotyczy źródeł post_* i reel_*.",
    [
        {"key": ["posts", "last_posts"], "type": "range",
         "label": "Ile ostatnich postów/rolek", "unit": "szt.", "step": 1,
         "desc": "Ile ostatnich postów/rolek targetu przerobić."},
        {"key": ["posts", "skip_pinned"], "type": "bool", "label": "Pomijaj przypięte",
         "desc": "Pomijaj posty przypięte — bywają stare."},
    ],
).vals({"posts": {"last_posts": {"min": 3, "max": 6}, "skip_pinned": True}})
_b.section(
    "Ile zbierać",
    "Limity na jedno źródło — w Zaawansowanych, bo rzadko zmieniane.",
    [
        {"key": ["collect", "per_post_likers"], "type": "range",
         "label": "Lajkujących / post", "unit": "szt.", "step": 1, "advanced": True,
         "desc": "Ile lajkujących zebrać z jednego posta/rolki."},
        {"key": ["collect", "per_post_commenters"], "type": "range",
         "label": "Komentujących / post", "unit": "szt.", "step": 1, "advanced": True,
         "desc": "Ilu komentujących zebrać z jednego posta/rolki."},
        {"key": ["collect", "per_list"], "type": "range",
         "label": "Z listy followers/following", "unit": "szt.", "step": 1, "advanced": True,
         "desc": "Ile profili z listy obserwujących/obserwowanych."},
        {"key": ["collect", "max_scrolls_per_list"], "type": "num",
         "label": "Max scrolli / lista", "min": 1, "advanced": True,
         "desc": "Limit przewinięć jednej listy."},
    ],
).vals({"collect": {"per_post_likers": {"min": 15, "max": 40},
                    "per_post_commenters": {"min": 5, "max": 20},
                    "per_list": {"min": 50, "max": 200},
                    "max_scrolls_per_list": 25}})
_b.section(
    "Filtry zapisu", "",
    [
        {"key": ["filters", "min_comment_likes"], "type": "num",
         "label": "Min. lajków komentarza", "min": 0, "advanced": True,
         "desc": "Zapisuj komentujących tylko przy ≥ N lajków komentarza. 0 = wszyscy."},
        {"key": ["filters", "only_with_story_ring"], "type": "bool",
         "label": "Tylko z ringiem relacji", "advanced": True,
         "desc": "Zapisuj wyłącznie profile z aktywną relacją."},
        {"key": ["filters", "skip_already_scraped_days"], "type": "num",
         "label": "Pomiń już zebrane (dni)", "min": 0, "advanced": True,
         "desc": "Nie zapisuj profili, które już mamy z ostatnich N dni."},
    ],
).vals({"filters": {"min_comment_likes": 0, "only_with_story_ring": False,
                    "skip_already_scraped_days": 30}})
_b.section(
    "Zapis (harvest)",
    "enabled jest zawsze True w defaultach — meta harvestu.",
    B.harvest(("harvest",), enabled=True, collect=(50, 200),
              source_tag="auto", label_what="zebranych",
              enabled_label="Zapis do raw_profiles"),
)

TARGET_SCRAPER = _plugin(
    "target_scraper", "Scraper targetów", "discovery", "ready",
    "home_feed", "home_feed",
    "Wchodzi na konto z listy i zbiera username-y (lajkujący, komentujący, listy). "
    "Zero interakcji — czysty zbieracz.",
    _b,
)


# ============================================================
# P7 — reels_warmup
# ============================================================
_b = _Builder()
_b.section(
    "Budżet", "",
    B.budget(("budget",), unit_label="rolek",
             unit_desc="Ile rolek obejrzeć.", items=(8, 20), minutes=12,
             items_label="Max rolek", duration_label="Max czas (min)"),
)
_b.section(
    "Zachowanie",
    "Rozgrzewka: oglądanie rolek bez AI i bez zbierania. Celowo prosty plugin.",
    [
        {"key": ["watch_reel"], "type": "range", "label": "Czas oglądania rolki",
         "unit": "s", "step": 0.1, "advanced": True,
         "desc": "Biznesowy parametr: ile sekund bot 'ogląda' jedną rolkę. "
                  "Obecnie używany gdy `reels.watch` pracuje w trybie czasu oglądania."},
        {"key": ["like", "enabled"], "type": "bool", "label": "Lajkuj podczas rozgrzewki",
         "desc": "Czy lajkować cokolwiek podczas rozgrzewki."},
        B.chance_field(["like"], "chance", label="Szansa lajka %",
                       desc="Szansa na lajk rolki. Trzymaj nisko — to rozgrzewka."),
        {"key": ["skip_ads"], "type": "bool", "label": "Przewijaj reklamy szybciej",
         "desc": "Przewijaj reklamy szybciej niż zwykłe rolki."},
    ],
).vals({"watch_reel": {"min": 2, "max": 3},
        "like": {"enabled": True, "chance": B.chance("p", 0.15)}, "skip_ads": True})

REELS_WARMUP = _plugin(
    "reels_warmup", "Rolki — rozgrzewka", "warmup", "ready",
    "home_feed", "home_feed",
    "Oglądanie rolek bez analizy AI i bez zbierania profili. Buduje historię "
    "aktywności na świeżym koncie/klonie.",
    _b,
)


# ============================================================
# niche_discover — one-shot z Ustawień konta (nie ze scenariuszy)
# Pipeline: posty → rolki → finalize konta → merge wielu kont
# ============================================================
_b = _Builder()
_b.section(
    "Źródła",
    "Skąd zbierać sygnały do opisania niszy.",
    [
        {"key": ["seed"], "type": "textarea", "label": "Seed tekstowy", "rows": 2,
         "desc": "Opcjonalny seed tekstowy (ręczny opis, z którego AI startuje)."},
        {"key": ["sources", "own_profile"], "type": "bool", "label": "Własny profil",
         "desc": "Zbieraj treści z własnej siatki profilu."},
        {"key": ["sources", "competitors"], "type": "bool", "label": "Konkurencja",
         "desc": "Dokładaj treści z profili konkurencji."},
        {"key": ["competitors", "list"], "type": "tags", "label": "Lista konkurencji",
         "desc": "Username-y konkurencji (bez @). Analizujemy wszystkie wpisane "
                 "na listę, w tej kolejności — po wpisaniu wciśnij Enter."},
    ],
).vals({
    "seed": "",
    "sources": {"own_profile": True, "competitors": False},
    "competitors": {"list": []},
})
_b.section(
    "Zbieranie",
    "Te same limity dla własnego profilu i konkurencji. "
    "Zawsze od najnowszej treści, po kolei; opis i zrzut do AI są obowiązkowe.",
    [
        {"key": ["collect", "posts", "count"], "type": "num", "label": "Ile postów",
         "min": 1, "max": 20, "unit": "szt.", "advanced": True,
         "desc": "Ile najnowszych postów z każdego profilu (własny / konkurencja)."},
        {"key": ["collect", "reels", "count"], "type": "num", "label": "Ile rolek",
         "min": 0, "max": 20, "unit": "szt.", "advanced": True,
         "desc": "Ile najnowszych rolek z każdego profilu (zakładka Rolki)."},
        {"key": ["collect", "reel_frames"], "type": "num", "label": "Klatki na rolkę",
         "min": 1, "max": 12, "advanced": True,
         # min=1: jedna klatka OK; max bez zmian (12)
         "desc": "Ile klatek z jednej rolki skleić w kolaż (1–12)."},
        {"key": ["collect", "reel_frame_interval_s"], "type": "num",
         "label": "Odstęp klatek (s)", "min": 1, "max": 5, "step": 0.5,
         "advanced": True,
         "desc": "Odstęp między klatkami rolki w sekundach (np. 1 / 1.5 / 2; min 1)."},
    ],
).vals({
    "collect": {
        "posts": {"count": 4},
        "reels": {"count": 4},
        "reel_frames": 4,
        "reel_frame_interval_s": 1.5,
    },
})
_b.section(
    "AI (posty → rolki → konto → merge)",
    "Pipeline Vision: niche_discover_posts / reels / account / merge.",
    [
        {"key": ["ai", "batch_size"], "type": "num", "label": "Batch postów",
         "min": 1, "max": 9, "advanced": True,
         "desc": "Ile POSTÓW w jednym wywołaniu AI (zbiorczy composite + opisy). "
                 "Rolki zawsze analizowane osobno (1 rolka = 1 call)."},
        {"key": ["ai", "keywords_count"], "type": "num", "label": "Ile keywords",
         "min": 1, "max": 30,
         "desc": "Dokładna liczba keywords (bez #) w finalnej niszy — ani mniej, ani więcej."},
        {"key": ["ai", "hashtags_count"], "type": "num", "label": "Ile hashtagów",
         "min": 1, "max": 30,
         "desc": "Dokładna liczba hashtagów (z #) w finalnej niszy — ani mniej, ani więcej."},
        {"key": ["ai", "strictness"], "type": "select", "label": "Precyzja niszy",
         "options": ["loose", "balanced", "strict"],
         "option_labels": {
             "loose": "Luźna (szeroki zakres)",
             "balanced": "Zrównoważona",
             "strict": "Restrykcyjna (część wspólna)",
         },
         "advanced": True,
         "desc": "loose = unia motywów; balanced = powtarzalne sygnały; "
                 "strict = wąski rdzeń / część wspólna przy merge."},
    ],
).vals({"ai": {"batch_size": 4, "keywords_count": 8, "hashtags_count": 8,
               "strictness": "balanced"}})
# Sync fraz + tryb merge discovered — checkbox/select w panelu Nisza
# (obok accepted), nie w accordionie „Zaawansowane”.
# Manual zawsze zachowane; append=dopisz discovered, replace=nadpisz discovered.
_b.vals({"write": {
    "sync_content_search_phrases": False,
    "discovered_merge": "append",
}})

NICHE_DISCOVER = _plugin(
    "niche_discover", "Discovery niszy", "config", "ready",
    "home_feed", "home_feed",
    "One-shot: posty → rolki → finalize konta → (opcjonalnie) merge konkurencji. "
    "Bez budżetu kolejki — limity to tylko collect.posts/reels. "
    "Zapisuje account.niche z accepted=false. "
    "NIE należy do scenariuszy — uruchamiane tylko z Ustawień konta.",
    _b,
    queueable=False,
    ui_hidden=True,
)


# ============================================================
PLUGINS = [
    FEED_BROWSER,
    REELS_NICHE,
    CONTENT_SEARCH,
    PROFILE_INTERACT,
    MASS_STORIES,
    TARGET_SCRAPER,
    REELS_WARMUP,
    NICHE_DISCOVER,
]

_BY_ID = {p["id"]: p for p in PLUGINS}


def plugin_ids() -> list[str]:
    return [p["id"] for p in PLUGINS]


def get_plugin(plugin_id: str) -> dict:
    if plugin_id not in _BY_ID:
        raise KeyError(f"nieznany plugin: {plugin_id} (znane: {plugin_ids()})")
    return _BY_ID[plugin_id]


def all_defaults() -> dict:
    """Domyślne ustawienia wszystkich pluginów — baza dla nowego konta."""
    return {p["id"]: _deep_copy(p["defaults"]) for p in PLUGINS}


def _deep_copy(o):
    if isinstance(o, dict):
        return {k: _deep_copy(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_deep_copy(v) for v in o]
    return o
