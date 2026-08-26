"""search.pick_result — tap w wynik profilowy z listy.

Ustawia `scratch.baseline_screen` na **aktualny** ekran search (zwykle
`search_input_active` przy sugestiach po type_query bez submit) — żeby
`profile.exit` / powrót w `profile_interact` nie celował w `search_results`
i nie spadał na home_feed.

`prefer_exact=True` + podany `username`: TYLKO dokładny match.
Bez fallbacku na pierwszy wiersz — live (technature699): szukano
`___anji_i`, a brak w liście → brano `ishioanji` i „gubiło" wizytę.
"""
from __future__ import annotations
from v2.core.screen_map import flatten_hierarchy
from v2.runner.step import StepResult
from v2.steps.registry import register


USER_ROW_IDS = (
    "com.instagram.android:id/row_search_user_container",
    "com.instagram.android:id/row_user_container_base",
)
USERNAME_IDS = (
    "com.instagram.android:id/row_search_user_username",
    "com.instagram.android:id/row_user_primary_name",
)

# Ekrany, z których wolno wracać po wizycie profilu (pętla profile_interact)
_SEARCH_BASELINES = ("search_input_active", "search_results", "search_top")


def _normalize_username(raw: str | None) -> str:
    """Username bez @ / spacji — porównania case-insensitive."""
    return (raw or "").strip().lstrip("@").lower()


@register
class SearchPickResult:
    name = "search.pick_result"
    requires_screen = None

    def run(self, ctx, params):
        want_username = _normalize_username(params.get("username"))
        prefer_exact  = bool(params.get("prefer_exact", True))

        # Baseline PRZED tapem — po wejściu na profil top już nie jest search
        top = ctx.navigator.identify().top
        baseline = top if top in _SEARCH_BASELINES else "search_input_active"

        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))

        # zbierz wiersze — pary (username_text, row_bounds_click_target)
        rows = _collect_user_rows(flat)
        if not rows:
            return StepResult.failed("brak wyników użytkowników")

        target_row = None
        if prefer_exact and want_username:
            # Tylko dokładny nick — bez cichego rows[0] (zły profil = zła wizyta)
            for u, bounds in rows:
                if _normalize_username(u) == want_username:
                    target_row = (u, bounds)
                    break
            if not target_row:
                seen = [u for u, _ in rows[:8]]
                ctx.log("search.pick_result.no_exact",
                        want=want_username, seen=seen)
                return StepResult.failed(
                    f"brak dokładnego wyniku '{want_username}' "
                    f"(widoczne: {', '.join(seen) or '—'})")
        else:
            # Brak preferencji / brak want — pierwszy wynik profilowy
            target_row = rows[0]

        u, bounds = target_row
        c = _center(bounds)
        if not c:
            return StepResult.failed(f"nie mogę wycentrować taka bounds={bounds!r}")

        ctx.scratch["baseline_screen"] = baseline
        ctx.scratch["expected_username"] = u    # druga droga na nick, patrz profile.visit
        ctx.ht.tap(*c, pause_kind="tap")
        # potwierdź że profile_other (albo profile_self jeśli klikneliśmy w siebie)
        if not ctx.navigator.ensure("profile_other", max_steps=3):
            return StepResult.failed(f"po tap w '{u}' nie znaleziono profile_other")
        return StepResult.ok(username=u, baseline=baseline)


def _collect_user_rows(flat):
    """Zwraca listę (username, bounds_do_kliknięcia).
    Prosto: dla każdego row_container znajdujemy dziecko username i klik idzie w container."""
    rows = []
    # bez pełnego trawersowania — używamy heurystyki: pary sąsiadujących ID
    # w kolejności DFS. Prostsze: dla każdego USERNAME_IDS znajdź, weź jego bounds.
    for n in flat:
        if n.get("resource-id") in USERNAME_IDS and n.get("text"):
            u = n["text"].strip()
            b = n.get("bounds")
            if b:
                rows.append((u, b))
    # dedup w kolejności pojawienia
    seen = set(); out = []
    for u, b in rows:
        if u in seen: continue
        seen.add(u); out.append((u, b))
    return out


def _center(bounds: str):
    try:
        a, b = bounds.split("][")
        x1, y1 = map(int, a.strip("[").split(","))
        x2, y2 = map(int, b.strip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return None
