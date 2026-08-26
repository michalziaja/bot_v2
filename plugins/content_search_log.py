"""Krotkie, ludzkie komunikaty z przebiegu content_search (stdout / panel).

Wzorzec jak `reel_log` / `profile_log`: prefix = id pluginu, bez ANSI.
Events.jsonl nadal zbiera surowe dane — tu tylko to, co operator czyta na zywo.

Kroki `reels.watch` (bramka) loguja juz z `log_prefix=content_search`
przez reel_log — tu reszta sesji: fraza, siatka, kafelki, like, harvest, koniec.
"""
from __future__ import annotations
import sys

# Prefix = id pluginu — panel koloruje przez CSS (.ev-plugin)
_PREFIX = "content_search"


def say(msg: str) -> None:
    """Jedna linia statusu — zawsze z prefixem content_search z przodu."""
    if not (msg or "").strip():
        print(flush=True)
        return
    try:
        print(f"  {_PREFIX} {msg}", flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        line = f"  {_PREFIX} {msg}\n"
        sys.stdout.buffer.write(line.encode(enc, errors="replace"))
        sys.stdout.flush()


# ------------------------------------------------------------------
# sesja / fraza / wyszukiwanie
# ------------------------------------------------------------------
def no_phrases_line() -> str:
    return "Brak fraz w ustawieniach"


def phrase_start_line(query: str, *, source: str | None = None) -> str:
    """Start frazy — operator widzi co wpisujemy (#gym / keyword / lokalizacja)."""
    q = (query or "").strip() or "?"
    src = (source or "").strip()
    if src and src != "hashtag":
        return f"Start — {q} ({src})"
    return f"Start — {q}"


def searching_line(query: str, i: int | None = None,
                   n: int | None = None) -> str:
    """Start frazy: „Szukam #gym… 1/2”."""
    from v2.plugins.progress_log import with_progress
    q = (query or "").strip() or "?"
    return with_progress(f"Szukam {q}…", i, n)


def results_ok_line() -> str:
    return "Wyniki — siatka OK"


def results_fail_line(query: str | None = None) -> str:
    q = f" ({query})" if query else ""
    return f"Wyniki — brak siatki{q}"


def search_open_fail_line() -> str:
    return "Szukaj — nie otwarto"


# ------------------------------------------------------------------
# kafelki / treść
# ------------------------------------------------------------------
def open_tile_line(n: int, *, kind: str | None = None,
                   total: int | None = None) -> str:
    """Kafelek N[/total] w tej frazie (1-based) — po udanym open z siatki."""
    from v2.plugins.progress_log import xy
    label = {"reel": "rolka", "post": "post"}.get(kind or "", "")
    # Preferuj „Kafelek 2/8 (rolka)” gdy znamy total
    frac = xy(n, total) if total is not None else str(n)
    if label:
        return f"Kafelek {frac} ({label})"
    return f"Kafelek {frac}"


def open_tile_fail_line(reason: str | None = None) -> str:
    r = str(reason or "")
    mapping = {
        "no_tiles": "brak kafelkow",
        "pool_exhausted": "pula wyczerpana",
        "no_matching_kind": "brak pasujacego typu",
        "nothing_opened": "nie otwarto",
    }
    human = mapping.get(r) or (r or "skip")
    return f"Kafelek — skip ({human})"


def post_opened_line() -> str:
    """Post z siatki — bez bramki rolek i bez Vision (AI tylko na rolkach)."""
    return "Post — otwarty (bez AI / bramki rolek)"


def reel_skip_line(reason: str | None = None) -> str:
    """Watch/AI nie OK — szczegoly juz z watch / analyze (prefix content_search)."""
    r = (reason or "").strip()
    if r:
        return f"Rolka — pomijam ({r})"
    return "Rolka — pomijam (filtr / blad)"


def search_obstacle_line(*, overlay: str | None = None,
                         pkg: str | None = None) -> str:
    """foreign_app / Settings — jeden komunikat zamiast dumpa type_query.retry."""
    blob = f"{overlay or ''} {pkg or ''}".lower()
    if "settings" in blob:
        return "Search — przeszkoda UI (ustawienia), wracam…"
    if "foreign_app" in blob:
        return "Search — przeszkoda UI (inna aplikacja), wracam…"
    return "Search — przeszkoda UI, wracam…"


# ------------------------------------------------------------------
# like / harvest / powrot
# ------------------------------------------------------------------
def like_ok_line(*, kind: str | None = None) -> str:
    """Udana lajk — spójnie z reels_niche („Polubiono”).

    `kind` zostaje w sygnaturze (callers); na stdout wystarczy jedno słowo —
    prefix pluginu i tak mówi skąd (content_search / profile_interact).
    """
    _ = kind
    return "Polubiono"


def like_skip_line(reason: str | None = None) -> str:
    r = str(reason or "").strip()
    # Szansa — szum; wołający zwykle milczy (QUIET), tu fallback gdy jednak say
    if r in ("chance_missed", "no_chance") or "chance" in r:
        return "Pomijam lajk (szansa)"
    if "daily" in r:
        return "Like — limit dzienny"
    if r:
        return f"Like — skip ({r})"
    return "Like — skip"


def harvest_line(where: str, saved: int, *, dupes: int = 0) -> str:
    """Jak target_scraper: Zapisano N (D dup) — bez raw harvest_usernames.done.

    `where` zostaje w sygnaturze (lajkujący / komentujący w JSONL), na stdout
    krótko jak scraper — operator i tak widzi „Otwieram listę…" wcześniej.
    """
    _ = where  # kontekst listy — tylko dla wołającego / przyszłości
    saved_i = int(saved or 0)
    dup_i = int(dupes or 0)
    if dup_i > 0:
        return f"Zapisano {saved_i} ({dup_i} dup)"
    return f"Zapisano {saved_i}"


def hot_start_line(where: str, n: int) -> str:
    label = "lajkujacych" if where == "likers" else "komentujacych"
    return f"Hot — {n} z listy {label}"


def hot_profile_line(username: str | None, i: int | None = None,
                     n: int | None = None, *, where: str | None = None) -> str:
    """Hot: „Hot @alice 1/3 z listy lajkujacych” (X/Y + SessionStats)."""
    from v2.plugins.progress_log import with_progress
    who = f"@{username}" if username else "@?"
    label = "lajkujacych" if where == "likers" else (
        "komentujacych" if where == "comments" else None)
    msg = with_progress(f"Hot {who}", i, n)
    if label:
        return f"{msg} z listy {label}"
    # Bez where — krótko jak wcześniej (Profil @nick), gdy brak i/n
    if i is None:
        return f"Profil {who}"
    return msg


def hot_no_candidates_line() -> str:
    """Koniec hot: brak kolejnych nickow — wracamy do siatki #."""
    return "Hot — brak kandydatow, koniec"


def hot_open_fail_line(username: str | None, *, why: str | None = None,
                       landed: str | None = None,
                       saw_profile: bool = False) -> str:
    """Tap z listy nie dal trwalego profilu — rozróżnij zgubienie vs pudło.

    Live 14:23: profil mignął / otworzył się, poll złapał `reels_feed` i
    raportował „nie otwarto" — mylące. `opened_then_lost` / `saw_profile`
    → inna fraza.
    """
    who = f"@{username}" if username else "@?"
    detail = why or landed
    lost = (why == "opened_then_lost") or bool(saw_profile)
    if lost:
        if detail:
            return f"Hot — otwarto i zgubiono {who} ({detail})"
        return f"Hot — otwarto i zgubiono {who}"
    if detail:
        return f"Hot — nie otwarto {who} ({detail})"
    return f"Hot — nie otwarto {who}"


def hot_done_line(*, visited: int = 0, followed: int = 0) -> str:
    return f"Hot — wizyty {visited}, follow {followed}"


def hot_lost_sheet_line(where: str, *, top: str | None = None) -> str:
    """Sheet lajkujacych/komentujacych zgubiony po wizycie — skip reszty hot."""
    label = "lajkow" if where == "likers" else "komentarzy"
    extra = f" (jest {top})" if top else ""
    # Nie „do wynikow” — scrolle w viewerze mogą jeszcze trwać
    return f"Hot — utracono liste {label}{extra}, wracam do tresci"


def viewer_scroll_line(n: int, total: int) -> str:
    """Swipe w viewerze po analizie — n z X (1-based), zanim wrócimy do siatki."""
    return f"Scroll {n}/{total}…"


def viewer_lost_line(*, phase: str | None = None) -> str:
    """Po swipe / przed swipe: nie jesteśmy już w treści — przerywamy pętlę."""
    if phase == "after_scroll":
        return "Viewer — utracono tresc po scrollu, wracam do wynikow"
    if phase == "before_scroll":
        return "Viewer — brak tresci przed scrollem, wracam do wynikow"
    return "Viewer — utracono tresc, wracam do wynikow"


def back_results_line(*, more: bool = True) -> str:
    if more:
        return "Powrot do wynikow → kolejny"
    return "Powrot do wynikow"


def results_lost_line(phrase: str | None = None) -> str:
    p = f" ({phrase})" if phrase else ""
    return f"Wyniki utracone{p} — koncze fraze"


# ------------------------------------------------------------------
# koniec sesji
# ------------------------------------------------------------------
def done_line(*, phrases: int = 0, items: int = 0, reels: int = 0,
              posts: int = 0, liked: int = 0, likers_saved: int = 0,
              commenters_saved: int = 0, hot_visited: int = 0,
              hot_followed: int = 0, no_tiles: int = 0,
              tag_variants_phrases: int = 0, tag_variants_collected: int = 0,
              stopped_by: str | None = None, **_extra) -> str:
    """Podsumowanie sesji — liczby jak w profile_interact.done.

    `_extra` — ignoruj nieznane klucze ze stats (forward-compat).
    """
    stop = f", stop={stopped_by}" if stopped_by else ""
    tags = ""
    if tag_variants_phrases or tag_variants_collected:
        tags = (f", znaczniki {tag_variants_phrases}"
                f" (zebrane {tag_variants_collected})")
    return (f"Koniec — frazy {phrases}, treść {items} "
            f"(rolki {reels}, posty {posts}), like {liked}, "
            f"harvest L{likers_saved}/C{commenters_saved}, "
            f"hot {hot_visited}/{hot_followed}{tags}{stop}")
