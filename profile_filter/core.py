"""Rdzeń filtra IG przez web API + proxy (bez interaktywnego input).

Transport: **wyłącznie curl_cffi** z impersonacją Chrome.
Jak `TARGETY/proxy1.py`: URL+fields, timeout 10s, sleep 0.5s, nagłówki
x-ig-app-id / Accept-Language, nowy Client/Session na request.

IP: **rotacja przy KAŻDYM requeście** — świeży session-ID (residential)
albo gateway rotate; nigdy nie trzymamy sticky IP między requestami.
"""
from __future__ import annotations

import random
import re
import secrets as pysecrets
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

try:
    from curl_cffi import requests as creq
except ImportError:  # pragma: no cover — wymagane w runtime sieciowym
    creq = None

# Domyślne kryteria (jak w proxy1)
DEFAULT_FOLLOWERS_MIN = 60
DEFAULT_FOLLOWERS_MAX = 20000
MAX_RETRIES_PER_USER = 4
REQUEST_SLEEP_S = 0.5
REQUEST_TIMEOUT_S = 10

API_URL = (
    "https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    "&fields=is_private,edge_owner_to_timeline_media.count,edge_followed_by.count,"
    "biography_with_entities.raw_text"
)

# Nagłówki jak proxy1 — bez User-Agent (ustawia impersonate Chrome)
PROXY1_HEADERS = {
    "x-ig-app-id": "936619743392459",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    "Accept": "*/*",
}

IMPERSONATE_TARGETS = (
    "chrome124", "chrome131", "chrome136", "chrome120", "edge101",
)

_ARABIC_HINDI = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFE70-\uFEFF\u0900-\u097F\u0980-\u09FF]"
)
# session-xxxx / session_xxxx w haśle proxy (rayobyte, marsproxies, …)
_SESSION_IN_PASS = re.compile(r"(session[-_])([A-Za-z0-9]+)", re.I)


@dataclass
class FilterCriteria:
    """Kryteria akceptacji profilu — z account.profile_filter lub CLI."""
    followers_min: int = DEFAULT_FOLLOWERS_MIN
    followers_max: int = DEFAULT_FOLLOWERS_MAX
    reject_arabic_bio: bool = True
    require_public: bool = True
    min_posts: int = 1

    @classmethod
    def from_dict(cls, d: dict | None) -> "FilterCriteria":
        d = d or {}
        return cls(
            followers_min=int(d.get("followers_min", DEFAULT_FOLLOWERS_MIN)),
            followers_max=int(d.get("followers_max", DEFAULT_FOLLOWERS_MAX)),
            reject_arabic_bio=bool(d.get("reject_arabic_bio", True)),
            require_public=bool(d.get("require_public", True)),
            min_posts=int(d.get("min_posts", 1)),
        )


@dataclass
class UserData:
    is_private: bool
    post_count: int
    follower_count: int
    biography: str


def load_proxies() -> Dict[str, str | List[str]]:
    """Pula proxy z secrets.json (string lub lista URL-i jak PROXY_URLs)."""
    from v2.config import secrets
    return secrets.get_filter_proxies()


def contains_arabic_or_hindi(text: str) -> bool:
    return bool(_ARABIC_HINDI.search(text or ""))


def evaluate_user(username: str, user_data: UserData,
                  criteria: FilterCriteria) -> str:
    """Zwraca 'ok' | 'remove' na podstawie kryteriów (bez sieci)."""
    if criteria.reject_arabic_bio and contains_arabic_or_hindi(user_data.biography):
        return "remove"
    if criteria.require_public and user_data.is_private:
        return "remove"
    if user_data.post_count < criteria.min_posts:
        return "remove"
    if not (criteria.followers_min < user_data.follower_count
            < criteria.followers_max):
        return "remove"
    return "ok"


def parse_user_data(data: Dict) -> Optional[UserData]:
    user = (data.get("data") or {}).get("user")
    if not user:
        return None
    biography = (user.get("biography_with_entities") or {}).get("raw_text")
    if biography is None:
        biography = user.get("biography") or ""
    return UserData(
        is_private=user.get("is_private", False),
        post_count=(user.get("edge_owner_to_timeline_media") or {}).get("count", 0),
        follower_count=(user.get("edge_followed_by") or {}).get("count", 0),
        biography=biography,
    )


def _is_rotating_gateway(url: str) -> bool:
    """Webshare-style rotate endpoint — IP zmienia się samo na request."""
    u = (url or "").lower()
    return "-rotate@" in u or "rotate:" in u or "/rotate" in u


def _is_rayobyte_host(host: str) -> bool:
    return "rayobyte" in (host or "").lower()


def mint_fresh_proxy_url(url: str) -> str:
    """Nowy exit-IP na ten request (nowy session-ID).

    - gateway ``*-rotate``: bez zmian (rotuje po stronie dostawcy)
    - rayobyte: ``pass-country-PL-session-<id>-duration-1`` (jak komentarze proxy1)
    - inne z ``session-``/``session_`` w haśle: podmień ID (jak pula PROXY_URLs)
    """
    if not url or _is_rotating_gateway(url):
        return url
    parts = urlsplit(url)
    if not parts.hostname or "@" not in (parts.netloc or ""):
        return url
    userinfo, hostport = parts.netloc.rsplit("@", 1)
    if ":" not in userinfo:
        return url
    user, password = userinfo.split(":", 1)
    sid = pysecrets.token_hex(4)

    if _SESSION_IN_PASS.search(password):
        new_pass = _SESSION_IN_PASS.sub(rf"\g<1>{sid}", password, count=1)
    elif _is_rayobyte_host(parts.hostname):
        base_pass = password
        if "-country-" in base_pass:
            base_pass = base_pass.split("-country-")[0]
        new_pass = f"{base_pass}-country-PL-session-{sid}-duration-1"
    else:
        # Brak mechanizmu session w haśle — zostaw URL (np. zwykły DC)
        return url

    netloc = f"{user}:{new_pass}@{hostport}"
    return urlunsplit((
        parts.scheme, netloc, parts.path, parts.query, parts.fragment))


# Alias kompatybilności z testami / wcześniejszym API
def expand_sticky_sessions(url: str, n: int = 1) -> List[str]:
    """Wygeneruj n świeżych URL (różne session-ID) — diagnostyka / testy."""
    if not url:
        return []
    if _is_rotating_gateway(url):
        return [url]
    return [mint_fresh_proxy_url(url) for _ in range(max(1, n))]


def _urls_from_entry(item: str | List[str]) -> List[str]:
    """Bazy URL z secrets — BEZ trwałego sticky pool (świeże ID przy requeście)."""
    pool: List[str] = []
    if isinstance(item, list):
        for u in item:
            u = (u or "").strip()
            if u:
                pool.append(u)
    else:
        u = (item or "").strip()
        if u:
            pool.append(u)
    seen: set[str] = set()
    uniq: List[str] = []
    for u in pool:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def build_proxy_groups(proxies: Dict[str, str | List[str]],
                       proxy_mode: str = "losowo") -> Dict[str, List[str]]:
    """Grupy bazowych URL per nazwa (równa szansa dostawcy przy pick)."""
    if not proxies:
        return {}
    mode = proxy_mode or "losowo"
    if mode == "losowo":
        names = list(proxies.keys())
    elif mode in proxies:
        names = [mode]
    else:
        raise ValueError(
            f"Nieznany tryb proxy: {mode}. "
            f"Dostępne: {', '.join(list(proxies) + ['losowo'])}"
        )
    groups: Dict[str, List[str]] = {}
    for name in names:
        urls = _urls_from_entry(proxies[name])
        if urls:
            groups[name] = urls
    return groups


def build_proxy_pool(proxies: Dict[str, str | List[str]],
                     proxy_mode: str = "losowo") -> List[str]:
    """Płaska lista bazowych URL (jak PROXY_URLs w proxy1)."""
    groups = build_proxy_groups(proxies, proxy_mode)
    flat: List[str] = []
    seen: set[str] = set()
    for urls in groups.values():
        for u in urls:
            if u not in seen:
                seen.add(u)
                flat.append(u)
    return flat


@dataclass
class ScrapeStats:
    total_requests: int = 0
    checked: int = 0
    skip: int = 0
    saved: int = 0
    removed: int = 0


class InstagramScraper:
    """Scraper — curl_cffi + Chrome; nowe IP na każdy request."""

    def __init__(self, proxy_mode: str = "losowo",
                 criteria: FilterCriteria | None = None,
                 proxies: Dict[str, str | List[str]] | None = None,
                 log: Callable[[str], None] | None = None):
        if creq is None:
            raise RuntimeError(
                "Brak curl_cffi — zainstaluj: pip install curl_cffi")
        raw = proxies if proxies is not None else load_proxies()
        self.proxy_mode = proxy_mode or "losowo"
        self.proxy_groups = build_proxy_groups(raw, self.proxy_mode)
        self.proxy_pool = build_proxy_pool(raw, self.proxy_mode)
        if not self.proxy_pool:
            raise RuntimeError(
                "Brak proxy filtrów w config/secrets.json "
                "(klucz filter_proxies)")
        self.proxies = raw
        self.criteria = criteria or FilterCriteria()
        _log = log or print

        def _safe_log(msg: str) -> None:
            try:
                _log(msg)
            except UnicodeEncodeError:
                _log(str(msg).encode("ascii", "replace").decode("ascii"))

        self.log = _safe_log
        self.stats = ScrapeStats()
        self.impersonate = random.choice(IMPERSONATE_TARGETS)
        self.current_proxy = ""
        # Pierwsza rotacja od razu
        self.rotate_for_request()
        self.log(
            f"[filter] curl_cffi impersonate={self.impersonate} "
            f"bases={len(self.proxy_pool)} providers={list(self.proxy_groups)} "
            f"mode={self.proxy_mode} rotate_every_request=1"
        )

    def _pick_base(self) -> str:
        name = random.choice(list(self.proxy_groups.keys()))
        return random.choice(self.proxy_groups[name])

    def rotate_for_request(self) -> None:
        """Nowe IP + fingerprint przed KAŻDYM requestem (wymóg jak proxy1 pool)."""
        base = self._pick_base()
        self.current_proxy = mint_fresh_proxy_url(base)
        others = [t for t in IMPERSONATE_TARGETS if t != self.impersonate]
        self.impersonate = random.choice(others or list(IMPERSONATE_TARGETS))

    # Alias — wywołania po błędzie też pełna rotacja
    def change_proxy(self) -> None:
        self.rotate_for_request()

    def _get(self, url: str):
        """Jednorazowa Session curl_cffi (nowe połączenie = nowe IP)."""
        time.sleep(REQUEST_SLEEP_S)
        # Session per request — jak nowy httpx.Client w proxy1
        with creq.Session(impersonate=self.impersonate) as session:
            return session.get(
                url,
                headers=dict(PROXY1_HEADERS),
                proxies={"http": self.current_proxy,
                         "https": self.current_proxy},
                timeout=REQUEST_TIMEOUT_S,
            )

    def scrape_user(self, username: str) -> str:
        """'ok' | 'remove' | 'skip' (retry pozniej)."""
        url = API_URL.format(username=username)
        for _attempt in range(1, MAX_RETRIES_PER_USER + 1):
            # Rotacja IP przed każdym requestem (takze po sukcesie poprzedniego usera)
            self.rotate_for_request()
            self.stats.total_requests += 1
            host = urlsplit(self.current_proxy).hostname or "?"
            self.log(f"Uzywam proxy: {host} ({self.impersonate})")
            try:
                response = self._get(url)
            except Exception as e:
                self.log(f"SKIP powod: blad polaczenia ({type(e).__name__})")
                continue

            status = response.status_code
            if status == 200:
                try:
                    user_data = parse_user_data(response.json())
                except ValueError:
                    self.log("SKIP powod: odpowiedz nie jest poprawnym JSON-em")
                    continue
                if user_data is None:
                    self.log("SKIP powod: odpowiedz bez danych uzytkownika")
                    continue
                self.stats.checked += 1
                result = evaluate_user(username, user_data, self.criteria)
                if result == "ok":
                    self.log(f"Save {username}")
                    self.stats.saved += 1
                else:
                    self.log(f"Remove {username}")
                    self.stats.removed += 1
                self.log("----------------------------")
                return result

            if status == 404:
                self.log(f"{username} not found.")
                self.log("----------------------------")
                self.stats.removed += 1
                return "remove"

            self.log(f"SKIP powod HTTP: {status}")

        self.stats.skip += 1
        return "skip"
