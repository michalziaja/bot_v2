"""Wspólna obsługa sheetów nad rolką — lajkujący i komentarze.

Trzy kroki (`harvest_likers`, `harvest_commenters`, `open_profile_from_sheet`)
robiły to samo: otwórz sheet, rozwiń, przewiń, wróć na feed. Helpery były
skopiowane, więc poprawka w jednym miejscu nie docierała do pozostałych.

Sheet ma w drzewie ID ekranu bazowego (`reels_feed`) — dlatego mapa ma dla
niego `wins_over`, a wyjście idzie przez BACK, nie tap w dolną nawigację
(lekcja L4c z ARCHITEKTURA.md §3.4).
"""
from __future__ import annotations

import time

REELS = "reels_feed"

# Dodatkowa pauza PO rozwinięciu sheetu, ponad `wait_for_stable_ui`.
# Drzewo potrafi być „stabilne", zanim RecyclerView dociągnie wiersze —
# dump zrobiony w tym oknie zwracał pierwszy ekran listy i zbieranie
# kończyło się na kilku nickach. Czekamy zegarowo, nie humanizerem:
# to czas na dociągnięcie UI, a nie zachowanie człowieka do skalowania.
EXPAND_SETTLE_S = 1.0

# Mikro-pauzy „oddychania" — UI czasem nie nadąża mimo wait_for_stable.
# NIE zastępują EXPAND_SETTLE / humanizera; dokładamy je tam, gdzie korkuje.
_BREATH_AFTER_EXPAND_S = 0.15   # przed hierarchy dump po pullu
_BREATH_AFTER_BACK_S = 0.12     # po BACK zanim kolejny identify
_BREATH_BEFORE_HARVEST_S = 0.2  # po expand, przed pierwszym scrollem listy


def operator_say(ctx, msg: str) -> None:
    """Stdout dla operatora — prefix z aktywnego pluginu (flush w say).

    Kolejność: content_search / target_scraper / profile_interact / reels_niche.
    Hot z # trzyma `content_search_active` — wtedy nie udawaj profile_interact.
    """
    if not (msg or "").strip():
        return
    try:
        if ctx.scratch.get("content_search_active"):
            from v2.plugins.content_search_log import say
            say(msg)
        elif ctx.scratch.get("target_scraper_active"):
            from v2.plugins.scraper_log import say
            say(msg)
        elif ctx.scratch.get("profile_interact_active"):
            from v2.plugins.profile_log import say
            say(msg)
        else:
            from v2.plugins.reel_log import say
            say(msg)
    except Exception:
        print(f"  {msg}", flush=True)


# Listy (lajkujący, obserwujący) i panel komentarzy używają różnych ID nicku.
LIKER_USERNAME_IDS = (
    "com.instagram.android:id/row_user_primary_name",
    "com.instagram.android:id/row_search_user_username",
    "com.instagram.android:id/follow_list_username",
)
# Komentarze — klasyczne ID (gdy są). Live A50 (2026): nicki to TextView
# BEZ resource-id, z text=content-desc=nick (patrz collect_rows orphan).
# NIE: row_feed_photo_profile_name — to autor posta nad wątkiem; harvest
# dopisywał from_target do raw_profiles. Wykluczaj też exclude= w harvest.
COMMENT_USERNAME_IDS = (
    "com.instagram.android:id/row_comment_textview_username",
    "com.instagram.android:id/row_user_primary_name",
    "com.instagram.android:id/row_search_user_username",
    "com.instagram.android:id/follow_list_username",
)

LIKERS_LINK_HINTS = ("polubi", "liked by")
COMMENTS_ICON_HINTS = ("komentar", "comment")

# Outer wrapery IG mają top≈status-bar (~80px) NAWET przy half-sheet — NIE używać
# ich do pomiaru. Live A50: prawdziwy peek = drag_handle @ ~34%H.
_DRAG_HANDLE_IDS = (
    "com.instagram.android:id/bottom_sheet_drag_handle_frame",
    "com.instagram.android:id/bottom_sheet_drag_handle_prism",
    "com.instagram.android:id/drag_handle_view",
    "com.instagram.android:id/bottom_sheet_drag_handle_view",
)
# Tytuł listy („Reakcje") — tuż pod uchwytem, dobra kotwica geometrii
_SHEET_TITLE_IDS = (
    "com.instagram.android:id/title_text_view",
)
# Kontenery listy (NIE outer bottom_sheet_container @ y≈80)
_SHEET_LIST_IDS = (
    "com.instagram.android:id/bottom_sheet_container_view",
    "com.instagram.android:id/layout_listview_parent_container",
)

# Pełny ekran: górna krawędź uchwytu/sheetu < ten próg %H (live: half≈34%, full≈<18%)
_FULL_SCREEN_TOP_PCT = 0.18

_ROW_MATCH_PX = 100      # jak blisko w pionie element musi być, by należeć do wiersza
_AVATAR_MAX_X = 300      # avatar jest po LEWEJ; po prawej stoi przycisk „Obserwuj"


def center(bounds) -> tuple[int, int] | None:
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None


def _bounds_top(bounds) -> int | None:
    try:
        a, _b = str(bounds).split("][")
        return int(a.strip("[").split(",")[1])
    except Exception:
        return None


def _bounds_box(bounds) -> tuple[int, int, int, int] | None:
    """(x1,y1,x2,y2) albo None."""
    try:
        a, b = str(bounds).split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return x1, y1, x2, y2
    except Exception:
        return None


def _hierarchy_flat(ctx) -> list:
    """Hierarchy → lista węzłów (toleruje już spłaszczone listy z testów)."""
    try:
        from v2.core.screen_map import flatten_hierarchy
        raw = ctx.device.hierarchy(force=True)
    except Exception:
        return []
    if isinstance(raw, list):
        if not raw:
            return []
        head = raw[0] if isinstance(raw[0], dict) else {}
        if "children" not in head and (
                "resource-id" in head or "bounds" in head or "text" in head):
            return raw
        out = []
        for n in raw:
            if isinstance(n, dict) and "children" in n:
                out.extend(flatten_hierarchy(n))
            elif isinstance(n, dict):
                out.append(n)
        return out
    return flatten_hierarchy(raw)


def dump_sheet_geometry(ctx) -> dict:
    """Hierarchy dump geometrii sheetu — JEDYNE źródło prawdy full-screen.

    Live A50 (half „Reakcje"):
      drag_handle y≈806 (34%H), lista od ~977, outer container y=83 (IGNORUJ).
    Full screen: handle/title top < ~18% H, wysokość obszaru ≈ H.
    """
    w, h = ctx.account.device["_hardware"]["resolution"]
    flat = _hierarchy_flat(ctx)
    handle_y = None
    handle_src = None
    title_y = None
    list_top = None
    list_h = None

    for e in flat:
        rid = e.get("resource-id") or ""
        box = _bounds_box(e.get("bounds"))
        if not box:
            continue
        _x1, y1, _x2, y2 = box
        if rid in _DRAG_HANDLE_IDS:
            # Najwyższy (najmniejszy y) uchwyt w sensownym zakresie
            if 40 < y1 < int(h * 0.92):
                if handle_y is None or y1 < handle_y:
                    handle_y, handle_src = y1, rid.split("/")[-1]
        if rid in _SHEET_TITLE_IDS:
            if 40 < y1 < int(h * 0.92):
                if title_y is None or y1 < title_y:
                    title_y = y1
        if rid in _SHEET_LIST_IDS:
            # Bierz kontener z topem w strefie sheetu (nie outer @ ~80 jeśli jest też niższy)
            if int(h * 0.12) < y1 < int(h * 0.92):
                lh = y2 - y1
                if list_top is None or y1 < list_top:
                    list_top, list_h = y1, lh

    # sheet_top = uchwyt > tytuł > lista (NIGDY outer wrapper @ status-bar)
    if handle_y is not None:
        sheet_top, top_src = handle_y, handle_src or "drag_handle"
    elif title_y is not None:
        sheet_top, top_src = title_y, "title"
    elif list_top is not None:
        sheet_top, top_src = list_top, "list"
    else:
        # Awaria: pierwszy nick − offset (tylko diagnostyka)
        nick_ys = []
        for e in flat:
            rid = e.get("resource-id") or ""
            if rid in LIKER_USERNAME_IDS + COMMENT_USERNAME_IDS:
                y = _bounds_top(e.get("bounds"))
                if y is not None and 80 < y < int(h * 0.95):
                    nick_ys.append(y)
        if nick_ys:
            sheet_top = max(40, min(nick_ys) - 100)
            top_src = "nick_fallback"
        else:
            sheet_top, top_src = None, "missing"

    sheet_h = (h - sheet_top) if sheet_top is not None else None
    top_pct = (sheet_top / h) if sheet_top is not None else None
    # FULL SCREEN: top blisko góry (<18% H) — sam wzrost wierszy NIE wystarcza
    full_screen = bool(
        sheet_top is not None and sheet_top < int(h * _FULL_SCREEN_TOP_PCT))
    rows = len(collect_rows(flat, LIKER_USERNAME_IDS + COMMENT_USERNAME_IDS))
    return {
        "screen_h": h,
        "screen_w": w,
        "sheet_top": sheet_top,
        "sheet_top_pct": round(100.0 * top_pct, 1) if top_pct is not None else None,
        "sheet_h": sheet_h,
        "list_top": list_top,
        "list_h": list_h,
        "handle_y": handle_y,
        "title_y": title_y,
        "top_src": top_src,
        "rows": rows,
        "full_screen": full_screen,
    }


def _sheet_handle_y(ctx, h: int, *, geo: dict | None = None) -> int:
    """Y startu pull-up: ŚRODEK uchwytu drag_handle (nie lista nicków, nie outer).

    Live: half-sheet handle @ ~34%H. Swipe ze środka listy (60–85%H) = SCROLL.
    `geo` — reuse dumpa z fazy expand (bez kolejnego hierarchy force).
    """
    geo = geo if geo is not None else dump_sheet_geometry(ctx)
    # Preferuj środek paska uchwytu (frame bywa ~60px wysoki)
    if geo.get("handle_y") is not None:
        return int(geo["handle_y"]) + 30
    if geo.get("title_y") is not None:
        return int(geo["title_y"]) + 20
    if geo.get("sheet_top") is not None:
        y = int(geo["sheet_top"]) + 40
        # Nie startuj z obszaru listy (scroll) — max ~45%H gdy brak handle
        return min(y, int(h * 0.45))
    return int(h * 0.38)  # typowy peek bez ID


def expand_sheet(ctx, *, pulls: int = 1, geo: dict | None = None) -> dict:
    """Jeden mocny pull-up z UCHWYTU w GÓRĘ (do ~5% H) — ciągnie SHEET, nie listę.

    Zwraca `{y_from, y_to, pulls, x}`. Potwierdzenie: `expand_sheet_confirmed`.
    `geo` — opcjonalny dump before (handle_y bez dodatkowego ADB).
    """
    w, h = ctx.account.device["_hardware"]["resolution"]
    x = w // 2
    y_from = _sheet_handle_y(ctx, h, geo=geo)
    y_to = int(h * 0.05)
    # Musi być wyraźny ruch w GÓRĘ (y maleje); inaczej fallback na strefę handle peek
    if y_from - y_to < int(h * 0.20):
        y_from = int(h * 0.40)
        y_to = int(h * 0.05)
    # Nie startuj z głębokiej listy (>55%H) — to scroll RecyclerView
    if y_from > int(h * 0.55):
        y_from = int(h * 0.38)
    n = max(1, int(pulls))
    for i in range(n):
        # Wolny, długi drag = sheet; szybki fling bywa scroll
        ctx.ht.swipe(x, y_from, x, y_to, duration_ms=700, pause_kind="tap")
        ctx.device.wait_for_stable_ui(max_wait_s=1.2)
        if i + 1 < n:
            time.sleep(0.3)
            # Po 1. pullu przelicz handle (mógł podjechać wyżej) — jeden dump
            y_from = _sheet_handle_y(ctx, h)
            if y_from > int(h * _FULL_SCREEN_TOP_PCT) + 40:
                pass  # jeszcze half — kolejny pull z nowego handle
            else:
                break
    time.sleep(EXPAND_SETTLE_S)
    return {"y_from": y_from, "y_to": y_to, "pulls": n, "x": x}


def _sheet_top_y(ctx, h: int) -> int | None:
    """Górna krawędź REALNEGO sheetu (handle/title) — nie outer wrapper."""
    return dump_sheet_geometry(ctx).get("sheet_top")


def _count_sheet_rows(ctx) -> int:
    return int(dump_sheet_geometry(ctx).get("rows") or 0)


def sheet_looks_expanded(ctx, *, rows_before: int = 0,
                         geo: dict | None = None) -> dict:
    """full_screen TYLKO z dumpa geometrii — wzrost wierszy sam NIE wystarcza.

    `geo` — podaj dump z tej fazy (before/after), żeby nie dublować hierarchy.
    """
    geo = geo if geo is not None else dump_sheet_geometry(ctx)
    full_screen = bool(geo.get("full_screen"))
    rows = int(geo.get("rows") or 0)
    grew = rows > rows_before and (rows - rows_before) >= 2
    return {
        "confirmed": full_screen,  # synonim: tylko full screen
        "full_screen": full_screen,
        "rows": rows,
        "sheet_top": geo.get("sheet_top"),
        "sheet_top_pct": geo.get("sheet_top_pct"),
        "sheet_h": geo.get("sheet_h"),
        "screen_h": geo.get("screen_h"),
        "handle_y": geo.get("handle_y"),
        "top_src": geo.get("top_src"),
        "full_top": full_screen,
        "grew": grew,  # diagnostyka; NIE ustawia confirmed
        "h": geo.get("screen_h"),
    }


def expand_sheet_confirmed(ctx, *, max_pulls: int = 3) -> dict:
    """Pull-up z uchwytu → dump → full_screen? retry. Potem wolno harvest.

    `confirmed` == `full_screen` z hierarchy (sheet_top < 18% H).
    Jedna geo na fazę (before / after_pull) — bez podwójnego force dump.
    Operator: tylko „Otwieram listę…" PRZED tapem (open_engagement / harvest);
    tu bez „Rozwijam…" / „Rozwinięto" — geometria tylko w events.jsonl.
    """
    before = dump_sheet_geometry(ctx)
    try:
        ctx.log("sheet.expand_dump", phase="before", **{
            k: before.get(k) for k in (
                "sheet_top", "sheet_top_pct", "sheet_h", "screen_h",
                "handle_y", "rows", "full_screen", "top_src")
        })
    except Exception:
        pass
    rows_before = int(before.get("rows") or 0)
    top_before = before.get("sheet_top")
    pulls_done = 0
    # Reuse before — zero drugiego dumpa na starcie
    last = sheet_looks_expanded(ctx, rows_before=rows_before, geo=before)
    info = {}
    # Już full? nie ciągnij zbędnie (bez stdout — wystarczy „Otwieram listę…")
    if last.get("full_screen"):
        return {
            "y_from": None, "y_to": None, "pulls": 0, "x": None,
            "confirmed": True, "full_screen": True,
            "rows_before": rows_before, "rows_after": last.get("rows"),
            "sheet_top_before": top_before,
            "sheet_top_after": last.get("sheet_top"),
            "sheet_h": last.get("sheet_h"), "screen_h": last.get("screen_h"),
            "sheet_top_pct": last.get("sheet_top_pct"),
            "pulls_done": 0, "full_top": True, "grew": False,
            "top_src": last.get("top_src"),
        }
    # Pull bez komunikatu operatorowi — dumpy idą do events.jsonl
    geo_hint = before
    for _ in range(max(1, int(max_pulls))):
        info = expand_sheet(ctx, pulls=1, geo=geo_hint)
        pulls_done += 1
        # Krótki oddech przed dumpem — RecyclerView bywa opóźniony vs stable_ui
        time.sleep(_BREATH_AFTER_EXPAND_S)
        # Jedna geo po pullu — check + log z tego samego dumpa
        after = dump_sheet_geometry(ctx)
        last = sheet_looks_expanded(ctx, rows_before=rows_before, geo=after)
        try:
            ctx.log("sheet.expand_dump", phase=f"after_pull_{pulls_done}", **{
                k: after.get(k) for k in (
                    "sheet_top", "sheet_top_pct", "sheet_h", "screen_h",
                    "handle_y", "rows", "full_screen", "top_src")
            }, y_from=info.get("y_from"), y_to=info.get("y_to"))
        except Exception:
            pass
        if last.get("full_screen"):
            break
        geo_hint = after  # kolejny pull z aktualnego handle
        time.sleep(0.25)
    rows_after = int(last.get("rows") or 0)
    confirmed = bool(last.get("full_screen"))
    return {
        **info,
        "confirmed": confirmed,
        "full_screen": confirmed,
        "rows_before": rows_before,
        "rows_after": rows_after,
        "sheet_top_before": top_before,
        "sheet_top_after": last.get("sheet_top"),
        "sheet_h": last.get("sheet_h"),
        "screen_h": last.get("screen_h"),
        "sheet_top_pct": last.get("sheet_top_pct"),
        "pulls_done": pulls_done,
        "full_top": confirmed,
        "grew": bool(last.get("grew")),
        "top_src": last.get("top_src"),
        "handle_y": last.get("handle_y"),
    }


def _pop_cs_sheet_flags(ctx) -> None:
    """Harvest z # skończył sheet — precz flagi, by FP comments nie dostał BACK."""
    ctx.scratch.pop("_cs_opened_sheet", None)
    ctx.scratch.pop("_cs_opened_comments", None)


def return_to_reels(ctx) -> bool:
    """Zamknij overlay (max 3× back), aż wrócimy na reels_feed.

    content_search: STOP na viewerze. Po liście lajkujących identify bywa
    `reel_comments_sheet` (FP) — drugi BACK = search_results (live 02:59).
    BACK z comments tylko gdy sami otworzyliśmy komentarze.
    """
    cs = bool(ctx.scratch.get("content_search_active"))
    sheets = {
        "reel_likers_list",
        "followers_list", "following_list",
        "profile_other", "profile_self",
        "story_viewer", "share_sheet", "profile_options_sheet",
    }
    for _ in range(3):
        state = ctx.navigator.identify()
        top = state.top
        if cs and top in ("search_results", "search_top", "search_input_active"):
            ctx.log("reels.return_to_reels.overshoot_results", top=top)
            _pop_cs_sheet_flags(ctx)
            return False
        if top == REELS and not state.overlay:
            _pop_cs_sheet_flags(ctx)
            return True
        if cs:
            # Nie BACK-uj overlayu / comments-FP viewera (jak _dismiss_sheet_stack)
            should = top in sheets
            if top == "reel_comments_sheet":
                should = bool(ctx.scratch.get("_cs_opened_comments"))
            if not should:
                ctx.scratch["_cs_on_viewer"] = True
                _pop_cs_sheet_flags(ctx)
                return True
        ctx.device.press_back()
        ctx.humanizer.pause("tap")
        time.sleep(_BREATH_AFTER_BACK_S)
        if cs:
            nxt = ctx.navigator.identify().top
            _pop_cs_sheet_flags(ctx)
            if nxt in ("search_results", "search_top", "search_input_active"):
                ctx.log("reels.return_to_reels.overshoot_results", top=nxt)
                return False
            if nxt == REELS:
                return True
            if nxt not in sheets:
                # comments-FP / home / overlay — jeden BACK wystarczy
                ctx.scratch["_cs_on_viewer"] = True
                return True
    if cs:
        top = ctx.navigator.identify().top
        _pop_cs_sheet_flags(ctx)
        return top == REELS
    return ctx.navigator.ensure(REELS, max_steps=4)


def open_likers_sheet(ctx) -> bool:
    """Tap w link „Liczba polubień: X" / „Polubione przez"."""
    ok = _tap_first_clickable(ctx, LIKERS_LINK_HINTS, pause="between_steps")
    if ok and ctx.scratch.get("content_search_active"):
        ctx.scratch["_cs_opened_sheet"] = True
    return ok


def open_comments_sheet(ctx) -> bool:
    ok = _tap_first_clickable(ctx, COMMENTS_ICON_HINTS, pause="read_comments")
    if ok and ctx.scratch.get("content_search_active"):
        ctx.scratch["_cs_opened_sheet"] = True
        # Osobno od lajkujących: comments-FP po likers NIE jest prawdziwym sheetem
        ctx.scratch["_cs_opened_comments"] = True
    return ok


def _tap_first_clickable(ctx, hints, pause: str) -> bool:
    from v2.core.screen_map import flatten_hierarchy
    flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
    for n in flat:
        if not n.get("clickable"):
            continue
        blob = ((n.get("text") or "") + " " + (n.get("content-desc") or "")).lower()
        if any(h in blob for h in hints):
            c = center(n.get("bounds"))
            if c:
                ctx.ht.tap(*c, pause_kind="tap")
                ctx.humanizer.pause(pause)
                return True
    return False


# ============================================================
# wiersze listy
# ============================================================
def collect_rows(flat: list[dict], username_ids=LIKER_USERNAME_IDS) -> list[dict]:
    """Wiersze widoczne na liście: `{username, center, bounds, avatar, ring}`.

    `center` to środek NICKU, `bounds` = (x1,y1,x2,y2) tekstu — do bezpiecznego
    tapu (`nick_tap_xy`). `avatar` — środek awatara (tap otwiera relację przy
    ringu). Pomylenie celów = story zamiast profilu.

    Sheety nie mają kontenera na wiersz, więc elementy wiążemy po współrzędnej
    Y — ten sam sposób, którym stary bot wiązał ring z awatarem.

    `ring` to **stan trójwartościowy**: `True` = potwierdzona aktywna relacja,
    `None` = nie umiemy stwierdzić na tej wersji IG. Rozróżnienie jest istotne,
    bo `require_ring` przy samych `None` musi pominąć wszystkich, a nie wpuścić
    wszystkich — patrz `pick_candidates`.
    """
    users, rings, avatars = [], [], []
    # Orphan TextView TYLKO gdy w id jest klasyczny comment username
    # (LIKER_IDS dzielą row_user_* z COMMENT — nie włączaj wtedy orphanów)
    _comment_classic = "com.instagram.android:id/row_comment_textview_username"
    want_orphans = _comment_classic in tuple(username_ids)
    for n in flat:
        rid = (n.get("resource-id") or "")
        box = _bounds_box(n.get("bounds"))
        c = center(n.get("bounds"))
        if not c:
            continue
        # text albo content-desc (niektóre sheet'y komentarzy dają nick w desc)
        text = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()
        nick = text or desc
        if rid in username_ids and nick and _looks_like_username(nick):
            users.append((nick.lstrip("@"), c, box))
            continue
        # Live comments sheet: TextView bez id, text == desc == nick
        if (want_orphans and not rid
                and _is_orphan_comment_username(n, text, desc)):
            users.append((text.lstrip("@"), c, box))
            continue
        if _is_ring_overlay(rid, n.get("class"), c):
            rings.append(c)
        if _is_avatar(rid, c):
            avatars.append(c)

    rows = []
    seen = set()
    for username, c, box in users:
        if username in seen:
            continue
        seen.add(username)
        rows.append({"username": username, "center": c, "bounds": box,
                     "avatar": _nearest(avatars, c[1]),
                     "ring": _ring_near(rings, c[1])})
    return rows


def nick_tap_xy(row: dict, *, bias: str = "safe") -> tuple[int, int]:
    """Punkt tapnięcia w NICK — profil, nie relacja i nie „Obserwuj".

    Live 14:06 (prefer_ring): środek krótkiego nicku (`agna.bk`) wpadał w
    rozszerzony hitbox ringu → `story_viewer`, po BACK wyglądało jak
    `no_profile` na liście. Stary bot na likers szedł przez search-in-sheet;
    tu zostajemy przy bezpośrednim tapie, ale z biasem w PRAWO od awatara.

    `bias`:
      - ``safe`` — ~2/3 szerokości tekstu (dalej od ringu, przed Obserwuj)
      - ``right`` — prawie prawa krawędź nicku (retry po przypadkowym story)
    """
    box = row.get("bounds")
    if not box:
        return tuple(row["center"])  # type: ignore[return-value]
    x1, y1, x2, y2 = box
    w = max(1, x2 - x1)
    if bias == "right":
        x = x2 - max(8, w // 8)
    else:
        x = x1 + (w * 2) // 3
    # Podłoga: poza strefą awatara / ringu (hitbox bywa szerszy niż ImageView)
    avatar = row.get("avatar")
    floor = _AVATAR_MAX_X + 40
    if avatar:
        floor = max(floor, int(avatar[0]) + 100)
    x = max(x, floor, x1 + 8)
    # Sufit: nie wychodź poza tekst nicku (dalej stoi Obserwuj)
    x = min(x, x2 - 6)
    if x < x1:
        x = (x1 + x2) // 2
    return int(x), int((y1 + y2) // 2)


def _looks_like_username(s: str) -> bool:
    """Odrzuć zdania/przyciski; zostaw typowe nicki IG (bez spacji, krótki)."""
    s = (s or "").strip().lstrip("@")
    if not s or len(s) > 40 or " " in s:
        return False
    # Typowe etykiety UI, nie nicki (PL/EN, ze znakami diakrytycznymi)
    bad = ("obserwuj", "follow", "following", "polub", "like", "odpowiedz",
           "odpowiedź", "reply", "tłumacz", "translate", "reakcje", "comments",
           "komentarz", "dodaj", "gif", "multimedia", "posty")
    low = s.casefold()
    if any(b in low for b in bad):
        return False
    return True


def _is_orphan_comment_username(n: dict, text: str, desc: str) -> bool:
    """Nick na sheetcie komentarzy bez resource-id (live IG A50).

    Wzorzec: class=TextView, pusty id, text i content-desc = ten sam nick,
    nieclickable. Przycisk „Odpowiedź" to View/clickable — odrzucamy.
    """
    cls = (n.get("class") or "").lower()
    if "textview" not in cls:
        return False
    if n.get("clickable"):
        return False
    if not text or not _looks_like_username(text):
        return False
    # Oba pola zwykle wypełnione tym samym nickiem
    if desc and desc.lstrip("@").casefold() != text.lstrip("@").casefold():
        return False
    return True


def _is_avatar(rid: str, c) -> bool:
    """Awatar wiersza — zawsze po LEWEJ. Po prawej stoi przycisk „Obserwuj"
    z podobnym wrapperem i tap w niego obserwuje zamiast otwierać relację."""
    rid = (rid or "").lower()
    return "imageview" in rid and c[0] <= _AVATAR_MAX_X


def _nearest(points, target_y: int):
    best, best_d = None, _ROW_MATCH_PX
    for c in points:
        d = abs(c[1] - target_y)
        if d < best_d:
            best, best_d = c, d
    return best


def _is_ring_overlay(rid: str, cls, c) -> bool:
    """Ring to nakładka `android.view.View` w wrapperze awatara.

    Wiersz bez relacji ma dwa elementy awatara (FrameLayout + ImageView);
    wiersz z aktywną relacją ma trzeci — właśnie ten View. Filtr `x ≤ 300`
    jest obowiązkowy: po prawej stronie wiersza stoi przycisk „Obserwuj"
    z podobnym wrapperem, a pomyłka kończy się obserwowaniem zamiast
    otwarcia relacji.
    """
    rid = (rid or "").lower()
    if "imageview" not in rid:
        return False
    if (cls or "").lower() != "android.view.view":
        return False
    return c[0] <= _AVATAR_MAX_X


def _ring_near(rings, target_y: int):
    for c in rings:
        if abs(c[1] - target_y) < _ROW_MATCH_PX:
            return True
    return None if not rings else False


def pick_candidates(rows: list[dict], *, prefer_ring=True, require_ring=False,
                    exclude: set | None = None) -> list[dict]:
    """Uszereguj kandydatów do interakcji „na gorąco".

    `prefer_ring` tylko sortuje — kandydaci z potwierdzoną relacją idą pierwsi,
    reszta zostaje w kolejce. `require_ring` odsiewa twardo i przepuszcza
    wyłącznie POTWIERDZONE ringi; przy nieznanym stanie (`None`) nie zgadujemy,
    bo cała wartość tego przełącznika to gwarancja, że profil jest publiczny.
    """
    exclude = exclude or set()
    out = [r for r in rows if r["username"] not in exclude]
    if require_ring:
        return [r for r in out if r["ring"] is True]
    if prefer_ring:
        out.sort(key=lambda r: r["ring"] is not True)   # stabilny: True najpierw
    return out
