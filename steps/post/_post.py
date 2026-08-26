"""Wspólne elementy ekranu pojedynczej treści (post albo rolka z siatki).

**Selektory NIEPOTWIERDZONE.** Żaden dump w `calibration/samples/` nie pokrywa
ekranu hashtaga ani otwartego posta — te ID pochodzą z tej rodziny wersji IG,
ale nie były sprawdzone na naszym urządzeniu. Każdy krok, który ich używa,
kończy się `skipped` z wyraźnym powodem zamiast udawać sukces, więc pierwszy
przebieg na żywo da diagnozę, a nie przypadkowe tapnięcia.

Po kalibracji (E7) listy zostaną przycięte do tego, co faktycznie występuje.
"""
from __future__ import annotations

import re

LIKE_BTN_IDS = ("com.instagram.android:id/row_feed_button_like",
                "com.instagram.android:id/like_button")
LIKED_MARKERS = ("nie lubię", "unlike", "polubione", "liked")

# „polubi" łapie też „Liczba polubień" / „Polubione przez" (viewer Clips)
LIKERS_LINK_HINTS = ("polubi", "liked by", "wyświetl polubienia", "like count")
COMMENTS_LINK_HINTS = ("komentar", "comment")

# Viewer posta z siatki profilu (action_bar „Posty"): serce/komentarz bywają
# nieclickable — wejście w listę to klikalny LICZNIK obok przycisku.
_FEED_LIKE_BTN = "com.instagram.android:id/row_feed_button_like"
_FEED_COMMENT_BTN = "com.instagram.android:id/row_feed_button_comment"
_LIKE_COUNT_IDS = (
    "com.instagram.android:id/like_count",
    "com.instagram.android:id/like_count_text",
    "com.instagram.android:id/like_and_view_count_header_container",
)
_COUNT_RE = re.compile(
    r"^\d[\d\s\u00a0.,]*\s*(tys\.?|mln\.?|k|m)?$", re.IGNORECASE)

# Kafelki siatki. `grid_card_layout_container` = wyniki wyszukiwania,
# `image_button` = siatka postów na profilu, reszta = zakładka Rolki / clipy.
TILE_IDS = (
    "com.instagram.android:id/grid_card_layout_container",
    "com.instagram.android:id/image_button",
    "com.instagram.android:id/media_thumbnail",
    "com.instagram.android:id/image_preview",
    "com.instagram.android:id/media_container",
    "com.instagram.android:id/preview_clip",
    "com.instagram.android:id/clips_grid_item",
    "com.instagram.android:id/video_thumbnail",
)

# Typ treści widać JUŻ W SIATCE, w opisie kafelka:
#   „Autor rolki: ALIONA, wiersz: 0, kolumna: 0"
#   „Autor zdjęcia: _crs24_, wiersz: 0, kolumna: 0"
REEL_DESC_HINTS = (
    "autor rolki", "reel by", "author of the reel",
    "rolka autorstwa", "video by",
)
PHOTO_DESC_HINTS = ("autor zdjęcia", "photo by", "author of the photo")

# Wzorzec wymaga SŁOWA + LICZBY, a nie samego podciągu. Pierwsza wersja szukała
# po prostu „row" i na żywym przebiegu dopasowała polskie „w-p-r-o-w-adzania"
# w systemowym okienku wyboru klawiatury — bot tapnął w dialog Androida,
# biorąc go za kafelek siatki. To ta sama pułapka, przed którą ostrzega
# komentarz przy POPUP_HINTS: allowlista konkretów, nigdy generyczne słowa.
TILE_DESC_RE = re.compile(
    r"\b(?:wiersz|rz[ąa]d|row)\b\s*:?\s*\d+", re.IGNORECASE | re.UNICODE)
# Zakładka Rolki: czasem „Rolka …", „Video …" bez numeru wiersza
REEL_TILE_DESC_RE = re.compile(
    r"\b(?:rolka|reel)\b", re.IGNORECASE | re.UNICODE)

IG_PACKAGE_HINT = "instagram"

# Czy otwarta treść to rolka, czy zwykły post
REEL_MARKERS = ("clips_video_container", "clips_media_component",
                "clips_viewer_media_container")

# Tytuł action bara w viewerze postów z profilu (PL/EN)
POST_VIEWER_TITLES = ("posty", "posts", "publications")
# Gdy to nadal widać — jesteśmy na siatce / zakładce profilu, nie w treści
PROFILE_GRID_HINTS = (
    "zdjęcia, na których jesteś",
    "photos of you",
    "obserwuj",
    "message",
    "wiadomość",
)


def center(bounds):
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None


def collect_tiles(flat: list[dict], *, allow_square_fallback: bool = False
                  ) -> list[dict]:
    """Kafelki siatki w kolejności, w jakiej renderuje je Instagram.

    `allow_square_fallback` — tylko dla zakładki Rolki (kwadratowe klikalne
    komórki bez znanego ID). Na liście sugestii wyszukiwania MUSI zostać False,
    inaczej „Otwórz relację" w trayu wygląda jak kafelek.
    """
    out = []
    for e in flat:
        pkg = (e.get("package") or "").lower()
        if pkg and IG_PACKAGE_HINT not in pkg:
            continue
        rid = (e.get("resource-id") or "")
        desc = e.get("content-desc") or ""
        rid_tail = rid.split("/")[-1] if "/" in rid else rid
        # preview_clip_play_count = badge, nie cały kafelek — pomiń
        if rid_tail == "preview_clip_play_count":
            continue
        known_id = rid in TILE_IDS or any(
            rid.endswith(x.split("/")[-1]) for x in TILE_IDS)
        if not (known_id or TILE_DESC_RE.search(desc)
                or REEL_TILE_DESC_RE.search(desc)):
            continue
        c = center(e.get("bounds"))
        if c:
            out.append({"center": c, "desc": desc, "kind": tile_kind(desc),
                        "bounds": e.get("bounds")})

    if allow_square_fallback and len(out) < 2:
        out.extend(_collect_square_grid_fallbacks(flat, existing=out))

    merged: dict = {}
    for t in out:
        key = (t["center"][0] // 20, t["center"][1] // 20)
        prev = merged.get(key)
        if prev is None or (not prev["desc"] and t["desc"]):
            merged[key] = t

    return sorted(merged.values(),
                  key=lambda t: (t["center"][1] // 50, t["center"][0]))


def _collect_square_grid_fallbacks(flat: list[dict], *,
                                   existing: list[dict]) -> list[dict]:
    """Klikalne ~kwadratowe komórki w dolnej części ekranu (siatka rolek)."""
    skip_desc = ("otwórz relację", "open story", "relację", "story")
    taken = {(t["center"][0] // 20, t["center"][1] // 20) for t in existing}
    out = []
    for e in flat:
        pkg = (e.get("package") or "").lower()
        if pkg and IG_PACKAGE_HINT not in pkg:
            continue
        if not e.get("clickable"):
            continue
        desc = (e.get("content-desc") or "")
        dlow = desc.casefold()
        if any(s in dlow for s in skip_desc):
            continue
        b = e.get("bounds") or ""
        c = center(b)
        if not c:
            continue
        try:
            a, r = str(b).split("][")
            x1, y1 = map(int, a.strip("[").split(","))
            x2, y2 = map(int, r.strip("]").split(","))
        except Exception:
            continue
        w, h = x2 - x1, y2 - y1
        # kafelek ~1/3 szerokości, kwadratowy, poniżej headera profilu
        if w < 200 or h < 200 or y1 < 500:
            continue
        if abs(w - h) > max(80, int(w * 0.35)):
            continue
        key = (c[0] // 20, c[1] // 20)
        if key in taken:
            continue
        taken.add(key)
        out.append({"center": c, "desc": desc,
                    "kind": tile_kind(desc) or "reel", "bounds": b})
    return out


def tile_kind(desc: str | None) -> str | None:
    """`reel` / `post` / `None` gdy opis nie mówi, co to za treść."""
    d = (desc or "").lower()
    if any(h in d for h in REEL_DESC_HINTS) or REEL_TILE_DESC_RE.search(d or ""):
        # „5 zdjęć …" to post/karuzela, nie rolka mimo słowa video czasem
        if any(h in d for h in PHOTO_DESC_HINTS):
            return "post"
        if "zdję" in d and "autor rolki" not in d and "rolka" not in d:
            return "post"
        return "reel"
    if any(h in d for h in PHOTO_DESC_HINTS):
        return "post"
    return None


def is_reel(flat: list[dict]) -> bool:
    for e in flat:
        rid = (e.get("resource-id") or "").lower()
        if any(m in rid for m in REEL_MARKERS):
            return True
    return False


def is_post_viewer(flat: list[dict]) -> bool:
    """Czy jesteśmy w viewerze postów (tytuł „Posty" / like bez siatki profilu)."""
    for e in flat:
        rid = (e.get("resource-id") or "")
        text = (e.get("text") or "").strip().casefold()
        if "action_bar_title" in rid and text in POST_VIEWER_TITLES:
            return True
    has_like = any((e.get("resource-id") or "") in LIKE_BTN_IDS for e in flat)
    if has_like and not still_on_profile_grid(flat):
        return True
    return False


def still_on_profile_grid(flat: list[dict]) -> bool:
    """True gdy nadal widać siatkę / nagłówek profilu (tap nie otworzył treści)."""
    tiles = collect_tiles(flat)
    if len(tiles) >= 4:
        return True
    bio = any("profile_header_bio" in (e.get("resource-id") or "") for e in flat)
    follow = any("profile_header_follow" in (e.get("resource-id") or "")
                 or "follow_button" in (e.get("resource-id") or "")
                 for e in flat)
    # bio + follow + choć kilka kafelków = profil
    if bio and follow and len(tiles) >= 1:
        return True
    return False


def media_viewer_open(flat: list[dict], want_kinds: list[str] | None = None) -> bool:
    """Czy po tapnięciu w kafelek faktycznie weszliśmy w post/rolkę."""
    if still_on_profile_grid(flat):
        return False
    kinds = want_kinds or []
    if not kinds:
        return is_post_viewer(flat) or is_reel(flat)
    if "post" in kinds and is_post_viewer(flat):
        return True
    if "reel" in kinds and is_reel(flat):
        return True
    return False


def find_like_button(flat: list[dict]):
    for e in flat:
        if (e.get("resource-id") or "") in LIKE_BTN_IDS:
            blob = ((e.get("content-desc") or "") + " " + (e.get("text") or "")).lower()
            return center(e.get("bounds")), any(m in blob for m in LIKED_MARKERS)
    return None, False


def find_clickable_with(flat: list[dict], hints) -> tuple | None:
    for e in flat:
        if not e.get("clickable"):
            continue
        blob = ((e.get("text") or "") + " " + (e.get("content-desc") or "")).lower()
        if any(h in blob for h in hints):
            c = center(e.get("bounds"))
            if c:
                return c
    return None


def _bounds_box(bounds) -> tuple[int, int, int, int] | None:
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return x1, y1, x2, y2
    except Exception:
        return None


def _looks_like_count(text: str) -> bool:
    """„258 tys." / „699" / „1 310" — licznik obok serca/komentarza."""
    t = (text or "").strip().replace("\u00a0", " ")
    if not t:
        return False
    return bool(_COUNT_RE.match(t))


def _count_right_of(flat: list[dict], anchor_rid: str) -> tuple | None:
    """Klikalny licznik na prawo od przycisku (ten sam rząd Y)."""
    anchor = None
    for e in flat:
        if (e.get("resource-id") or "") == anchor_rid:
            anchor = e
            break
    if not anchor:
        return None
    box = _bounds_box(anchor.get("bounds"))
    if not box:
        return None
    x1, y1, x2, y2 = box
    mid_y = (y1 + y2) // 2
    best = None  # (x, center)
    for e in flat:
        if not e.get("clickable"):
            continue
        text = (e.get("text") or "").strip()
        if not _looks_like_count(text):
            continue
        c = center(e.get("bounds"))
        eb = _bounds_box(e.get("bounds"))
        if not c or not eb:
            continue
        ex1, ey1, ex2, ey2 = eb
        # na prawo od ikony, ten sam rząd
        if ex1 >= x2 - 8 and abs(((ey1 + ey2) // 2) - mid_y) < 90:
            if best is None or c[0] < best[0]:
                best = (c[0], c)
    return best[1] if best else None


def find_likers_entry(flat: list[dict]) -> tuple | None:
    """Wejście w listę lajkujących — Clips (tekst/ID) albo viewer „Posty" (licznik)."""
    c = find_clickable_with(flat, LIKERS_LINK_HINTS)
    if c:
        return c
    for e in flat:
        if (e.get("resource-id") or "") in _LIKE_COUNT_IDS and e.get("clickable"):
            c = center(e.get("bounds"))
            if c:
                return c
    # Siatka profilu → media viewer: „258 tys." obok row_feed_button_like
    return _count_right_of(flat, _FEED_LIKE_BTN)


def find_comments_entry(flat: list[dict]) -> tuple | None:
    """Wejście w komentarze — tekst / licznik obok ikony / środek przycisku.

    Na viewerze „Posty" ikona bywa `clickable=false`, a klikalny jest wrapper
    lub liczba „699" po prawej — stąd kilka strategii.
    """
    c = find_clickable_with(flat, COMMENTS_LINK_HINTS)
    if c:
        return c
    c = _count_right_of(flat, _FEED_COMMENT_BTN)
    if c:
        return c
    # Wrapper clickable nachodzący na ikonę komentarza
    btn_box = None
    for e in flat:
        if (e.get("resource-id") or "") == _FEED_COMMENT_BTN:
            btn_box = _bounds_box(e.get("bounds"))
            break
    if btn_box:
        bx1, by1, bx2, by2 = btn_box
        for e in flat:
            if not e.get("clickable"):
                continue
            eb = _bounds_box(e.get("bounds"))
            if not eb:
                continue
            ex1, ey1, ex2, ey2 = eb
            # nachodzi na ikonę w tym samym rzędzie
            if ey1 <= by2 and ey2 >= by1 and ex1 <= bx2 and ex2 >= bx1:
                c = center(e.get("bounds"))
                if c:
                    return c
        # Ostatecznie: tap w środek ikony (rodzic przejmie touch)
        return ((bx1 + bx2) // 2, (by1 + by2) // 2)
    return None
