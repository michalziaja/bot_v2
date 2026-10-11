"""Odczyt/zapis ustawień pluginów i kolejki zadań.

Ustawienia pluginów mieszkają w configu konta pod kluczem `plugins`:
    config/accounts/<alias>.json  →  { ..., "plugins": { "<id>": {...} } }

Kolejka zadań (= scenariusz) jest w osobnych plikach, bo zmienia się dużo
częściej niż config i nie ma sensu przepuszczać jej przez walidację schematu
konta:
    config/accounts/<alias>.queues/<nazwa>.json
Domyślna kolejka to po prostu scenariusz o zarezerwowanej nazwie `default`
(patrz `DEFAULT_SCENARIO` niżej) — jedna lista, bez ukrytego drugiego bytu.

Zapisane akcje (presety kroku) — NIE runy — to nazwane zestawy
plugin + overrides, analogicznie w osobnym katalogu:
    config/accounts/<alias>.actions/<slug>.json

Ustawienia rozwiązujemy trójstopniowo (ostatni wygrywa):
    catalog.defaults  ←  config konta  ←  overrides zadania z kolejki
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from v2.plugins.catalog import PLUGINS, all_defaults, get_plugin, plugin_ids

CONFIG_ROOT = Path(__file__).resolve().parent.parent / "config"


def deep_merge(base: dict, over: dict) -> dict:
    """Głęboki merge dwóch słowników — `over` wygrywa.

    Listy są NADPISYWANE, nie sklejane: `allow_verdict: ["match"]` w override
    ma znaczyć dokładnie to, a nie dopisanie do domyślnej listy.

    `null` w override = brak klucza (nie nadpisuje defaultu). Panel bywa
    zapisuje `"value": null` przy pustym polu chance — bez tego merge
    dawał `{"mode":"p","value":null}` → p=0 → zero lajków.
    """
    out = dict(base)
    for k, v in over.items():
        if v is None:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ============================================================
# Ustawienia pluginów
# ============================================================
def _account_path(account: str) -> Path:
    p = CONFIG_ROOT / "accounts" / f"{account}.json"
    if not p.exists():
        raise FileNotFoundError(f"config konta nie istnieje: {p}")
    return p


def prune_to_schema(stored: dict, defaults: dict) -> dict:
    """Odetnij klucze, których nie ma już w katalogu.

    Katalog się zmienia (pole znika, zmienia kształt), a zapisane ustawienia
    zostają w pliku konta. Bez przycięcia `deep_merge` wnosiłby martwe klucze
    z powrotem — formularz ich nie pokazuje, więc nie da się ich usunąć
    z panelu, a plugin dostawałby jednocześnie stary i nowy wariant.

    Poddrzewa o innym KSZTAŁCIE niż domyślny (np. dawne `{"p": 0.5}` wobec
    dzisiejszego `{"mode", "value"}`) są odrzucane w całości — wartość wraca
    do domyślnej zamiast zlepiać się z nową.
    """
    out = {}
    for k, v in stored.items():
        if k not in defaults:
            continue
        # null z panelu = „nie ustawione” — nie trzymaj w clean, inaczej
        # deep_merge i tak by je pominął, ale zostawałby śmieć w pliku.
        if v is None:
            continue
        dv = defaults[k]
        if isinstance(dv, dict) and isinstance(v, dict):
            if dv and not set(v) & set(dv):
                continue        # zupełnie inny kształt — bierzemy domyślny
            sub = prune_to_schema(v, dv)
            if sub:
                out[k] = sub
        elif not isinstance(dv, dict) and not isinstance(v, dict):
            out[k] = v
        # niezgodność typu (dict vs skalar) — pomijamy, wygra domyślna
    return out


def read_settings(account: str) -> dict:
    """Ustawienia wszystkich pluginów dla konta: defaults ← config konta.

    Konto, które nigdy nie było zapisane przez nowy panel, dostaje pełne
    defaults — dzięki temu formularz zawsze ma komplet pól.
    """
    cfg = json.loads(_account_path(account).read_text(encoding="utf-8"))
    stored = cfg.get("plugins") or {}
    out = {}
    for pid, defaults in all_defaults().items():
        clean = prune_to_schema(stored.get(pid) or {}, defaults)
        out[pid] = deep_merge(defaults, clean)
    return out


def diff_from_defaults(vals: dict, defaults: dict) -> dict:
    """Zostaw tylko to, co RÓŻNI SIĘ od wartości domyślnych z katalogu.

    Panel wysyła komplet pól, ale zapisywanie ich wszystkich miałoby dwie
    wady: config konta puchnie o ponad 200 pozycji, a zmiana domyślnej
    w katalogu nie dociera do kont, które jej nigdy świadomie nie ruszały.
    """
    out = {}
    for k, v in vals.items():
        if k not in defaults:
            continue
        dv = defaults[k]
        if isinstance(dv, dict) and isinstance(v, dict) and dv and set(v) & set(dv):
            sub = diff_from_defaults(v, dv)
            if sub:
                out[k] = sub
        elif v != dv:
            out[k] = v
    return out


def write_settings(account: str, settings: dict) -> dict:
    """Zapisz ustawienia pluginów do configu konta (z backupem i walidacją).

    Zapisujemy TYLKO znane pluginy — literówka w id nie zaśmieci configu —
    i tylko wartości różne od domyślnych (patrz `diff_from_defaults`).
    Przy okazji z pliku znikają klucze wycofane z katalogu.
    """
    unknown = [k for k in settings if k not in plugin_ids()]
    if unknown:
        raise ValueError(f"nieznane pluginy w body: {unknown}")

    path = _account_path(account)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    defaults = all_defaults()

    merged = cfg.get("plugins") or {}
    for pid, vals in settings.items():
        d = defaults[pid]
        prev = prune_to_schema(merged.get(pid) or {}, d)
        combined = deep_merge(prev, prune_to_schema(vals or {}, d))
        delta = diff_from_defaults(combined, d)
        if delta:
            merged[pid] = delta
        else:
            merged.pop(pid, None)
    cfg["plugins"] = merged

    backup = path.with_suffix(".json.bak")
    backup.write_bytes(path.read_bytes())
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from v2.config.schema import load_account
        # config_root jawnie — inaczej walidacja czytałaby z domyślnego katalogu,
        # a nie z tego, na którym właśnie zapisaliśmy
        load_account(account, config_root=CONFIG_ROOT)
    except Exception as e:
        path.write_bytes(backup.read_bytes())
        raise ValueError(f"walidacja padła — przywrócono backup: {e}") from e

    return {"ok": True, "backup": str(backup)}


def resolve(account: str, plugin_id: str, overrides: dict | None = None) -> dict:
    """Rozwiąż finalne ustawienia zadania: defaults ← config ← overrides.

    Używane przez `--dry-run` i (docelowo) przez JobRunner.
    """
    get_plugin(plugin_id)   # walidacja id
    return deep_merge(read_settings(account)[plugin_id], overrides or {})


# ============================================================
# Nisza konta (account.niche) — zapis z discovery
# ============================================================
_NICHE_STR_KEYS = ("description", "audience")
# keywords/hashtags = scalona lista (manual + discovered) do UI / promptów
# *_manual / *_discovered = źródła (ręczne vs AI); sync content_search bierze scaloną
_NICHE_PHRASE_SOURCE_KEYS = (
    "keywords_manual", "keywords_discovered",
    "hashtags_manual", "hashtags_discovered",
)
_NICHE_LIST_KEYS = ("keywords", "hashtags", "exclusions") + _NICHE_PHRASE_SOURCE_KEYS


def niche_is_accepted(account_or_niche) -> bool:
    """Czy AI engagement może korzystać z niszy.

    Brak klucza `accepted` (stare konta) = True. Jawne False = discovery
    czeka na zatwierdzenie w panelu — Vision ma skipnąć, nie failować kolejki.
    """
    niche = account_or_niche
    if hasattr(account_or_niche, "niche"):
        niche = getattr(account_or_niche, "niche", None) or {}
    elif isinstance(account_or_niche, dict) and "niche" in account_or_niche and \
            not any(k in account_or_niche for k in (*_NICHE_STR_KEYS, "accepted")):
        niche = account_or_niche.get("niche") or {}
    if not isinstance(niche, dict):
        return True
    if "accepted" not in niche:
        return True
    return bool(niche.get("accepted"))


def _as_str_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if not isinstance(v, list):
        raise ValueError(f"oczekiwano list[str], dostano {type(v).__name__}")
    out = []
    for x in v:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


def _as_hashtag_list(v) -> list[str]:
    """Lista hashtagów z wymuszonym prefiksem #."""
    out = []
    for x in _as_str_list(v):
        out.append("#" + x.lstrip("#"))
    return out


def _dedup_phrases(items, *, force_hash: bool = False) -> list[str]:
    """Dedup case-insensitive; zachowaj pierwszą formę zapisu."""
    out, seen = [], set()
    for x in items or []:
        s = str(x).strip()
        if not s:
            continue
        if force_hash:
            s = "#" + s.lstrip("#")
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def merge_niche_phrases(manual, discovered, *, force_hash: bool = False) -> list[str]:
    """Scalona lista: najpierw manual, potem discovered (bez duplikatów)."""
    return _dedup_phrases(
        list(manual or []) + list(discovered or []),
        force_hash=force_hash,
    )


def ensure_niche_phrase_sources(niche: dict | None) -> dict:
    """Źródła keywords/hashtags — legacy płaskie listy → discovered.

    Stare konta mają tylko `keywords` / `hashtags` (z AI). Traktujemy je jako
    discovered, żeby kolejny replace mógł je nadpisać; manual zostaje puste
    aż operator coś dopisze w panelu.
    """
    n = niche if isinstance(niche, dict) else {}
    has_src = any(k in n for k in _NICHE_PHRASE_SOURCE_KEYS)
    if has_src:
        return {
            "keywords_manual": _dedup_phrases(n.get("keywords_manual")),
            "keywords_discovered": _dedup_phrases(n.get("keywords_discovered")),
            "hashtags_manual": _dedup_phrases(
                n.get("hashtags_manual"), force_hash=True),
            "hashtags_discovered": _dedup_phrases(
                n.get("hashtags_discovered"), force_hash=True),
        }
    # legacy: wszystko z analizy / starego zapisu → discovered
    return {
        "keywords_manual": [],
        "keywords_discovered": _dedup_phrases(n.get("keywords")),
        "hashtags_manual": [],
        "hashtags_discovered": _dedup_phrases(n.get("hashtags"), force_hash=True),
    }


def apply_discovered_phrases(
    existing: dict | None,
    new_keywords,
    new_hashtags,
    *,
    mode: str = "append",
) -> dict:
    """Wgraj wynik AI do discovered; manual bez zmian.

    mode:
      - append  — dopisz nowe do poprzednich discovered (dedup)
      - replace — nadpisz tylko discovered nową listą z AI
    Pozycje z manual nie trafiają do discovered (nawet gdy AI je zwróci).
    Zwraca 6 kluczy list (źródła + scalone).
    """
    src = ensure_niche_phrase_sources(existing)
    m_kw = list(src["keywords_manual"])
    m_ht = list(src["hashtags_manual"])
    new_kw = _dedup_phrases(new_keywords)
    new_ht = _dedup_phrases(new_hashtags, force_hash=True)
    merge_mode = (mode or "append").strip().lower()
    if merge_mode not in ("append", "replace"):
        merge_mode = "append"

    if merge_mode == "replace":
        d_kw = list(new_kw)
        d_ht = list(new_ht)
    else:
        d_kw = _dedup_phrases(
            list(src["keywords_discovered"]) + new_kw)
        d_ht = _dedup_phrases(
            list(src["hashtags_discovered"]) + new_ht, force_hash=True)

    # discovered nie duplikuje manual (manual wygrywa w scalonej liście)
    m_kw_set = {x.casefold() for x in m_kw}
    m_ht_set = {x.casefold() for x in m_ht}
    d_kw = [x for x in d_kw if x.casefold() not in m_kw_set]
    d_ht = [x for x in d_ht if x.casefold() not in m_ht_set]

    return {
        "keywords_manual": m_kw,
        "keywords_discovered": d_kw,
        "hashtags_manual": m_ht,
        "hashtags_discovered": d_ht,
        "keywords": merge_niche_phrases(m_kw, d_kw),
        "hashtags": merge_niche_phrases(m_ht, d_ht, force_hash=True),
    }


def reconcile_niche_phrases(prev: dict | None, new: dict | None) -> dict:
    """Po ręcznej edycji w panelu: scalona lista → źródła.

    Reguła:
      - pozycja była w manual i nadal jest → manual
      - była w discovered i nadal jest → discovered
      - nowa (nigdzie wcześniej) → manual
      - usunięta z UI → znika z obu źródeł
    description / audience / exclusions / meta — z `new` (bez zmian).
    `tag_variants` — przepuszczane (normalize) albo zachowane z prev.
    """
    prev = prev if isinstance(prev, dict) else {}
    new = dict(new) if isinstance(new, dict) else {}
    src = ensure_niche_phrase_sources(prev)

    def _split(merged, manual_prev, discovered_prev, *, force_hash=False):
        man_set = {x.casefold() for x in manual_prev}
        disc_set = {x.casefold() for x in discovered_prev}
        # oryginalna forma zapisu ze źródeł (stabilniejsze niż UI)
        casemap = {}
        for x in list(manual_prev) + list(discovered_prev):
            casemap.setdefault(x.casefold(), x)
        man_out, disc_out = [], []
        for raw in _as_str_list(merged):
            item = ("#" + raw.lstrip("#")) if force_hash else raw
            key = item.casefold()
            # preferuj casing ze źródła jeśli znamy
            item = casemap.get(key, item)
            if key in man_set:
                man_out.append(item)
            elif key in disc_set:
                disc_out.append(item)
            else:
                # operator dodał w panelu → zawsze manual
                man_out.append(item)
        return (
            _dedup_phrases(man_out, force_hash=force_hash),
            _dedup_phrases(disc_out, force_hash=force_hash),
        )

    # jeśli formularz nie przysłał list — zachowaj poprzednie źródła
    if "keywords" in new:
        m_kw, d_kw = _split(
            new.get("keywords"),
            src["keywords_manual"], src["keywords_discovered"],
        )
    else:
        m_kw, d_kw = src["keywords_manual"], src["keywords_discovered"]
    if "hashtags" in new:
        m_ht, d_ht = _split(
            new.get("hashtags"),
            src["hashtags_manual"], src["hashtags_discovered"],
            force_hash=True,
        )
    else:
        m_ht, d_ht = src["hashtags_manual"], src["hashtags_discovered"]

    new["keywords_manual"] = m_kw
    new["keywords_discovered"] = d_kw
    new["hashtags_manual"] = m_ht
    new["hashtags_discovered"] = d_ht
    new["keywords"] = merge_niche_phrases(m_kw, d_kw)
    new["hashtags"] = merge_niche_phrases(m_ht, d_ht, force_hash=True)
    # mapa wariantów Tags — nie dropuj przy zapisie panelu bez pola
    if "tag_variants" in new:
        new["tag_variants"] = normalize_tag_variants_map(new.get("tag_variants"))
    else:
        new["tag_variants"] = normalize_tag_variants_map(prev.get("tag_variants"))
    return new


def normalize_tag_variant_expression(expression: str) -> str:
    """Klucz mapy tag_variants: `#…` dla hashtagów, strip dla keywords."""
    s = str(expression or "").strip()
    if not s:
        return ""
    if s.startswith("#"):
        return "#" + s.lstrip("#")
    return s


def normalize_tag_variants_map(raw) -> dict:
    """Wyczyść mapę expression → list[#]: dedup casefold, puste klucze out."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    # casefold klucza → forma zachowana (pierwszy zapis)
    key_by_cf: dict[str, str] = {}
    for raw_key, raw_vals in raw.items():
        expr = normalize_tag_variant_expression(raw_key)
        if not expr:
            continue
        cf = expr.casefold()
        stored_key = key_by_cf.get(cf)
        if stored_key is None:
            key_by_cf[cf] = expr
            stored_key = expr
            out[stored_key] = []
        merged = _dedup_phrases(
            list(out.get(stored_key) or []) + _as_hashtag_list(raw_vals),
            force_hash=True,
        )
        out[stored_key] = merged
    return out


def ensure_tag_variants(niche: dict | None) -> dict:
    """Zwróć znormalizowaną mapę niche.tag_variants (brak → {})."""
    n = niche if isinstance(niche, dict) else {}
    return normalize_tag_variants_map(n.get("tag_variants"))


def merge_tag_variants(
    niche: dict | None,
    expression: str,
    variants: list,
    *,
    max_variants: int | None = None,
    include_seed: bool = False,
    trim: bool = False,
) -> dict:
    """Merge + dedup `niche.tag_variants[expression]` → zaktualizowana mapa.

    - klucz = znormalizowane expression (match casefold, zachowaj pierwszy klucz)
    - wartości zawsze z `#`, dedup casefold; kolejność: stare, potem nowe
    - `include_seed=False` (default): nie wstawiaj samego seeda do listy
    - `max_variants`: obetnij prefix; jeśli już zapisane > limitu — nie kasuj
      bez `trim=True`
    Nie rusza innych wyrażeń. Zwraca całą mapę (do wstawienia w niche).
    """
    niche = niche if isinstance(niche, dict) else {}
    mapping = normalize_tag_variants_map(niche.get("tag_variants"))
    expr = normalize_tag_variant_expression(expression)
    if not expr:
        return mapping

    # znajdź istniejący klucz (casefold) — zachowaj pierwszą formę zapisu
    expr_cf = expr.casefold()
    stored_key = expr
    for k in mapping:
        if k.casefold() == expr_cf:
            stored_key = k
            break

    old = list(mapping.get(stored_key) or [])
    new_vals = _dedup_phrases(_as_hashtag_list(variants), force_hash=True)

    if not include_seed:
        seed_cf = (
            ("#" + expr.lstrip("#")).casefold()
            if expr.startswith("#")
            else None
        )
        # keyword seed też może trafić jako #keyword — odfiltruj obie formy
        seed_forms = {expr.casefold()}
        if seed_cf:
            seed_forms.add(seed_cf)
        else:
            seed_forms.add(("#" + expr).casefold())
        new_vals = [v for v in new_vals if v.casefold() not in seed_forms]

    merged = _dedup_phrases(old + new_vals, force_hash=True)

    if max_variants is not None and max_variants >= 0 and len(merged) > max_variants:
        if trim or len(old) <= max_variants:
            merged = merged[:max_variants]
        else:
            # limit < już zapisane — nie kasuj starych; nie doklejaj nowych
            merged = _dedup_phrases(old, force_hash=True)

    mapping[stored_key] = merged
    return mapping


def write_niche_tag_variants(
    account: str,
    expression: str,
    variants: list,
    *,
    max_variants: int | None = None,
    include_seed: bool = False,
    trim: bool = False,
) -> dict:
    """Persist merge_tag_variants do config konta; zwraca zaktualizowane niche."""
    path = _account_path(account)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    niche = dict(cfg.get("niche") or {})
    niche["tag_variants"] = merge_tag_variants(
        niche,
        expression,
        variants,
        max_variants=max_variants,
        include_seed=include_seed,
        trim=trim,
    )
    return write_niche(account, niche)


def write_niche(account: str, niche: dict) -> dict:
    """Zapisz sekcję niche do config/accounts/<account>.json z .bak.

    Nie kasuje obcych przyszłych kluczy. Waliduje stringi / list[str].
    Zwraca zapisany dict niche.
    """
    if not isinstance(niche, dict):
        raise ValueError("niche musi być obiektem")

    path = _account_path(account)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    prev = dict(cfg.get("niche") or {})

    clean: dict = dict(prev)
    for k in _NICHE_STR_KEYS:
        if k in niche:
            v = niche[k]
            clean[k] = "" if v is None else str(v).strip()
    for k in _NICHE_LIST_KEYS:
        if k in niche:
            clean[k] = _as_str_list(niche[k])
    if "accepted" in niche:
        clean["accepted"] = bool(niche["accepted"])
    if "discovered_at" in niche:
        clean["discovered_at"] = niche["discovered_at"]
    # mapa wariantów Tags (expression → list[#])
    if "tag_variants" in niche:
        clean["tag_variants"] = normalize_tag_variants_map(niche.get("tag_variants"))
    # inne znane / przyszłe klucze (np. reasons, last_ai_usage) —
    # stringi / bool / liczby / listy / dict (meta z discovery)
    for k, v in niche.items():
        if k == "tag_variants":
            continue
        if k in clean or k in _NICHE_STR_KEYS or k in _NICHE_LIST_KEYS:
            continue
        if isinstance(v, (str, bool)) or v is None:
            clean[k] = v
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            clean[k] = v
        elif isinstance(v, list):
            clean[k] = _as_str_list(v)
        elif isinstance(v, dict):
            # np. last_ai_usage — breakdown tokenów z ostatniego discovery
            clean[k] = v

    cfg["niche"] = clean
    backup = path.with_suffix(".json.bak")
    backup.write_bytes(path.read_bytes())
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def clear_niche(account: str) -> dict:
    """Wyczyść account.niche (z .bak) — pusty szkielet pod formularz panelu.

    Usuwa też accepted / discovered_at / reasons / tag_variants itd.
    Operator zaczyna od zera (albo odpala discovery od nowa).
    """
    path = _account_path(account)
    if not path.exists():
        raise FileNotFoundError(f"brak configu konta: {account}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    empty = {
        "description": "",
        "audience": "",
        "keywords": [],
        "hashtags": [],
        "exclusions": [],
        "keywords_manual": [],
        "keywords_discovered": [],
        "hashtags_manual": [],
        "hashtags_discovered": [],
        "tag_variants": {},
    }
    backup = path.with_suffix(".json.bak")
    backup.write_bytes(path.read_bytes())
    cfg["niche"] = empty
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return empty


# ============================================================
# Kolejka zadań = scenariusze — JEDNA lista, bez ukrytego rozróżnienia
# ============================================================
# Scalone 2026-08-03: „domyślna kolejka" i „nazwane scenariusze" były dwoma
# równoległymi bytami (plik-rodzeństwo `<alias>.queue.json` kontra podkatalog
# `<alias>.queues/<nazwa>.json`), mimo identycznego kształtu danych — w
# panelu wyglądało to jak dwa osobne mechanizmy. Teraz WSZYSTKIE kolejki,
# włącznie z domyślną, mieszkają w jednym miejscu: `<alias>.queues/<nazwa>.json`.
# Domyślna to po prostu scenariusz o zarezerwowanej nazwie `DEFAULT_SCENARIO`
# — widoczny na liście jak każdy inny, tylko nie da się go usunąć (musi
# zawsze być coś, co odpali sidebar/scheduler bez podanej nazwy).
#
# Podkatalog (nie plik-rodzeństwo) jest nadal ważny z tego samego powodu co
# wcześniej: `list_accounts()` w panelu robi płaski `glob("*.json")` po
# `accounts/` i pomyliłby plik kolejki z kontem.
DEFAULT_SCENARIO = "default"

# Slug pliku zapisanej akcji — ten sam alfabet co nazwa runu w panelu.
ACTION_SLUG_RE = re.compile(r"^[a-zA-Z0-9._-]{1,40}$")
# Polskie znaki w nazwie wyświetlanej → ASCII w slugu pliku.
_PL_SLUG_TRANS = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
})

# Kolejki efemeryczne harmonogramu: blok na osi czasu, który ma własne zmiany
# (override per slot), materializuje się w plik o tym prefiksie tuż przed
# startem. Dzięki temu JobRunner i CLI dostają zwykły scenariusz i nie muszą
# znać pojęcia „blok". Prefiks wyklucza je z listy w panelu — użytkownik nigdy
# nie wybiera ich ręcznie.
SLOT_QUEUE_PREFIX = "_slot_"


def _queue_presets_dir(account: str) -> Path:
    return CONFIG_ROOT / "accounts" / f"{account}.queues"


def _legacy_default_queue_path(account: str) -> Path:
    """Lokalizacja domyślnej kolejki SPRZED scalenia — tylko do
    jednorazowej migracji przy pierwszym odczycie po aktualizacji."""
    return CONFIG_ROOT / "accounts" / f"{account}.queue.json"


def _queue_path(account: str, name: str | None = None) -> Path:
    _account_path(account)   # konto musi istnieć
    name = name or DEFAULT_SCENARIO
    p = _queue_presets_dir(account) / f"{name}.json"
    if name == DEFAULT_SCENARIO and not p.exists():
        legacy = _legacy_default_queue_path(account)
        if legacy.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(legacy.read_bytes())
            legacy.unlink()
    return p


def list_queue_presets(account: str) -> list[str]:
    """Wszystkie scenariusze, WŁĄCZNIE z `default` — jedna lista, żadnego
    ukrytego drugiego bytu „kolejka" obok „scenariuszy". Kolejki efemeryczne
    harmonogramu (`SLOT_QUEUE_PREFIX`) są pominięte — to nie są scenariusze
    użytkownika, tylko materializacja pojedynczego bloku."""
    _account_path(account)
    _queue_path(account, DEFAULT_SCENARIO)   # wyzwól migrację starej domyślnej kolejki, jeśli jest
    d = _queue_presets_dir(account)
    names = {p.stem for p in d.glob("*.json")
             if not p.stem.endswith(".bak")
             and not p.stem.startswith(SLOT_QUEUE_PREFIX)} if d.exists() else set()
    names.add(DEFAULT_SCENARIO)
    return sorted(names, key=lambda n: (n != DEFAULT_SCENARIO, n))


def write_slot_queue(account: str, block_id: str, data: dict) -> str:
    """Zmaterializuj kolejkę bloku harmonogramu i zwróć nazwę scenariusza
    do przekazania jako `--queue=`."""
    name = f"{SLOT_QUEUE_PREFIX}{block_id}"
    write_queue(account, data, name=name)
    return name


def drop_slot_queue(account: str, block_id: str) -> None:
    """Skasuj materializację bloku po jego zakończeniu — bez tego katalog
    scenariuszy puchnie o jeden plik na każdą wykonaną sesję."""
    d = _queue_presets_dir(account)
    for p in d.glob(f"{SLOT_QUEUE_PREFIX}{block_id}.json*"):
        try:
            p.unlink()
        except OSError:
            pass


EMPTY_QUEUE = {
    "queue": [],
    "between_tasks_s": {"min": 60, "max": 240},
    "on_task_error": "continue",
}


def read_queue(account: str, name: str | None = None) -> dict:
    p = _queue_path(account, name)
    if not p.exists():
        return json.loads(json.dumps(EMPTY_QUEUE))
    data = json.loads(p.read_text(encoding="utf-8"))
    return deep_merge(json.loads(json.dumps(EMPTY_QUEUE)), data)


def enable_queued_plugins(account: str, tasks: list[dict] | None = None) -> list[str]:
    """Włącz na koncie pluginy z włączonych zadań kolejki.

    Katalog: „Wyłączony nie da się dodać do kolejki.” Nowe pluginy
    (np. publish_media) startują z ``enabled: False``. Zapis runy / Start
    to decyzja operatora — bez tego JobRunner cicho skipuje
    ``plugin_disabled_for_account`` i sesja wygląda na OK.
    Scheduler nie woła tej funkcji (zostaje kill-switch).
    """
    pids: list[str] = []
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        if t.get("enabled", True) is False:
            continue
        pid = t.get("plugin")
        if pid and pid in plugin_ids() and pid not in pids:
            pids.append(pid)
    if not pids:
        return []
    settings = read_settings(account)
    to_on = [pid for pid in pids if not settings[pid].get("enabled")]
    if not to_on:
        return []
    # Tylko flaga enabled — bez write_settings/load_account (to by
    # odpalało probe ADB przy każdym zapisie runy).
    path = _account_path(account)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    merged = cfg.get("plugins") or {}
    if not isinstance(merged, dict):
        merged = {}
    for pid in to_on:
        prev = merged.get(pid)
        prev = dict(prev) if isinstance(prev, dict) else {}
        prev["enabled"] = True
        merged[pid] = prev
    cfg["plugins"] = merged
    backup = path.with_suffix(".json.bak")
    backup.write_bytes(path.read_bytes())
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return to_on


def write_queue(account: str, data: dict, name: str | None = None) -> dict:
    """Zapisz kolejkę (domyślną albo nazwany scenariusz). Waliduje id
    pluginów i nadaje brakujące id zadań."""
    tasks = data.get("queue")
    if not isinstance(tasks, list):
        raise ValueError("pole 'queue' musi być listą zadań")

    known = plugin_ids()
    clean = []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            raise ValueError(f"zadanie #{i + 1} nie jest obiektem")
        pid = t.get("plugin")
        if pid not in known:
            raise ValueError(f"zadanie #{i + 1}: nieznany plugin {pid!r}")
        ov = t.get("overrides") or {}
        if not isinstance(ov, dict):
            raise ValueError(f"zadanie #{i + 1}: 'overrides' musi być obiektem JSON")
        item = {
            "id":        t.get("id") or f"t{uuid.uuid4().hex[:6]}",
            "plugin":    pid,
            "note":      (t.get("note") or "").strip(),
            "budget":    t.get("budget") or {},
            "overrides": ov,
            "enabled":   bool(t.get("enabled", True)),
        }
        # Opcjonalny wskaźnik na zapisaną akcję — kopia, nie live-link.
        # Runtime go ignoruje; panel używa do „Zaktualizuj zapisaną”.
        fa = (t.get("from_action") or "").strip()
        if fa and ACTION_SLUG_RE.match(fa):
            item["from_action"] = fa
        clean.append(item)

    out = {
        "queue": clean,
        "between_tasks_s": data.get("between_tasks_s") or EMPTY_QUEUE["between_tasks_s"],
        "on_task_error":   data.get("on_task_error") or "continue",
    }
    p = _queue_path(account, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.with_suffix(".json.bak").write_bytes(p.read_bytes())
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    # Zapis runy = operator chce te pluginy odpalić (nie cichy skip).
    enabled = enable_queued_plugins(account, clean)
    return {"ok": True, "tasks": len(clean), "path": str(p),
            "enabled_plugins": enabled}


def delete_queue_preset(account: str, name: str) -> None:
    if not name:
        raise ValueError("nazwa scenariusza wymagana")
    if name == DEFAULT_SCENARIO:
        raise ValueError("nie można usunąć scenariusza domyślnego")
    p = _queue_path(account, name)
    if not p.exists():
        raise FileNotFoundError(f"scenariusz '{name}' nie istnieje")
    p.unlink()
    bak = p.with_suffix(".json.bak")
    if bak.exists():
        bak.unlink()


# ============================================================
# Zapisane akcje — named preset kroku (plugin + overrides)
# ============================================================
# To NIE jest nowy typ runu. Run nadal = lista tasków w
# `<alias>.queues/<nazwa>.json`. Zapisana akcja to nazwany zestaw
# plugin_id + overrides (+ budżet), analogicznie per konto w
# `<alias>.actions/<slug>.json`. Wstawienie do runu kopiuje pola
# (osobny task.id) — edycja kroku nie rusza presetu, dopóki user
# nie „Zapisz jako” / „Zaktualizuj zapisaną”.


def _actions_dir(account: str) -> Path:
    return CONFIG_ROOT / "accounts" / f"{account}.actions"


def _action_path(account: str, slug: str) -> Path:
    _account_path(account)  # konto musi istnieć
    if not ACTION_SLUG_RE.match(slug or ""):
        raise ValueError(
            "id zapisanej akcji: 1–40 znaków, litery/cyfry/._- (bez spacji)")
    return _actions_dir(account) / f"{slug}.json"


def slugify_action_id(name: str, plugin: str = "") -> str:
    """Nazwa wyświetlana → slug pliku (ASCII, jak nazwa runu)."""
    raw = (name or "").strip().casefold().translate(_PL_SLUG_TRANS)
    raw = re.sub(r"[\s+/]+", "-", raw)
    raw = re.sub(r"[^a-z0-9._-]+", "", raw)
    raw = re.sub(r"-{2,}", "-", raw).strip(".-_")
    if not raw:
        raw = re.sub(r"[^a-z0-9._-]+", "", (plugin or "akcja").casefold()) or "akcja"
    return raw[:40]


def _unique_action_slug(account: str, base: str) -> str:
    """Dobierz wolny slug — przy kolizji `hashtagi`, `hashtagi-2`, …"""
    d = _actions_dir(account)
    slug = base[:40] or "akcja"
    n = 2
    while (d / f"{slug}.json").exists():
        suffix = f"-{n}"
        slug = (base[: max(1, 40 - len(suffix))] + suffix)
        n += 1
        if n > 99:
            slug = (base[:33] + "-" + uuid.uuid4().hex[:6])[:40]
            break
    return slug


def _clone_json(obj):
    """Głęboka kopia przez JSON — ten sam wzorzec co pusta kolejka."""
    return json.loads(json.dumps(obj))


def _clean_action_payload(data: dict) -> dict:
    """Walidacja jak przy zapisie taska kolejki + nazwa/komentarz."""
    if not isinstance(data, dict):
        raise ValueError("ciało zapisanej akcji musi być obiektem JSON")
    pid = data.get("plugin")
    if pid not in plugin_ids():
        raise ValueError(f"nieznany plugin {pid!r}")
    ov = data.get("overrides") or {}
    if not isinstance(ov, dict):
        raise ValueError("'overrides' musi być obiektem JSON")
    budget = data.get("budget") or {}
    if not isinstance(budget, dict):
        raise ValueError("'budget' musi być obiektem JSON")
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("pole 'name' (nazwa wyświetlana) jest wymagane")
    comment = (data.get("comment") or data.get("notes") or "").strip()
    return {
        "name": name,
        "comment": comment,
        "plugin": pid,
        "overrides": _clone_json(ov),
        "budget": _clone_json(budget),
        "note": (data.get("note") or "").strip(),
    }


def list_actions(account: str) -> list[dict]:
    """Wszystkie zapisane akcje konta — pełne obiekty (do biblioteki)."""
    _account_path(account)
    d = _actions_dir(account)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        if p.stem.endswith(".bak"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        item = dict(data)
        item["id"] = p.stem
        out.append(item)
    return out


def read_action(account: str, slug: str) -> dict:
    p = _action_path(account, slug)
    if not p.exists():
        raise FileNotFoundError(f"zapisana akcja '{slug}' nie istnieje")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"zapisana akcja '{slug}' nie jest obiektem JSON")
    data["id"] = slug
    return data


def create_action(account: str, data: dict) -> dict:
    """Nowa zapisana akcja. `id`/`slug` opcjonalne — inaczej z `name`."""
    clean = _clean_action_payload(data)
    raw_id = (data.get("id") or data.get("slug") or "").strip()
    if raw_id:
        if not ACTION_SLUG_RE.match(raw_id):
            raise ValueError(
                "id zapisanej akcji: 1–40 znaków, litery/cyfry/._- (bez spacji)")
        slug = raw_id
        if _action_path(account, slug).exists():
            raise FileExistsError(f"zapisana akcja '{slug}' już istnieje")
    else:
        slug = _unique_action_slug(
            account, slugify_action_id(clean["name"], clean["plugin"]))
    p = _action_path(account, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = dict(clean)
    p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "id": slug, "path": str(p), **body}


def update_action(account: str, slug: str, data: dict) -> dict:
    """Nadpisz preset (nazwa / komentarz / overrides). Plugin można zmienić."""
    p = _action_path(account, slug)
    if not p.exists():
        raise FileNotFoundError(f"zapisana akcja '{slug}' nie istnieje")
    prev = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(prev, dict):
        prev = {}
    merged = dict(prev)
    merged.update(data or {})
    # PUT może przysłać samo name/comment — dopełnij plugin z pliku.
    if not merged.get("plugin"):
        merged["plugin"] = prev.get("plugin")
    if "overrides" not in (data or {}) and "overrides" in prev:
        merged["overrides"] = prev["overrides"]
    if "budget" not in (data or {}) and "budget" in prev:
        merged["budget"] = prev["budget"]
    if not (merged.get("name") or "").strip():
        merged["name"] = prev.get("name") or slug
    clean = _clean_action_payload(merged)
    p.with_suffix(".json.bak").write_bytes(p.read_bytes())
    p.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "id": slug, "path": str(p), **clean}


def delete_action(account: str, slug: str) -> None:
    p = _action_path(account, slug)
    if not p.exists():
        raise FileNotFoundError(f"zapisana akcja '{slug}' nie istnieje")
    p.unlink()
    bak = p.with_suffix(".json.bak")
    if bak.exists():
        bak.unlink()


def action_to_task(action: dict) -> dict:
    """Kopia presetu → nowy task kolejki (osobne id, bez live-linku)."""
    if not isinstance(action, dict):
        raise ValueError("zapisana akcja musi być obiektem")
    pid = action.get("plugin")
    if pid not in plugin_ids():
        raise ValueError(f"nieznany plugin {pid!r}")
    ov = action.get("overrides") or {}
    if not isinstance(ov, dict):
        raise ValueError("'overrides' musi być obiektem JSON")
    budget = action.get("budget") or {}
    if not isinstance(budget, dict):
        raise ValueError("'budget' musi być obiektem JSON")
    slug = (action.get("id") or "").strip()
    task = {
        "id": f"t{uuid.uuid4().hex[:6]}",
        "plugin": pid,
        "note": (action.get("note") or action.get("name") or "").strip(),
        "budget": _clone_json(budget),
        "overrides": _clone_json(ov),
        "enabled": True,
    }
    if slug and ACTION_SLUG_RE.match(slug):
        task["from_action"] = slug
    return task


def resolve_tasks(account: str, tasks: list[dict]) -> list[dict]:
    """Rozwiąż listę zadań podaną wprost — nie musi istnieć na dysku.

    Wydzielone z `resolve_queue`, bo harmonogram liczy czas także dla kolejek,
    które istnieją tylko w bloku osi czasu (override per slot) i nigdy nie
    trafiły do pliku scenariusza.
    """
    settings = read_settings(account)
    out = []
    for t in tasks:
        pid = t.get("plugin")
        if pid not in settings:
            raise ValueError(f"nieznany plugin: {pid!r}")
        pl = get_plugin(pid)
        resolved = deep_merge(settings[pid], t.get("overrides") or {})
        if t.get("budget"):
            resolved = deep_merge(resolved, {"budget": t["budget"]})
        out.append({
            "id": t.get("id", ""), "plugin": pid, "label": pl["label"],
            "enabled": t.get("enabled", True),
            "note": t.get("note", ""),
            "status": pl["status"],
            "plugin_enabled_for_account": bool(settings[pid].get("enabled")),
            "resolved": resolved,
        })
    return out


def resolve_queue(account: str, name: str | None = None) -> list[dict]:
    """Podgląd całej kolejki z rozwiązanymi ustawieniami — dla `--dry-run`.

    To jest główne narzędzie do sprawdzenia, czy merge trójstopniowy robi to,
    czego się spodziewamy, ZANIM cokolwiek dotknie telefonu.
    """
    return resolve_tasks(account, read_queue(account, name)["queue"])
