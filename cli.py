"""bot CLI — jedno wejście: `python -m v2.cli start --account=X --flow=Y`.

Buduje Context, rejestruje kroki, uruchamia FlowRunner.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path

# umożliw import v2.* niezależnie skąd uruchamiane
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import atexit

from v2.config.schema     import load_account
from v2.core.device       import UiAuto2Device
from v2.core.uiauto_server import ensure_running as ensure_uiauto
from v2.core.scrcpy_client import ScrcpyClient
from v2.core.human_touch  import HumanTouch
from v2.core.humanizer    import Humanizer
from v2.core.events       import EventLogger, new_session_id
from v2.core.screen_map   import ScreenMap
from v2.core.navigator    import Navigator
from v2.runner.context    import Context
from v2.runner.flow_runner import FlowRunner
from v2.steps.registry    import load_all

V2_ROOT = Path(__file__).resolve().parent
DATA_ROOT = V2_ROOT / "data"
CALIB_ROOT = V2_ROOT / "calibration"


def _log_humanizer_pause(events: EventLogger, **kwargs) -> None:
    """Zapisz `humanizer.pause` — bez mikro-spam; pełny dump: V2_DEBUG_PAUSES=1."""
    from v2.core.humanizer import should_log_pause
    debug = (os.environ.get("V2_DEBUG_PAUSES") or "").strip().lower() in (
        "1", "true", "yes", "on")
    if not should_log_pause(
            kwargs.get("pause_kind") or "",
            long_pause_added_s=float(kwargs.get("long_pause_added_s") or 0),
            debug=debug):
        return
    events.log("humanizer.pause", **kwargs)


def build_context(account_id: str, speed: float | None = None) -> Context:
    """`speed` nadpisuje globalną prędkość z configu — do przebiegów testowych,
    żeby nie trzeba było ruszać pliku konta."""
    account = load_account(account_id)
    hw = account.device["_hardware"]

    # Auto-start uiautodev jeśli nie działa. Jeśli działa (np. odpalony ręcznie
    # w innym terminalu) — używamy go i NIE ubijamy przy exit.
    ensure_uiauto(hw["uiautodev"]["base_url"])

    # Screen map — na razie zawsze SM_A505FN_draft.json
    map_path = CALIB_ROOT / f"{hw['model']}_draft.json"
    if not map_path.exists():
        raise SystemExit(f"[cli] brak mapy UI: {map_path} — uruchom kalibrację")

    ig_pkg     = account.device["app_clone_package"]
    # Jeden proces na adb serial — inaczej dwa joby przeplatają input text
    # (live: nutrition coaching ↔ polish beauty → garbled pole).
    from v2.core.device_lock import DeviceBusy, DeviceLock
    session_id = new_session_id()
    _dev_lock = DeviceLock(DATA_ROOT, hw["adb_id"])
    try:
        _dev_lock.acquire(owner=account_id, session_id=session_id)
    except DeviceBusy as e:
        raise SystemExit(f"[cli] DEVICE BUSY: {e}") from e

    device     = UiAuto2Device(serial=hw["adb_id"], base_url=hw["uiautodev"]["base_url"],
                               ig_package=ig_pkg)

    # scrcpy — startujemy raz per sesja, kończymy na exit
    scrcpy = ScrcpyClient(device=device,
                          port=hw.get("scrcpy", {}).get("port", 27183),
                          # default 900 = poprzednie 720 × 1.25 (discovery)
                          max_size=hw.get("scrcpy", {}).get("max_size", 900),
                          max_fps=hw.get("scrcpy", {}).get("max_fps", 15))
    try:
        fb = scrcpy.start()
        atexit.register(scrcpy.stop)
        print(f"[scrcpy] {fb.codec} stream OK  ({scrcpy.width}x{scrcpy.height})")
    except Exception as e:
        print(f"[scrcpy] START FAILED — kroki reels będą używać screenshot HTTP: {e}")
        fb = None

    screen_map = ScreenMap.load(map_path)
    humanizer  = Humanizer.load(account.humanizer.get("profile_name", "default"),
                                overrides=account.humanizer.get("overrides") or None,
                                speed=speed if speed is not None
                                      else account.humanizer.get("speed"))
    events     = EventLogger(account.account_id, session_id, DATA_ROOT)
    # Pauzy → events.jsonl (telemetry). Domyślnie bez mikro-tap/swipe/between_steps
    # (zalewały panel SSE). Pełny dump: V2_DEBUG_PAUSES=1.
    humanizer.on_pause = lambda **kwargs: _log_humanizer_pause(events, **kwargs)
    ht         = HumanTouch(device=device, humanizer=humanizer)
    navigator  = Navigator(device=device, screen_map=screen_map, ig_package=ig_pkg, events=events)
    # trzymaj referencję w scratch — atexit i tak zwolni, ale nie GC w trakcie
    events.log("device.lock.acquired", serial=hw["adb_id"], pid=os.getpid())

    ctx = Context(
        device=device, ht=ht, navigator=navigator, screen_map=screen_map,
        humanizer=humanizer, events=events, account=account,
        session_id=session_id, ig_package=ig_pkg, fb=fb,
    )
    ctx.scratch["_device_lock"] = _dev_lock
    # Live session_stats.json — soft import, żeby kolejka szła bez ModuleNotFoundError
    try:
        from v2.data.session_stats import SessionStats
        ss = SessionStats(account.account_id, DATA_ROOT)
        # Event session.progress synchronicznie z flush(emit_event=True)
        ss._on_progress = lambda prog: ctx.log("session.progress", **prog)
        ctx.session_stats = ss
    except Exception:
        # Brak / półpodpięty moduł — runner i progress_log tolerują None
        ctx.session_stats = None
    return ctx


def cmd_start(args):
    load_all()
    ctx = build_context(args.account)
    print(f"\n[cli] account={args.account}  flow={args.flow}  session={ctx.session_id}")
    print(f"[cli] events → {ctx.events.file_path}\n")
    runner = FlowRunner(ctx=ctx)
    t0 = time.monotonic()
    summary = runner.run(args.flow)
    dt = time.monotonic() - t0
    print(f"\n[cli] KONIEC {'OK' if summary['ok'] else 'FAIL'}  "
          f"steps ok={summary['steps_ok']} skip={summary['steps_skip']} "
          f"failed={summary['steps_failed']}  {dt:.1f}s\n")
    return 0 if summary["ok"] else 1


def cmd_run_queue(args):
    """Uruchom kolejkę zadań konta (zakładka Kolejka w panelu)."""
    from v2.plugins import store
    from v2.plugins.registry import load_all as load_plugins, is_implemented

    if args.dry_run:
        return _dry_run_queue(args)

    load_all()
    load_plugins()
    ctx = build_context(args.account, speed=getattr(args, "speed", None))
    print(f"\n[cli] account={args.account}  session={ctx.session_id}")
    print(f"[cli] events → {ctx.events.file_path}")
    if ctx.humanizer.speed != 1.0:
        print(f"[cli] prędkość: ×{ctx.humanizer.speed:g} "
              f"(pauzy krótsze {ctx.humanizer.speed:g}-krotnie)")

    # miękki stop z poprzedniego przebiegu nie może ubić nowego
    stop = DATA_ROOT / "accounts" / args.account / "STOP"
    if stop.exists():
        stop.unlink()
        print("[cli] usunięto zaległy plik STOP")

    plugin_oneshot = getattr(args, "plugin", None)
    if plugin_oneshot and not is_implemented(plugin_oneshot):
        print(f"[cli] plugin '{plugin_oneshot}' nie ma implementacji")
        return 1

    from v2.plugins.runner import JobRunner
    runner = JobRunner(ctx=ctx, data_root=DATA_ROOT, only=args.only,
                       queue_name=getattr(args, "queue", None),
                       max_items_override=args.max_items,
                       max_queue_duration_s=getattr(args, "max_duration_s", None),
                       plugin_oneshot=plugin_oneshot)
    t0 = time.monotonic()
    summary = runner.run(args.account)
    dt = time.monotonic() - t0
    print(f"\n[cli] KONIEC {'OK' if summary['ok'] else 'FAIL'}  "
          f"zadania ok={summary['tasks_ok']} skip={summary['tasks_skipped']} "
          f"failed={summary['tasks_failed']}  jednostek={summary['items_done']}  {dt:.1f}s\n")
    return 0 if summary["ok"] else 1


def _dry_run_queue(args) -> int:
    """Wypisz rozwiązane ustawienia każdego zadania i wyjdź — bez telefonu.

    Główne narzędzie do sprawdzenia scalania: domyślne z katalogu → config
    konta → override zadania.
    """
    import json
    from v2.plugins import store
    from v2.plugins.registry import load_all as load_plugins, is_implemented
    load_plugins()

    rows = store.resolve_queue(args.account, name=getattr(args, "queue", None))
    if args.only:
        rows = [r for r in rows if r["id"] == args.only]
    if not rows:
        print("(kolejka pusta)")
        return 0

    for r in rows:
        flags = []
        if not r["enabled"]:
            flags.append("WYŁĄCZONE w kolejce")
        if not r["plugin_enabled_for_account"]:
            flags.append("plugin WYŁĄCZONY dla konta")
        if not is_implemented(r["plugin"]):
            flags.append("BRAK IMPLEMENTACJI")
        print()
        print("=" * 64)
        print(f"  {r['id']}  {r['plugin']}  [{r['status']}]"
              + (f"  — {r['note']}" if r["note"] else ""))
        if flags:
            print("  ! " + " | ".join(flags))
        print("=" * 64)
        print(json.dumps(r["resolved"], ensure_ascii=False, indent=1))
    print(f"\n[dry-run] {len(rows)} zadań — telefon nietknięty\n")
    return 0


def cmd_login(args):
    """Flow logowania: wake → unlock → uruchom klon → wpisz login/hasło →
    potwierdź popupy → jeśli IG prosi o kod e-mail/SMS, czekaj aż panel go
    dostarczy i wpisz.

    Świadomie NIE idzie dalej niż to — captcha/suspicious login/ban kończy się
    ręcznie na telefonie (patrz `steps/system/login_if_needed.py` i
    `steps/system/enter_verification_code_if_needed.py`)."""
    load_all()
    ctx = build_context(args.account)
    print(f"\n[cli] account={args.account}  tryb=logowanie  session={ctx.session_id}")
    print(f"[cli] events → {ctx.events.file_path}\n")

    from v2.steps.registry import get as get_step
    from v2.steps.system import login_log
    # Etykiety PL do terminala — szczegóły kroków idą z login_log.say()
    steps = [
        ("system.wake", {}, "Budzenie telefonu"),
        ("system.unlock", {}, "Odblokowanie"),
        ("system.ensure_ig_running", {}, "Uruchamiam Instagram"),
        ("system.login_if_needed", {}, "Logowanie (login/hasło + popupy)"),
        ("system.enter_verification_code_if_needed", {}, "Kod weryfikacyjny (jeśli jest)"),
    ]
    for name, params, label in steps:
        login_log.say(f"— {label}")
        step = get_step(name)()
        res = step.run(ctx, params)
        print(f"   {res.status:8s} {name}  {res.payload}"
              + (f"  error={res.error}" if res.error else ""), flush=True)
        ctx.log("step", step=name, status=res.status, payload=res.payload)
        if res.status == "failed":
            login_log.say(f"PRZERWANO: {res.error}")
            print(f"\n[cli] PRZERWANO na {name} — {res.error}\n")
            return 1

    login_log.say("GOTOWE — jeśli został captcha/2FA poza panelem, dokończ ręcznie")
    print("\n[cli] KONIEC — jeśli telefon poprosił o 2FA/potwierdzenie, dokończ ręcznie.\n")
    return 0


def cmd_stats(args):
    """Podsumowanie pracy konta — bez telefonu, prosto z plików.

    Ta sama treść co endpoint panelu (`full_report`), żeby terminal i przeglądarka
    nie mogły pokazać dwóch różnych prawd. Na serwerze przez ssh to będzie główny
    sposób sprawdzenia, co się dzieje.
    """
    import json as _json
    from v2.data import stats

    account = load_account(args.account)
    rep = stats.full_report(args.account, DATA_ROOT, days=args.days,
                            limits=account.limits)

    if args.json:
        print(_json.dumps(rep, ensure_ascii=False, indent=1))
        return 0

    daily, today = rep["daily"], rep["daily"]["today"]
    print(f"\n  KONTO {args.account}   ostatnie {args.days} dni\n")

    if today:
        print("  DZIŚ")
        for kind, lim in (today["limits"] or {}).items():
            cap = f"/{lim['cap']}" if lim["cap"] else ""
            pct = f"  ({lim['pct']}%)" if lim["pct"] is not None else ""
            print(f"    {kind:8s} {lim['used']}{cap}{pct}")
        print(f"    profile zebrane   {today['profiles_collected']}"
              f"   odwiedzone {today['profiles_visited']}")
        print(f"    czas pracy        {_hms(today['active_s'])}"
              f"   sesji {today['sessions']}")

    print(f"\n  {'data':12s} {'like':>5s} {'foll':>5s} {'zebr':>6s} "
          f"{'zadań':>6s} {'czas':>9s}")
    for d in daily["days"]:
        a = d["actions"]
        tasks = sum(d["tasks"].values())
        print(f"  {d['date']:12s} {a['like']:5d} {a['follow']:5d} "
              f"{d['profiles_collected']:6d} {tasks:6d} {_hms(d['active_s']):>9s}")
    t = daily["totals"]
    print(f"  {'RAZEM':12s} {t['actions']['like']:5d} {t['actions']['follow']:5d} "
          f"{t['profiles_collected']:6d} {sum(t['tasks'].values()):6d} "
          f"{_hms(t['active_s']):>9s}")

    if rep["plugins"]:
        print(f"\n  {'plugin':18s} {'przeb.':>6s} {'jedn.':>6s} {'błędy':>6s} "
              f"{'mediana':>8s}   powody zatrzymania")
        for p in rep["plugins"]:
            med = f"{p['median_s']}s" if p["median_s"] is not None else "-"
            stopped = " ".join(f"{k}={v}" for k, v in p["stopped_by"].items())
            print(f"  {p['plugin']:18s} {p['runs']:6d} {p['items']:6d} "
                  f"{p['failed']:6d} {med:>8s}   {stopped}")

    errors = [e for d in daily["days"] for e in d["errors"]]
    if errors:
        print(f"\n  BŁĘDY ({len(errors)}):")
        for e in errors[-5:]:
            print(f"    {e['plugin']}: {e['error'][:120]}")

    g = rep["growth"]
    if g["points"]:
        first, last = g["points"][0], g["points"][-1]
        delta = (last["followers"] or 0) - (first["followers"] or 0)
        print(f"\n  KONTO: obserwujący {last['followers']} "
              f"({delta:+d} od {first['ts'][:10]})   "
              f"posty {last['posts']}   obserwowani {last['following']}")
    elif g["enabled_hint"]:
        print(f"\n  KONTO: {g['enabled_hint']}")

    print()
    return 0


def _hms(seconds: float) -> str:
    """Jednostki jawnie — „23:56" czyta się jak prawie doba, a to 23 minuty."""
    s = int(seconds or 0)
    if s >= 3600:
        return f"{s // 3600}h {(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s}s"


def cmd_known(_args):
    load_all()
    from v2.steps.registry import known
    print("Znane kroki:")
    for n in known(): print("  -", n)
    return 0


def cmd_where(args):
    """Diagnostyka: pokaż aktualny stan telefonu + rekomendację akcji."""
    ctx = build_context(args.account)
    s = ctx.navigator.identify()
    print()
    print(f"  package = {s.package}")
    print(f"  overlay = {s.overlay}")
    print(f"  top     = {s.top}   (hits={s.hits})")
    print(f"  matches = {[(m, len(h)) for m, h in s.matches[:5]]}")
    print(f"  ambiguous={s.ambiguous}  unknown={s.unknown}")
    print()
    # rekomendacja
    if s.overlay:
        print(f"  → OVERLAY: {s.overlay}. Zwykle wystarcza `recover --target=home_feed`.")
    elif s.top is None:
        print("  → UNKNOWN. Spróbuj `recover --target=home_feed`.")
    else:
        print(f"  → Jesteśmy na {s.top}. Możesz `recover --target=<screen>` aby przejść gdzie indziej.")
    return 0


def cmd_scheduler(args):
    """Scheduler bez UI — do uruchamiania w tle bez przeglądarki.

    Ten sam `scheduler/tick.py` co wątek w panelu, ale bez FastAPI: pilnuje
    jednego subprocessu `run-queue` zamiast `SessionManager`. Blokada
    „jedno urządzenie = jedna aktywna sesja" wynika z tego, że jest tylko
    jeden `state["proc"]" na raz.

    UWAGA: nie uruchamiaj tego razem z `panel` — panel ma własną pętlę
    schedulera i dwa procesy sięgałyby po ten sam telefon.
    """
    import os
    import subprocess
    from v2.scheduler.state import is_enabled
    from v2.scheduler.tick import run_forever

    state = {"proc": None}

    def is_busy() -> bool:
        p = state["proc"]
        return bool(p and p.poll() is None)

    def start(account: str, scenario: str | None, max_duration_s: float | None = None):
        cmd = [sys.executable, "-u", "-m", "v2.cli", "run-queue", f"--account={account}"]
        if scenario:
            cmd.append(f"--queue={scenario}")
        if max_duration_s:
            cmd.append(f"--max-duration-s={max_duration_s:.0f}")
        log_dir = DATA_ROOT / "accounts" / account / "sessions"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        state["proc"] = subprocess.Popen(
            cmd, cwd=str(V2_ROOT.parent),
            stdout=(log_dir / f"{ts}-scheduler-stdout.log").open("wb"),
            stderr=(log_dir / f"{ts}-scheduler-stderr.log").open("wb"),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    if not is_enabled(DATA_ROOT):
        print("[scheduler] master switch WYŁĄCZONY — nic się nie odpali. "
              "Włącz go w panelu (zakładka Harmonogram) albo komendą "
              "`scheduler-switch --on`.")
    run_forever(DATA_ROOT, V2_ROOT / "config", is_busy, start, interval_s=args.interval)
    return 0


def cmd_scheduler_switch(args):
    """Master switch harmonogramu z terminala — odpowiednik przycisku
    START/STOP w panelu, do użycia przez ssh."""
    from v2.scheduler.state import read, set_enabled
    if args.on and args.off:
        raise SystemExit("--on i --off wykluczają się")
    if args.on or args.off:
        s = set_enabled(DATA_ROOT, bool(args.on))
    else:
        s = read(DATA_ROOT)
    print(f"harmonogram: {'WŁĄCZONY' if s['enabled'] else 'zatrzymany'}"
          + (f"  (zmiana: {s['changed_at']})" if s.get("changed_at") else ""))
    return 0


def cmd_panel(args):
    """Uruchom dashboard (FastAPI + HTML) na localhost.

    Foreground w tym procesie — bez Popen/detached. Shutdown (sygnały,
    atexit, SetConsoleCtrlHandler) jest w `panel.server.run`.
    """
    from v2.panel.server import run
    print(f"[panel] startuję na http://{args.host}:{args.port}")
    print(f"[panel] zatrzymanie: Ctrl+C w tym terminalu (zwalnia port {args.port})")
    if args.no_browser:
        print("[panel] --no-browser: nie zamykaj terminala bez Ctrl+C — inaczej proces "
              "zostanie w tle i zajmie port")
    # Nie spawnuj panelu jako child — root cause Errno 10048 na Windows.
    run(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_recover(args):
    """Doprowadź do zadeklarowanego ekranu (drabinka L0–L6) bez odpalania flow."""
    ctx = build_context(args.account)
    target = args.target
    print(f"\n  → próba doprowadzenia do {target} (max_steps={args.max_steps})\n")
    ok = ctx.navigator.ensure(target, max_steps=args.max_steps)
    print()
    if ok:
        print(f"  OK — jesteśmy na {target}")
        return 0
    s = ctx.navigator.identify()
    print(f"  FAIL — utknęliśmy na top={s.top}  overlay={s.overlay}  pkg={s.package}")
    return 1


def cmd_filter_profiles(args):
    """Promocja raw_profiles → target_candidates przez filtr HTTP (bez ADB)."""
    from v2.profile_filter.pipeline import run_filter_for_account, write_last_run
    from v2.scheduler.profile_filter_tick import run_now

    config_root = V2_ROOT / "config"

    # dry-run / force: bez globalnego locka (testy / awaria)
    if args.dry_run or args.force_unlocked:
        summary = run_filter_for_account(
            args.account,
            data_root=DATA_ROOT,
            config_root=config_root,
            limit=args.limit,
            proxy=args.proxy,
            dry_run=bool(args.dry_run),
        )
        if not args.dry_run:
            write_last_run(args.account, DATA_ROOT, summary)
        print_filter_summary(summary)
        return 0

    result = run_now(
        args.account,
        DATA_ROOT,
        config_root,
        limit=args.limit,
        proxy=args.proxy,
    )
    if not result.get("ok"):
        print(f"[filter] FAIL: {result.get('error')}")
        return 1
    print_filter_summary(result)
    return 0


def print_filter_summary(summary: dict) -> None:
    import json
    slim = {k: v for k, v in summary.items()
            if not str(k).startswith("usernames_")}
    for key in ("usernames_ok", "usernames_removed", "usernames_skipped"):
        if key in summary:
            slim[key] = summary[key][:20]
            slim[f"{key}_n"] = len(summary[key])
    print(json.dumps(slim, ensure_ascii=False, indent=2))


def _resolve_adb_serial(*, account: str | None, device: str | None) -> str:
    """adb_id z konta (`load_account`) albo wprost z `config/devices/<alias>.json`."""
    import json as _json
    from v2.config.schema import CONFIG_ROOT

    if bool(account) == bool(device):
        raise SystemExit("[cli] podaj dokładnie jedno: --account=… albo --device=…")

    if account:
        acc = load_account(account)
        serial = (acc.device.get("_hardware") or {}).get("adb_id")
        if not serial:
            raise SystemExit(f"[cli] konto {account}: brak adb_id w device hardware")
        return str(serial)

    # --device=alias → plik devices/<alias>.json (bez pełnego load_account)
    path = CONFIG_ROOT / "devices" / f"{device}.json"
    if not path.exists():
        raise SystemExit(f"[cli] brak urządzenia: {path}")
    try:
        dev = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"[cli] nieczytelny device JSON: {e}") from e
    serial = (dev.get("adb_id") or "").strip()
    if not serial or serial == "REPLACE":
        raise SystemExit(f"[cli] device:{device}: brak adb_id")
    return serial


def cmd_airplane_toggle(args):
    """Jednorazowy test ADB: airplane ON → pauza → OFF (Wi‑Fi wraca).

    Bez SIM / bez schedulera / bez panelu / bez DeviceLock — tylko shell ADB.
    """
    from v2.core.airplane import toggle_airplane

    serial = _resolve_adb_serial(account=args.account, device=args.device)
    print(f"[cli] airplane-toggle  serial={serial}  pause={args.pause:g}s")
    toggle_airplane(serial, pause_s=float(args.pause))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("start", help="uruchom flow dla konta")
    ps.add_argument("--account", required=True)
    ps.add_argument("--flow",    required=True)
    ps.set_defaults(func=cmd_start)

    pq = sub.add_parser("run-queue", help="uruchom kolejkę zadań konta")
    pq.add_argument("--account", required=True)
    pq.add_argument("--only", default=None, help="uruchom tylko zadanie o tym id")
    pq.add_argument("--queue", default=None,
                    help="nazwany scenariusz (<alias>.queues/<nazwa>.json) zamiast domyślnej kolejki")
    pq.add_argument("--max-items", type=int, default=None,
                    help="nadpisz budżet jednostek we WSZYSTKICH zadaniach (do testów)")
    pq.add_argument("--max-duration-s", type=float, default=None,
                    help="twarda rama czasowa CAŁEJ kolejki w sekundach — budżety "
                         "zadań są przycinane, żeby sesja zmieściła się w oknie "
                         "(używa tego harmonogram blokowy)")
    pq.add_argument("--dry-run", action="store_true",
                    help="wypisz rozwiązane ustawienia i wyjdź, bez dotykania telefonu")
    pq.add_argument("--speed", type=float, default=None,
                    help="globalna prędkość na ten przebieg (2 = dwa razy szybciej)")
    pq.add_argument("--plugin", default=None,
                    help="one-shot: syntetyczna kolejka z jednym pluginem "
                         "(np. niche_discover) — bez zapisu do *.queues")
    pq.set_defaults(func=cmd_run_queue)

    pl = sub.add_parser("login", help="krótki flow: wake→unlock→klon→wpisz login/hasło")
    pl.add_argument("--account", required=True)
    pl.set_defaults(func=cmd_login)

    pst = sub.add_parser("stats", help="podsumowanie pracy konta (bez telefonu)")
    pst.add_argument("--account", required=True)
    pst.add_argument("--days", type=int, default=7)
    pst.add_argument("--json", action="store_true",
                     help="surowy JSON — to samo, co zwraca panel")
    pst.set_defaults(func=cmd_stats)

    pk = sub.add_parser("known", help="wypisz zarejestrowane kroki")
    pk.set_defaults(func=cmd_known)

    pw = sub.add_parser("where", help="diagnostyka: pokaż aktualny stan ekranu")
    pw.add_argument("--account", required=True)
    pw.set_defaults(func=cmd_where)

    psc = sub.add_parser("scheduler", help="scheduler bez UI — odpala sesje wg harmonogramu kont")
    psc.add_argument("--interval", type=int, default=60, help="co ile sekund sprawdzać sloty")
    psc.set_defaults(func=cmd_scheduler)

    pss = sub.add_parser("scheduler-switch", help="włącz/wyłącz harmonogram (master switch)")
    pss.add_argument("--on", action="store_true", help="włącz egzekwowanie planu")
    pss.add_argument("--off", action="store_true", help="zatrzymaj harmonogram")
    pss.set_defaults(func=cmd_scheduler_switch)

    pp = sub.add_parser("panel", help="uruchom dashboard w przeglądarce")
    pp.add_argument("--host", default="127.0.0.1")
    pp.add_argument("--port", type=int, default=8765)
    pp.add_argument("--no-browser", action="store_true", help="nie otwieraj przeglądarki automatycznie")
    pp.set_defaults(func=cmd_panel)

    pr = sub.add_parser("recover", help="doprowadź do wskazanego ekranu bez flow")
    pr.add_argument("--account", required=True)
    pr.add_argument("--target",  required=True, help="np. home_feed / reels_feed / profile_self")
    pr.add_argument("--max-steps", type=int, default=6)
    pr.set_defaults(func=cmd_recover)

    pf = sub.add_parser(
        "filter-profiles",
        help="filtr HTTP: raw_profiles → target_candidates (bez telefonu)")
    pf.add_argument("--account", required=True)
    pf.add_argument("--limit", type=int, default=None,
                    help="ile username sprawdzić (domyślnie z profile_filter.limit_per_run)")
    pf.add_argument("--proxy", default=None,
                    help="webshare | rayobyte | losowo (nadpisuje config)")
    pf.add_argument("--dry-run", action="store_true",
                    help="oceniaj bez zapisu state/candidates (wymaga sieci lub mock)")
    pf.add_argument("--force-unlocked", action="store_true",
                    help="pomiń globalny lock filtra (tylko awaryjnie)")
    pf.set_defaults(func=cmd_filter_profiles)

    # Tylko terminalowy smoke test ADB — nie podpinać do harmonogramu / sesji
    pa = sub.add_parser(
        "airplane-toggle",
        help="test ADB: airplane ON-pauza-OFF (Wi-Fi; bez SIM / bez schedulera)")
    pa.add_argument("--account", default=None,
                    help="alias konta -> adb_id z devices/<device_alias>.json")
    pa.add_argument("--device", default=None,
                    help="alias urzadzenia (np. phone_A) -> adb_id wprost")
    pa.add_argument("--pause", type=float, default=3.0,
                    help="sekundy miedzy ON a OFF (domyslnie 3)")
    pa.set_defaults(func=cmd_airplane_toggle)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
