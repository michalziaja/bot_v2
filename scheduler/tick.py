"""Pętla schedulera — co `interval_s` sprawdza plan dnia i odpala bloki.

Rozdział odpowiedzialności: KIEDY i CO jest już zdecydowane we wzorcu cyklu
(`scheduler/device_planner.py` układa bloki z policzonym czasem trwania), tick
tylko materializuje dzisiejszy dzień i egzekwuje gotową decyzję. Nie losuje
godzin, nie wybiera scenariusza, nie rozstrzyga kolizji — gdyby to robił, panel
pokazywałby jeden plan, a telefon wykonywał inny.

`is_busy`/`start` są wstrzykiwane, żeby ten sam moduł obsługiwał dwa
uruchomienia: wątek w procesie panelu (przez `SessionManager`) i
`python -m v2.cli scheduler` bez UI (przez bezpośredni subprocess).

`start(account, scenario, max_duration_s)` — `max_duration_s` to reszta okna
bloku. Blok odpalony z opóźnieniem dostaje krótszą ramę, żeby i tak skończył
się o czasie i nie wjechał w slot następnego konta.
"""
from __future__ import annotations

import random
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from v2.scheduler import device_planner as dp
from v2.scheduler import state as sched_state

# Ile minut po starcie bloku wolno go jeszcze odpalić. Poza tym oknem blok
# jest oznaczany jako `missed` — widoczny w panelu, a nie znikający po cichu
# (stara wersja gubiła slot bez śladu, gdy urządzenie było zajęte).
GRACE_MIN = 5.0


def tick_once(data_root: Path, config_root: Path,
              is_busy: Callable[[], bool],
              start: Callable[..., None],
              rng: random.Random | None = None,
              now: datetime | None = None) -> list[dict]:
    """Jedno przejście po wszystkich urządzeniach. Zwraca listę podjętych
    (albo pominiętych) akcji — do logowania przez wywołującego."""
    rng = rng or random.Random()
    now = now or datetime.now()
    actions: list[dict] = []

    if not sched_state.is_enabled(data_root):
        return actions

    today = now.date().isoformat()
    for device in dp.list_devices(config_root):
        if not dp.accounts_for_device(config_root, device):
            continue

        plan = dp.materialize(device, data_root, config_root, today, rng=rng)

        for b in dp.expire_missed(device, data_root, plan, now, GRACE_MIN):
            actions.append({"device": device, "account": b["account"],
                            "block": b["id"], "start": b["start"], "result": "missed"})

        block = dp.due_block(plan, now, GRACE_MIN)
        if not block:
            continue

        if is_busy():
            # Blok zostaje `planned` — jeśli urządzenie zwolni się w oknie
            # łaski, jeszcze zdąży; poza nim złapie go `expire_missed`.
            actions.append({"device": device, "account": block["account"],
                            "block": block["id"], "start": block["start"],
                            "result": "device_busy"})
            continue

        queue_name = _materialize_queue(block)
        remaining = dp.remaining_seconds(block, now)
        try:
            start(block["account"], queue_name, remaining)
        except Exception as e:
            dp.mark_block(device, data_root, today, block["id"], "failed", error=str(e))
            actions.append({"device": device, "account": block["account"],
                            "block": block["id"], "start": block["start"],
                            "result": "start_failed", "error": str(e)})
            continue

        dp.mark_block(device, data_root, today, block["id"], "running",
                      started_at=now.isoformat(timespec="seconds"))
        actions.append({"device": device, "account": block["account"],
                        "block": block["id"], "start": block["start"],
                        "result": "started", "scenario": queue_name,
                        "max_duration_s": round(remaining)})

    return actions


def _materialize_queue(block: dict) -> str:
    """Blok z własną kopią zadań zapisuje się jako kolejka efemeryczna —
    JobRunner dostaje zwykły scenariusz i nie musi znać pojęcia bloku."""
    from v2.plugins import store
    override = block.get("queue_override")
    if not override:
        return block.get("scenario") or store.DEFAULT_SCENARIO
    return store.write_slot_queue(block["account"], block["id"], override)


def finish_running_blocks(data_root: Path, config_root: Path,
                          now: datetime | None = None) -> list[dict]:
    """Domknij bloki, które zostały w stanie `running`, choć ich okno minęło.

    Sesja kończy się w subprocessie, o którym plan nic nie wie; bez tego
    sprzątania blok wisiałby jako `running` do końca dnia i blokował widok.
    """
    now = now or datetime.now()
    today = now.date().isoformat()
    closed = []
    for device in dp.list_devices(config_root):
        plan = dp.read_plan(device, data_root, today)
        if not plan:
            continue
        for b in plan.get("blocks", []):
            if b.get("status") != "running":
                continue
            if dp.remaining_seconds(b, now) > 0:
                continue
            dp.mark_block(device, data_root, today, b["id"], "done",
                          ended_at=now.isoformat(timespec="seconds"))
            _drop_slot_queue(b)
            closed.append({"device": device, "block": b["id"], "account": b["account"]})
    return closed


def _drop_slot_queue(block: dict) -> None:
    """Materializacja bloku żyje tylko na czas jego wykonania — inaczej
    katalog scenariuszy puchłby o plik na każdą sesję."""
    if not block.get("queue_override"):
        return
    from v2.plugins import store
    try:
        store.drop_slot_queue(block["account"], block["id"])
    except Exception:
        pass


def run_forever(data_root: Path, config_root: Path,
                is_busy: Callable[[], bool],
                start: Callable[..., None],
                interval_s: int = 60) -> None:
    print(f"[scheduler] start — sprawdzanie co {interval_s}s")
    while True:
        try:
            finish_running_blocks(data_root, config_root)
            actions = tick_once(data_root, config_root, is_busy, start)
            for a in actions:
                extra = f"  scenario={a['scenario']}" if a.get("scenario") else ""
                print(f"[scheduler] {a['account']}  blok={a['start']}  {a['result']}{extra}")
        except Exception as e:
            print(f"[scheduler] tick error: {e}")
        time.sleep(interval_s)
