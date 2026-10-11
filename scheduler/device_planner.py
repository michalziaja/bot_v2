"""Harmonogram urządzenia: powtarzalny cykl dni + materializacja konkretnej daty.

**Czemu plan jest na urządzenie, nie na konto.** Jeden telefon obsługuje kilka
kont, ale w danej chwili tylko jedno: sesje sterują tą samą aplikacją, więc dwie
naraz to przypadkowe tapnięcia w cudzym ekranie. Wcześniej każde konto planowało
w izolacji i kolizję wykrywał dopiero runtime — slot, który trafił na zajęte
urządzenie, przepadał bez ponowienia. Tutaj slot jest ODCINKIEM: ma start
i policzony czas trwania (`scheduler/estimator.py`), więc kolizję widać już
przy układaniu.

**Czemu cykl, a nie kalendarz.** Użytkownik projektuje RYTM („tak wygląda mój
tydzień"), nie listę dat. Plan jest więc wzorcem 1, 3 albo 7 dni
(`scheduler/state.cycle_days`), który się powtarza. Dwie warstwy:

    data/devices/<alias>/cycle.json           ← wzorzec, to się edytuje
    data/devices/<alias>/schedule/<data>.json ← konkretny dzień, to się wykonuje

Wzorzec nie ma statusów — te powstają dopiero przy materializacji daty
(`materialize`). Dzięki temu „wykonane wczoraj" nie zabija bloku na jutro,
a edycja wzorca działa od najbliższego powtórzenia.

Blok:
    id             — stabilny identyfikator (przetrwa przesunięcie i edycję)
    account        — które konto
    scenario       — nazwa scenariusza bazowego
    start          — "HH:MM"
    duration_s     — zarezerwowane okno; ta sama liczba idzie do JobRunnera
                     jako `--max-duration-s`, więc rezerwacja jest wiążąca
    queue_override — None = weź `.queues/<scenario>.json`; słownik = własna
                     kopia zadań tego bloku (edycja per slot nie rusza
                     scenariusza bazowego ani innych bloków)
    locked         — użytkownik ustawił ręcznie; regeneracja cyklu go omija
    status         — tylko w zmaterializowanym dniu:
                     planned | running | done | failed | missed
"""
from __future__ import annotations

import json
import math
import random
import uuid
from datetime import date as date_cls, datetime
from pathlib import Path

from v2.plugins.store import DEFAULT_SCENARIO
from v2.scheduler import state as sched_state
from v2.scheduler.planner import hhmm_to_min, min_to_hhmm, pick_scenario

CYCLE_VERSION = 3
PLAN_VERSION = 3

DEFAULT_SCHEDULE = {
    "enabled": False,          # czy konto uczestniczy w planie
    "sessions_per_day": {"min": 1, "max": 1},
    "scenarios": [],           # puste = zawsze scenariusz domyślny
}

# Odstęp między sesjami RÓŻNYCH kont na tym samym telefonie. Nie jest to
# margines bezpieczeństwa techniczny (od tego jest bufor w wycenie), tylko
# realizm: dwa konta przełączające się w tej samej sekundzie to wzorzec.
MIN_GAP_RANGE_S = (300, 1200)

# Ile prób losowego umieszczenia bloku, zanim przejdziemy na układanie
# sekwencyjne (pierwsza wolna szczelina). Losowanie daje nieregularne godziny;
# fallback gwarantuje, że dzień da się zapełnić także przy ciasnym oknie.
PLACEMENT_ATTEMPTS = 200

DAY_MIN = 24 * 60

DAY_LABELS_7 = ["Poniedziałek", "Wtorek", "Środa", "Czwartek",
                "Piątek", "Sobota", "Niedziela"]


class PlanError(Exception):
    """Operacja na planie niemożliwa — np. kolizja albo brak bloku."""


# ============================================================
# cykl: który dzień wzorca odpowiada dacie
# ============================================================
def cycle_index(date: str | date_cls, cycle_days: int) -> int:
    """Cykl 7-dniowy wyrównujemy do dni tygodnia — „poniedziałek" w planie ma
    być naprawdę poniedziałkiem. Krótsze cykle to czysty rytm, więc liczą się
    modulo z numeru dnia."""
    d = date_cls.fromisoformat(date) if isinstance(date, str) else date
    if cycle_days == 7:
        return d.weekday()
    return d.toordinal() % max(cycle_days, 1)


def day_label(index: int, cycle_days: int) -> str:
    if cycle_days == 1:
        return "Codziennie"
    if cycle_days == 7:
        return DAY_LABELS_7[index]
    return f"Dzień {index + 1}"


# ============================================================
# ścieżki i I/O
# ============================================================
def cycle_path(device: str, data_root: Path) -> Path:
    return Path(data_root) / "devices" / device / "cycle.json"


def plan_path(device: str, data_root: Path, date: str) -> Path:
    return Path(data_root) / "devices" / device / "schedule" / f"{date}.json"


def _write_json(path: Path, data: dict) -> dict:
    """Zapis przez plik tymczasowy — przerwany zapis nie zostawia planu
    w połowie, bo tick czyta te same pliki co panel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return data


def _read_json(path: Path, version: int) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if data.get("version") == version else None


# ============================================================
# konfiguracja kont przypisanych do urządzenia
# ============================================================
def _read_account_raw(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_devices(config_root: Path) -> list[str]:
    return sorted(p.stem for p in (Path(config_root) / "devices").glob("*.json"))


def accounts_for_device(config_root: Path, device: str,
                        only_enabled: bool = True) -> list[dict]:
    """Konta przypisane do urządzenia wraz z ich sekcją `schedule`.

    Czytamy surowy JSON zamiast `load_account`, żeby planowanie działało też
    wtedy, gdy config konta ma braki blokujące uruchomienie sesji — plan ma
    się dać obejrzeć i poprawić w panelu, a nie wybuchać.
    """
    out = []
    for p in sorted((Path(config_root) / "accounts").glob("*.json")):
        if p.stem.endswith(".queue") or p.stem.endswith(".bak"):
            continue
        cfg = _read_account_raw(p)
        if not cfg:
            continue
        if (cfg.get("device") or {}).get("device_alias") != device:
            continue
        sch = {**DEFAULT_SCHEDULE, **(cfg.get("schedule") or {})}
        if only_enabled and not sch.get("enabled"):
            continue
        out.append({"account": p.stem, "schedule": sch})
    return out


def device_for_account(config_root: Path, account: str) -> str | None:
    cfg = _read_account_raw(Path(config_root) / "accounts" / f"{account}.json")
    if not cfg:
        return None
    return (cfg.get("device") or {}).get("device_alias")


def known_account_ids(config_root: Path) -> set[str]:
    """Alias'e z `config/accounts/*.json` (bez .bak / .queue)."""
    out: set[str] = set()
    for p in (Path(config_root) / "accounts").glob("*.json"):
        if p.stem.endswith(".queue") or p.stem.endswith(".bak"):
            continue
        out.add(p.stem)
    return out


def _block_overlaps_window(b: dict, win: tuple[int, int]) -> bool:
    """Czy blok nachodzi na godziny działania — poza oknem nie widać go na osi
    i nie powinien zużywać puli sessions_per_day."""
    start = block_start_min(b)
    end = block_end_min(b)
    return end > win[0] and start < win[1]


# ============================================================
# sprzątanie orphanów / czyszczenie osi
# ============================================================
def scrub_orphan_blocks(device: str, data_root: Path, config_root: Path,
                        *, known: set[str] | None = None) -> dict:
    """Usuń ze wzorca i zmaterializowanych dni bloki kont, których już nie ma
    w config/accounts/. Po usunięciu konta wcześniej zostawały locked orphan'y
    (np. a50_clone3) — psuły limity i zajmowały sloty regeneracji."""
    known = known if known is not None else known_account_ids(config_root)
    removed_cycle = 0
    removed_plans = 0
    cycle = read_cycle(device, data_root)
    if cycle:
        dirty = False
        for day in cycle.get("days", []):
            before = day.get("blocks") or []
            after = [b for b in before if b.get("account") in known]
            if len(after) != len(before):
                removed_cycle += len(before) - len(after)
                day["blocks"] = after
                dirty = True
        if dirty:
            annotate_cycle(cycle)
            _write_json(cycle_path(device, data_root), cycle)

    sched_dir = Path(data_root) / "devices" / device / "schedule"
    if sched_dir.is_dir():
        for p in sched_dir.glob("*.json"):
            plan = _read_json(p, PLAN_VERSION)
            if not plan:
                continue
            before = plan.get("blocks") or []
            after = [b for b in before if b.get("account") in known]
            if len(after) != len(before):
                removed_plans += len(before) - len(after)
                plan["blocks"] = after
                _write_json(p, plan)

    return {"device": device, "removed_cycle": removed_cycle,
            "removed_plans": removed_plans}


def purge_account_from_plans(account: str, data_root: Path,
                             config_root: Path) -> dict:
    """Po DELETE konta — wyrzuć jego bloki ze wszystkich urządzeń."""
    devices = list_devices(config_root)
    # katalogi planu mogą istnieć także dla urządzeń już skasowanych z configu
    devices_root = Path(data_root) / "devices"
    if devices_root.is_dir():
        for p in devices_root.iterdir():
            if p.is_dir() and p.name not in devices:
                devices.append(p.name)
    total = {"account": account, "devices": [], "removed_cycle": 0,
             "removed_plans": 0}
    # known = wszystkie konta MINUS właśnie usuwane (plik config mógł już zniknąć)
    known = known_account_ids(config_root) - {account}
    for dev in devices:
        r = scrub_orphan_blocks(dev, data_root, config_root, known=known)
        if r["removed_cycle"] or r["removed_plans"]:
            total["devices"].append(dev)
            total["removed_cycle"] += r["removed_cycle"]
            total["removed_plans"] += r["removed_plans"]
    return total


def clear_device_timeline(device: str, data_root: Path) -> dict:
    """Wyczyść całą oś urządzenia: cycle.json + schedule/*.json."""
    removed = {"cycle": False, "schedule_files": 0}
    cp = cycle_path(device, data_root)
    if cp.exists():
        try:
            cp.unlink()
            removed["cycle"] = True
        except OSError:
            pass
    sched_dir = Path(data_root) / "devices" / device / "schedule"
    if sched_dir.is_dir():
        for p in list(sched_dir.glob("*.json")) + list(sched_dir.glob("*.json.tmp")):
            try:
                p.unlink()
                removed["schedule_files"] += 1
            except OSError:
                pass
    return {"ok": True, "device": device, "removed": removed}


# ============================================================
# geometria bloków
# ============================================================
def block_start_min(b: dict) -> int:
    return hhmm_to_min(b["start"])


def block_end_min(b: dict) -> int:
    """Koniec zaokrąglony w górę do minuty — rezerwacja ma być nie mniejsza
    niż realne okno, nigdy mniejsza."""
    return block_start_min(b) + math.ceil(float(b.get("duration_s") or 0) / 60)


def block_gap_min(b: dict) -> int:
    return math.ceil(float(b.get("gap_s") or MIN_GAP_RANGE_S[0]) / 60)


def blocks_conflict(a: dict, b: dict) -> bool:
    """Dwa bloki kolidują, gdy ich okna (powiększone o wymagany odstęp)
    zachodzą na siebie."""
    gap = max(block_gap_min(a), block_gap_min(b))
    return (block_start_min(a) < block_end_min(b) + gap
            and block_start_min(b) < block_end_min(a) + gap)


def find_collisions(blocks: list[dict]) -> list[dict]:
    """Pary kolidujących bloków — panel podświetla je na czerwono i proponuje
    rozsunięcie."""
    out = []
    ordered = sorted(blocks, key=block_start_min)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if block_start_min(b) >= block_end_min(a) + max(block_gap_min(a), block_gap_min(b)):
                break   # posortowane — dalsze są jeszcze później
            if blocks_conflict(a, b):
                overlap = (min(block_end_min(a), block_end_min(b))
                           - max(block_start_min(a), block_start_min(b)))
                out.append({"a": a["id"], "b": b["id"],
                            "a_account": a["account"], "b_account": b["account"],
                            "overlap_min": max(overlap, 0)})
    return out


def _fits(candidate_start: int, block: dict, placed: list[dict],
          win: tuple[int, int]) -> bool:
    probe = {**block, "start": min_to_hhmm(candidate_start)}
    if candidate_start < win[0] or block_end_min(probe) > win[1]:
        return False
    return not any(blocks_conflict(probe, other) for other in placed)


def _place(block: dict, placed: list[dict], win: tuple[int, int],
           rng: random.Random) -> int | None:
    """Znajdź start dla bloku: najpierw losowo (nieregularne godziny),
    a gdy okno jest ciasne — pierwsza wolna szczelina od początku."""
    span = win[1] - win[0] - math.ceil(float(block["duration_s"]) / 60)
    if span < 0:
        return None

    for _ in range(PLACEMENT_ATTEMPTS):
        cand = win[0] + rng.randint(0, span)
        if _fits(cand, block, placed, win):
            return cand

    # fallback sekwencyjny — próbujemy tuż za każdym już umieszczonym blokiem
    candidates = [win[0]]
    for other in sorted(placed, key=block_start_min):
        gap = max(block_gap_min(other), block_gap_min(block))
        candidates.append(block_end_min(other) + gap)
    for cand in candidates:
        if _fits(cand, block, placed, win):
            return cand
    return None


# ============================================================
# budowa cyklu
# ============================================================
def _new_block_id() -> str:
    return f"b{uuid.uuid4().hex[:6]}"


def _default_estimate(account: str, scenario: str, queue_override: dict | None) -> dict:
    from v2.scheduler.estimator import estimate_queue
    return estimate_queue(account, scenario, queue_data=queue_override)


def _est_summary(est: dict) -> dict:
    return {"breakdown": est.get("breakdown", {}),
            "task_count": est.get("task_count", 0),
            "warnings": est.get("warnings", [])}


def _build_day(entries: list[dict], win: tuple[int, int], kept: list[dict],
               rng: random.Random, estimate_fn) -> dict:
    """Ułóż jeden dzień wzorca: wszystkie sesje wszystkich kont na jednej osi."""
    sessions = []
    for entry in entries:
        acc, sch = entry["account"], entry["schedule"]
        n_cfg = sch.get("sessions_per_day") or {"min": 1, "max": 1}
        lo, hi = int(n_cfg.get("min", 1)), int(n_cfg.get("max", 1))
        n = rng.randint(min(lo, hi), max(lo, hi))
        # locked poza oknem nie widać na osi — nie zużywają puli sesji
        n -= sum(1 for b in kept
                 if b["account"] == acc and _block_overlaps_window(b, win))

        for _ in range(max(n, 0)):
            scenario = pick_scenario(sch, rng)
            try:
                est = estimate_fn(acc, scenario, None)
            except Exception as e:
                sessions.append({"account": acc, "scenario": scenario,
                                 "error": f"wycena nieudana: {e}"})
                continue
            sessions.append({"account": acc, "scenario": scenario,
                             "duration_s": est["total_s"], "est": _est_summary(est),
                             "gap_s": rng.randint(*MIN_GAP_RANGE_S)})

    # poza oknem nie trzymamy locked — inaczej „duch” o 05:14 blokuje widok
    placed = [b for b in kept if _block_overlaps_window(b, win)]
    dropped = []
    # najdłuższe najpierw — krótkie sesje łatwiej domknąć w resztkach okna
    for s in sorted(sessions, key=lambda x: -(x.get("duration_s") or 0)):
        if s.get("error"):
            dropped.append({**s, "reason": s["error"]})
            continue
        block = {
            "id": _new_block_id(), "account": s["account"], "scenario": s["scenario"],
            "start": min_to_hhmm(win[0]), "duration_s": s["duration_s"],
            "gap_s": s["gap_s"], "est": s["est"], "queue_override": None,
            "locked": False,
        }
        start = _place(block, placed, win, rng)
        if start is None:
            dropped.append({"account": s["account"], "scenario": s["scenario"],
                            "duration_s": s["duration_s"],
                            "reason": "nie mieści się w godzinach działania "
                                      "bez kolizji z inną sesją"})
            continue
        block["start"] = min_to_hhmm(start)
        placed.append(block)

    return {"blocks": sorted(placed, key=block_start_min), "dropped": dropped}


def build_cycle(device: str, data_root: Path, config_root: Path,
                rng: random.Random | None = None, estimate_fn=None,
                keep_locked: bool = True) -> dict:
    """Ułóż cały wzorzec od nowa. Bloki `locked` zostają tam, gdzie były —
    ręczne ustawienie użytkownika przeżywa regenerację.

    Orphan'y (konta usunięte z config/accounts) oraz locked poza oknem godzin
    działania są pomijane — nie zajmują slotów ani nie psują limitów w UI.
    """
    rng = rng or random.Random()
    estimate_fn = estimate_fn or _default_estimate

    # najpierw zdysku wyrzuć bloki usuniętych kont (także z schedule/*.json)
    scrub_orphan_blocks(device, data_root, config_root)

    settings = sched_state.read(data_root)
    n_days = settings["cycle_days"]
    win = (settings["active_hours"][0] * 60, min(settings["active_hours"][1] * 60, DAY_MIN))
    entries = accounts_for_device(config_root, device)
    known = known_account_ids(config_root)

    prev = read_cycle(device, data_root) or {}
    prev_days = {d["index"]: d for d in prev.get("days", [])}

    days = []
    for i in range(n_days):
        kept = [b for b in (prev_days.get(i, {}).get("blocks") or [])
                if keep_locked and b.get("locked") and b.get("account") in known]
        day = _build_day(entries, win, kept, rng, estimate_fn)
        days.append({"index": i, **day})

    cycle = {
        "version": CYCLE_VERSION,
        "device": device,
        "cycle_days": n_days,
        "active_hours": settings["active_hours"],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": days,
    }
    annotate_cycle(cycle)
    return _write_json(cycle_path(device, data_root), cycle)


def read_cycle(device: str, data_root: Path) -> dict | None:
    return _read_json(cycle_path(device, data_root), CYCLE_VERSION)


def load_or_build_cycle(device: str, data_root: Path, config_root: Path,
                        rng: random.Random | None = None,
                        estimate_fn=None) -> dict:
    """Wzorzec z dysku, albo świeżo ułożony. Zmiana długości cyklu lub godzin
    działania unieważnia stary wzorzec — inaczej panel pokazywałby 7 kolumn
    z planu, który powstał dla 3 dni.

    Przy odczycie filtrujemy orphan'y; jeśli coś usunięto albo zostały bloki
    poza oknem godzin — budujemy wzorzec od nowa (enabled konta wracają na oś).
    """
    scrub = scrub_orphan_blocks(device, data_root, config_root)
    settings = sched_state.read(data_root)
    cycle = read_cycle(device, data_root)
    win = (settings["active_hours"][0] * 60,
           min(settings["active_hours"][1] * 60, DAY_MIN))

    need_rebuild = scrub["removed_cycle"] > 0
    if cycle and not need_rebuild:
        # locked poza oknem „zjada” sessions_per_day i znika z widoku osi
        for day in cycle.get("days", []):
            blocks = day.get("blocks") or []
            if any(not _block_overlaps_window(b, win) for b in blocks):
                need_rebuild = True
                break

    if (not need_rebuild and cycle
            and cycle.get("cycle_days") == settings["cycle_days"]
            and cycle.get("active_hours") == settings["active_hours"]):
        return annotate_cycle(cycle)
    return annotate_cycle(build_cycle(device, data_root, config_root, rng, estimate_fn))


def annotate_cycle(cycle: dict) -> dict:
    """Dolicz to, czego nie trzymamy w pliku: kolizje i obciążenie okna."""
    h = cycle.get("active_hours") or [0, 24]
    span = max((h[1] - h[0]) * 60, 1)
    for day in cycle.get("days", []):
        blocks = day.get("blocks", [])
        day["collisions"] = find_collisions(blocks)
        used = sum(block_end_min(b) - block_start_min(b) + block_gap_min(b) for b in blocks)
        day["load_pct"] = round(used / span * 100)
        day["label"] = day_label(day["index"], cycle.get("cycle_days", 1))
    cycle["window"] = {"from_min": h[0] * 60, "to_min": h[1] * 60}
    return cycle


# ============================================================
# edycja wzorca (oś czasu w panelu)
# ============================================================
def _load_cycle_for_edit(device: str, data_root: Path) -> dict:
    cycle = read_cycle(device, data_root)
    if cycle is None:
        raise PlanError(f"brak wzorca harmonogramu dla urządzenia '{device}' — "
                        f"ułóż go najpierw")
    return cycle


def _day(cycle: dict, index: int) -> dict:
    d = next((x for x in cycle["days"] if x["index"] == index), None)
    if d is None:
        raise PlanError(f"cykl nie ma dnia o indeksie {index}")
    return d


def _find(day: dict, block_id: str) -> dict:
    b = next((x for x in day["blocks"] if x["id"] == block_id), None)
    if b is None:
        raise PlanError(f"brak bloku {block_id} w dniu {day['index']}")
    return b


def _save_cycle(cycle: dict, device: str, data_root: Path,
                config_root: Path | None = None,
                force_resync_ids: set[str] | None = None) -> dict:
    for day in cycle["days"]:
        day["blocks"].sort(key=block_start_min)
    annotate_cycle(cycle)
    _write_json(cycle_path(device, data_root), cycle)
    if config_root is not None:
        resync_date(device, data_root, config_root,
                    force_ids=force_resync_ids)
    return cycle


def move_block(device: str, data_root: Path, config_root: Path, day_index: int,
               block_id: str, start: str, *, force: bool = False) -> dict:
    """Przesuń blok we wzorcu. Bez `force` odmawiamy, gdy powstałaby kolizja —
    plan, który sam sobie przeczy, jest gorszy niż odrzucona zmiana."""
    cycle = _load_cycle_for_edit(device, data_root)
    day = _day(cycle, day_index)
    block = _find(day, block_id)
    probe = {**block, "start": start}
    clash = [o for o in day["blocks"] if o["id"] != block_id and blocks_conflict(probe, o)]
    if clash and not force:
        who = ", ".join(f"{o['account']} {o['start']}" for o in clash)
        raise PlanError(f"kolizja z: {who}")
    block["start"] = start
    block["locked"] = True     # ręczne ustawienie przeżywa regenerację
    return _save_cycle(cycle, device, data_root, config_root,
                       force_resync_ids={block_id})


def set_block_queue(device: str, data_root: Path, config_root: Path, day_index: int,
                    block_id: str, *, scenario: str | None = None,
                    queue_override: dict | None = None,
                    account: str | None = None,
                    estimate_fn=None) -> dict:
    """Podmień zawartość bloku i przelicz jego czas trwania.

    `queue_override=None` odpina kopię i wraca do scenariusza bazowego;
    słownik zapisuje własną kopię zadań, nie ruszając pliku scenariusza.
    """
    estimate_fn = estimate_fn or _default_estimate
    cycle = _load_cycle_for_edit(device, data_root)
    block = _find(_day(cycle, day_index), block_id)

    if account:
        block["account"] = account
    if scenario is not None:
        block["scenario"] = scenario
    block["queue_override"] = queue_override

    est = estimate_fn(block["account"], block["scenario"], queue_override)
    block["duration_s"] = est["total_s"]
    block["est"] = _est_summary(est)
    block["locked"] = True
    return _save_cycle(cycle, device, data_root, config_root,
                       force_resync_ids={block_id})


def add_block(device: str, data_root: Path, config_root: Path, day_index: int,
              account: str, scenario: str | None = None, start: str = "12:00",
              estimate_fn=None) -> dict:
    """Ręczny slot na osi: zawsze `locked`, żeby auto-plan go respektował.

    Przy regeneracji cyklu `_build_day` odejmuje locked bloki od puli
    `sessions_per_day` — ręczne dodanie nie znika i nie dubluje się z auto.
    """
    estimate_fn = estimate_fn or _default_estimate
    cycle = _load_cycle_for_edit(device, data_root)
    day = _day(cycle, day_index)
    scenario = scenario or DEFAULT_SCENARIO
    est = estimate_fn(account, scenario, None)
    day["blocks"].append({
        "id": _new_block_id(), "account": account, "scenario": scenario,
        "start": start, "duration_s": est["total_s"], "gap_s": MIN_GAP_RANGE_S[0],
        "est": _est_summary(est), "queue_override": None, "locked": True,
    })
    return _save_cycle(cycle, device, data_root, config_root)


def delete_block(device: str, data_root: Path, config_root: Path,
                 day_index: int, block_id: str) -> dict:
    cycle = _load_cycle_for_edit(device, data_root)
    day = _day(cycle, day_index)
    _find(day, block_id)
    day["blocks"] = [b for b in day["blocks"] if b["id"] != block_id]
    return _save_cycle(cycle, device, data_root, config_root)


def set_block_locked(device: str, data_root: Path, config_root: Path,
                     day_index: int, block_id: str, locked: bool) -> dict:
    """Przypnij / odepnij blok — regeneracja omija tylko `locked=True`.

    Osobny helper, bo `move` / `queue` zawsze ustawiają kłódkę (ręczna edycja
    ma przetrwać ułożenie), a inspector musi umieć ją zdjąć.
    """
    cycle = _load_cycle_for_edit(device, data_root)
    block = _find(_day(cycle, day_index), block_id)
    block["locked"] = bool(locked)
    return _save_cycle(cycle, device, data_root, config_root,
                       force_resync_ids={block_id})


def autospread(device: str, data_root: Path, config_root: Path,
               day_index: int) -> dict:
    """Rozsuń kolidujące bloki, zachowując kolejność — przycisk „rozsuń"
    w panelu. Wcześniejsze zostają, późniejsze przesuwają się w dół."""
    cycle = _load_cycle_for_edit(device, data_root)
    day = _day(cycle, day_index)
    ordered = sorted(day["blocks"], key=block_start_min)
    prev = None
    moved_ids: set[str] = set()
    for b in ordered:
        if prev is not None:
            # odstęp musi zadowolić OBA bloki — inaczej rozsunięcie zostawia
            # kolizję, którą samo miało usunąć
            earliest = block_end_min(prev) + max(block_gap_min(prev), block_gap_min(b))
            if block_start_min(b) < earliest:
                b["start"] = min_to_hhmm(earliest)
                b["locked"] = True
                moved_ids.add(b["id"])
        prev = b
    day["blocks"] = ordered
    return _save_cycle(cycle, device, data_root, config_root,
                       force_resync_ids=moved_ids or None)


# ============================================================
# materializacja konkretnej daty
# ============================================================
def read_plan(device: str, data_root: Path, date: str) -> dict | None:
    return _read_json(plan_path(device, data_root, date), PLAN_VERSION)


def materialize(device: str, data_root: Path, config_root: Path,
                date: str | None = None, rng: random.Random | None = None,
                estimate_fn=None) -> dict:
    """Plan konkretnej daty: z dysku, albo odbity ze wzorca cyklu.

    Wzorzec nie ma statusów — dostają je dopiero bloki tego dnia. To rozdzielenie
    jest po to, żeby powtarzalny plan dało się wykonywać każdego dnia od nowa
    i żeby edycja wzorca nie kasowała historii dnia, który już trwa.
    """
    date = date or date_cls.today().isoformat()
    existing = read_plan(device, data_root, date)
    if existing is not None:
        return existing

    cycle = load_or_build_cycle(device, data_root, config_root, rng, estimate_fn)
    idx = cycle_index(date, cycle["cycle_days"])
    template = next((d for d in cycle["days"] if d["index"] == idx), {"blocks": [], "dropped": []})
    return _write_json(plan_path(device, data_root, date),
                       _plan_from_template(device, date, idx, template, cycle))


def _plan_from_template(device: str, date: str, idx: int,
                        template: dict, cycle: dict) -> dict:
    return {
        "version": PLAN_VERSION,
        "date": date,
        "device": device,
        "cycle_index": idx,
        "active_hours": cycle.get("active_hours"),
        "materialized_at": datetime.now().isoformat(timespec="seconds"),
        "blocks": [{**json.loads(json.dumps(b)), "status": "planned",
                    "started_at": None, "ended_at": None}
                   for b in template.get("blocks", [])],
        "dropped": template.get("dropped", []),
    }


def resync_date(device: str, data_root: Path, config_root: Path,
                date: str | None = None,
                force_ids: set[str] | None = None) -> dict | None:
    """Przenieś zmiany wzorca na dzień, który już został zmaterializowany.

    Domyślnie bloki, które zdążyły wystartować (status ≠ planned), zostają
    nietknięte — inaczej edycja *innego* slotu kasowałaby ślad po sesji.
    `force_ids` to wyjątek: jawna edycja / przesunięcie konkretnego bloku
    (także done/missed/failed) nadpisuje pola ze wzorca. `missed`/`failed`
    wracają do `planned`, żeby dało się je odpalić ponownie; `done` zachowuje
    status historii; `running` nigdy nie jest ruszany.
    """
    date = date or date_cls.today().isoformat()
    force_ids = set(force_ids or ())
    plan = read_plan(device, data_root, date)
    if plan is None:
        return None
    cycle = read_cycle(device, data_root)
    if cycle is None:
        return plan

    idx = cycle_index(date, cycle["cycle_days"])
    template = next((d for d in cycle["days"] if d["index"] == idx), {"blocks": []})
    touched = {b["id"]: b for b in plan["blocks"] if b.get("status") != "planned"}

    fresh = _plan_from_template(device, date, idx, template, cycle)
    merged: list[dict] = []
    for b in fresh["blocks"]:
        old = touched.get(b["id"])
        if old is None:
            merged.append(b)
            continue
        if old.get("status") == "running":
            # sesja w toku — pełna ochrona, nawet przy force
            merged.append(old)
            continue
        if b["id"] in force_ids:
            # jawna edycja tego bloku: pola wzorca + sensowny status
            nb = {**b}
            st = old.get("status")
            if st in ("missed", "failed"):
                nb["status"] = "planned"
                nb["started_at"] = None
                nb["ended_at"] = None
            else:
                # done (i inne): zachowaj historię runtime, przyjmij start/scenariusz
                nb["status"] = st
                nb["started_at"] = old.get("started_at")
                nb["ended_at"] = old.get("ended_at")
            merged.append(nb)
        else:
            merged.append(old)

    fresh["blocks"] = merged
    # blok usunięty ze wzorca, ale już wykonany dziś — zostaje jako historia
    known = {b["id"] for b in fresh["blocks"]}
    fresh["blocks"] += [b for bid, b in touched.items() if bid not in known]
    fresh["blocks"].sort(key=block_start_min)
    return _write_json(plan_path(device, data_root, date), fresh)


def mark_block(device: str, data_root: Path, date: str, block_id: str,
               status: str, **extra) -> dict:
    plan = read_plan(device, data_root, date)
    if plan is None:
        raise PlanError(f"brak zmaterializowanego planu na {date}")
    block = next((b for b in plan["blocks"] if b["id"] == block_id), None)
    if block is None:
        raise PlanError(f"brak bloku {block_id} w planie {date}")
    block["status"] = status
    block.update(extra)
    return _write_json(plan_path(device, data_root, date), plan)


# ============================================================
# egzekucja — co należy uruchomić teraz
# ============================================================
def due_block(plan: dict, now: datetime, grace_min: float = 5.0) -> dict | None:
    """Blok, który powinien już działać: start minął o nie więcej niż
    `grace_min`. Wcześniej niż start NIE startujemy — okno następnego konta
    zaczyna się dokładnie tam, gdzie kończy się to."""
    now_min = now.hour * 60 + now.minute + now.second / 60
    for b in sorted(plan.get("blocks", []), key=block_start_min):
        if b.get("status") != "planned":
            continue
        start = block_start_min(b)
        if start <= now_min <= start + grace_min:
            return b
    return None


def expire_missed(device: str, data_root: Path, plan: dict, now: datetime,
                  grace_min: float = 5.0) -> list[dict]:
    """Oznacz przeterminowane bloki jako `missed`, zamiast pozwolić im zniknąć
    po cichu. Przegapiony slot musi być widoczny w panelu — to sygnał, że dzień
    jest przeładowany albo scheduler stał."""
    now_min = now.hour * 60 + now.minute + now.second / 60
    missed = []
    for b in plan.get("blocks", []):
        if b.get("status") == "planned" and block_start_min(b) + grace_min < now_min:
            b["status"] = "missed"
            missed.append(b)
    if missed:
        _write_json(plan_path(device, data_root, plan["date"]), plan)
    return missed


def remaining_seconds(block: dict, now: datetime) -> float:
    """Ile z okna bloku faktycznie zostało. Blok odpalony z opóźnieniem
    dostaje krótszą ramę, żeby nadal skończył się o czasie."""
    now_min = now.hour * 60 + now.minute + now.second / 60
    return max((block_end_min(block) - now_min) * 60, 0.0)
