"""Job generate → build → install klona IG (panel + CLI)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from v2.cloner.identity import (
    ORIG_PACKAGE,
    IdentityError,
    clone_occupancy,
    generate_clone_identity,
    is_clone_package,
    is_ig_package,
    list_spoof_profiles,
    next_clone_packages,
    normalize_safeguards,
)
from v2.core.device import ig_ps_has_package
from v2.core.device_lock import DeviceBusy, DeviceLock, peek_device_lock

# Komentarz: callbacki testów — bez telefonu / bez apktool
AdbFn = Callable[..., str]
LogFn = Callable[[str], None]


class CloneJobError(RuntimeError):
    """Błąd tworzenia klona (brak APK, adb, lock, appcloner)."""


class CloneBusy(CloneJobError):
    """Inny job klona albo sesja bota trzyma telefon."""


_job_lock = threading.Lock()
_job: dict = {
    "running": False,
    "device_alias": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "state": "idle",  # idle | running | ok | error
    "package": None,
    "identity": None,
    "log_path": None,
    "apk": None,
    "verified": None,
}
_job_proc: subprocess.Popen | None = None
_job_cancel = threading.Event()


def _appcloner_candidates() -> tuple[Path, Path]:
    """Sibling `../appcloner` (obok bot_v2) oraz `bot_v2/appcloner`."""
    here = Path(__file__).resolve()
    return here.parents[2] / "appcloner", here.parents[1] / "appcloner"


def pick_appcloner_root(
    *,
    env: str,
    sibling: Path,
    local: Path,
    create: bool = False,
) -> Path:
    """Wybierz katalog appclonera.

    Kolejność: `APPCLONER_ROOT` → katalog z `clone_instagram.py` (sibling, potem
    lokalny) → przy `create=True` zawsze `bot_v2/appcloner/` (APK) → istniejący
    katalog. Pusty sibling nie może zasłonić skryptu w `bot_v2/appcloner`.
    """
    env_s = (env or "").strip()
    if env_s:
        p = Path(env_s).expanduser().resolve()
        if not p.is_dir():
            raise CloneJobError(f"APPCLONER_ROOT nie istnieje: {p}")
        return p
    # Komentarz: najpierw ten root, w którym leży clone_instagram.py
    for cand in (sibling, local):
        if (cand / "clone_instagram.py").is_file():
            return cand
    if create:
        local.mkdir(parents=True, exist_ok=True)
        return local
    if sibling.is_dir():
        return sibling
    if local.is_dir():
        return local
    raise CloneJobError(
        "nie znaleziono appcloner — ustaw APPCLONER_ROOT albo trzymaj repo "
        "obok bot_v2 (../appcloner)"
    )


def appcloner_root(*, create: bool = False) -> Path:
    """Katalog repo appcloner: APPCLONER_ROOT, sibling `../appcloner`, albo v2/appcloner.

    `create=True` — zrób `bot_v2/appcloner/` pod pobrane APK, nawet bez clone_instagram.py.
    """
    sibling, local = _appcloner_candidates()
    return pick_appcloner_root(
        env=(os.environ.get("APPCLONER_ROOT") or ""),
        sibling=sibling,
        local=local,
        create=create,
    )


def clone_script(root: Path | None = None) -> Path:
    """Ścieżka do clone_instagram.py."""
    r = root or appcloner_root()
    p = r / "clone_instagram.py"
    if not p.is_file():
        raise CloneJobError(f"brak clone_instagram.py w {r}")
    return p


def require_source_apk(root: Path | None = None, apk: str | None = None) -> Path:
    """Źródłowy APK z dysku: wybrany plik w `apks/` albo `instagram.apk`.

    Panel podaje nazwę / względną ścieżkę z `appcloner/apks`. Bez wyboru
    szukamy `instagram.apk`, potem pierwszego `apks/*.apk`.
    """
    r = root or appcloner_root()
    raw = (apk or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            name = p.name
            if name == "instagram.apk":
                p = r / "instagram.apk"
            elif (r / raw).is_file():
                p = r / raw
            else:
                p = r / "apks" / name
        p = p.expanduser().resolve()
        try:
            p.relative_to(r.resolve())
        except ValueError as e:
            raise CloneJobError("ścieżka APK poza appcloner") from e
        if p.suffix.lower() != ".apk" or not p.is_file():
            raise CloneJobError(f"brak APK: {p.name}")
        return p
    instagram = r / "instagram.apk"
    if instagram.is_file():
        return instagram
    apks = r / "apks"
    if apks.is_dir():
        found = sorted(apks.glob("*.apk"))
        if found:
            return found[0]
    raise CloneJobError(
        f"brak {instagram} — wybierz APK z {apks} albo włóż instagram.apk"
    )


def _is_java_binary(path: Path) -> bool:
    """True gdy `path` to wykonywalny plik (symlink do java też przechodzi)."""
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _is_mise_shim(path: Path) -> bool:
    """Shim `java` → mise nie jest JDK — pada bez `mise use java`."""
    try:
        real = path.resolve()
    except OSError:
        return False
    name = real.name.lower()
    return name == "mise" or name.startswith("mise-")


def _is_usable_java(path: Path) -> bool:
    """Wykonywalny java, ale nie stub menedżera wersji."""
    return _is_java_binary(path) and not _is_mise_shim(path)


def _java_from_home(home: str) -> Path | None:
    """JAVA_HOME → bin/java (albo jre/bin/java w starszych layoutach)."""
    root = Path(home).expanduser()
    for rel in ("bin/java", "jre/bin/java"):
        cand = root / rel
        if _is_usable_java(cand):
            return cand
    return None


def _java_search_homes() -> list[Path]:
    """Katalogi domowe: HOME procesu i właściciel repo (panel bywa bez mise w PATH)."""
    seen: set[str] = set()
    out: list[Path] = []
    repo_home = Path(__file__).resolve().parents[2]
    for raw in (os.environ.get("HOME"), str(Path.home()), str(repo_home)):
        if not raw:
            continue
        p = Path(raw).expanduser()
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _well_known_java_bins() -> list[Path]:
    """Typowe lokalizacje JVM poza PATH (Arch, sdkman, mise)."""
    out: list[Path] = [
        Path("/usr/bin/java"),
        Path("/usr/lib/jvm/default/bin/java"),
        Path("/usr/lib/jvm/default-java/bin/java"),
    ]
    jvm = Path("/usr/lib/jvm")
    if jvm.is_dir():
        out.extend(sorted(jvm.glob("*/bin/java")))
    for home in _java_search_homes():
        sdk_root = home / ".sdkman" / "candidates" / "java"
        if sdk_root.is_dir():
            out.append(sdk_root / "current" / "bin" / "java")
            out.extend(sorted(sdk_root.glob("*/bin/java")))
        mise_java = home / ".local" / "share" / "mise" / "installs" / "java"
        if mise_java.is_dir():
            out.extend(sorted(mise_java.glob("*/bin/java")))
    return out


def resolve_java() -> str | None:
    """Ścieżka do `java`: PATH, potem JAVA_HOME, potem katalogi JVM."""
    found = shutil.which("java")
    if found:
        p = Path(found)
        if _is_usable_java(p):
            return str(p)
    home = (os.environ.get("JAVA_HOME") or "").strip()
    if home:
        from_home = _java_from_home(home)
        if from_home is not None:
            return str(from_home.resolve())
    for cand in _well_known_java_bins():
        if _is_usable_java(cand):
            return str(cand.resolve())
    return None


def require_jdk() -> str:
    """JDK do apktool / uber-apk-signer (`java -jar`) — nie tylko PATH panelu."""
    found = resolve_java()
    if not found:
        raise CloneJobError("brak JDK — `java` nie jest w PATH")
    return found


def java_subprocess_env(java_bin: str | None = None) -> dict[str, str]:
    """Env dla clone_instagram.py: `java` na PATH i JAVA_HOME, nawet gdy panel ich nie ma."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    java = Path(java_bin or require_jdk()).resolve()
    bin_dir = str(java.parent)
    path = env.get("PATH") or ""
    parts = [p for p in path.split(os.pathsep) if p]
    if bin_dir not in parts:
        env["PATH"] = bin_dir + (os.pathsep + path if path else "")
    # Komentarz: /usr/bin/java po resolve() to zwykle …/jvm/<ver>/bin/java
    if java.parent.name == "bin":
        home = java.parent.parent
        if str(home) not in ("/", "/usr", "/usr/local"):
            env["JAVA_HOME"] = str(home)
    return env


def device_is_busy(data_root: Path, serial: str) -> bool:
    """True gdy inna sesja trzyma DeviceLock (bot na tym adb_id)."""
    lock = DeviceLock(data_root, serial)
    try:
        lock.acquire(owner="clone-preflight", session_id="probe")
        lock.release()
        return False
    except DeviceBusy:
        return True


def src_cache_paths(appcloner: Path, apk: Path) -> tuple[Path, Path]:
    """(instagram_src w cache, cache.json) — ten sam układ co clone_instagram."""
    base = Path(appcloner) / "clone" / "_cache" / Path(apk).stem
    return base / "instagram_src", base / "cache.json"


def src_cache_is_hit(appcloner: Path, apk: Path) -> bool:
    """True gdy cache.json zgadza się z APK (stem/size/mtime) i jest manifest."""
    apk = Path(apk)
    if not apk.is_file():
        return False
    cached, marker = src_cache_paths(appcloner, apk)
    if not marker.is_file() or not (cached / "AndroidManifest.xml").is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        st = apk.stat()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return (
        str(data.get("apk") or "") == apk.stem
        and int(data.get("size") or -1) == int(st.st_size)
        and int(data.get("mtime") or -1) == int(st.st_mtime)
    )


def describe_src_cache(appcloner: Path, apk: Path | None) -> str:
    """Jedna linia `cache HIT` / `cache MISS` — panel widzi czemu job jest szybki."""
    if apk is None or not Path(apk).is_file():
        return "cache MISS (brak APK)"
    apk = Path(apk)
    if src_cache_is_hit(appcloner, apk):
        return f"cache HIT ({apk.stem})"
    return f"cache MISS ({apk.stem})"


def preflight_create(
    device_alias: str,
    *,
    config_root: Path,
    data_root: Path,
    source_apk: str | None = None,
    one_clone_job: bool = True,
) -> dict:
    """Walidacja przed 202: device, adb_id, lock, APK z dysku, JDK.

    Rzucane CloneBusy / CloneJobError → HTTP 409 w panelu.
    `one_clone_job` (domyślnie ON): 409 gdy sesja bota trzyma serial.
    """
    _path, dev = load_device(config_root, device_alias)
    serial = (dev.get("adb_id") or "").strip()
    if not serial or serial == "REPLACE":
        raise CloneJobError(f"device:{device_alias}: brak adb_id")
    # Komentarz: DeviceLock + 409 — jeden job klona / bot na serialu
    if one_clone_job:
        holder = peek_device_lock(data_root, serial)
        if holder or device_is_busy(data_root, serial):
            who = ""
            if isinstance(holder, dict) and holder.get("owner"):
                who = f" ({holder.get('owner')})"
            raise CloneBusy(
                f"urządzenie {serial} zajęte przez sesję bota{who} — "
                "Stop przed klonem (one_clone_job)"
            )
    elif device_is_busy(data_root, serial):
        # Komentarz: lock i tak jest w create_clone — 409 gdy telefon zajęty
        raise CloneBusy(
            f"urządzenie {serial} zajęte przez sesję bota — Stop przed klonem")
    root = appcloner_root()
    require_source_apk(root, apk=source_apk)
    require_jdk()
    return {"serial": serial, "appcloner": str(root)}


def job_snapshot() -> dict:
    """Status bez Popen — do GET /api/clones/status."""
    with _job_lock:
        st = {k: v for k, v in _job.items()}
    path = st.get("log_path")
    st["log_tail"] = _tail(path, 24)
    st["log"] = _read(path)
    st["running"] = bool(st.get("running"))
    return st


_UNRESOLVED_RES_PREFIX = "W: Unresolved resource reference:"


def summarize_apktool_log(text: str) -> str:
    """Zwiń tysiące `W: Unresolved resource` — szum split/AAB, nie błąd klona.

    Ostrzeżenia apktool przy decode IG z AAB/split nie przerywają `apktool b`.
    Panel ma pokazać instalację (sukces / MISSING_SPLIT), nie ścianę W:.
    """
    if not text:
        return text
    n = 0
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith(_UNRESOLVED_RES_PREFIX):
            n += 1
            continue
        out.append(line)
    if n:
        out.append(
            f"[clone-job] apktool: {n} ostrzeżeń Unresolved resource "
            "(szum split/AAB, oczekiwane — nie są błędem buildu)"
        )
    return "\n".join(out)


def compact_clone_log(log_file) -> None:
    """Po apktool przepisz log na dysku bez ściany W: (fh zostaje otwarty)."""
    name = getattr(log_file, "name", None)
    if not name or str(name).startswith("<"):
        return
    try:
        log_file.flush()
        p = Path(name)
        raw = p.read_text(encoding="utf-8", errors="replace")
        compact = summarize_apktool_log(raw)
        if compact == raw.rstrip("\n"):
            return
        p.write_text(compact + "\n", encoding="utf-8")
        log_file.seek(0, 2)
    except OSError:
        return


def _tail(path: Optional[str], n: int = 24) -> list[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Komentarz: filtr przed ogonem — inaczej 24 ostatnie linie to same W:
    text = summarize_apktool_log(text)
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return lines[-n:] if lines else []


def _read(path: Optional[str], max_chars: int = 400_000) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = summarize_apktool_log(text)
    return text[-max_chars:] if len(text) > max_chars else text


def _log(log: LogFn | None, msg: str) -> None:
    line = f"[clone-job] {msg}"
    if log:
        log(line)
    else:
        print(line, flush=True)


def default_adb(serial: str, args: list[str], timeout: float = 180) -> str:
    """Surowy adb -s SERIAL … (install / pm list)."""
    r = subprocess.run(
        ["adb", "-s", serial, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        raise CloneJobError(f"adb {' '.join(args)} exit={r.returncode}: {out}")
    return out


# Komentarz: preview w panelu nie może czekać 180 s na `pm list` — tożsamość
# jest lokalnym RNG + JSON urządzenia, ADB tylko odrzuca zajęte APPID.
PREVIEW_ADB_TIMEOUT_S = 3.0


def list_installed_packages(
    serial: str,
    adb: AdbFn = default_adb,
    timeout: float | None = None,
) -> set[str]:
    """package:com.foo z `pm list packages` — pusta gdy adb padnie."""
    try:
        if timeout is None:
            out = adb(serial, ["shell", "pm", "list", "packages"])
        else:
            try:
                out = adb(serial, ["shell", "pm", "list", "packages"],
                          timeout=timeout)
            except TypeError:
                # Komentarz: testowe adb(serial, args) bez timeout=
                out = adb(serial, ["shell", "pm", "list", "packages"])
    except Exception:
        return set()
    found: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            found.add(line.split(":", 1)[1].strip())
    return found


def _argv_opt(cmd: list[str], flag: str, value) -> None:
    """Doklej `--flag value` tylko gdy wartość niepusta (jak ręczny CLI)."""
    if value is None:
        return
    text = str(value).strip()
    if text:
        cmd.extend([flag, text])


def build_clone_argv(identity: dict, *, script: Path, force: bool = True) -> list[str]:
    """Argv jak ręczny: package, android-id, model, fingerprint + reszta tożsamości.

    Odpowiednik:
      python clone_instagram.py --package … --android-id … --model … \\
        --fingerprint … --force --build --sign
    plus opcjonalne `--apk`, GAID, MediaDRM, czasy, GPS, DNS, SIM, MAC, IMEI.
    """
    cmd = [
        sys.executable, "-u", str(script),
        "--package", identity["package"],
        "--android-id", identity["android_id"],
        "--model", identity.get("model") or "Samsung Galaxy A54 5G",
        "--fingerprint", identity["fingerprint"],
        "--build", "--sign",
    ]
    # Komentarz: te same flagi co w docstringu clone_instagram.py
    _argv_opt(cmd, "--gaid", identity.get("gaid"))
    _argv_opt(cmd, "--media-drm-id", identity.get("media_drm_id"))
    _argv_opt(cmd, "--build-time", identity.get("build_time"))
    _argv_opt(cmd, "--boot-time", identity.get("boot_time"))
    _argv_opt(cmd, "--gps-lat", identity.get("gps_lat"))
    _argv_opt(cmd, "--gps-lon", identity.get("gps_lon"))
    _argv_opt(cmd, "--dns", identity.get("dns"))
    _argv_opt(cmd, "--sim-country", identity.get("sim_country"))
    _argv_opt(cmd, "--sim-operator", identity.get("sim_operator"))
    _argv_opt(cmd, "--sim-operator-code", identity.get("sim_operator_code"))
    _argv_opt(cmd, "--mac", identity.get("mac"))
    _argv_opt(cmd, "--imei", identity.get("imei"))
    _argv_opt(cmd, "--proxy", identity.get("proxy"))
    _argv_opt(cmd, "--apk", identity.get("apk"))
    _argv_opt(cmd, "--dir-prefix", identity.get("dir_prefix"))
    if force:
        cmd.append("--force")
    return cmd


def run_appcloner_build(
    identity: dict,
    *,
    script: Path,
    log_file,
    cancel: threading.Event | None = None,
    popen_factory=subprocess.Popen,
) -> None:
    """Subprocess clone_instagram.py; stdout/err → log_file."""
    global _job_proc
    cmd = build_clone_argv(identity, script=script)
    proc = popen_factory(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(script.parent),
        env=java_subprocess_env(),
    )
    _job_proc = proc
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise CloneJobError("anulowano")
            time.sleep(0.25)
        if proc.returncode != 0:
            raise CloneJobError(f"appcloner exit={proc.returncode}")
    finally:
        _job_proc = None
        compact_clone_log(log_file)


_DIR_PREFIX_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_dir_prefix(raw: str) -> str:
    """Alias urządzenia → segment clone/<prefix>/ (nie _cache)."""
    text = _DIR_PREFIX_SAFE.sub("_", (raw or "").strip()).strip("._-")
    if text.lower() in ("_cache", "cache"):
        text = f"dev_{text}"
    return text[:80]


def signed_apk_path(appcloner: Path, package: str, prefix: str = "") -> Path:
    """clone/<prefix>/<APPID>/<APPID>.apk — prefix = telefon, żeby APPID się nie gryzł."""
    pre = sanitize_dir_prefix(prefix)
    base = Path(appcloner) / "clone"
    if pre:
        return base / pre / package / f"{package}.apk"
    return base / package / f"{package}.apk"


_PKG_SAFE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


def _is_missing_split_error(msg: str) -> bool:
    """True gdy PackageManager żąda splitów przy pojedynczym APK."""
    return "INSTALL_FAILED_MISSING_SPLIT" in (msg or "")


def install_apk(
    serial: str,
    apk: Path,
    adb: AdbFn = default_adb,
    log: LogFn | None = None,
) -> None:
    """adb install -r --no-incremental (jak ręczny klon, bez install-incremental).

    Przy MISSING_SPLIT ponów z `--no-streaming`. Nie woła `install-multiple`.
    Nigdy nie rusza stock `com.instagram.android`.
    """
    if not apk.is_file():
        raise CloneJobError(f"brak podpisanego APK: {apk}")
    apk_s = str(apk)
    try:
        # Komentarz: --no-incremental = ten sam argv co ręczny adb na A50
        adb(serial, ["install", "-r", "--no-incremental", apk_s])
        return
    except CloneJobError as first:
        if not _is_missing_split_error(str(first)):
            raise
        # Komentarz: ten adb (37) robi Streamed Install; --no-streaming jest w helpie
        _log(log, "MISSING_SPLIT po `install -r --no-incremental` — ponawiam z --no-streaming")
        adb(serial, ["install", "-r", "--no-incremental", "--no-streaming", apk_s])


def uninstall_apk(serial: str, package: str, adb: AdbFn = default_adb) -> str:
    """adb uninstall — tylko klon, nigdy stock IG."""
    pkg = (package or "").strip()
    if not is_clone_package(pkg):
        raise CloneJobError(f"nie odinstaluję stock/obcego pakietu: {pkg}")
    return adb(serial, ["uninstall", pkg])


def drop_clone_record(device: dict, package: str) -> dict:
    """Wyrzuć package z clones[] (po uninstall / usunięciu konta)."""
    pkg = (package or "").strip()
    device["clones"] = [c for c in (device.get("clones") or []) if c.get("package") != pkg]
    return device


def accounts_holding_package(
    config_root: Path,
    package: str,
    *,
    exclude_alias: str = "",
) -> list[str]:
    """Aliasy kont z tym APPID (poza `exclude_alias` — kasowane konto)."""
    pkg = (package or "").strip()
    if not pkg:
        return []
    skip = (exclude_alias or "").strip()
    out: list[str] = []
    acc_dir = Path(config_root) / "accounts"
    if not acc_dir.is_dir():
        return []
    for path in sorted(acc_dir.glob("*.json")):
        alias = path.stem
        if skip and alias == skip:
            continue
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        got = ((cfg.get("device") or {}).get("app_clone_package") or "").strip()
        if got == pkg:
            out.append(alias)
    return out


def packages_held_on_device(config_root: Path, device_alias: str) -> set[str]:
    """APPID, które konta tego telefonu nadal mają w configu."""
    want = (device_alias or "").strip()
    held: set[str] = set()
    acc_dir = Path(config_root) / "accounts"
    if not want or not acc_dir.is_dir():
        return held
    for path in acc_dir.glob("*.json"):
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        dev = cfg.get("device") or {}
        if (dev.get("device_alias") or "").strip() != want:
            continue
        pkg = (dev.get("app_clone_package") or "").strip()
        if pkg:
            held.add(pkg.lower())
    return held


def prune_stale_clone_records(
    device: dict,
    installed: Iterable[str],
    held: Iterable[str],
) -> list[str]:
    """Wyrzuć z clones[] klony, których nie ma na telefonie i żadne konto nie trzyma."""
    inst = {p.strip().lower() for p in installed if p}
    keep_held = {p.strip().lower() for p in held if p}
    kept: list[dict] = []
    dropped: list[str] = []
    for c in device.get("clones") or []:
        pkg = (c.get("package") or "").strip()
        key = pkg.lower()
        if not is_clone_package(pkg):
            kept.append(c)
            continue
        if key in inst or key in keep_held:
            kept.append(c)
            continue
        dropped.append(pkg)
    if dropped:
        device["clones"] = kept
    return dropped


def uninstall_clone_for_deleted_account(
    *,
    config_root: Path,
    device_alias: str,
    package: str,
    account_alias: str,
    adb: AdbFn = default_adb,
) -> dict:
    """Po skasowaniu konta: `adb uninstall` klona, jeśli nikt inny go nie trzyma.

    Nigdy stock IG. Błąd ADB wraca w `error` — nie rzuca (kasowanie konta i tak leci).
    """
    pkg = (package or "").strip()
    result = {
        "package": pkg or None,
        "uninstalled": False,
        "dropped": False,
        "skipped": None,
        "error": None,
        "holders": [],
    }
    if not pkg:
        result["skipped"] = "brak_pakietu"
        return result
    if not is_clone_package(pkg):
        result["skipped"] = "stock_or_foreign"
        return result
    holders = accounts_holding_package(
        config_root, pkg, exclude_alias=account_alias)
    result["holders"] = holders
    if holders:
        result["skipped"] = "shared"
        return result
    try:
        path, dev = load_device(config_root, device_alias)
    except CloneJobError as e:
        result["error"] = str(e)
        return result
    serial = (dev.get("adb_id") or "").strip()
    if not serial or serial == "REPLACE":
        result["error"] = "brak adb_id"
        return result
    try:
        uninstall_apk(serial, pkg, adb=adb)
        result["uninstalled"] = True
    except CloneJobError as e:
        result["error"] = str(e)
        return result
    drop_clone_record(dev, pkg)
    save_device(path, dev)
    result["dropped"] = True
    return result


def _ident_float(raw: dict, key: str) -> float | None:
    """Opcjonalny float z panelu (GPS); puste → None."""
    val = raw.get(key)
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError) as e:
        raise CloneJobError(f"tożsamość: złe {key}") from e


# Komentarz: cloner wymaga android_id + fingerprint + model. Reszta z checkboxów.
# Checkbox w panelu → klucze tożsamości (gps = para lat/lon)
_IDENT_USE_KEYS = {
    "gaid": ("gaid",),
    "mac": ("mac",),
    "imei": ("imei",),
    "media_drm_id": ("media_drm_id",),
    "build_time": ("build_time",),
    "boot_time": ("boot_time",),
    "gps": ("gps_lat", "gps_lon"),
    "dns": ("dns",),
    "sim_country": ("sim_country",),
    "sim_operator": ("sim_operator",),
    "sim_operator_code": ("sim_operator_code",),
}


def _identity_use_map(raw: dict) -> dict[str, bool] | None:
    """Mapa checkboxów z panelu albo None (stary klient = użyj wszystkich)."""
    use = raw.get("use")
    if not isinstance(use, dict) or not use:
        return None
    return {str(k): bool(v) for k, v in use.items()}


def _apply_identity_use(out: dict, use: dict[str, bool] | None) -> dict:
    """Wyzeruj odznaczone pola — nie trafią do argv (`_argv_opt` pomija puste)."""
    if not use:
        return out
    for flag, keys in _IDENT_USE_KEYS.items():
        if use.get(flag, True):
            continue
        for key in keys:
            out[key] = "" if key not in ("gps_lat", "gps_lon") else None
    out["use"] = use
    return out


def sanitize_identity(raw: dict) -> dict:
    """Tożsamość z panelu — pola CLI clone_instagram.py + walidacja APPID.

    `use` (opcjonalne): które spoofy faktycznie wysłać. Brak `use` = jak dawniej
    (MAC/IMEI wymagane). Odznaczony MAC/IMEI nie blokuje joba.
    """
    if not isinstance(raw, dict):
        raise CloneJobError("zła tożsamość")
    pkg = str(raw.get("package") or "").strip()
    if not _PKG_SAFE.fullmatch(pkg) or pkg.lower() == ORIG_PACKAGE:
        raise CloneJobError(f"zły APPID: {pkg}")
    use = _identity_use_map(raw)
    out = {
        "package": pkg,
        "android_id": str(raw.get("android_id") or "").strip(),
        "gaid": str(raw.get("gaid") or "").strip(),
        "fingerprint": str(raw.get("fingerprint") or "").strip(),
        "mac": str(raw.get("mac") or "").strip(),
        "imei": str(raw.get("imei") or "").strip(),
        "model": str(raw.get("model") or "").strip(),
        "media_drm_id": str(raw.get("media_drm_id") or "").strip(),
        "build_time": str(raw.get("build_time") or "").strip(),
        "boot_time": str(raw.get("boot_time") or "").strip(),
        "gps_lat": _ident_float(raw, "gps_lat"),
        "gps_lon": _ident_float(raw, "gps_lon"),
        "dns": str(raw.get("dns") or "").strip(),
        "sim_country": str(raw.get("sim_country") or "").strip(),
        "sim_operator": str(raw.get("sim_operator") or "").strip(),
        "sim_operator_code": str(raw.get("sim_operator_code") or "").strip(),
        "proxy": str(raw.get("proxy") or "").strip(),
        "apk": str(raw.get("apk") or raw.get("apk_filename") or "").strip(),
        # Komentarz: checkboxy startu z dialogu — backend zawsze force-stop stock + klonów
        "safeguards": normalize_safeguards(raw.get("safeguards")),
    }
    out = _apply_identity_use(out, use)
    if not out["android_id"]:
        raise CloneJobError("tożsamość: brak android_id")
    if not out["fingerprint"] or not out["model"]:
        raise CloneJobError("tożsamość: brak fingerprint / model")
    need_mac = use is None or use.get("mac", True)
    need_imei = use is None or use.get("imei", True)
    if need_mac and not out["mac"]:
        raise CloneJobError("tożsamość: brak MAC")
    if need_imei and not out["imei"]:
        raise CloneJobError("tożsamość: brak IMEI")
    return out


def preview_clone(
    device_alias: str,
    *,
    config_root: Path,
    extra_from_adb: bool = True,
    adb: AdbFn = default_adb,
    preferred_model: str | None = None,
) -> dict:
    """Losowa tożsamość + wolne APPID — bez APK, JDK i appclonera.

    ADB (`pm list`) jest opcjonalne i ma krótki timeout: pad / wiszący adb
    nie blokuje MAC/IMEI/fingerprint w dialogu Utwórz konto.
    `preferred_model` — operator wybrał model: nie rotujemy na inny profil.
    """
    _path, dev = load_device(config_root, device_alias)
    serial = (dev.get("adb_id") or "").strip()
    extra: set[str] = set()
    adb_error = None
    if extra_from_adb and serial and serial != "REPLACE":
        try:
            extra = list_installed_packages(
                serial, adb=adb, timeout=PREVIEW_ADB_TIMEOUT_S)
        except Exception as e:
            extra = set()
            adb_error = str(e)
    held = packages_held_on_device(config_root, device_alias)
    adb_ok = bool(extra)
    # Komentarz: żywy pm list → wolne APPID z telefonu, nie ze starych clones[]
    if adb_ok:
        dropped = prune_stale_clone_records(dev, extra, held)
        if dropped:
            save_device(_path, dev)
            _path, dev = load_device(config_root, device_alias)
    occ = clone_occupancy(dev, extra, adb_ok=adb_ok, held_packages=held)
    try:
        ident = generate_clone_identity(
            dev, extra_packages=extra, preferred_model=preferred_model,
            occupied_packages=occ)
    except IdentityError as e:
        raise CloneJobError(str(e)) from e
    used = occ
    return {
        "identity": ident,
        # Komentarz: szablony fingerprint ({token}) — select modelu w panelu
        "spoof_profiles": list_spoof_profiles(),
        "suggested_packages": next_clone_packages(used, 8),
        "used_packages": sorted(used),
        "adb_id": serial,
        "has_adb": bool(serial and serial != "REPLACE"),
        "device_alias": device_alias,
        "adb_error": adb_error,
    }


# Komentarz: com.instagram.android jest prefiksem androib — szukaj `package/`
# tylko w liniach focused/resumed, nie w całym dumpsys (recents też ma ACTIVITY).
_FOCUS_LINE_MARKERS = (
    "mResumedActivity",
    "mFocusedActivity",
    "topResumedActivity",
    "mCurrentFocus",
    "mFocusedApp",
)


def _package_owns_resumed(package: str, dump: str) -> str:
    """Zwróć linię focused/resumed klona albo pusty string.

    Samo `package in dump` jest za słabe: pidof + recents dają fałszywy sukces
    gdy InstagramAppShell żyje chwilę, a MainTabActivity pada na InflateException.
    """
    if not package or not dump:
        return ""
    needle = f"{package}/"
    for raw in dump.splitlines():
        line = raw.strip()
        if needle not in line:
            continue
        if any(m in line for m in _FOCUS_LINE_MARKERS):
            return line
    return ""


def _extract_fatal_logcat(text: str) -> str:
    """Wycinek FATAL / InflateException z `logcat -d` — pusto gdy cisza."""
    if not text:
        return ""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if "FATAL EXCEPTION" in ln or "InflateException" in ln:
            start = i
            break
    if start is None:
        art = [ln for ln in lines if "AndroidRuntime" in ln]
        return "\n".join(art[-30:]) if art else ""
    return "\n".join(lines[start : min(len(lines), start + 40)])


def _adb_shell_loose(adb: AdbFn, serial: str, args: list[str]) -> str:
    """adb shell … — pusto przy CloneJobError (dumpsys/logcat bywają flakowe)."""
    try:
        return adb(serial, ["shell", *args]) or ""
    except CloneJobError:
        return ""


def _dumpsys_focus_blob(serial: str, adb: AdbFn) -> str:
    """Okno + activities — stąd mCurrentFocus / mResumedActivity."""
    parts: list[str] = []
    for spec in (
        ["dumpsys", "window", "displays"],
        ["dumpsys", "activity", "activities"],
    ):
        chunk = _adb_shell_loose(adb, serial, spec)
        if chunk:
            parts.append(chunk)
    return "\n".join(parts)


def _fatal_logcat_blob(serial: str, adb: AdbFn) -> str:
    """Po crashu: buffer AndroidRuntime, potem `-b crash`."""
    raw = _adb_shell_loose(
        adb, serial, ["logcat", "-d", "-t", "250", "-s", "AndroidRuntime:E"]
    )
    fatal = _extract_fatal_logcat(raw)
    if fatal:
        return fatal
    crash = _adb_shell_loose(adb, serial, ["logcat", "-d", "-t", "80", "-b", "crash"])
    return _extract_fatal_logcat(crash) or (crash.strip()[:2000] if crash else "")


# Komentarz: package/ w linii focus — stock `android` też pasuje do androi*
_FOCUS_CLONE_PKG = re.compile(r"(com\.instagram\.androi[A-Za-z0-9_]*)/", re.I)


def _other_clone_in_focus(target: str, dump: str) -> str:
    """Inny IG (stock lub klon, nie target) w focused/resumed — albo pusty string."""
    if not dump:
        return ""
    target_l = (target or "").strip().lower()
    for raw in dump.splitlines():
        line = raw.strip()
        if not any(m in line for m in _FOCUS_LINE_MARKERS):
            continue
        for match in _FOCUS_CLONE_PKG.finditer(line):
            pkg = match.group(1)
            if pkg.lower() == target_l:
                continue
            # Komentarz: stock też blokuje — w jednym czasie tylko 1 Instagram
            if is_ig_package(pkg):
                return pkg
    return ""


def _installed_ig_packages(serial: str, adb: AdbFn, installed: set[str] | None) -> set[str]:
    """Zainstalowane pakiety IG (stock + klony) z `pm list` albo z joba."""
    raw = installed if installed is not None else list_installed_packages(serial, adb=adb)
    return {p for p in raw if p and is_ig_package(p)}


def _wait_other_clone_pids_gone(
    serial: str,
    others: list[str],
    *,
    adb: AdbFn,
    wait_s: float,
    log: LogFn | None,
) -> str:
    """Czekaj aż pidof innych IG (stock + klony) będzie pusty. Pusto = OK."""
    if not others:
        return ""
    deadline = time.monotonic() + max(0.0, float(wait_s))
    leftover: list[str] = list(others)
    while True:
        # Komentarz: ps NAME, nie pidof — comm 15 znaków myli stock z klonem
        ps_out = _adb_shell_loose(adb, serial, ["ps", "-A"])
        if not (ps_out or "").strip():
            ps_out = _adb_shell_loose(adb, serial, ["ps"])
        leftover = [pkg for pkg in others if ig_ps_has_package(ps_out, pkg)]
        if not leftover:
            return ""
        for pkg in leftover:
            _log(log, f"safeguards: leftover {pkg} — ponawiam force-stop")
            _adb_shell_loose(adb, serial, ["am", "force-stop", pkg])
        if time.monotonic() >= deadline:
            msg = (
                "sole_ig_pid: inne IG nadal mają PID: "
                + ", ".join(leftover)
            )
            _log(log, msg)
            return msg
        time.sleep(0.15)


def apply_launch_safeguards(
    serial: str,
    package: str,
    *,
    adb: AdbFn = default_adb,
    safeguards: dict | None = None,
    installed: set[str] | None = None,
    log: LogFn | None = None,
    wait_s: float = 2.5,
) -> dict:
    """Force-stop każdego innego IG przed am start (bez uninstall).

    Always-on: `am force-stop` stock + `com.instagram.androi*` oprócz targetu
    (także zminimalizowane). Potem sole_ig_pid + no_parallel_ig — jak inny
    IG żyje, nie startujemy. Zwraca ``{ok, error, stopped}``.
    """
    sg = normalize_safeguards(safeguards)
    pkgs = _installed_ig_packages(serial, adb, installed)
    target_l = (package or "").strip().lower()
    # Komentarz: stock + klony; zminimalizowany też dostaje force-stop, nie HOME
    others = sorted(
        p for p in pkgs
        if is_ig_package(p) and p.strip().lower() != target_l
    )
    stopped: list[str] = []

    _log(log, "safeguards: always-on force-stop innych IG (stock + klony)")
    if any(p.strip().lower() == ORIG_PACKAGE for p in others):
        _log(log, "safeguards: stop_stock ON — force-stop stock (bez uninstall)")

    for pkg in others:
        _log(log, f"safeguards: am force-stop {pkg}")
        _adb_shell_loose(adb, serial, ["am", "force-stop", pkg])
        stopped.append(pkg)

    if stopped and wait_s > 0:
        time.sleep(min(0.3, float(wait_s)))

    # Komentarz: pidof leftover (także stock) — nie odpalamy drugiego IG
    _log(log, "safeguards: sole_ig_pid ON — czekam aż pidof innych IG pusty")
    sole_err = _wait_other_clone_pids_gone(
        serial, others, adb=adb, wait_s=wait_s, log=log)
    if sole_err:
        return {"ok": False, "error": sole_err, "stopped": stopped}

    focus = _dumpsys_focus_blob(serial, adb)
    rival = _other_clone_in_focus(package, focus)
    if rival:
        msg = (
            f"no_parallel_ig: inny IG nadal w focus ({rival}) — nie startuję"
        )
        _log(log, msg)
        return {"ok": False, "error": msg, "stopped": stopped}

    if sg.get("one_clone_job"):
        _log(log, "safeguards: one_clone_job ON (DeviceLock + 409 gdy bot trzyma serial)")
    return {"ok": True, "error": None, "stopped": stopped}


def verify_clone_launch(
    serial: str,
    package: str,
    *,
    adb: AdbFn = default_adb,
    wait_s: float = 2.5,
    safeguards: dict | None = None,
    installed: set[str] | None = None,
    log: LogFn | None = None,
) -> dict:
    """Po install: start LAUNCHER, sukces tylko gdy resumed/focus to ten pakiet.

    `pidof` kłamie: proces InstagramAppShell żyje chwilę po InflateException.
    Przed monkey/am start: force-stop stock + innych klonów (bez uninstall).
    """
    path_out = adb(serial, ["shell", "pm", "path", package])
    if "package:" not in (path_out or ""):
        return {
            "ok": False,
            "error": f"brak pm path dla {package}",
            "pm_path": path_out or "",
            "pid": "",
            "focus_hit": False,
            "resumed": "",
            "fatal": "",
            "stopped": [],
        }
    # Komentarz: zawsze ON — stop innych androi* zanim target dostanie focus
    sg_result = apply_launch_safeguards(
        serial, package, adb=adb, safeguards=safeguards,
        installed=installed, log=log, wait_s=wait_s)
    if not sg_result.get("ok"):
        return {
            "ok": False,
            "error": sg_result.get("error") or "safeguards: nie startuję",
            "pm_path": (path_out or "").strip(),
            "pid": "",
            "focus_hit": False,
            "resumed": "",
            "fatal": "",
            "stopped": sg_result.get("stopped") or [],
        }
    # Komentarz: czyść logcat przed startem, żeby FATAL był z tego odpalenia
    _adb_shell_loose(adb, serial, ["logcat", "-c"])
    try:
        adb(serial, ["shell", "monkey", "-p", package,
                     "-c", "android.intent.category.LAUNCHER", "1"])
    except CloneJobError:
        pass
    try:
        adb(serial, ["shell", "am", "start",
                     "-a", "android.intent.action.MAIN",
                     "-c", "android.intent.category.LAUNCHER", "-p", package])
    except CloneJobError:
        pass
    time.sleep(max(0.0, float(wait_s)))
    pid = _adb_shell_loose(adb, serial, ["pidof", package]).strip()
    focus = _dumpsys_focus_blob(serial, adb)
    resumed = _package_owns_resumed(package, focus)
    if resumed:
        return {
            "ok": True,
            "pm_path": (path_out or "").strip(),
            "pid": pid,
            "focus_hit": True,
            "resumed": resumed,
            "fatal": "",
            "error": None,
            "stopped": sg_result.get("stopped") or [],
        }
    fatal = _fatal_logcat_blob(serial, adb)
    err = (
        f"klon {package} nie ma focused/resumed activity "
        f"(pid={pid or 'brak'} nie wystarcza)"
    )
    if fatal:
        err = f"{err}\n{fatal}"
    return {
        "ok": False,
        "pm_path": (path_out or "").strip(),
        "pid": pid,
        "focus_hit": False,
        "resumed": "",
        "fatal": fatal,
        "error": err,
        "stopped": sg_result.get("stopped") or [],
    }


def append_clone_record(
    device: dict,
    *,
    package: str,
    identity: dict,
    note: str = "z panelu",
) -> dict:
    """Dopisz / zaktualizuj clones[] na kopii device dict."""
    clones = list(device.get("clones") or [])
    for c in clones:
        if c.get("package") == package:
            c["identity"] = identity
            c["note"] = note
            device["clones"] = clones
            return device
    next_slot = max((int(c.get("slot") or 0) for c in clones), default=0) + 1
    clones.append({
        "slot": next_slot,
        "package": package,
        "note": note,
        "identity": identity,
    })
    device["clones"] = clones
    return device


def save_device(path: Path, device: dict) -> None:
    """Zapis devices/<alias>.json."""
    path.write_text(json.dumps(device, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def load_device(config_root: Path, alias: str) -> tuple[Path, dict]:
    path = Path(config_root) / "devices" / f"{alias}.json"
    if not path.exists():
        raise CloneJobError(f"urządzenie '{alias}' nie istnieje")
    try:
        dev = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise CloneJobError(f"nieczytelny device JSON: {e}") from e
    return path, dev


def spoof_fields_from_identity(identity: dict) -> dict:
    """Pola account.device.spoofed_* kopiowane przy tworzeniu konta."""
    return {
        "spoofed_mac": identity.get("mac") or None,
        "spoofed_imei": identity.get("imei") or None,
        "spoofed_android_id": identity.get("android_id") or None,
    }


def identity_for_package(device: dict, package: str) -> dict | None:
    """identity z clones[] dla APPID albo None."""
    for c in device.get("clones") or []:
        if c.get("package") == package and isinstance(c.get("identity"), dict):
            return c["identity"]
    return None


def create_clone(
    device_alias: str,
    *,
    config_root: Path,
    data_root: Path,
    appcloner: Path | None = None,
    adb: AdbFn = default_adb,
    list_packages: Callable[[str], set[str]] | None = None,
    run_build: Callable | None = None,
    acquire_lock: bool = True,
    log: LogFn | None = None,
    cancel: threading.Event | None = None,
    identity: dict | None = None,
    verify: bool = True,
    verify_wait_s: float = 2.5,
) -> dict:
    """Pełny przebieg: generate → appcloner → adb install → zapis clones[] → launch.

    `identity` z panelu (podgląd spoofa) — inaczej losujemy. Testy podmieniają
    adb / run_build; `verify=False` pomija start i sprawdzenie resumed/focus.
    """
    path, dev = load_device(config_root, device_alias)
    serial = (dev.get("adb_id") or "").strip()
    if not serial or serial == "REPLACE":
        raise CloneJobError(f"device:{device_alias}: brak adb_id")

    # Komentarz: root musi wskazywać appcloner nawet gdy test/panel podmienia
    # run_build — inaczej install szuka APK w cwd (./clone/…) zamiast clone/<APPID>/
    root = appcloner or appcloner_root()
    script = None
    if run_build is None:
        script = clone_script(root)
        require_jdk()

    lock = None
    if acquire_lock:
        lock = DeviceLock(data_root, serial)
        try:
            lock.acquire(owner=f"clone:{device_alias}", session_id="clone-app")
        except DeviceBusy as e:
            raise CloneBusy(str(e)) from e

    try:
        extra: set[str] = set()
        lister = list_packages or (lambda s: list_installed_packages(s, adb=adb))
        extra = lister(serial)
        held = packages_held_on_device(config_root, device_alias)
        if extra:
            dropped = prune_stale_clone_records(dev, extra, held)
            if dropped:
                save_device(path, dev)
                path, dev = load_device(config_root, device_alias)
        occ = clone_occupancy(dev, extra, adb_ok=bool(extra), held_packages=held)
        ident = sanitize_identity(identity) if identity else generate_clone_identity(
            dev, extra_packages=extra, occupied_packages=occ)
        # Komentarz: ten sam APPID na dwóch telefonach → osobny folder
        ident["dir_prefix"] = sanitize_dir_prefix(device_alias)
        pkg = ident["package"]
        if not _PKG_SAFE.fullmatch(pkg):
            raise CloneJobError(f"zły APPID: {pkg}")
        # Komentarz: panel job podmienia run_build — i tak rozwiąż apks/<nazwa>
        if ident.get("apk") or run_build is None:
            ident["apk"] = str(require_source_apk(root, apk=ident.get("apk")))
        sg = normalize_safeguards(ident.get("safeguards"))
        ident["safeguards"] = sg
        # Komentarz: panel widzi czemu drugi klon tej wersji jest szybki
        _log(log, describe_src_cache(root, Path(ident["apk"]) if ident.get("apk") else None))
        if sg.get("one_clone_job"):
            _log(log, "safeguards: one_clone_job ON (DeviceLock + 409 gdy bot trzyma serial)")
        _log(
            log,
            f"tożsamość: {pkg} model={ident.get('model')} "
            f"mac={ident['mac']} imei={ident['imei']}",
        )

        def _default_build(one, logf) -> None:
            # Komentarz: panel przekazuje otwarty log; CLI pisze na stdout
            target = logf if logf is not None else sys.stdout
            run_appcloner_build(
                one, script=script, log_file=target, cancel=cancel)

        builder = run_build or _default_build
        builder(ident, None)

        apk = signed_apk_path(root, pkg, prefix=ident["dir_prefix"])
        _log(log, f"instaluję {apk} na {serial}")
        if cancel is not None and cancel.is_set():
            raise CloneJobError("anulowano")
        install_apk(serial, apk, adb=adb, log=log)

        # odśwież JSON — mogło się zmienić w międzyczasie
        path, dev = load_device(config_root, device_alias)
        # Komentarz: pełny zestaw jak w identity.json / CLI, nie tylko fp+package
        slim_ident = {
            "package": ident["package"],
            "android_id": ident["android_id"],
            "gaid": ident.get("gaid"),
            "fingerprint": ident["fingerprint"],
            "mac": ident["mac"],
            "imei": ident["imei"],
            "model": ident.get("model"),
            "media_drm_id": ident.get("media_drm_id"),
            "build_time": ident.get("build_time"),
            "boot_time": ident.get("boot_time"),
            "gps_lat": ident.get("gps_lat"),
            "gps_lon": ident.get("gps_lon"),
            "dns": ident.get("dns"),
            "sim_country": ident.get("sim_country"),
            "sim_operator": ident.get("sim_operator"),
            "sim_operator_code": ident.get("sim_operator_code"),
            "apk": ident.get("apk"),
            "use": ident.get("use"),
            "safeguards": ident.get("safeguards"),
            "dir_prefix": ident.get("dir_prefix"),
        }
        append_clone_record(dev, package=pkg, identity=slim_ident, note="z panelu")
        save_device(path, dev)
        _log(log, f"zapisano clones[] + {pkg}")

        verified = None
        if verify:
            _log(log, f"otwieram {pkg} i sprawdzam resumed/focus (pid nie wystarcza)")
            verified = verify_clone_launch(
                serial, pkg, adb=adb, wait_s=verify_wait_s,
                safeguards=ident.get("safeguards"),
                installed=extra,
                log=log,
            )
            if not verified.get("ok"):
                raise CloneJobError(verified.get("error") or "klon nie wstał")
            _log(
                log,
                f"OK resumed={verified.get('resumed') or '—'} "
                f"pid={verified.get('pid') or '—'}",
            )
        return {
            "identity": slim_ident,
            "apk": str(apk),
            "device_alias": device_alias,
            "verified": verified,
        }
    finally:
        if lock is not None:
            lock.release()


def request_cancel() -> bool:
    """Ustaw flagę cancel i terminate subprocess appclonera."""
    _job_cancel.set()
    proc = _job_proc
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
        return True
    return bool(_job.get("running"))


def start_job_thread(
    device_alias: str,
    *,
    config_root: Path,
    data_root: Path,
    identity: dict | None = None,
) -> dict:
    """Start w tle — 202 z panelu.  CloneBusy jeśli już leci."""
    with _job_lock:
        if _job.get("running"):
            raise CloneBusy(
                f"klonowanie już trwa: device={_job.get('device_alias')}")
    # Komentarz: tożsamość i APK z dysku PRZED running=True — 409 zamiast joba w error
    ident = sanitize_identity(identity) if identity else None
    sg = normalize_safeguards((ident or {}).get("safeguards"))
    preflight_create(
        device_alias, config_root=config_root, data_root=data_root,
        source_apk=(ident or {}).get("apk"),
        one_clone_job=bool(sg.get("one_clone_job", True)),
    )
    with _job_lock:
        if _job.get("running"):
            raise CloneBusy(
                f"klonowanie już trwa: device={_job.get('device_alias')}")
        _job_cancel.clear()
        log_dir = Path(data_root) / "clones"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"{ts}-{device_alias}.log"
        _job.update({
            "running": True,
            "device_alias": device_alias,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "state": "running",
            "package": None,
            "identity": ident,
            "log_path": str(log_path),
            "apk": None,
            "verified": None,
        })

    def _worker():
        try:
            with log_path.open("w", encoding="utf-8") as fh:
                def log_line(msg: str) -> None:
                    fh.write(msg.rstrip() + "\n")
                    fh.flush()

                ac_root = appcloner_root()

                def run_build(one, _unused):
                    run_appcloner_build(
                        one, script=clone_script(ac_root),
                        log_file=fh, cancel=_job_cancel)

                result = create_clone(
                    device_alias,
                    config_root=config_root,
                    data_root=data_root,
                    appcloner=ac_root,
                    run_build=run_build,
                    log=log_line,
                    cancel=_job_cancel,
                    identity=ident,
                    verify=True,
                )
            one = result["identity"]
            with _job_lock:
                _job.update({
                    "running": False,
                    "finished_at": time.time(),
                    "error": None,
                    "state": "ok",
                    "package": one.get("package"),
                    "identity": one,
                    "apk": result.get("apk"),
                    "verified": result.get("verified"),
                })
        except Exception as e:
            with _job_lock:
                _job.update({
                    "running": False,
                    "finished_at": time.time(),
                    "error": str(e),
                    "state": "error",
                })
            try:
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"[clone-job] BŁĄD: {e}\n")
            except OSError:
                pass

    threading.Thread(target=_worker, name=f"clone-{device_alias}",
                     daemon=True).start()
    return job_snapshot()
