"""Helpers do wyciągania meta bieżącej rolki z hierarchii.

Licznik lajków/komentarzy nie ma resource-id w tej wersji IG — jest tylko w
`content-desc` przycisków po prawej stronie ekranu. Format polski (PL):
  "Liczba polubień: 1090. Wyświetl polubienia."
  "Liczba komentarzy: 23. Wyświetl komentarze."
  "Repostowano 26 razy"                          — reposts
  "Liczba ponownych udostępnień: 413"            — shares (alt)
  "Liczba zapisań to 58"                         — saves

Dla EN dodaj alternatywne wzorce w COUNT_PATTERNS.
"""
from __future__ import annotations
import re
from v2.core.screen_map import flatten_hierarchy

IDS = {
    "author":         "com.instagram.android:id/clips_author_username",
    "media_component":"com.instagram.android:id/clips_media_component",
}

# Opis: `clips_caption_component` bywa PUSTYM kontenerem (tekst siedzi w
# dziecku `caption_text_view`), więc sprawdzamy oba — tak samo jak
# `reels.read_caption_comments`.
CAPTION_IDS = ("com.instagram.android:id/caption_text_view",
               "com.instagram.android:id/clips_caption_component")

# {klucz meta → lista regexów szukających liczby w content-desc}
# Kolejność ważna — pierwszy hit wygrywa.
COUNT_PATTERNS = {
    "likes": [
        re.compile(r"Liczba polubie[nń]:\s*([\d\s.,KkMm]+)", re.I),
        re.compile(r"(\d[\d\s.,]*[KkMm]?)\s+likes?", re.I),
    ],
    "comments": [
        re.compile(r"Liczba komentarzy:\s*([\d\s.,KkMm]+)", re.I),
        re.compile(r"(\d[\d\s.,]*[KkMm]?)\s+comments?", re.I),
    ],
    "reposts": [
        re.compile(r"Repostowano\s+(\d[\d\s.,]*[KkMm]?)\s+razy", re.I),
        re.compile(r"Liczba ponownych udost[eę]pnie[nń]:\s*([\d\s.,KkMm]+)", re.I),
    ],
    "saves": [
        re.compile(r"Liczba zapisa[nń] to\s+(\d[\d\s.,]*[KkMm]?)", re.I),
    ],
}


AD_MARKERS = ("sponsorowane", "sponsored", "reklama")  # PL / EN


def read_current_reel(device, flat: list[dict] | None = None) -> dict:
    """Zwraca {author, likes, comments, reposts, saves, is_ad, caption, marker}.
    is_ad=True gdy w drzewie widnieje etykieta 'Sponsorowane' / 'Sponsored' / 'Reklama'.

    Opcjonalny `flat` pozwala uniknąć kolejnego dumpu gdy krok już go zrobił.
    """
    if flat is None:
        flat = flatten_hierarchy(device.hierarchy(force=True))
    out = {"author": None, "likes": None, "comments": None,
           "reposts": None, "saves": None, "is_ad": False,
           "caption": None, "marker": None}
    for n in flat:
        rid  = n.get("resource-id")
        text = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()

        # autor
        if rid == IDS["author"] and text:
            out["author"] = text

        # opis — pomaga odróżnić rolki TEGO SAMEGO autora. Bywa niedostępny
        # (kontener bez tekstu), wtedy marker opiera się na licznikach.
        if rid in CAPTION_IDS and not out["caption"]:
            blob_cap = (text or desc).strip()
            if blob_cap:
                out["caption"] = blob_cap

        # liczniki — parsujemy content-desc + text (button ma oba)
        blob = f"{desc} {text}".strip()
        if not blob:
            continue

        # is_ad — jakikolwiek node z etykietą "Sponsorowane"
        if not out["is_ad"]:
            low = blob.lower()
            if any(m in low for m in AD_MARKERS):
                out["is_ad"] = True
        for key, patterns in COUNT_PATTERNS.items():
            if out[key] is not None:  # już mamy
                continue
            for pat in patterns:
                m = pat.search(blob)
                if m:
                    v = _to_int(m.group(1))
                    if v is not None:
                        out[key] = v
                        break
    out["marker"] = build_marker(out)
    return out


def build_marker(meta: dict) -> str | None:
    """Odcisk bieżącej rolki — do wykrywania, czy swipe faktycznie przewinął.

    Wcześniej markerem był SAM AUTOR. Dwie kolejne rolki tego samego twórcy
    dawały identyczny marker, więc weryfikacja swipe'a uznawała, że nic się
    nie zmieniło — bot przewijał drugi raz albo zostawał na tej samej rolce
    i powtarzał na niej akcje (otwierał komentarze dwa razy pod rząd).

    Składamy więc kilka niezależnych sygnałów. Liczniki bywają nieodczytane
    (nakładka renderuje się z opóźnieniem), dlatego marker powstaje z tego,
    co akurat jest dostępne. `None` = nic sensownego nie odczytano i wołający
    NIE MOŻE założyć, że rolka się zmieniła.
    """
    parts = []
    if meta.get("author"):
        parts.append(f"au:{meta['author']}")
    cap = (meta.get("caption") or "").strip()
    if cap:
        # prefiksy muszą być rozłączne — samo "c" oznaczałoby zarówno caption,
        # jak i comments, więc dwa różne odczyty mogłyby dać ten sam marker
        parts.append(f"cap:{cap[:60]}")
    for key in ("likes", "comments", "reposts", "saves"):
        if meta.get(key) is not None:
            parts.append(f"{key[:2]}:{meta[key]}")
    return "|".join(parts) if parts else None


def _to_int(s: str) -> int | None:
    # "1 234" / "1.2K" / "1,234"
    s = s.strip()
    m = re.match(r"^([\d.,\s]+)\s*([KkMm]?)$", s)
    if not m: return None
    raw   = m.group(1).replace(" ", "").replace(",", "").replace(".", "")
    mult  = m.group(2).lower()
    try:   n = int(raw)
    except: return None
    if mult == "k": n *= 1_000
    if mult == "m": n *= 1_000_000
    return n
