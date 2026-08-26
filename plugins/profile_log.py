"""Krotkie, ludzkie komunikaty z przebiegu profile_interact (stdout / panel).

Wzorzec jak `reel_log` / `scraper_log`: prefix = id pluginu, bez ANSI.
Events.jsonl nadal zbiera surowe dane — tu tylko to, co operator czyta na zywo.
"""
from __future__ import annotations
import sys

# Prefix = id pluginu — panel koloruje przez CSS (.ev-plugin)
_PREFIX = "profile_interact"


def say(msg: str) -> None:
    """Jedna linia statusu — zawsze z prefixem profile_interact z przodu."""
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


def fmt_count(n) -> str:
    """Liczby czytelne dla operatora (jak w reel_log)."""
    if n is None:
        return "?"
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} mln"
    if n >= 10_000:
        return f"{n / 1000:.0f} tys."
    if n >= 1000:
        return f"{n / 1000:.1f} tys."
    return str(n)


# ------------------------------------------------------------------
# wizyta / search
# ------------------------------------------------------------------
def visit_start_line(username: str | None, i: int | None = None,
                     n: int | None = None) -> str:
    """Wizyta @x [1/5] — X/Y z budżetu pluginu."""
    from v2.plugins.progress_log import with_progress
    who = f"@{username}" if username else "@?"
    return with_progress(f"Wizyta {who}", i, n)


def next_profile_line(username: str | None, i: int | None = None,
                      n: int | None = None) -> str:
    """Kolejny nick w tej samej sesji (reuse search) + opcjonalne X/Y."""
    from v2.plugins.progress_log import with_progress
    who = f"@{username}" if username else "@?"
    return with_progress(f"Kolejny profil — czyszcze szukaj → {who}", i, n)


def search_reuse_line() -> str:
    return "Szukaj — reuse (bez open)"


def open_failed_line(username: str | None) -> str:
    who = f"@{username}" if username else "@?"
    return f"Nie otwarto profilu {who}"


def no_candidates_line() -> str:
    return "Brak kandydatow w kolejce"


def done_line(*, visited: int = 0, followed: int = 0, skipped: int = 0,
              failed: int = 0, tiles_liked: int = 0) -> str:
    return (f"Koniec — wizyty {visited}, follow {followed}, "
            f"skip {skipped}, fail {failed}, lajki kafelkow {tiles_liked}")


# ------------------------------------------------------------------
# sanity / gate
# ------------------------------------------------------------------
def sanity_reject_line(why: str, username: str | None = None) -> str:
    """Prywatny / juz obserwowany — krotki odrzut."""
    who = f" @{username}" if username else ""
    w = str(why or "")
    if "private" in w:
        return f"ODRZUT: prywatny{who}"
    if "already_following" in w:
        return f"ODRZUT: juz obserwujesz{who}"
    return f"ODRZUT: {w}{who}"


def gate_fail_line(why: str, username: str | None = None) -> str:
    """Gate fail po ludzku (ratio / bio / followers…)."""
    who = f" @{username}" if username else ""
    parts = [p.strip() for p in str(why or "").split(",") if p.strip()]
    human = [_gate_reason_pl(p) for p in parts] or ["bramka"]
    return f"ODRZUT (bramka): {'; '.join(human)}{who}"


def _gate_reason_pl(why: str) -> str:
    w = str(why or "")
    if w == "private":
        return "profil prywatny"
    if w == "posts_unknown":
        return "nieznana liczba postow"
    if w.startswith("posts<"):
        return f"za malo postow ({w})"
    if w == "followers_unknown":
        return "nieznana liczba obserwujacych"
    if w.startswith("followers<"):
        return f"za malo obserwujacych ({w})"
    if w.startswith("followers>"):
        return f"za duzo obserwujacych ({w})"
    if w.startswith("ratio<"):
        return f"slaby stosunek followers/following ({w})"
    if w.startswith("bio_excl:"):
        return f"bio: wykluczenie „{w.split(':', 1)[1]}”"
    return w


# ------------------------------------------------------------------
# story / siatka / AI
# ------------------------------------------------------------------
def story_watching_line() -> str:
    return "Relacja — ogladam…"


def story_none_line() -> str:
    return "Relacja — brak"


def story_fail_line() -> str:
    return "Relacja — nie otwarto"


def grid_capture_line() -> str:
    return "Siatka — robie zrzut…"


def grid_ai_wait_line() -> str:
    return "Siatka — czekam na AI…"


def grid_capture_fail_line() -> str:
    return "Siatka — zrzut nieudany"


def say_grid_ai_result(result: dict | None) -> None:
    """Podsumowanie AI siatki + powody kafelków niche (jak score/powod w reels).

    1) Liczby: niche / excluded / neutral
    2) Per kafelek niche: `Kafelek N — niche: <reason>`
    3) Wykluczone skrótowo (numery + krótki reason, max kilka)
    Bez raw JSON — operator widzi które kafelki pasują i dlaczego.

    Idempotent: `_grid_ai_said` — Vision i visit mogą wołać tę samą
    funkcję bez duplikatu linii w terminalu.
    """
    r = result if isinstance(result, dict) else {}
    # Już wypisane (np. vision.run → tu → visit) — nie powtarzaj
    if r.get("_grid_ai_said"):
        return

    err = r.get("_error") or r.get("error")
    if err == "niche_not_accepted":
        say("Siatka AI — nisza niezaakceptowana, pomijam")
        r["_grid_ai_said"] = True
        return
    if err:
        say(f"Siatka AI — blad ({err})")
        r["_grid_ai_said"] = True
        return

    tiles = r.get("tiles") or {}
    ordered = _sorted_tile_items(tiles)
    if not ordered:
        say("Siatka AI — brak kafelkow")
        r["_grid_ai_said"] = True
        return

    def _cat(t) -> str:
        # Model bywa niespójny z casingiem ("Niche" / "NICHE")
        return str((t or {}).get("category") or "").strip().lower()

    n_niche = sum(1 for _, t in ordered if _cat(t) == "niche")
    n_excl = sum(1 for _, t in ordered if _cat(t) == "excluded")
    n_neut = sum(1 for _, t in ordered if _cat(t) == "neutral")
    say(f"Siatka AI — niche {n_niche}, excluded {n_excl}, neutral {n_neut} "
        f"(z {len(ordered)})")

    # Pasujące — numer + reason z AI (mirror say_ai_result: decyzja + powod)
    niche_items = [(n, t) for n, t in ordered if _cat(t) == "niche"]
    if niche_items:
        nums = ", ".join(str(n) for n, _ in niche_items)
        say(f"pasuje: {nums}")
        for n, t in niche_items:
            say(f"Kafelek {n} — niche: {_tile_reason(t)}")
    else:
        say("pasuje: (brak)")

    # Wykluczone — krótko (przy 0 pomijamy)
    excl_items = [(n, t) for n, t in ordered if _cat(t) == "excluded"]
    if excl_items:
        # Max 4 linie — reszta tylko jako lista numerów
        shown = excl_items[:4]
        rest = excl_items[4:]
        for n, t in shown:
            say(f"Kafelek {n} — excluded: {_tile_reason(t)}")
        if rest:
            say("wykluczone tez: " + ", ".join(str(n) for n, _ in rest))

    r["_grid_ai_said"] = True


def _sorted_tile_items(tiles) -> list[tuple]:
    """[(num, tile_dict), ...] rosnąco po numerze kafelka.

    Akceptuje dict (klucze \"1\"..) albo listę [{num/id/tile, ...}, ...].
    """
    if isinstance(tiles, list):
        # AI czasem zwraca listę zamiast mapy numer→kafelek
        as_dict = {}
        for i, t in enumerate(tiles, 1):
            if not isinstance(t, dict):
                continue
            raw_n = t.get("num", t.get("id", t.get("tile", i)))
            as_dict[str(raw_n)] = t
        tiles = as_dict
    if not isinstance(tiles, dict):
        return []

    def _key(item):
        k = item[0]
        try:
            return (0, int(k))
        except (TypeError, ValueError):
            return (1, str(k))
    out = []
    for k, t in sorted(tiles.items(), key=_key):
        try:
            num = int(k)
        except (TypeError, ValueError):
            num = k
        out.append((num, t if isinstance(t, dict) else {}))
    return out


def _tile_reason(tile: dict | None, *, max_len: int = 120) -> str:
    """Krótki reason z AI; ucięty do czytelnej linii terminala.

    Szuka `reason` / `powod` / `reasons[0]` — modele bywają niespójne.
    """
    t = tile or {}
    r = t.get("reason") or t.get("powod") or ""
    if not r:
        reasons = t.get("reasons")
        if isinstance(reasons, list) and reasons:
            r = reasons[0]
        elif isinstance(reasons, str):
            r = reasons
    r = str(r or "").strip()
    if not r:
        return "(brak)"
    if len(r) <= max_len:
        return r
    return r[: max_len - 3] + "..."


def match_ok_line(*, n_niche=None, n_excluded=None) -> str:
    """Siatka przeszła match — odpowiednik ODRZUT, ale PASS."""
    bits = []
    if n_niche is not None:
        bits.append(f"niche={n_niche}")
    if n_excluded is not None:
        bits.append(f"excl={n_excluded}")
    extra = f" [{', '.join(bits)}]" if bits else ""
    return f"OK (siatka){extra}"


def match_fail_line(why: str, *, n_niche=None, n_excluded=None) -> str:
    """Match fail po ludzku."""
    w = str(why or "match")
    mapping = {
        "no_ai_tiles": "brak wyniku AI / kafelkow",
    }
    human = mapping.get(w)
    if not human:
        if w.startswith("excluded_ratio>"):
            human = f"za duzo wykluczen ({w})"
        elif w.startswith("valid_tiles<"):
            human = f"za malo kafelkow po wykluczeniach ({w})"
        elif w.startswith("niche_tiles<"):
            human = f"za malo kafelkow niszy ({w})"
        elif w.startswith("ratio<"):
            human = f"za niski udzial niszy ({w})"
        else:
            human = w
    extra = ""
    if n_niche is not None or n_excluded is not None:
        extra = f" [niche={n_niche if n_niche is not None else '?'}, " \
                f"excl={n_excluded if n_excluded is not None else '?'}]"
    return f"ODRZUT (siatka): {human}{extra}"


# ------------------------------------------------------------------
# utrata ekranu profilu
# ------------------------------------------------------------------
def left_profile_line(where: str | None = None, *, top: str | None = None) -> str:
    """Wizyta zeszła z profile_other — krótko PL zamiast raw top=/where=.

    Szczegóły (top, where) zostają w events.jsonl (`profile.visit.left_profile`).
    """
    w = str(where or "").strip()
    # Mapowanie where → fraza dla operatora
    if w == "after_like_tiles":
        return "Profil — utracony ekran (po lajkach kafelków)"
    if w == "after_story":
        return "Profil — utracony ekran (po relacji)"
    if w in ("grid", "after_grid") or w.startswith("grid"):
        return "Profil — utracony ekran (przy siatce)"
    if top:
        return f"Wizyta przerwana — nie na profilu (jest {top})"
    return "Wizyta przerwana — nie na profilu"


# ------------------------------------------------------------------
# lajki / follow
# ------------------------------------------------------------------
def tiles_like_line(nums, liked: int) -> str:
    """Ktore kafelki i ile polubionych."""
    if not nums and liked <= 0:
        return "Lajki kafelkow — brak kandydatow"
    label = ",".join(str(n) for n in nums) if nums else "—"
    return f"Lajki kafelkow — {label} (ok {liked})"


def follow_ok_line(*, scoring: float | None = None) -> str:
    sc = f", score {scoring:.2f}" if scoring is not None else ""
    return f"Follow{sc}"


def follow_skip_line(*, scoring: float | None = None, reason: str | None = None) -> str:
    """Follow nie doszedł — zawsze z powodem gdy action=follow a followed=false.

    Live 13:56: „Skip follow — score 0.79" przy decision=follow myliło operatora.
    """
    sc = f"score {scoring:.2f}" if scoring is not None else None
    if reason:
        if sc:
            return f"Follow — nie ({reason}, {sc})"
        return f"Follow — nie ({reason})"
    if sc:
        return f"Skip follow — {sc}"
    return "Skip follow"


def follow_limit_line(reason: str | None = None) -> str:
    return f"Follow — limit ({reason or 'limit'})"
