"""Pola niszy do promptów engagement (reel_niche / profile_grid).

Discovery zapisuje description / audience / keywords / hashtags w
`account.niche` — bez generowania exclusions.
`keywords` / `hashtags` to scalona lista (manual + discovered); źródła
w `*_manual` / `*_discovered` — promptery biorą scaloną.

Wykluczenia biorą się z ustawień pluginu (rolki vs profil osobno), z
opcjonalnym fallbackiem do ręcznych `niche.exclusions`.
"""
from __future__ import annotations

# Siatka profilu: # na miniaturze niewidoczne; discovery bywa 50+ wariantów
GRID_MAX_KEYWORDS = 12
GRID_MAX_HASHTAGS = 8


def effective_exclusions(niche: dict | None, plugin_ai_cfg: dict | None) -> list[str]:
    """Lista wykluczeń na prompt: plugin wygrywa, pusty → niche.exclusions.

    Rolki (`reels_niche.ai.exclusions`) i profil (`grid_ai.exclusions`) mają
    niezależne listy w panelu — operator ustawia je tam (discovery ich nie
    wypełnia).
    """
    plugin = plugin_ai_cfg or {}
    extra = _as_str_list(plugin.get("exclusions"))
    if extra:
        return extra
    return _as_str_list((niche or {}).get("exclusions"))


def niche_prompt_fields(niche: dict | None, *, exclusions: list | None = None,
                        max_keywords: int | None = None,
                        max_hashtags: int | None = None) -> dict:
    """Placeholdery wspólne dla reel_niche.yml i profile_grid.yml.

    `max_keywords` / `max_hashtags` — obetnij ogon listy (kolejność zachowana).
    None = bez limitu (rolki potrzebują pełnych #). Wykluczeń nie tniemy.
    """
    n = niche or {}
    return {
        "opis": n.get("description") or "",
        "audiencja": n.get("audience") or "",
        "slowa_kluczowe": _clip_str_list(n.get("keywords"), max_keywords),
        "hashtagi": _clip_str_list(n.get("hashtags"), max_hashtags),
        "wykluczenia": list(exclusions) if exclusions is not None
        else _as_str_list(n.get("exclusions")),
    }


def _clip_str_list(v, max_n: int | None) -> list[str]:
    """Dedup jak `_as_str_list`, potem ewentualne obcięcie do `max_n`."""
    xs = _as_str_list(v)
    if max_n is None or int(max_n) < 0:
        return xs
    return xs[: int(max_n)]


def _as_str_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    out: list[str] = []
    for x in (v or []):
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out
