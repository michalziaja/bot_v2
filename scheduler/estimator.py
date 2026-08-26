"""Ile MAKSYMALNIE potrwa scenariusz — podstawa układania nienachodzących
na siebie sesji kilku kont na jednym telefonie.

Bez tej liczby harmonogram zna tylko punkt startu i nie ma jak stwierdzić, czy
blok konta A skończy się przed startem konta B. Liczymy GÓRNE ograniczenie,
nie wartość oczekiwaną — lepiej zarezerwować za dużo i mieć luki niż wjechać
sesją w sesję (jedno urządzenie, jedna aplikacja, konflikt kończy się
przypadkowymi tapnięciami w cudzej sesji).

Składniki (patrz `plugins/runner.py:JobRunner.run`):
    Σ budget.max_duration_min zadań, które faktycznie się wykonają
  + (n-1) × between_tasks_s.max            — przerwy między zadaniami
  + prolog i epilog                        — wybudzenie, IG, nawigacja, wygaszenie
  + bufor                                  — na przekroczenie ostatniej jednostki

Czemu `max_duration_min` NIE dzieli się przez `humanizer.speed`: to twardy
limit czasu zadania, nie pauza. `speed` skraca tylko pauzy — przerwy między
zadaniami i część prologu. Zadanie z limitem 25 min przy `speed=4` nadal może
zająć 25 min, po prostu wykona w tym czasie więcej jednostek.

Ta sama funkcja zasila planner (rezerwacja czasu na osi) i panel (podgląd
rozbicia w edytorze bloku) — jedna liczba, żeby plan i UI nie mogły pokazać
dwóch różnych prawd.
"""
from __future__ import annotations

# Prolog: wake → unlock → uruchomienie klonu → nawigacja na home.
# Epilog: powrót na home → zamknięcie IG → wygaszenie. Pauzy humanizera dają
# ~5-10 s, reszta to nawigacja i start aplikacji — stąd zapas.
PROLOGUE_EPILOGUE_S = 120

# Zadanie bez `max_duration_min` nie ma limitu czasu w ogóle (patrz
# `Budget.from_settings` — `None` = tylko max_items). Do planowania musimy
# przyjąć JAKĄŚ liczbę; przyjmujemy tę i zgłaszamy ostrzeżenie, bo to
# zgadywanka, nie gwarancja.
DEFAULT_TASK_CAP_MIN = 15

DEFAULT_SAFETY = 0.10


def estimate_tasks(rows: list[dict], between_tasks_s: dict | None = None,
                   *, speed: float = 1.0, safety: float = DEFAULT_SAFETY) -> dict:
    """Rozbicie czasu dla już rozwiązanych zadań (`store.resolve_tasks`)."""
    speed = float(speed) if speed and speed > 0 else 1.0
    warnings: list[str] = []
    per_task: list[dict] = []
    tasks_s = 0.0

    for r in rows:
        if not r.get("enabled", True):
            continue
        if not r.get("plugin_enabled_for_account", True):
            warnings.append(f"{r['plugin']}: plugin wyłączony dla konta — pominięty w wycenie")
            continue

        minutes = ((r.get("resolved") or {}).get("budget") or {}).get("max_duration_min")
        if minutes:
            seconds = float(minutes) * 60
            assumed = False
        else:
            seconds = DEFAULT_TASK_CAP_MIN * 60
            assumed = True
            warnings.append(f"{r['plugin']}: brak max_duration_min — przyjęto "
                            f"{DEFAULT_TASK_CAP_MIN} min, czas nie jest gwarantowany")
        tasks_s += seconds
        per_task.append({"id": r.get("id", ""), "plugin": r["plugin"],
                         "label": r.get("label", r["plugin"]),
                         "seconds": round(seconds), "assumed": assumed})

    n = len(per_task)
    gap = between_tasks_s or {}
    gap_max = float(gap.get("max", 0) or 0)
    gaps_s = (max(n - 1, 0) * gap_max) / speed

    overhead_s = float(PROLOGUE_EPILOGUE_S) if n else 0.0
    base = tasks_s + gaps_s + overhead_s
    buffer_s = base * float(safety)
    total_s = base + buffer_s

    if not n:
        warnings.append("brak włączonych zadań — blok nic nie zrobi")

    return {
        "total_s": round(total_s),
        "tasks": per_task,
        "breakdown": {
            "tasks_s": round(tasks_s),
            "gaps_s": round(gaps_s),
            "overhead_s": round(overhead_s),
            "buffer_s": round(buffer_s),
        },
        "task_count": n,
        "speed": speed,
        "safety": float(safety),
        "warnings": warnings,
    }


def estimate_queue(account: str, queue_name: str | None = None, *,
                   queue_data: dict | None = None,
                   speed: float | None = None,
                   safety: float = DEFAULT_SAFETY) -> dict:
    """Wycena scenariusza konta. `queue_data` pozwala wycenić kolejkę, która
    istnieje tylko w bloku osi czasu i nie ma pliku na dysku."""
    from v2.plugins import store

    q = queue_data if queue_data is not None else store.read_queue(account, name=queue_name)
    rows = store.resolve_tasks(account, q.get("queue") or [])
    if speed is None:
        speed = account_speed(account)
    est = estimate_tasks(rows, q.get("between_tasks_s"), speed=speed, safety=safety)
    est["account"] = account
    est["queue_name"] = queue_name
    return est


def account_speed(account: str) -> float:
    """`humanizer.speed` konta — bez twardego błędu, gdy config jest w remoncie."""
    from v2.plugins.store import CONFIG_ROOT
    import json
    p = CONFIG_ROOT / "accounts" / f"{account}.json"
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
        s = float((cfg.get("humanizer") or {}).get("speed") or 1.0)
        return s if s > 0 else 1.0
    except Exception:
        return 1.0


def human_duration(seconds: float) -> str:
    """`1h 09m` / `24m` — jednostki jawnie, jak w `cli._hms`."""
    s = int(seconds or 0)
    if s >= 3600:
        return f"{s // 3600}h {(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m"
    return f"{s}s"
