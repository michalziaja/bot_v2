"""Czas zatrzymania na treści, liczony z tego, ile jest do przeczytania.

Dotąd „czytanie opisu" to był stały zakres 2–6 s niezależnie od tego, czy opis
ma 8 czy 800 znaków. Z zewnątrz wygląda to jak automat: identyczny rozkład
czasów bez związku z treścią.

Model:

    dwell = rzut_oka + czytanie + (12% szans na) zawieszenie

  - **rzut oka** 1.2–3.5 s — obejrzenie zdjęcia/klatki, zanim w ogóle padnie
    wzrok na tekst,
  - **czytanie** = proza / tempo, gdzie tempo to 13–22 znaków na sekundę
    losowane per post (ludzie czytają nierówno), przycięte do 16 s — nikt nie
    czyta opisu w feedzie dwie minuty,
  - **zawieszenie** 3–10 s — co ósmy, dziewiąty post coś zatrzymuje uwagę.

**Proza to nie to samo co opis.** Z tekstu wycinamy @wzmianki, #hashtagi i URL-e:
ściana trzydziestu hashtagów nie jest czytaniem, a bez tego filtra dawałaby
kilkanaście sekund wpatrywania się w ekran.
"""
from __future__ import annotations

import random
import re

GLANCE_S = (1.2, 3.5)
CHARS_PER_S = (13.0, 22.0)
READ_CAP_S = 16.0
LINGER_CHANCE = 0.12
LINGER_S = (3.0, 10.0)

_MENTION = re.compile(r"[@#][\w.\-_]+", re.UNICODE)
_URL = re.compile(r"https?://\S+|\b\w+\.(com|pl|net|org|io|co)\b\S*", re.IGNORECASE)
_WS = re.compile(r"\s+")


def prose_length(text: str | None) -> int:
    """Długość tekstu po usunięciu wzmianek, hashtagów i linków."""
    if not text:
        return 0
    t = _URL.sub(" ", str(text))
    t = _MENTION.sub(" ", t)
    return len(_WS.sub(" ", t).strip())


def compute(text: str | None = None, rng: random.Random | None = None,
            *, fallback: tuple[float, float] | None = None) -> dict:
    """Zwróć `{seconds, prose, mode, parts}` — komplet do zalogowania.

    `fallback` to zakres używany, gdy tekstu NIE UDAŁO SIĘ odczytać (a nie gdy
    post go nie ma). Rozróżnienie jest istotne: post bez opisu naprawdę zajmuje
    tylko rzut oka, a nieodczytany opis to luka w mapie UI i lepiej wtedy trzymać
    się starego, bezpiecznego zakresu.
    """
    r = rng or random

    if text is None and fallback:
        lo, hi = fallback
        return {"seconds": round(r.uniform(lo, hi), 2), "prose": None,
                "mode": "fallback", "parts": {}}

    prose = prose_length(text)
    glance = r.uniform(*GLANCE_S)
    read = min(prose / r.uniform(*CHARS_PER_S), READ_CAP_S) if prose else 0.0
    linger = r.uniform(*LINGER_S) if r.random() < LINGER_CHANCE else 0.0

    total = glance + read + linger
    return {
        "seconds": round(total, 2),
        "prose": prose,
        "mode": "content_aware",
        "parts": {"glance": round(glance, 2), "read": round(read, 2),
                  "linger": round(linger, 2)},
    }
