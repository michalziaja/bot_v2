"""Parser nagłówka profilu — działa dla profile_self i profile_other.

Wyciąga z jednego dumpa: liczniki (posty/followers/following), is_private,
already_following, has_story_ring, bio, verified.

Bio: klasyczne `profile_header_bio_text` ALBO TextView pod MetaComposeView
(nowsze IG bez resource-id — ścieżki typu
…/MetaComposeView/View/View/TextView).
"""
from __future__ import annotations
import re
from v2.core.screen_map import flatten_hierarchy
from v2.steps.profile._grid import parse_bounds


# Wspólne ID (te same co stats_snapshot dla własnego profilu, +
# obce warianty które mogą pojawiać się na profile_other).
# Wartości TextView + stacked (content-desc typu „235obserwatorzy" / „1,2 tys.obserwujący")
# jako fallback, gdy value-node nie da się sparsować.
COUNT_IDS = {
    "posts":     ("com.instagram.android:id/profile_header_familiar_post_count_value",
                  "com.instagram.android:id/profile_header_post_count",
                  "com.instagram.android:id/profile_header_post_count_front_familiar"),
    "followers": ("com.instagram.android:id/profile_header_familiar_followers_value",
                  "com.instagram.android:id/profile_header_followers_count",
                  "com.instagram.android:id/profile_header_followers_stacked_familiar"),
    "following": ("com.instagram.android:id/profile_header_familiar_following_value",
                  "com.instagram.android:id/profile_header_following_count",
                  "com.instagram.android:id/profile_header_following_stacked_familiar"),
}
BIO_IDS = ("com.instagram.android:id/profile_header_bio_text",)
# Nick i imię — warianty per wersja IG. Pierwsze dwa POTWIERDZONE na dumpie
# z żywego przebiegu (SM-A505FN); wcześniej parser znał tylko te dalsze
# i zwracał `username=None`, przez co profile zapisywały się jako „unknown",
# a dedup odwiedzonych przestawał działać.
NAME_IDS = ("com.instagram.android:id/profile_header_full_name_above_vanity",
            "com.instagram.android:id/profile_header_full_name")
USER_IDS = ("com.instagram.android:id/action_bar_title",
            "com.instagram.android:id/action_bar_large_title_auto_size")
FOLLOW_BTN_IDS = (
    "com.instagram.android:id/profile_header_follow_button",
    "com.instagram.android:id/profile_header_user_action_follow_button",
)
STORY_RING_ID = "com.instagram.android:id/reel_ring"
# Klikalna ramka awatara (tap otwiera relację) — pewniejsza niż sam `reel_ring`.
STORY_AVATAR_FRAME_ID = (
    "com.instagram.android:id/row_profile_header_imageview_frame_layout"
)
# Empty-state kontener prywatnego konta (IG 2024+) — niezależny od locale tekstu.
PRIVATE_EMPTY_STATE_IDS = (
    "com.instagram.android:id/private_profile_empty_state",
)
# Teksty PL/EN — pełne frazy (stary bot). NIE samo „prywatny": nowszy IG
# pokazuje „To konto jest prywatne" (nijaki), a „prywatny" ∉ „prywatne".
# Live @ghescrime 2026-08-09: stary matcher → is_private=false → siatka+AI.
# Bez luźnego „prywatn*" — łapałoby nicki/bio typu „…_prywatne".
PRIVATE_HINTS = (
    "to konto jest prywatne",
    "ten profil jest prywatny",
    "konto jest prywatne",
    "profil jest prywatny",
    "this account is private",
    "this profile is private",
    "account is private",
)
FOLLOWING_LABELS = ("obserwujesz", "following")
FOLLOW_LABELS    = ("obserwuj", "follow")

# Compose bio — chrome UI, nie treść bio
_BIO_UI_NOISE = {
    "obserwuj", "obserwujesz", "follow", "following", "message", "wiadomość",
    "edit profile", "edytuj profil", "posty", "posts", "followers", "obserwujący",
    "obserwowani", "following", "reels", "rolki", "tagged", "oznaczenia",
    "thread", "threads", "share profile", "udostępnij profil",
}
_META_COMPOSE = "com.facebook.compose.view.metacomposeview"


def story_ring_tap_xy(flat: list[dict]) -> tuple[int, int] | None:
    """Środek awatara z ringiem — do tapnięcia relacji.

    Stałe współrzędne % ekranu trafiały *pod* awatar (w wyróżnione relacje /
    pustkę) i story się nie otwierało. Bierzemy realne bounds z drzewa:
    najpierw klikalną ramkę, potem `reel_ring`.
    """
    frame = None
    ring = None
    for n in flat or []:
        rid = n.get("resource-id") or ""
        box = parse_bounds(n.get("bounds"))
        if not box:
            continue
        if rid == STORY_AVATAR_FRAME_ID:
            frame = box
        elif rid == STORY_RING_ID:
            ring = box
    box = frame or ring
    if not box:
        return None
    x1, y1, x2, y2 = box
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def read_profile_header(device) -> dict:
    """Zwraca dict z metadanymi profilu. None dla brakujących pól."""
    raw = device.hierarchy(force=True)
    # testy czasem podają już spłaszczoną listę zamiast drzewa
    if isinstance(raw, list):
        flat = raw
        tree = None
    else:
        flat = flatten_hierarchy(raw)
        tree = raw

    out = {
        "username": None,
        "full_name": None,
        "posts": None, "followers": None, "following": None,
        "is_private": False,
        "already_following": False,
        "has_story_ring": False,
        "verified": False,
        "bio": None,
        "bio_source": None,  # resource_id | compose_textview
    }

    for n in flat:
        rid  = n.get("resource-id") or ""
        text = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()
        blob = f"{text} {desc}".lower()

        # username (top bar)
        if rid in USER_IDS and text and not out["username"]:
            out["username"] = text
        if rid in NAME_IDS and text and not out["full_name"]:
            out["full_name"] = text

        # bio — klasyczne resource-id
        if rid in BIO_IDS and text and not out["bio"]:
            out["bio"] = text
            out["bio_source"] = "resource_id"

        # story ring
        if rid == STORY_RING_ID:
            out["has_story_ring"] = True

        # counts — najpierw text, potem content-desc (stacked / sklejone etykiety)
        for k, ids in COUNT_IDS.items():
            if rid in ids and out[k] is None:
                val = _to_int(text) if text else None
                if val is None and desc:
                    val = _to_int(desc)
                if val is not None:
                    out[k] = val

        # follow button
        if rid in FOLLOW_BTN_IDS or "follow" in rid.lower():
            if any(lbl in blob for lbl in FOLLOWING_LABELS):
                out["already_following"] = True
            elif any(lbl in blob for lbl in FOLLOW_LABELS):
                out["already_following"] = False

        # private — resource-id (locale-agnostic) albo tekst PL/EN
        if rid in PRIVATE_EMPTY_STATE_IDS:
            out["is_private"] = True
        elif any(h in blob for h in PRIVATE_HINTS):
            out["is_private"] = True

    # nowsze IG: bio w MetaComposeView bez resource-id
    if not out["bio"] and tree is not None:
        compose = extract_compose_bio(tree, exclude_names={
            (out.get("username") or "").strip(),
            (out.get("full_name") or "").strip(),
        })
        if compose:
            out["bio"] = compose
            out["bio_source"] = "compose_textview"

    return out


def extract_compose_bio(root: dict, *, exclude_names: set[str] | None = None,
                        max_y_ratio: float = 0.62) -> str | None:
    """Bio z TextView pod MetaComposeView (górna część profilu).

    Ścieżki z żywego IG kończą się:
      …/MetaComposeView/android.view.View/android.view.View/android.widget.TextView
    """
    exclude = {x.casefold() for x in (exclude_names or set()) if x}
    candidates: list[tuple[int, str]] = []  # (len, text)
    screen_h = _estimate_screen_height(root)

    def _walk(node: dict, ancestors: list[str]) -> None:
        p = node.get("properties") or {}
        cls = (p.get("class") or "").strip()
        text = (p.get("text") or "").strip()
        rid = (p.get("resource-id") or "").strip()
        under_compose = any(_META_COMPOSE in a.casefold() for a in ancestors)
        is_tv = cls.endswith("TextView") or cls == "android.widget.TextView"

        if under_compose and is_tv and text and not rid:
            if _looks_like_bio_text(text, exclude=exclude):
                y1 = _bounds_y1(p.get("bounds") or "")
                max_y = int(screen_h * max_y_ratio)
                if y1 is None or y1 <= max_y:
                    candidates.append((len(text), text))

        next_anc = ancestors + ([cls] if cls else [])
        for c in node.get("children") or []:
            _walk(c, next_anc)

    _walk(root, [])
    if not candidates:
        return None
    # najdłuższy sensowny tekst = zwykle pełne bio
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def _estimate_screen_height(root: dict) -> int:
    """Max y2 z drzewa (albo 2340 fallback)."""
    best = 0

    def _walk(node: dict) -> None:
        nonlocal best
        p = node.get("properties") or {}
        try:
            box = parse_bounds(p.get("bounds") or "")
            if box:
                best = max(best, int(box[3]))
        except Exception:
            pass
        for c in node.get("children") or []:
            _walk(c)

    _walk(root)
    return best if best >= 800 else 2340


def _looks_like_bio_text(text: str, *, exclude: set[str]) -> bool:
    t = text.strip()
    if len(t) < 3:
        return False
    low = t.casefold()
    if low in exclude:
        return False
    if low in _BIO_UI_NOISE:
        return False
    # czyste liczniki / etykiety
    if re.fullmatch(r"[\d\s.,KkMm]+", t):
        return False
    if low in {"posty", "posts", "followers", "following", "obserwujący",
               "obserwowani", "rolki", "reels"}:
        return False
    return True


def _bounds_y1(bounds: str) -> int | None:
    try:
        box = parse_bounds(bounds)
        return int(box[1]) if box else None
    except Exception:
        return None


# Sufiksy skróconych liczników IG (PL + EN). Kropka po tys./mln. jest opcjonalna.
_COUNT_SUFFIX_MULT = {
    "tys": 1_000,
    "k": 1_000,
    "mln": 1_000_000,
    "m": 1_000_000,
}
# NBSP / narrow no-break / thin / figure space — IG bywa je wkłada zamiast zwykłej spacji
_COUNT_SPACE_CHARS = ("\u00a0", "\u202f", "\u2009", "\u2007")
# Pełne dopasowanie albo prefix (content-desc: „235obserwatorzy", „1,2 tys.obserwujący")
_COUNT_RE = re.compile(
    r"^([\d]+(?:[\s.,]\d+)*)\s*(tys\.?|mln\.?|[km])?",
    re.IGNORECASE,
)


def _to_int(s: str) -> int | None:
    """Parsuje licznik profilu: '42', '1 234', '1.2K', '1,2 tys.', '12,5 tys.', '1.2M'.

    Stary regex akceptował tylko opcjonalne [KkMm] i wycinał WSZYSTKIE kropki/przecinki
    z liczby — stąd `1.2K` → 12000 zamiast 1200, a `1,2 tys.` → None (gate:
    followers_unknown). Tu: z sufiksem traktujemy ,/. jako dziesiętne; bez sufiksu
    usuwamy je jako separatory tysięcy.
    """
    if not s:
        return None
    t = str(s).strip().lower()
    for ch in _COUNT_SPACE_CHARS:
        t = t.replace(ch, " ")
    m = _COUNT_RE.match(t)
    if not m or not m.group(1):
        return None
    raw = m.group(1).replace(" ", "")
    suffix = (m.group(2) or "").rstrip(".").lower()
    if suffix in _COUNT_SUFFIX_MULT:
        # skrót: jeden separator dziesiętny (PL przecinek / EN kropka)
        num_s = raw.replace(",", ".")
        if num_s.count(".") > 1:
            parts = num_s.split(".")
            num_s = "".join(parts[:-1]) + "." + parts[-1]
        try:
            val = float(num_s)
        except ValueError:
            return None
        return int(val * _COUNT_SUFFIX_MULT[suffix])
    # pełna liczba: spacje/,/. = separatory tysięcy
    digits = re.sub(r"[.,]", "", raw)
    try:
        return int(digits)
    except ValueError:
        return None


def eval_gate(header: dict, gate: dict) -> dict:
    """Bramka bezwzględna dla ścieżki B (profile z rolek — nie pre-filtered).
    Zwraca {pass: bool, why: str}."""
    if not gate:
        return {"pass": True, "why": "no_gate"}
    reasons = []
    if gate.get("is_private_ok") is False and header.get("is_private"):
        reasons.append("private")
    if "min_posts" in gate:
        v = header.get("posts")
        if v is None: reasons.append("posts_unknown")
        elif v < gate["min_posts"]: reasons.append(f"posts<{gate['min_posts']}")
    if "min_followers" in gate:
        v = header.get("followers")
        if v is None: reasons.append("followers_unknown")
        elif v < gate["min_followers"]: reasons.append(f"followers<{gate['min_followers']}")
    if "max_followers" in gate:
        v = header.get("followers")
        if v is not None and v > gate["max_followers"]:
            reasons.append(f"followers>{gate['max_followers']}")
    if "min_ratio" in gate:
        f = header.get("followers") or 0
        ff = header.get("following") or 0
        if ff > 0:
            ratio = f / ff
            if ratio < gate["min_ratio"]:
                reasons.append(f"ratio<{gate['min_ratio']}")
    bio = (header.get("bio") or "").lower()
    for excl in gate.get("bio_exclusions") or []:
        if excl.lower() in bio:
            reasons.append(f"bio_excl:{excl}"); break
    return {"pass": not reasons, "why": ",".join(reasons) or "ok"}


def eval_sanity(header: dict, sanity: dict) -> dict:
    """Sanity check — zawsze wykonywany. Domyślnie: is_private + already_following."""
    reasons = []
    if sanity.get("abort_if_private", True) and header.get("is_private"):
        reasons.append("became_private")
    if sanity.get("abort_if_already_following", True) and header.get("already_following"):
        reasons.append("already_following")
    return {"pass": not reasons, "why": ",".join(reasons) or "ok"}
