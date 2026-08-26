"""Krotkie, ludzkie komunikaty z przebiegu rolek (stdout / panel).

Events.jsonl nadal zbiera surowe dane pod statystyki — tu tylko to, co
operator ma przeczytac w trakcie sesji.

Domyslny tag to `reels_niche`; `reels_warmup` / `content_search` podaja
wlasny `prefix=` (panel koloruje znane tagi na niebiesko przez CSS —
bez ANSI, bo bot pisze do pliku/pipe i ESC i tak nie dziala).
"""
from __future__ import annotations
import sys

# Default dla reels_niche; warmup/search nadpisuja przez say(..., prefix=...)
_PREFIX = "reels_niche"


def say(msg: str, *, prefix: str | None = None) -> None:
    """Jedna linia statusu — domyślnie prefix reels_niche (panel: niebieski).

    `content_search` / `reels_warmup` wołają te same kroki `reels.*` —
    wtedy `prefix=…`, żeby log nie wyglądał jak pętla reels_niche.
    """
    tag = prefix if prefix is not None else _PREFIX
    if not (msg or "").strip():
        print(flush=True)
        return
    try:
        print(f"  {tag} {msg}", flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        line = f"  {tag} {msg}\n"
        sys.stdout.buffer.write(line.encode(enc, errors="replace"))
        sys.stdout.flush()


def fmt_count(n) -> str:
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


def watch_line(*, author, seconds=None, interval_s=None, likes=None,
               comments=None, frames=None, caption_only=False) -> str:
    """Start watch: klatki albo tryb tylko-opis (`caption_only`)."""
    who = f"@{author}" if author else "@?"
    # Bez klatek — komunikat dla operatora (gate + ewentualnie opis)
    if caption_only or frames == 0:
        bits = [f"Rolka {who} (bez klatek)"]
    else:
        bits = [f"Klatki {who}"]
        if frames:
            bits.append(f"{frames} szt.")
        if interval_s is not None and seconds is None:
            bits.append(f"co {interval_s:.1f} s")
        elif seconds is not None:
            bits.append(f"~{seconds:.0f} s")
    if likes is not None:
        bits.append(f"{fmt_count(likes)} lajkow")
    if comments is not None:
        bits.append(f"{fmt_count(comments)} komentarzy")
    return ", ".join(bits)


def reel_progress_line(author: str | None, i: int | None = None,
                       n: int | None = None) -> str:
    """Start jednostki: „Rolka @user 2/3” (X/Y z SessionStats)."""
    from v2.plugins.progress_log import with_progress
    who = f"@{author}" if author else "@?"
    return with_progress(f"Rolka {who}", i, n)


def gate_ok_line(*, author, likes=None, comments=None) -> str:
    who = f"@{author}" if author else "@?"
    return (f"Filtr OK - {who}, {fmt_count(likes)} lajkow, "
            f"{fmt_count(comments)} komentarzy")


def reject_line(reason: str) -> str:
    return f"ODRZUT: {reason}"


def caption_line(caption: str | None) -> str:
    if not caption:
        return "Opis: brak / nie odczytano"
    preview = caption if len(caption) <= 90 else caption[:87] + "..."
    return f"Opis: {preview}"


def caption_reading_line() -> str:
    """Komunikat tuż PRZED tapem w opis — terminal = gest na telefonie."""
    return "Czytam opis..."


def comments_reading_line() -> str:
    """Komunikat przed otwarciem sheetu top komentarzy."""
    return "Czytam top komentarze..."


def ai_start_line() -> str:
    """Telefon idle — czekamy na API."""
    return "Analiza AI..."


def ai_mode_line(*, use_frames: bool, read_caption: bool) -> str | None:
    """Krótki opis trybu wejść AI — None gdy domyślnie oba ON."""
    if use_frames and read_caption:
        return None
    if use_frames and not read_caption:
        return "Tryb AI: tylko klatki"
    if not use_frames and read_caption:
        return "Tryb AI: tylko opis"
    return "Tryb AI: brak wejsc (klatki i opis OFF)"


def verdict_pl(verdict) -> str:
    """Werdykt AI po polsku."""
    v = (str(verdict) if verdict is not None else "").strip().lower()
    return {
        "match": "pasuje",
        "partial": "czesciowo",
        "off": "poza nisza",
        "excluded": "wykluczenie",
    }.get(v, v or "?")


def say_ai_result(result: dict | None) -> None:
    """Przystepna odpowiedz AI: score / decyzja / powod (bez surowego JSON)."""
    r = result or {}
    score = r.get("niche_score")
    verdict = r.get("verdict")
    reasons = r.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)] if reasons else []

    say(f"score: {score if score is not None else '?'}")
    say(f"decyzja: {verdict_pl(verdict)}")
    if not reasons:
        say("powod: (brak)")
    else:
        say(f"powod: {reasons[0]}")
        for extra in reasons[1:3]:
            say(f"       {extra}")


def ai_ok_line(*, author, verdict, score) -> str:
    who = f"@{author}" if author else "@?"
    return f"AI: pasuje ({verdict_pl(verdict)}, wynik {score}) - {who}"


def ai_reject_human(*, why, score=None, author=None) -> str:
    """Mapuj techniczne why na zdanie po polsku."""
    who = f" @{author}" if author else ""
    mapping = {
        "verdict=off": "poza nisza",
        "off": "poza nisza",
        "excluded": "trafienie w wykluczenia",
        "no_ai_result": "brak odpowiedzi AI",
        "niche_not_accepted": "nisza niezatwierdzona",
        "no_ai_inputs": "brak wejsc AI (klatki i opis OFF/puste)",
    }
    w = str(why or "odrzut")
    human = mapping.get(w) or mapping.get(w.lower())
    if not human:
        if w.startswith("verdict="):
            human = f"werdykt {w.split('=', 1)[1]}"
        elif w.startswith("score<"):
            human = f"za niski wynik ({w})"
        else:
            human = w
    sc = f", wynik {score}" if score is not None else ""
    return f"{human}{sc}{who}"


def next_reel_line(i: int | None = None, n: int | None = None) -> str:
    """Po ticku: która jednostka właśnie skończona / następna → X/Y."""
    from v2.plugins.progress_log import with_progress
    return with_progress("Nastepna rolka…", i, n)


def hot_profile_line(username: str | None, i: int | None = None,
                     n: int | None = None, *, where: str | None = None) -> str:
    """Hot follow z listy: „Hot @alice 1/3 z listy lajkujacych”."""
    from v2.plugins.progress_log import with_progress
    who = f"@{username}" if username else "@?"
    label = "lajkujacych" if where == "likers" else (
        "komentujacych" if where == "comments" else "listy")
    base = f"Hot {who}"
    msg = with_progress(base, i, n)
    if where:
        return f"{msg} z listy {label}"
    return msg


def swipe_ok_line(*, author_before=None, author_after=None) -> str:
    if author_after and author_before and author_after != author_before:
        return f"Przeszedlem do @{author_after} (z @{author_before})"
    if author_after:
        return f"Przeszedlem dalej (@{author_after})"
    return "Przeszedlem do nastepnej rolki"


def swipe_fail_line() -> str:
    return "Nie udalo sie zmienic rolki — zostajemy w miejscu"
