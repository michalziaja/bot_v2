"""Wspólne elementy podglądu relacji — gesty i detekcje.

Współrzędne gestów są **ułamkami rozdzielczości**, nie pikselami: zakresy
pochodzą z kalibracji na SM-A505FN (1080×2340) i tylko w tej postaci przenoszą
się na inne ekrany. Każdy z nich ma powód opisany przy stałej — to są miejsca,
w które wolno kliknąć, ustalone metodą prób na żywym koncie.
"""
from __future__ import annotations

from v2.core.screen_map import flatten_hierarchy

STORY = "story_viewer"

# Tap „dalej" w wąskim pasku przy PRAWEJ krawędzi: z dala od nagłówka (tam jest
# nick autora — tap otwiera jego profil) i od dolnego pola odpowiedzi.
NEXT_X_FRAC = (0.954, 0.987)
NEXT_Y_FRAC = (0.384, 0.748)

# Swipe w lewo = następny użytkownik (natywny gest IG). Y w środkowym pasie.
SWIPE_Y_FRAC = (0.470, 0.598)
SWIPE_FROM_X_FRAC = (0.787, 0.880)
SWIPE_TO_X_FRAC = (0.093, 0.185)

# Swipe zamykający w dół — lokalny gest pod nagłówkiem, z dala od krawędzi.
CLOSE_X_FRAC = (0.537, 0.602)
CLOSE_FROM_Y_FRAC = (0.124, 0.141)
CLOSE_TO_Y_FRAC = (0.419, 0.500)

# Potwierdzone na żywym dumpie: reklama w relacji ma własny element z etykietą.
# Trzymamy też szukanie po słowach kluczowych jako drugą drogę — etykieta może
# nazywać się inaczej w innej wersji IG — ale ID jest pierwsze, bo słowo
# „sponsorowane" potrafi pojawić się też w treści cudzej relacji.
SPONSORED_ID_HINTS = ("sponsored_label", "sponsored_pill", "reel_item_sponsored")
SPONSORED_HINTS = ("sponsorowane", "sponsored", "reklama")

# Klawiatura odpowiedzi / panel emotek nad relacją. Otwiera się po przypadkowym
# tapie w dolną część ekranu — dopóki jest, każdy kolejny gest ląduje w niej.
REPLY_MARKERS = ("reply_text", "story_composer_text", "story_message_composer",
                 "emoji_picker", "sticker_tray", "reaction_picker")


def _pick(rng, w: int, h: int, xf, yf) -> tuple[int, int]:
    return (int(rng.uniform(*xf) * w), int(rng.uniform(*yf) * h))


def resolution(ctx) -> tuple[int, int]:
    return ctx.account.device["_hardware"]["resolution"]


def in_viewer(ctx) -> bool:
    """Czy nadal jesteśmy w podglądzie relacji.

    Sprawdzane PRZED każdym gestem — relacja potrafi zamknąć się sama
    (koniec serii), a wtedy tap „dalej" trafia w to, co jest pod spodem.
    """
    return ctx.navigator.identify().top == STORY


def sponsored_marker(flat: list[dict]) -> str | None:
    """Zwróć element, który zdradza reklamę — albo `None`.

    Zwracamy TREŚĆ trafienia, nie samo `True`, bo detekcja opiera się na
    słowach kluczowych i przy fałszywym trafieniu z logu musi dać się odczytać,
    co konkretnie ją odpaliło.
    """
    by_keyword = None
    for e in flat:
        text = (e.get("text") or "")
        desc = (e.get("content-desc") or "")
        rid = (e.get("resource-id") or "").lower()
        short_rid = rid.split("id/")[-1]

        if any(h in rid for h in SPONSORED_ID_HINTS):
            return f"{short_rid or '-'}: {(text or desc)[:60]}"
        if by_keyword is None and any(h in f"{text} {desc}".lower()
                                      for h in SPONSORED_HINTS):
            by_keyword = f"{short_rid or '-'}: {(text or desc)[:60]}"
    return by_keyword


def is_sponsored(flat: list[dict]) -> bool:
    """Reklama w segmencie. Tap na niej otwiera stronę reklamodawcy, więc
    przejście dalej musi iść swipe'm w bok, nie tapem."""
    return sponsored_marker(flat) is not None


def has_reply_keyboard(flat: list[dict]) -> bool:
    for e in flat:
        rid = (e.get("resource-id") or "").lower()
        if any(m in rid for m in REPLY_MARKERS):
            return True
    return False


def dump(ctx) -> list[dict]:
    return flatten_hierarchy(ctx.device.hierarchy(force=True))


def dismiss_reply_keyboard(ctx, flat: list[dict]) -> list[dict]:
    """Jeden BACK gdy otworzyła się klawiatura odpowiedzi; zwraca świeży dump."""
    if not has_reply_keyboard(flat):
        return flat
    ctx.log("stories.reply_keyboard_dismiss")
    ctx.device.press_back()
    ctx.humanizer.pause("tap")
    return dump(ctx)


def tap_next_segment(ctx, rng) -> None:
    w, h = resolution(ctx)
    ctx.ht.tap(*_pick(rng, w, h, NEXT_X_FRAC, NEXT_Y_FRAC), pause_kind="tap")


def swipe_next_user(ctx, rng) -> None:
    w, h = resolution(ctx)
    y = int(rng.uniform(*SWIPE_Y_FRAC) * h)
    ctx.ht.swipe(int(rng.uniform(*SWIPE_FROM_X_FRAC) * w), y,
                 int(rng.uniform(*SWIPE_TO_X_FRAC) * w), y,
                 duration_ms=int(rng.uniform(350, 450)), pause_kind="tap")


def swipe_close(ctx, rng) -> None:
    w, h = resolution(ctx)
    x = int(rng.uniform(*CLOSE_X_FRAC) * w)
    ctx.ht.swipe(x, int(rng.uniform(*CLOSE_FROM_Y_FRAC) * h),
                 x, int(rng.uniform(*CLOSE_TO_Y_FRAC) * h),
                 duration_ms=int(rng.uniform(450, 500)), pause_kind="tap")
