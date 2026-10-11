"""Biblioteka bazowych APK IG w katalogu appclonera (instagram.apk + apks/)."""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from v2.cloner.identity import ORIG_PACKAGE
from v2.cloner.job import CloneJobError, appcloner_root, default_adb

# Komentarz: merge ARSC siedzi w appcloner/ (ten sam katalog co clone_instagram)
_APPCLONER_DIR = Path(__file__).resolve().parents[1] / "appcloner"
if _APPCLONER_DIR.is_dir() and str(_APPCLONER_DIR) not in sys.path:
    sys.path.insert(0, str(_APPCLONER_DIR))
from arsc_merge import (  # noqa: E402
    apk_has_resources_arsc,
    merge_resource_split_apks,
    parse_pm_paths,
)

# Komentarz: Aptoide v7 — lista wersji + getMeta daje URL pliku
APTOIDE_VERSIONS = "https://ws75.aptoide.com/api/7/listAppVersions/"
APTOIDE_META = "https://ws75.aptoide.com/api/7/app/getMeta"
_UA = "bot_v2-cloner/1 (Instagram APK source)"
_VER_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
# Komentarz: Aptoide listAppVersions — offset przy szukaniu exact ze stocka
_APTOIDE_PAGE_MAX = 40
_APTOIDE_SEEK_PAGES = 5
_MATCH_EXACT = "exact"
_MATCH_CLOSE = "close"
_MATCH_OTHER = "other"
_LABEL_EXACT = "jak na telefonie"
_LABEL_CLOSE = "blisko telefonu"

AdbFn = Callable[..., str]
LogFn = Callable[[str], None]

_fetch_lock = threading.Lock()
_fetch_job: dict = {
    "running": False,
    "state": "idle",
    "error": None,
    "version": None,
    "path": None,
    "bytes_done": 0,
    "bytes_total": 0,
    "log": "",
}


class ApkStoreError(CloneJobError):
    """Błąd pobierania / aktywacji bazowego APK."""


def apks_dir(root: Path | None = None, *, create: bool = True) -> Path:
    """Katalog `apks/` obok instagram.apk — tu lądują pobrane wersje."""
    r = root or appcloner_root(create=create)
    d = r / "apks"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def active_apk(root: Path | None = None, *, create: bool = False) -> Path:
    """Aktualne źródło klona: `<appcloner>/instagram.apk`."""
    r = root or appcloner_root(create=create)
    return r / "instagram.apk"


def _safe_ver(ver: str) -> str:
    s = _VER_SAFE.sub("_", (ver or "unknown").strip())[:64]
    return s or "unknown"


def _norm_ver(ver: str) -> str:
    """Porównanie vername bez spacji / wielkości liter."""
    return re.sub(r"\s+", "", (ver or "").strip().lower())


def _as_int(val) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    m = re.match(r"-?\d+", str(val).strip())
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def parse_vername(ver: str) -> tuple[int, ...]:
    """`444.0.0.46.85` → krotka liczb do odległości / major.minor."""
    parts: list[int] = []
    for chunk in re.split(r"\D+", (ver or "").strip()):
        if not chunk:
            continue
        try:
            parts.append(int(chunk))
        except ValueError:
            continue
    return tuple(parts)


def _major_minor(parts: tuple[int, ...]) -> tuple[int, int] | None:
    if not parts:
        return None
    minor = parts[1] if len(parts) > 1 else 0
    return (parts[0], minor)


def vername_distance(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    """Ważona |Δ| krotek — wcześniejsze człony (major) ważą mocniej."""
    n = max(len(a), len(b), 5)
    aa = a + (0,) * (n - len(a))
    bb = b + (0,) * (n - len(b))
    dist = 0
    for i, (x, y) in enumerate(zip(aa, bb)):
        dist += abs(x - y) * (10 ** (2 * (n - 1 - i)))
    return dist


def parse_dumpsys_version(dumpsys: str) -> dict:
    """Pierwszy `versionName=` / `versionCode=` z `dumpsys package`."""
    version = ""
    version_code = None
    for line in (dumpsys or "").splitlines():
        if version_code is None and "versionCode=" in line:
            raw = line.split("versionCode=", 1)[-1].strip().split()[0]
            version_code = _as_int(raw)
        if not version and "versionName=" in line:
            version = line.split("versionName=", 1)[-1].strip().split()[0]
        if version and version_code is not None:
            break
    return {"version": version, "version_code": version_code}


def _is_exact_row(row: dict, stock_version: str, stock_code: int | None) -> bool:
    ver = str(row.get("version") or "")
    if stock_version and _norm_ver(ver) == _norm_ver(stock_version):
        return True
    code = _as_int(row.get("version_code"))
    if stock_code is not None and code is not None and code == stock_code:
        return True
    return False


def rank_remote_versions(
    rows: list[dict],
    *,
    stock_version: str | None = None,
    stock_version_code: int | None = None,
    max_close: int = 16,
    max_other: int = 24,
) -> list[dict]:
    """Oznacz i posortuj wiersze Aptoide względem stock IG na telefonie.

    Reguła: exact (ten sam vername albo versionCode) → close (ten sam
    major.minor, najbliższy kod/vername) → reszta. Bez stocka zwraca kopie
    w oryginalnej kolejności, bez znaczników dopasowania.
    """
    stock_ver = (stock_version or "").strip()
    stock_code = _as_int(stock_version_code)
    if not stock_ver and stock_code is None:
        return [dict(r) for r in rows]
    stock_parts = parse_vername(stock_ver)
    stock_mm = _major_minor(stock_parts)
    annotated: list[dict] = []
    for i, row in enumerate(rows):
        rec = dict(row)
        parts = parse_vername(str(rec.get("version") or ""))
        row_code = _as_int(rec.get("version_code"))
        if _is_exact_row(rec, stock_ver, stock_code):
            # Komentarz: identyczny vername albo ten sam versionCode co dumpsys
            rec["match"] = _MATCH_EXACT
            rec["match_label"] = _LABEL_EXACT
            rec["distance"] = 0
        elif stock_mm is not None and _major_minor(parts) == stock_mm:
            rec["match"] = _MATCH_CLOSE
            rec["match_label"] = _LABEL_CLOSE
            if stock_code is not None and row_code is not None:
                rec["distance"] = abs(row_code - stock_code)
            else:
                rec["distance"] = vername_distance(parts, stock_parts)
        else:
            rec["match"] = _MATCH_OTHER
            rec["match_label"] = ""
            rec["distance"] = vername_distance(parts, stock_parts)
        rec["_ord"] = i
        rec["_vdist"] = vername_distance(parts, stock_parts)
        annotated.append(rec)
    annotated.sort(key=lambda r: (
        0 if r["match"] == _MATCH_EXACT else (1 if r["match"] == _MATCH_CLOSE else 2),
        int(r.get("distance") or 0),
        int(r.get("_vdist") or 0),
        r["_ord"],
    ))
    exact = [r for r in annotated if r["match"] == _MATCH_EXACT]
    close = [r for r in annotated if r["match"] == _MATCH_CLOSE]
    other = [r for r in annotated if r["match"] == _MATCH_OTHER]
    if max_close is not None:
        close = close[: max(0, int(max_close))]
    if max_other is not None:
        other = other[: max(0, int(max_other))]
    out = exact + close + other
    for rec in out:
        rec.pop("_ord", None)
        rec.pop("_vdist", None)
    return out


def apk_filename(version: str) -> str:
    """Nazwa pliku w apks/: instagram-<ver>.apk."""
    return f"instagram-{_safe_ver(version)}.apk"


def list_local(root: Path | None = None) -> list[dict]:
    """Lokalne APK + który jest aktywny (ten sam inode / kopia instagram.apk)."""
    r = root or appcloner_root(create=True)
    d = apks_dir(r, create=True)
    active = active_apk(r)
    active_size = active.stat().st_size if active.is_file() else None
    rows: list[dict] = []
    seen: set[str] = set()
    for p in sorted(d.glob("*.apk")):
        ver = p.stem.replace("instagram-", "", 1) if p.stem.startswith("instagram-") else p.stem
        rec = {
            "version": ver,
            "filename": p.name,
            "path": str(p),
            "size": p.stat().st_size,
            "local": True,
            "active": False,
            "source": "local",
        }
        if active_size is not None and rec["size"] == active_size:
            rec["active"] = True
        rows.append(rec)
        seen.add(ver)
    if active.is_file() and not any(x.get("active") for x in rows):
        rows.insert(0, {
            "version": "(aktywny)",
            "filename": active.name,
            "path": str(active),
            "size": active_size,
            "local": True,
            "active": True,
            "source": "local",
        })
    return rows


def resolve_stored_apk(root: Path, *, filename: str = "", path: str = "") -> Path:
    """APK z `apks/` albo `instagram.apk` — bez path traversal poza appcloner."""
    r = Path(root)
    name = (filename or "").strip()
    raw = (path or "").strip()
    if name:
        base = Path(name).name
        p = active_apk(r) if base == "instagram.apk" else apks_dir(r, create=True) / base
        if not p.is_file():
            raise ApkStoreError(f"brak APK: {base}")
        return p
    if raw:
        p = Path(raw).expanduser().resolve()
        try:
            p.relative_to(r.resolve())
        except ValueError as e:
            raise ApkStoreError("ścieżka APK poza appcloner") from e
        if p.suffix.lower() != ".apk" or not p.is_file():
            raise ApkStoreError(f"brak APK: {p}")
        return p
    raise ApkStoreError("podaj filename albo path")


def activate(root: Path, apk_path: Path) -> dict:
    """Skopiuj wybrany APK na instagram.apk (źródło appclonera)."""
    src = Path(apk_path)
    if not src.is_file():
        raise ApkStoreError(f"brak APK: {src}")
    dst = active_apk(root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"ok": True, "active": str(dst), "source": str(src), "size": dst.stat().st_size}


def _http_json(url: str, *, data: dict | None = None, timeout: float = 25) -> dict:
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": _UA, "Content-Type": "application/json", "Accept": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise ApkStoreError(f"Aptoide: {e}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ApkStoreError("Aptoide: zły JSON") from e


def _aptoide_version_rows(payload: dict) -> list[dict]:
    """Jedna strona listAppVersions → wiersze panelu (bez rankingu)."""
    out = []
    for item in payload.get("list") or []:
        f = item.get("file") or {}
        ver = str(f.get("vername") or "").strip()
        if not ver:
            continue
        out.append({
            "app_id": item.get("id"),
            "version": ver,
            "version_code": f.get("vercode"),
            "size": item.get("size") or f.get("filesize"),
            "md5": f.get("md5sum"),
            "local": False,
            "active": False,
            "source": "aptoide",
            "store": ((item.get("store") or {}).get("name") or ""),
        })
    return out


def list_remote(
    limit: int = 24,
    *,
    want_version: str | None = None,
    want_version_code: int | None = None,
) -> list[dict]:
    """Wersje IG z Aptoide (id + vername). `want_*` dociąga strony aż do exact."""
    page_limit = max(1, min(int(limit), _APTOIDE_PAGE_MAX))
    want = (want_version or "").strip()
    want_code = _as_int(want_version_code)
    seeking = bool(want or want_code is not None)
    max_pages = _APTOIDE_SEEK_PAGES if seeking else 1
    if seeking:
        page_limit = _APTOIDE_PAGE_MAX
    out: list[dict] = []
    seen: set = set()
    offset = 0
    for _ in range(max_pages):
        payload = _http_json(
            APTOIDE_VERSIONS,
            data={
                "package_name": ORIG_PACKAGE,
                "limit": page_limit,
                "offset": offset,
            },
        )
        chunk = _aptoide_version_rows(payload)
        added = 0
        for row in chunk:
            key = row.get("app_id")
            if key is None:
                key = (row.get("version"), row.get("version_code"), row.get("store"))
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
            added += 1
        if seeking and any(_is_exact_row(r, want, want_code) for r in out):
            break
        if not chunk or added == 0 or len(chunk) < page_limit:
            break
        offset += page_limit
    return out


def fetch_job_snapshot() -> dict:
    """Stan pobierania APK (panel poll)."""
    with _fetch_lock:
        return dict(_fetch_job)


def _log_fetch(msg: str) -> None:
    line = f"[apk] {msg}"
    with _fetch_lock:
        prev = _fetch_job.get("log") or ""
        _fetch_job["log"] = (prev + line + "\n")[-80_000:]


def download_version(root: Path, *, app_id: int, log: LogFn | None = None) -> dict:
    """getMeta → plik → apks/instagram-<ver>.apk → aktywuj jako instagram.apk."""
    meta = _http_json(f"{APTOIDE_META}?app_id={int(app_id)}")
    data = meta.get("data") or {}
    f = data.get("file") or {}
    url = (f.get("path") or f.get("path_alt") or "").strip()
    ver = str(f.get("vername") or "").strip() or str(app_id)
    if not url:
        raise ApkStoreError(f"brak URL dla app_id={app_id}")
    rank = str(((f.get("malware") or {}).get("rank") or "")).upper()
    if rank in ("CRITICAL", "WARN") and "VIRUS" in json.dumps(f.get("malware") or {}):
        raise ApkStoreError(f"Aptoide odrzucił APK (malware {rank})")
    dest = apks_dir(root) / apk_filename(ver)
    tmp = dest.with_suffix(".apk.part")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if log:
        log(f"pobieram {ver} → {dest.name}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            with _fetch_lock:
                _fetch_job["bytes_total"] = total
                _fetch_job["bytes_done"] = 0
            done = 0
            with tmp.open("wb") as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    with _fetch_lock:
                        _fetch_job["bytes_done"] = done
    except urllib.error.URLError as e:
        tmp.unlink(missing_ok=True)
        raise ApkStoreError(f"pobieranie: {e}") from e
    if tmp.stat().st_size < 1_000_000:
        tmp.unlink(missing_ok=True)
        raise ApkStoreError("pobrany plik za mały — to nie wygląda na APK")
    head = tmp.read_bytes()[:4]
    if head[:2] != b"PK":
        tmp.unlink(missing_ok=True)
        raise ApkStoreError("pobrany plik nie jest ZIP/APK")
    tmp.replace(dest)
    activated = activate(root, dest)
    if log:
        log(f"zapisano {dest.name} i ustawiono jako instagram.apk")
    return {
        "ok": True,
        "version": ver,
        "path": str(dest),
        "active": activated["active"],
        "size": dest.stat().st_size,
        "app_id": int(app_id),
    }


def start_fetch_thread(app_id: int, *, root: Path | None = None) -> dict:
    """202 z panelu — pobieranie w tle (jeden job naraz)."""
    with _fetch_lock:
        if _fetch_job.get("running"):
            raise ApkStoreError("pobieranie APK już trwa")
        _fetch_job.update({
            "running": True,
            "state": "running",
            "error": None,
            "version": None,
            "path": None,
            "bytes_done": 0,
            "bytes_total": 0,
            "log": "",
        })

    def _worker():
        try:
            r = root or appcloner_root(create=True)
            result = download_version(r, app_id=int(app_id), log=_log_fetch)
            with _fetch_lock:
                _fetch_job.update({
                    "running": False,
                    "state": "ok",
                    "version": result["version"],
                    "path": result["path"],
                    "error": None,
                })
        except Exception as e:
            _log_fetch(f"BŁĄD: {e}")
            with _fetch_lock:
                _fetch_job.update({
                    "running": False,
                    "state": "error",
                    "error": str(e),
                })

    threading.Thread(target=_worker, name=f"apk-fetch-{app_id}", daemon=True).start()
    return fetch_job_snapshot()


def read_installed_version(
    serial: str,
    *,
    adb: AdbFn = default_adb,
    package: str = ORIG_PACKAGE,
) -> dict:
    """versionName/versionCode stock IG z telefonu — bez uninstall / pull."""
    out = adb(serial, ["shell", "dumpsys", "package", package], timeout=25)
    rec = parse_dumpsys_version(out or "")
    rec["package"] = package
    rec["serial"] = serial
    if not rec.get("version") and rec.get("version_code") is None:
        raise ApkStoreError(f"brak versionName dla {package}")
    return rec


def pull_from_device(
    root: Path,
    serial: str,
    *,
    adb: AdbFn = default_adb,
    package: str = ORIG_PACKAGE,
) -> dict:
    """Zciągnij stock IG: **wszystkie** `pm path` (base + splity) i zmerguj zasoby.

    Sam `base.apk` nie ma `R.drawable.instagram` (0x7f082060) — to `split_config.xxhdpi`.
    Feature-splity bez `resources.arsc` (helium…) nie wchodzą do merge.
    """
    ver_out = adb(serial, ["shell", "dumpsys", "package", package])
    ver = parse_dumpsys_version(ver_out or "").get("version") or ""
    path_out = adb(serial, ["shell", "pm", "path", package])
    remotes = parse_pm_paths(path_out or "")
    if not remotes:
        raise ApkStoreError(f"pm path puste dla {package}")
    split_dir = apks_dir(root) / f"{Path(apk_filename(ver or 'device')).stem}-splits"
    if split_dir.exists():
        shutil.rmtree(split_dir)
    split_dir.mkdir(parents=True, exist_ok=True)
    pulled: list[Path] = []
    for remote in remotes:
        dest_part = split_dir / Path(remote).name
        adb(serial, ["pull", remote, str(dest_part)])
        if dest_part.is_file() and dest_part.stat().st_size > 0:
            pulled.append(dest_part)
    if not pulled:
        raise ApkStoreError("adb pull nie zostawił APK")
    dest = apks_dir(root) / apk_filename(ver or "device")
    dest.parent.mkdir(parents=True, exist_ok=True)
    resource_apks = [p for p in pulled if apk_has_resources_arsc(p)]
    merged = False
    if len(resource_apks) >= 2:
        # Komentarz: base + density/locale → jedna tabela z splash drawable
        merge_resource_split_apks(resource_apks, dest)
        merged = True
    else:
        prefer = next((p for p in pulled if p.name == "base.apk"), pulled[0])
        shutil.copy2(prefer, dest)
    if not dest.is_file() or dest.stat().st_size < 1000:
        raise ApkStoreError("adb pull nie zostawił APK")
    activated = activate(root, dest)
    return {
        "ok": True,
        "version": ver or "device",
        "path": str(dest),
        "active": activated["active"],
        "size": dest.stat().st_size,
        "source": "device",
        "serial": serial,
        "splits": [str(p) for p in pulled],
        "split_dir": str(split_dir),
        "merged": merged,
    }
