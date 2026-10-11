"""Live Insights (jobsaver) albo seed z recon — bez kroku schedulera.

Nie publikuje. Nie startuje drugiego scrcpy. Gdy telefon zajęty / offline
zapisuje seed z `calibration/recon_pro_dash/`, żeby karta nie była pusta.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from v2.core.adb_probe import AdbProbeError, adb
from v2.core.device_lock import DeviceBusy, DeviceLock, peek_device_lock
from v2.core.screen_map import flatten_hierarchy
from v2.data.pro_dash_chart import sample_item_chart
from v2.data.pro_dash_parse import (
    apply_seed_thumbs,
    crop_jpeg,
    parse_content_rows,
    parse_item_daily,
    parse_overview_kpis,
    parse_views_by_type,
    seed_scrape_from_recon,
    thumb_bounds_from_age,
    type_totals_from_rows,
)
from v2.data.pro_dash_store import CONTENT_KINDS, ProDashStore

# Domyślny recon — ten sam trial co karta.
DEFAULT_ACCOUNT = "jobsaver"


def ensure_store(account: str, data_root: Path, *,
                 ig_username: str = "",
                 auto_seed: bool = True) -> tuple[ProDashStore, dict]:
    """Wczytaj store; przy pustym dokumencie wsiej recon (karta niepusta)."""
    store = ProDashStore(account, data_root)
    doc = store.read()
    if auto_seed and store.is_empty(doc):
        return seed_into(store, ig_username=ig_username or doc.get("ig_username") or "")
    return store, store.public_view()


def seed_into(store: ProDashStore, *, ig_username: str = "") -> tuple[ProDashStore, dict]:
    """Wlej trial fetch + miniatury z JPG recon."""
    scrape = seed_scrape_from_recon()
    if ig_username:
        scrape["ig_username"] = ig_username
    apply_seed_thumbs(store, scrape)
    store.merge_scrape(scrape)
    return store, store.public_view()


def device_fetch_status(account: str, data_root: Path,
                        config_root: Path) -> dict:
    """Czy można iść na telefon: serial, adb, flock."""
    serial, pkg, uiauto = _account_device(account, config_root)
    if not serial:
        return {"ok": False, "reason": "offline",
                "detail": "brak device.adb_id w configu konta"}
    # krótki timeout — `adb devices` bywa zawieszony, UI nie może wisieć
    if serial not in _adb_serials_fast():
        return {"ok": False, "reason": "offline",
                "detail": f"adb nie widzi {serial}", "serial": serial}
    holder = peek_device_lock(data_root, serial)
    if holder:
        return {"ok": False, "reason": "busy",
                "detail": f"flock: {holder.get('owner')}",
                "serial": serial, "holder": holder}
    if not _uiauto_up(uiauto):
        return {"ok": False, "reason": "offline",
                "detail": f"uiauto nie odpowiada ({uiauto})",
                "serial": serial}
    return {"ok": True, "reason": "free", "serial": serial,
            "package": pkg, "uiauto": uiauto}


def _adb_serials_fast() -> set[str]:
    """Serials z `adb devices` — max ~2 s, bez wiszenia panelu."""
    try:
        out = adb(["devices", "-l"], timeout=2.0)
    except AdbProbeError:
        return set()
    found: set[str] = set()
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            found.add(parts[0])
    return found


def _uiauto_up(base_url: str, timeout: float = 1.2) -> bool:
    """Czy REST uiauto żyje — bez tego live scrape wisi na 15 s × N."""
    url = (base_url or "http://127.0.0.1:20242").rstrip("/") + "/"
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def fetch_into(store: ProDashStore, account: str, data_root: Path,
               config_root: Path, *,
               item_id: str | None = None,
               full_charts: bool = False) -> dict:
    """Live gdy telefon wolny; inaczej seed. Zwraca public_view + meta fetcha.

    `full_charts=True` — zrzut overlayu i odczyt linii Ten post / typowy.
    Trwa minuty; panel zostaje przy `False` (limit 22 s).
    """
    status = device_fetch_status(account, data_root, config_root)
    if not status.get("ok"):
        _store, view = seed_into(store, ig_username=store.read().get("ig_username") or "")
        view = dict(view)
        view["fetch"] = {
            "live": False,
            "reason": status.get("reason"),
            "detail": status.get("detail"),
            "seeded": True,
        }
        return view

    try:
        scrape = _live_scrape(
            serial=status["serial"],
            pkg=status.get("package") or "com.instagram.androia",
            uiauto=status.get("uiauto") or "http://127.0.0.1:20242",
            data_root=data_root,
            item_id=item_id,
            full_charts=full_charts,
        )
    except DeviceBusy as e:
        _store, view = seed_into(store)
        view = dict(view)
        view["fetch"] = {"live": False, "reason": "busy", "detail": str(e),
                         "seeded": True}
        return view
    except Exception as e:
        _store, view = seed_into(store)
        view = dict(view)
        view["fetch"] = {"live": False, "reason": "error", "detail": str(e)[:240],
                         "seeded": True}
        return view

    if scrape.get("thumbs"):
        for cid, blob in scrape["thumbs"].items():
            rel = store.save_thumb(cid, blob)
            for row in scrape.get("content") or []:
                if row.get("id") == cid:
                    row["thumb"] = rel
    store.merge_scrape(scrape)
    view = dict(store.public_view())
    view["fetch"] = {
        "live": True,
        "reason": "ok",
        "windows": list((scrape.get("windows") or {}).keys()),
        "content_n": len(scrape.get("content") or []),
        "item_id": item_id,
    }
    return view


def _account_device(account: str, config_root: Path) -> tuple[str | None, str, str]:
    """serial, package klona, base uiauto."""
    acc_path = Path(config_root) / "accounts" / f"{account}.json"
    try:
        acc = json.loads(acc_path.read_text(encoding="utf-8"))
    except Exception:
        return None, "com.instagram.android", "http://127.0.0.1:20242"
    dev_alias = ((acc.get("device") or {}).get("device_alias") or "").strip()
    pkg = ((acc.get("device") or {}).get("app_clone_package")
           or "com.instagram.android")
    serial = None
    uiauto = "http://127.0.0.1:20242"
    if dev_alias:
        dpath = Path(config_root) / "devices" / f"{dev_alias}.json"
        try:
            dev = json.loads(dpath.read_text(encoding="utf-8"))
            serial = (dev.get("adb_id") or "").strip() or None
            uiauto = ((dev.get("uiautodev") or {}).get("base_url")
                      or uiauto)
        except Exception:
            pass
    return serial, pkg, uiauto


def _live_scrape(*, serial: str, pkg: str, uiauto: str, data_root: Path,
                 item_id: str | None,
                 full_charts: bool = False) -> dict:
    """Wejdź w Statystyki, zbierz 7/14/30 + filtry zawartości + drill-in.

    Import UiAuto2Device dopiero tu — testy store nie ciągną ADB.
    Pełne wykresy pozycji: `full_charts` (piksele linii + OCR osi).
    """
    from v2.core.device import UiAuto2Device

    lock = DeviceLock(data_root, serial)
    lock.acquire(owner="pro_dash_fetch", session_id="insights-card")
    try:
        dev = UiAuto2Device(serial=serial, base_url=uiauto, ig_package=pkg)
        as_of = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        # panel 22 s; CLI z wykresami — overlay każdej pozycji (bez 3× tapów)
        deadline = time.monotonic() + (480 if full_charts else 22)
        _wake_for_insights(dev, pkg)
        if not _ensure_insights(dev, pkg):
            raise RuntimeError("nie udało się otworzyć Statystyk na telefonie")

        windows: dict = {}
        thumbs: dict[str, bytes] = {}
        content: list[dict] = []
        content_daily: dict[str, list] = {k: [] for k in CONTENT_KINDS}
        gaps = [
            "live: seria dzienna nadal może nie być w hierarchii — "
            "zapisujemy snapshot okna i wiersze; daily[] urośnie przy kolejnych dniach",
        ]

        skip_overview = bool(item_id and full_charts)
        if not skip_overview:
            for days in (7, 14, 30):
                if time.monotonic() > deadline:
                    gaps.append("live: limit czasu przy oknach — reszta z seeda")
                    break
                if not _select_range(dev, days):
                    gaps.append(f"live: nie przełączono chipa {days} dni")
                    continue
                time.sleep(0.6)
                hier = dev.hierarchy(force=True)
                nodes = _nodes_from_hier(hier)
                kpis = parse_overview_kpis(nodes)
                if kpis.get("views") is None:
                    gaps.append(f"live: brak karty Wyświetlenia dla {days} dni")
                by_type = parse_views_by_type(nodes)
                if by_type:
                    kpis["views_by_type"] = {
                        "Posty": by_type.get("post"),
                        "Relacje": by_type.get("story"),
                        "Rolki": by_type.get("reel"),
                    }
                windows[str(days)] = {
                    **kpis,
                    "days": days,
                    "as_of": as_of,
                    "as_of_date": as_of[:10],
                    "range_label": kpis.get("range_label") or f"{days} dni",
                }
        else:
            gaps.append("live: pominięto chipy Ogólne — tylko overlay pozycji")

        # Zawartość: wróć na 30 dni (najszerszy trial), filtry typów
        if time.monotonic() > deadline:
            gaps.append("live: pominięto zawartość (limit czasu)")
            return {
                "as_of": as_of, "source": "live", "gaps": gaps, "daily": [],
                "windows": windows, "content": content, "thumbs": thumbs,
                "content_daily": content_daily, "chart_sparse": None,
            }
        _select_range(dev, 30)
        _tap_text(dev, "Zawartość")
        time.sleep(0.7)
        for kind, label in (("post", "Posty"), ("reel", "Rolki"), ("story", "Relacje")):
            if time.monotonic() > deadline:
                gaps.append("live: limit czasu w filtrach zawartości")
                break
            if not _open_content_filter(dev, label):
                gaps.append(f"live: brak filtra {label}")
                continue
            time.sleep(0.7)
            hier = dev.hierarchy(force=True)
            jpg = dev.screenshot()
            nodes = _nodes_from_hier(hier)
            rows = parse_content_rows(nodes, default_kind=kind)
            for row in rows:
                bounds = thumb_bounds_from_age(row.get("age_bounds"))
                blob = crop_jpeg(jpg, bounds) if bounds else None
                if blob:
                    thumbs[row["id"]] = blob
                content.append(row)
            # Zawartość po filtrze to lista — nie wykres. Oś jak na Ogólne?
            if any(_parse_axis_hint(n) for n in nodes):
                gaps.append(f"live: {label} — widać etykiety osi; brak tooltipów")
            elif kind == "reel" and not rows:
                gaps.append("live: Rolki — empty state, bez wykresu")

        # Suma okna na as_of (gdy brak śladu / item.daily)
        _fill_type_as_of(content_daily, content, windows.get("30") or {},
                         as_of_date=as_of[:10])

        # Ogólne → filtr typu: karta Wyświetlenia = suma typu (nie dzienna)
        if (not skip_overview) and time.monotonic() <= deadline:
            _tap_text(dev, "Ogólne")
            time.sleep(0.5)
            for kind, label in (("post", "Posty"), ("story", "Relacje"),
                                ("reel", "Rolki")):
                if time.monotonic() > deadline:
                    break
                if not _select_overview_type(dev, label):
                    continue
                time.sleep(0.6)
                kpis = parse_overview_kpis(_nodes_from_hier(dev.hierarchy(force=True)))
                if kpis.get("views") is None:
                    continue
                if kind == "reel" and int(kpis["views"] or 0) == 0:
                    continue
                content_daily[kind] = [{
                    "date": as_of[:10],
                    "views": kpis["views"],
                    "source": "live_window",
                }]

        # Drill-in z listy Zawartość (nie z filtra Ogólne)
        _tap_text(dev, "Zawartość")
        time.sleep(0.5)
        targets = _chart_targets(content, item_id, full_charts=full_charts)
        gaps.append(
            f"live: drill-in full_charts={full_charts} "
            f"targets={len(targets)} content={len(content)}")
        if full_charts:
            _sample_item_windows(dev, targets, gaps, deadline)
        else:
            for target in targets:
                if time.monotonic() > deadline:
                    gaps.append("live: pominięto drill-in (limit czasu)")
                    break
                if not _open_content_filter(dev, _kind_label(target)):
                    continue
                time.sleep(0.4)
                if not _tap_content_row(dev, target):
                    continue
                time.sleep(1.0)
                detail_nodes = _nodes_from_hier(dev.hierarchy(force=True))
                daily = parse_item_daily(detail_nodes)
                if daily:
                    target["daily"] = daily
                else:
                    gaps.append(
                        f"live: drill-in {target.get('id')} — brak dat na wykresie "
                        "(zostaje snapshot w history)")
                _tap_desc(dev, "Wstecz")
                time.sleep(0.4)

        return {
            "as_of": as_of,
            "source": "live",
            "gaps": gaps,
            "daily": [],
            "windows": windows,
            "content": content,
            "content_daily": content_daily,
            "thumbs": thumbs,
            "chart_sparse": None,
        }
    finally:
        try:
            lock.release()
        except Exception:
            pass


def _nodes_from_hier(hier: dict) -> list[dict]:
    """Spłaszcz hierarchię do kształtu slim (text/desc/bounds)."""
    out = []
    for n in flatten_hierarchy(hier):
        out.append({
            "text": (n.get("text") or "").strip(),
            "desc": (n.get("content-desc") or "").strip(),
            "rid": (n.get("resource-id") or "").strip(),
            "bounds": n.get("bounds"),
            "click": bool(n.get("clickable")),
        })
    return out


def _wake_for_insights(dev, pkg: str) -> None:
    """Ekran ON + odblokowanie + klon na wierzchu — bez drugiego scrcpy."""
    if not dev.screen_is_on():
        dev.wake_up_key()
        time.sleep(0.7)
        if not dev.screen_is_on():
            dev.press_power()
            time.sleep(0.7)
    if not dev.screen_is_unlocked():
        # jak recon: swipe w górę
        dev.swipe(540, 1990, 540, 400, duration_ms=250)
        time.sleep(0.7)
    if pkg and dev.current_pkg() != pkg:
        dev.launch_app(pkg)
        time.sleep(1.8)


def _ensure_insights(dev, pkg: str) -> bool:
    """Profil → Pulpit → Statystyki (Wyświetlenia). Idempotentne gdy już tam jesteśmy."""
    hier = _nodes_from_hier(dev.hierarchy(force=True))
    texts = " ".join((n.get("text") or "") for n in hier).lower()
    if "statystyki" in texts and ("ogólne" in texts or "wyświetlenia" in texts):
        _tap_text(dev, "Ogólne")
        return True
    # profil (klonu)
    _tap_rid(dev, "com.instagram.android:id/profile_tab")
    time.sleep(0.8)
    if not _tap_text(dev, "Pulpit profesjonalny"):
        return False
    time.sleep(1.0)
    if not _tap_text(dev, "Wyświetlenia"):
        # czasem karta jest już „Statystyki”
        hier = _nodes_from_hier(dev.hierarchy(force=True))
        blob = " ".join((n.get("text") or "") for n in hier).lower()
        return "statystyki" in blob
    time.sleep(1.0)
    return True


def _select_range(dev, days: int) -> bool:
    """Otwórz sheet Okres i tapnij „N dni”."""
    label = f"{days} dni"
    if _tap_text(dev, label):
        # już wybrany chip na górze — otwiera sheet; tapnij opcję jeszcze raz
        time.sleep(0.4)
    if _tap_text(dev, label):
        time.sleep(0.5)
        return True
    # spróbuj przez „Okres”
    _tap_text(dev, "Okres")
    time.sleep(0.4)
    return _tap_text(dev, label)


def _select_overview_type(dev, label: str) -> bool:
    """Sheet „Cała zawartość” na Ogólne → Posty / Relacje / Rolki."""
    for probe in ("Cała zawartość", "Posty", "Relacje", "Rolki"):
        if _tap_text(dev, probe):
            time.sleep(0.35)
            break
    return _tap_text(dev, label)


def _fill_type_as_of(content_daily: dict, content: list[dict],
                     win30: dict, *, as_of_date: str) -> None:
    """Jeden punkt sumy okna per typ, gdy live nie dał śladu / item.daily."""
    sums = type_totals_from_rows(content)
    raw_bt = win30.get("views_by_type") or {}
    by_type = {}
    for label, kind in (("Posty", "post"), ("Relacje", "story"), ("Rolki", "reel")):
        v = raw_bt.get(label)
        if v is not None:
            by_type[kind] = v
    for kind in CONTENT_KINDS:
        if content_daily.get(kind):
            continue
        if kind == "reel" and not sums.get(kind):
            continue
        views = sums.get(kind)
        if views is None:
            views = by_type.get(kind)
        if views is None or (kind == "reel" and views == 0):
            continue
        content_daily[kind] = [{
            "date": as_of_date,
            "views": views,
            "source": "as_of_total",
        }]


def _parse_axis_hint(n: dict) -> bool:
    """Czy węzeł wygląda na etykietę osi (`6 sie` / `3 wrz`)."""
    t = (n.get("text") or "").strip().casefold()
    return bool(re.search(r"^\d{1,2}\s+(sty|lut|mar|kwi|maj|cze|lip|sie|wrz|paź|lis|gru)$", t))


def _open_content_filter(dev, label: str) -> bool:
    """Sheet typu zawartości: Posty / Rolki / Relacje."""
    # aktualny filtr jest po lewej (Posty/Relacje/… albo Cała zawartość)
    for probe in ("Cała zawartość", "Posty", "Relacje", "Rolki"):
        if _tap_text(dev, probe):
            time.sleep(0.4)
            break
    return _tap_text(dev, label)


def _kind_label(row: dict) -> str:
    return {"post": "Posty", "story": "Relacje", "reel": "Rolki"}.get(
        row.get("kind") or "post", "Posty")


def _chart_targets(content: list[dict], item_id: str | None, *,
                   full_charts: bool) -> list[dict]:
    """Które wiersze otworzyć: wszystkie posty/relacje albo jeden id."""
    if item_id:
        hit = next((r for r in content if r.get("id") == item_id), None)
        if hit:
            return [hit]
        # lista pusta (filtr nie wstał) — i tak otwieramy overlay po id/views
        kind = "story" if str(item_id).startswith("story_") else "post"
        views = None
        tail = str(item_id).rsplit("_", 1)[-1]
        if tail.isdigit():
            views = int(tail)
        return [{"id": item_id, "kind": kind, "views": views}]
    if full_charts:
        return [r for r in content if r.get("kind") in ("post", "story")]
    out = []
    for kind in ("post", "story"):
        hit = next((r for r in content if r.get("kind") == kind), None)
        if hit:
            out.append(hit)
    return out


def _sample_item_windows(dev, targets: list[dict], gaps: list,
                         deadline: float) -> None:
    """Wejdź w pozycję i odczytaj wykres z kadru (oś + piksele linii).

    Overlay posta nie ma chipów 7/14/30 — ta sama oś od publikacji.
    Serię zapisujemy w trzech oknach, żeby preview nie ginął przy zmianie chipa.
    """
    for target in targets:
        if time.monotonic() > deadline:
            gaps.append("live: limit czasu — nie wszystkie pozycje")
            return
        if not _open_content_filter(dev, _kind_label(target)):
            gaps.append(f"live: brak filtra {_kind_label(target)} przy {target.get('id')}")
            continue
        time.sleep(0.45)
        # lista po filtrze ma inne Y niż przy pierwszym skanie — odśwież bounds
        listed = parse_content_rows(
            _nodes_from_hier(dev.hierarchy(force=True)),
            default_kind=target.get("kind") or "post")
        match = next(
            (r for r in listed
             if r.get("id") == target.get("id")
             or (r.get("views") == target.get("views")
                 and (r.get("kind") or target.get("kind")) == target.get("kind"))),
            None)
        if match and match.get("age_bounds"):
            target["age_bounds"] = match["age_bounds"]
        if not _tap_content_row(dev, target):
            gaps.append(f"live: nie otwarto {target.get('id')}")
            continue
        time.sleep(0.8)
        nodes = _reveal_item_chart(dev)
        blob = " ".join((n.get("text") or "") for n in nodes).casefold()
        has_line = ("wyświetlenia na przestrzeni" in blob
                    or "wyswietlenia na przestrzeni" in blob
                    or "ten post" in blob)
        if not has_line:
            # relacja: donut bez linii — nie zgadujemy serii
            target.setdefault("window_gaps", {})["line"] = (
                "brak linii Ten post na overlayu")
            target["chart_kind"] = target.get("chart_kind") or "donut"
            _tap_desc(dev, "Wstecz")
            time.sleep(0.35)
            continue
        sampled = sample_item_chart(dev, nodes)
        n_this = len(sampled.get("this_post") or [])
        gaps.append(
            f"live: {target.get('id')} wykres ok={sampled.get('ok')} "
            f"pkt={n_this} {sampled.get('reason') or ''}".strip())
        if sampled.get("this_post"):
            series = sampled["this_post"]
            typical = sampled.get("typical") or []
            target["daily_mode"] = "cumulative"
            target["chart_kind"] = "line"
            # overlay bez chipa okresu — identyczna seria we wszystkich oknach
            target["ig_windows_identical"] = True
            target.setdefault("series_by_window", {})
            target.setdefault("typical_by_window", {})
            for win in ("7", "14", "30"):
                target["series_by_window"][win] = series
                if typical:
                    target["typical_by_window"][win] = typical
        else:
            gaps.append(
                f"live: {target.get('id')} — {sampled.get('reason')}")
        _tap_desc(dev, "Wstecz")
        time.sleep(0.35)


def _reveal_item_chart(dev) -> list[dict]:
    """Przewiń overlay aż widać legendę „Ten post” (wykres bywa pod foldem)."""
    nodes = _nodes_from_hier(dev.hierarchy(force=True))
    for _ in range(4):
        blob = " ".join((n.get("text") or "") for n in nodes).casefold()
        if "ten post" in blob and "wyświetlenia na przestrzeni" in blob:
            return nodes
        if "główne źródła" in blob or "glowne zrodla" in blob:
            return nodes
        dev.swipe(540, 1700, 540, 900, duration_ms=380)
        time.sleep(0.4)
        nodes = _nodes_from_hier(dev.hierarchy(force=True))
    return nodes


def _tap_content_row(dev, row: dict) -> bool:
    """Tap w wiersz po bounds wieku (środek rzędu)."""
    return _tap_bounds(dev, row.get("age_bounds"))


def _tap_text(dev, text: str) -> bool:
    hier = _nodes_from_hier(dev.hierarchy(force=True))
    needle = text.casefold()
    for n in hier:
        if needle in (n.get("text") or "").casefold():
            return _tap_bounds(dev, n.get("bounds"))
    return False


def _tap_desc(dev, desc: str) -> bool:
    hier = _nodes_from_hier(dev.hierarchy(force=True))
    needle = desc.casefold()
    for n in hier:
        if needle in (n.get("desc") or "").casefold():
            return _tap_bounds(dev, n.get("bounds"))
    return False


def _tap_rid(dev, rid: str) -> bool:
    hier = _nodes_from_hier(dev.hierarchy(force=True))
    for n in hier:
        if rid in (n.get("rid") or ""):
            return _tap_bounds(dev, n.get("bounds"))
    return False


def _tap_bounds(dev, bounds) -> bool:
    from v2.data.pro_dash_parse import parse_bounds
    xy = parse_bounds(bounds)
    if not xy:
        return False
    x = (xy[0] + xy[2]) // 2
    y = (xy[1] + xy[3]) // 2
    dev.tap(x, y)
    time.sleep(0.35)
    return True


__all__ = (
    "DEFAULT_ACCOUNT",
    "device_fetch_status",
    "ensure_store",
    "fetch_into",
    "seed_into",
)


def _cli() -> int:
    """CLI: python -m v2.data.pro_dash_fetch jobsaver --charts"""
    import argparse
    parser = argparse.ArgumentParser(
        description="Scrape Insights jobsaver — wykres wyświetleń pozycji.")
    parser.add_argument("account", nargs="?", default=DEFAULT_ACCOUNT)
    parser.add_argument("--charts", action="store_true",
                        help="odczyt linii Ten post / typowy post z overlayu")
    parser.add_argument("--item", default=None, help="tylko ten id zawartości")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    store = ProDashStore(args.account, root / "data")
    view = fetch_into(
        store, args.account, root / "data", root / "config",
        item_id=args.item, full_charts=bool(args.charts),
    )
    fetch = view.get("fetch") or {}
    print("live=", fetch.get("live"), "reason=", fetch.get("reason"),
          "detail=", fetch.get("detail", "")[:200])
    for g in (view.get("gaps") or [])[-8:]:
        print("  gap:", g)
    n = 0
    for kind in ("post", "story", "reel"):
        rows = ((view.get("content") or {}).get(kind) or [])
        n += len(rows)
        for r in rows:
            sw = r.get("series_by_window") or {}
            print(f"  {kind} {r.get('id')} windows={list(sw)} "
                  f"pts7={len(sw.get('7') or {})} kind={r.get('chart_kind')}")
    print("content_n=", n)
    return 0 if fetch.get("live") else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
