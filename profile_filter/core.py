"""Rdzeń filtra IG przez HTML profilu + proxy (bez interaktywnego input).

API ``web_profile_info`` zwraca 401 require_login na residential.
Bierzemy ``www.instagram.com/{nick}/``: followers z JSON w HTML,
posty z ``og:description`` (``34 Posts``), prywatność z ``is_private``.

User-Agent: NIE ustawiamy ręcznie — `impersonate=` dobiera UA + TLS.
Pula: chrome120 i chrome124. Rotacja 120↔124 co request.
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
# 400/401: jedna powtórka + inny dostawca; drugi raz → skip na ogon
HTTP_400_401_PAUSE_S = 8.0
HTTP_400_401_MAX_HITS = 2
# Pauza PRZED każdym HTTP (także retry) — jak InstagramScraper w proxy1.py
REQUEST_SLEEP_S = 2.0
# proxy1.py nie pauzuje między nickami — tylko sleep w _get
BETWEEN_USERS_SLEEP_S = 0.0
# Max sprawdzeń na run — `limit = 100` w proxy1.py
PROXY1_CHECK_LIMIT = 100
# Dłuższy backoff — IG częściej dusi krótkie retry
RETRY_BACKOFF_BASE_S = 4.0
RETRY_BACKOFF_MAX_S = 30.0
# Dłuższy timeout — residential PL potrafi iść >10 s
REQUEST_TIMEOUT_S = 30
DEBUG_SHOW_EXIT_IP = False
IPIFY_URL = "https://api.ipify.org?format=json"

# HTML profilu — API web_profile_info jest login-wallem
PROFILE_HTML_URL = "https://www.instagram.com/{username}/"
# Alias wsteczny — stare importy / testy
API_URL = PROFILE_HTML_URL

# HTML, nie JSON API — bez ręcznego UA
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
    "Referer": "https://www.instagram.com/",
}
PROXY1_HEADERS = HTML_HEADERS

# curl_cffi ma też 131/136 — tu zostajemy przy 120/124 jak wcześniej
IMPERSONATE_TARGETS = (
    "chrome120", "chrome124",
)
RETRYABLE_HTTP_STATUSES = {403, 408, 429, 500, 502, 503, 504}

_ARABIC_HINDI = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFE70-\uFEFF\u0900-\u097F\u0980-\u09FF]"
)
# session-xxxx / session_xxxx w haśle proxy (rayobyte, marsproxies, …)
_SESSION_IN_PASS = re.compile(r"(session[-_])([A-Za-z0-9]+)", re.I)
# Pola z HTML profilu (nie GraphQL API)
_NOT_FOUND_HTML = re.compile(
    r"Sorry, this page isn.?t available|Strona, której szukasz",
    re.I,
)
_FOLLOWER_JSON = re.compile(r'"follower_count"\s*:\s*(\d+)')
_PRIVATE_JSON = re.compile(r'"is_private"\s*:\s*(true|false)')
_BIO_JSON = re.compile(r'"biography"\s*:\s*"((?:\\.|[^"\\])*)"')
_MEDIA_JSON = re.compile(r'(?<![A-Za-z_])"media_count"\s*:\s*(\d+)')
_ALL_MEDIA_JSON = re.compile(r'"all_media_count"\s*:\s*(\d+)')
_OG_DESC = re.compile(
    r'property="og:description"\s+content="([^"]+)"',
    re.I,
)
_META_DESC = re.compile(
    r'name="description"\s+content="([^"]+)"',
    re.I,
)
_OG_COUNTS = re.compile(
    r"([\d.,]+\s*[KkMm]?)\s+Followers.*?([\d.,]+\s*[KkMm]?)\s+Posts",
    re.I | re.S,
)


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
    """Stary parser JSON API — fallback gdy body wygląda jak web_profile_info."""
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


def _parse_compact_count(token: str) -> int:
    """``8,581`` / ``687M`` / ``1.2K`` → int."""
    raw = (token or "").strip().replace("\xa0", "").replace(",", "")
    if not raw:
        return 0
    mult = 1.0
    if raw[-1] in "Kk":
        mult = 1000.0
        raw = raw[:-1]
    elif raw[-1] in "Mm":
        mult = 1_000_000.0
        raw = raw[:-1]
    try:
        return int(float(raw) * mult)
    except ValueError:
        return 0


def is_html_not_found(html: str) -> bool:
    """IG często zwraca 200 z komunikatem że profil nie istnieje."""
    return bool(_NOT_FOUND_HTML.search(html or ""))


def parse_profile_html(html: str) -> Optional[UserData]:
    """Wyciągnij followers / posty / private / bio z HTML profilu."""
    if not html:
        return None
    followers = None
    m = _FOLLOWER_JSON.search(html)
    if m:
        followers = int(m.group(1))
    posts = None
    m = _ALL_MEDIA_JSON.search(html)
    if m:
        posts = int(m.group(1))
    if posts is None:
        m = _MEDIA_JSON.search(html)
        if m:
            posts = int(m.group(1))
    og = _OG_DESC.search(html) or _META_DESC.search(html)
    if og:
        cm = _OG_COUNTS.search(og.group(1))
        if cm:
            if followers is None:
                followers = _parse_compact_count(cm.group(1))
            if posts is None:
                posts = _parse_compact_count(cm.group(2))
    if followers is None or posts is None:
        return None
    priv = False
    m = _PRIVATE_JSON.search(html)
    if m:
        priv = m.group(1) == "true"
    bio = ""
    m = _BIO_JSON.search(html)
    if m:
        bio = m.group(1).replace(r"\"", '"').replace(r"\n", "\n")
    return UserData(
        is_private=priv,
        post_count=posts,
        follower_count=followers,
        biography=bio,
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
    - rayobyte: ``pass-country-PL-session-<id>-duration-1`` (jak wcześniej)
    - inne z ``session-``/``session_`` w haśle: podmień ID
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
                 log: Callable[[str], None] | None = None,
                 request_sleep_s: float | None = None,
                 retry_backoff_base_s: float | None = None,
                 retry_backoff_max_s: float | None = None,
                 request_timeout_s: float | None = None,
                 log_exit_ip: bool | None = None):
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
        # 0 = bez pauzy (testy); None = domyślna stała
        self.request_sleep_s = (
            REQUEST_SLEEP_S if request_sleep_s is None
            else max(0.0, float(request_sleep_s)))
        self.retry_backoff_base_s = (
            RETRY_BACKOFF_BASE_S if retry_backoff_base_s is None
            else max(0.1, float(retry_backoff_base_s)))
        self.retry_backoff_max_s = (
            RETRY_BACKOFF_MAX_S if retry_backoff_max_s is None
            else max(self.retry_backoff_base_s,
                     float(retry_backoff_max_s)))
        self.request_timeout_s = (
            REQUEST_TIMEOUT_S if request_timeout_s is None
            else max(5.0, float(request_timeout_s)))
        self.log_exit_ip = (
            DEBUG_SHOW_EXIT_IP if log_exit_ip is None else bool(log_exit_ip))
        _log = log or print

        def _safe_log(msg: str) -> None:
            try:
                _log(msg)
            except UnicodeEncodeError:
                _log(str(msg).encode("ascii", "replace").decode("ascii"))

        self.log = _safe_log
        self.stats = ScrapeStats()
        # Start z losowego 120/124 — dalej rotacja co request
        self.impersonate = random.choice(IMPERSONATE_TARGETS)
        self.current_proxy = ""
        self.current_provider = ""
        # Pierwsza rotacja IP od razu
        self.rotate_for_request()
        self.log(
            f"[filter] curl_cffi impersonate={self.impersonate} "
            f"pool={list(IMPERSONATE_TARGETS)} "
            f"bases={len(self.proxy_pool)} providers={list(self.proxy_groups)} "
            f"mode={self.proxy_mode} transport=html "
            f"rotate_ip_every_request=1 "
            f"request_sleep={self.request_sleep_s:g}s "
            f"timeout={self.request_timeout_s:g}s "
            f"retry_backoff={self.retry_backoff_base_s:g}-{self.retry_backoff_max_s:g}s"
        )

    def _pick_base(self, avoid_provider: str | None = None) -> tuple[str, str]:
        """Wybierz bazowy URL i nazwę dostawcy; opcjonalnie omiń jednego."""
        providers = list(self.proxy_groups.keys())
        if avoid_provider and len(providers) > 1:
            # Retry: od razu inny dostawca, nie ten sam co padł.
            providers = [p for p in providers if p != avoid_provider] or providers
        name = random.choice(providers)
        return random.choice(self.proxy_groups[name]), name

    def rotate_for_request(self, avoid_provider: str | None = None) -> None:
        """Nowe IP; Chrome 120↔124 na każdy request."""
        base, provider = self._pick_base(avoid_provider)
        self.current_provider = provider
        self.current_proxy = mint_fresh_proxy_url(base)
        self._rotate_chrome()

    def _rotate_chrome(self) -> None:
        """Przełącz na drugi znany-działający Chrome (120 ↔ 124)."""
        others = [t for t in IMPERSONATE_TARGETS if t != self.impersonate]
        if others:
            self.impersonate = random.choice(others)

    # Alias — wywołania po błędzie też pełna rotacja
    def change_proxy(self) -> None:
        self.rotate_for_request()

    def _log_exit_ip(self) -> None:
        """Opcjonalny podgląd exit-IP przez aktualne proxy (ipify)."""
        if not self.log_exit_ip:
            return
        try:
            with creq.Session(impersonate=self.impersonate) as session:
                resp = session.get(
                    IPIFY_URL,
                    proxies={"http": self.current_proxy,
                             "https": self.current_proxy},
                    timeout=8,
                )
            ip = "?"
            try:
                ip = str((resp.json() or {}).get("ip") or "").strip() or "?"
            except Exception:
                ip = (getattr(resp, "text", None) or "?").strip()[:40]
            self.log(f"IP wyjscia: {ip}")
        except Exception as e:
            self.log(f"IP wyjscia: ? ({type(e).__name__})")

    def _get(self, url: str):
        """Jednorazowa Session curl_cffi (nowe połączenie = nowe IP)."""
        if self.request_sleep_s > 0:
            time.sleep(self.request_sleep_s)
        # Session per request — jak nowy httpx.Client w proxy1
        with creq.Session(impersonate=self.impersonate) as session:
            return session.get(
                url,
                headers=dict(HTML_HEADERS),
                proxies={"http": self.current_proxy,
                         "https": self.current_proxy},
                timeout=self.request_timeout_s,
            )

    def _retry_backoff_s(self, attempt: int, reason: str) -> float:
        """Policz łagodny backoff przed kolejną próbą."""
        mult = 1.0
        if reason in {"http_400", "http_401", "http_403"}:
            # Bardziej ostrożnie przy sygnałach anty-bot/rate-limit.
            mult = 1.6
        elif reason in {"user_null", "bad_json"}:
            # Odpowiedź częściowo poprawna — krótki oddech i ponów.
            mult = 0.8
        raw = self.retry_backoff_base_s * (2 ** max(0, attempt - 1)) * mult
        capped = min(self.retry_backoff_max_s, raw)
        jitter = random.uniform(0.0, min(0.35, capped * 0.1))
        return round(capped + jitter, 3)

    def _sleep_after_retry(self, username: str, attempt: int, reason: str) -> None:
        if attempt >= MAX_RETRIES_PER_USER:
            return
        delay = self._retry_backoff_s(attempt, reason)
        self.log(
            f"[filter][retry] {username} proba={attempt}/{MAX_RETRIES_PER_USER} "
            f"powod={reason} backoff={delay:g}s zmieniam dostawce"
        )
        time.sleep(delay)

    def scrape_user(self, username: str) -> str:
        """'ok' | 'remove' | 'skip' (retry pozniej)."""
        url = PROFILE_HTML_URL.format(username=username)
        avoid_provider: str | None = None
        # 400/401: max 2 strzały; drugi → skip na koniec kolejki
        hits_400_401 = 0
        for attempt in range(1, MAX_RETRIES_PER_USER + 1):
            # Rotacja IP przed każdym requestem
            self.rotate_for_request(avoid_provider=avoid_provider)
            avoid_provider = None
            self.stats.total_requests += 1
            host = urlsplit(self.current_proxy).hostname or "?"
            self.log(
                f"Uzywam proxy: {host} [{self.current_provider}] "
                f"({self.impersonate})")
            self._log_exit_ip()
            try:
                response = self._get(url)
            except Exception as e:
                self.log(f"SKIP powod: blad polaczenia ({type(e).__name__})")
                avoid_provider = self.current_provider
                self._sleep_after_retry(
                    username, attempt, f"conn_{type(e).__name__.lower()}")
                continue

            status = response.status_code
            if status == 200:
                body = ""
                try:
                    body = response.text or ""
                except Exception:
                    body = ""
                if is_html_not_found(body):
                    self.log(f"{username} not found.")
                    self.log("----------------------------")
                    self.stats.removed += 1
                    return "remove"
                user_data = parse_profile_html(body)
                if user_data is None:
                    # Fallback: gdyby jednak wrócił JSON API
                    try:
                        user_data = parse_user_data(response.json())
                    except Exception:
                        user_data = None
                if user_data is None:
                    self.log("SKIP powod: html profilu bez danych")
                    avoid_provider = self.current_provider
                    self._sleep_after_retry(username, attempt, "user_null")
                    continue
                self.stats.checked += 1
                result = evaluate_user(username, user_data, self.criteria)
                if result == "ok":
                    self.log(f"Save {username} (html)")
                    self.stats.saved += 1
                else:
                    self.log(f"Remove {username} (html)")
                    self.stats.removed += 1
                self.log("----------------------------")
                return result

            if status == 404:
                self.log(f"{username} not found.")
                self.log("----------------------------")
                self.stats.removed += 1
                return "remove"

            if status in (400, 401):
                hits_400_401 += 1
                self.log(f"SKIP powod HTTP: {status}")
                avoid_provider = self.current_provider
                if hits_400_401 >= HTTP_400_401_MAX_HITS:
                    self.log(
                        f"Skip {username} — drugi {status}, na koniec kolejki")
                    self.stats.skip += 1
                    return "skip"
                pause = max(HTTP_400_401_PAUSE_S, self._retry_backoff_s(
                    attempt, f"http_{status}"))
                self.log(
                    f"[filter][retry] {username} 400/401 — {pause:g}s, "
                    "zmieniam dostawce i Chrome")
                if pause > 0:
                    time.sleep(pause)
                continue

            self.log(f"SKIP powod HTTP: {status}")
            if status in RETRYABLE_HTTP_STATUSES:
                avoid_provider = self.current_provider
                self._sleep_after_retry(username, attempt, f"http_{status}")

        self.stats.skip += 1
        return "skip"
