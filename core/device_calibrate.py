"""Kalibracja nowego telefonu — agent przejmuje ADB i obchodzi znane ekrany.

Zrzut co krok (jpg + slim JSON + identify). Na końcu `{MODEL}_draft.json`
z żywymi bounds kotwic nav (A50 na 1080×2400 wpada w pasek systemowy).
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from v2.core.adb_probe import normalize_model
from v2.core import calibrate_state
from v2.steps.system._ui_helpers import center
from v2.core.screen_map import (
    flatten_hierarchy,
    ids_from_flat,
    texts_from_flat,
)
from v2.steps.registry import get as get_step
from v2.steps.system import login_log
from v2.steps.system.login_if_needed import _dismiss_gms_passkey
from v2.steps.system.signup_if_needed import _dismiss_help_sheet

# Komentarz: te same ID co A50 — po normalizacji klona są kanoniczne
NAV_ANCHOR_IDS = {
    "nav.feed_tab": "com.instagram.android:id/feed_tab",
    "nav.clips_tab": "com.instagram.android:id/clips_tab",
    "nav.direct_tab": "com.instagram.android:id/direct_tab",
    "nav.search_tab": "com.instagram.android:id/search_tab",
    "nav.profile_tab": "com.instagram.android:id/profile_tab",
    "search.input": "com.instagram.android:id/action_bar_search_edit_text",
    "home.notifications": "com.instagram.android:id/notification",
}

# Komentarz: 3-przyciskowy nav OxygenOS ~140 px — kotwice IG muszą być WYŻEJ
_DEFAULT_SYS_INSET = 140
_TAB_BAND = 126

CANONICAL_MAP = Path(__file__).resolve().parents[1] / "calibration" / "SM_A505FN_draft.json"

# Komentarz: None = tylko dump (launcher/lock OK). Zbiór = identify.top musi trafić.
# Komentarz: launch może wylądować na dowolnym tabie / wizardzie, nie tylko home
IG_LANDING = {
    "home_feed", "login_screen", "signup_contact", "signup_code",
    "signup_birthday", "date_picker", "ig_map_intro", "search_top",
    "reels_feed", "profile_self", "direct_inbox",
}
TAB_EXPECT = {
    "tab_feed": {"home_feed"},
    "tab_search": {"search_top", "search_input_active", "search_results"},
    "tab_reels": {"reels_feed"},
    "tab_direct": {"direct_inbox", "ig_map_intro"},
    "tab_profile": {"profile_self"},
}


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _label(n: dict) -> str:
    return ((n.get("text") or "") + " " + (n.get("content-desc") or "")).strip()


def slim_flat(flat: list[dict], limit: int = 80) -> list[dict]:
    """Krótka lista klikalnych / z tekstem — do STEP_MAP, bez całego drzewa."""
    rows = []
    for n in flat:
        lab = _label(n)
        rid = n.get("resource-id") or ""
        if not (n.get("clickable") or lab or rid.endswith("_tab")):
            continue
        rows.append({
            "rid": rid.split("/")[-1] if rid else "",
            "lab": lab[:80],
            "cls": (n.get("class") or "").split(".")[-1],
            "bounds": n.get("bounds"),
        })
        if len(rows) >= limit:
            break
    return rows


def harvest_anchors(flat: list[dict]) -> dict[str, dict]:
    """Żywe bounds kotwic z aktualnego drzewa."""
    by_id = {n.get("resource-id"): n for n in flat if n.get("resource-id")}
    out: dict[str, dict] = {}
    for name, rid in NAV_ANCHOR_IDS.items():
        n = by_id.get(rid)
        b = n.get("bounds") if n else None
        if b:
            out[name] = {
                "kind": "resource-id",
                "value": rid,
                "bounds": b,
                "note": "live harvest",
            }
    return out


def fallback_nav_anchors(w: int, h: int, inset: int = _DEFAULT_SYS_INSET) -> dict[str, dict]:
    """Pięć tabów nad paskiem systemowym — gdy harvest nie złapał (wylogowany)."""
    y2 = max(1, h - inset)
    y1 = max(0, y2 - _TAB_BAND)
    names = [
        "nav.feed_tab", "nav.clips_tab", "nav.direct_tab",
        "nav.search_tab", "nav.profile_tab",
    ]
    out = {}
    slot = w / 5
    for i, name in enumerate(names):
        x1, x2 = int(i * slot), int((i + 1) * slot)
        rid = NAV_ANCHOR_IDS[name]
        out[name] = {
            "kind": "resource-id",
            "value": rid,
            "bounds": f"[{x1},{y1}][{x2},{y2}]",
            "note": f"fallback nad inset={inset} (brak harvestu)",
        }
    return out


def write_model_map(
    *,
    model: str,
    resolution: list[int],
    harvested: dict[str, dict],
    dest: Path,
    base: Path = CANONICAL_MAP,
) -> Path:
    """Skopiuj fingerprinty A50, nadpisz meta + kotwice (live albo fallback)."""
    data = json.loads(base.read_text(encoding="utf-8"))
    w, h = int(resolution[0]), int(resolution[1])
    data["meta"] = {
        **data.get("meta", {}),
        "device_model": model,
        "resolution": [w, h],
        "captured_at": _iso(),
        "note": "wygenerowane przez cli calibrate — kotwice z telefonu",
    }
    anchors = dict(data.get("anchors") or {})
    if not harvested:
        harvested = fallback_nav_anchors(w, h)
    anchors.update(harvested)
    data["anchors"] = anchors
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return dest


def dump_step(
    ctx,
    out_dir: Path,
    slug: str,
    note: str,
    shot_fn: Callable[[], bytes] | None = None,
) -> dict[str, Any]:
    """Screenshot + identify + slim — agent widzi, gdzie jest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(ctx.device, "dismiss_keyguard"):
        ctx.device.dismiss_keyguard()
    hier = ctx.device.hierarchy(force=True)
    flat = flatten_hierarchy(hier)
    state = ctx.navigator.identify()
    raw = b""
    try:
        raw = shot_fn() if shot_fn else ctx.device.screenshot()
    except Exception:
        raw = b""
    ext = "png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
    img = out_dir / f"{slug}.{ext}"
    if raw:
        img.write_bytes(raw)
    rec = {
        "slug": slug,
        "note_pl": note,
        "at": _iso(),
        "identify": state.as_log(),
        "pkg": ctx.device.current_pkg(),
        "slim": slim_flat(flat),
        "ids_sample": sorted(ids_from_flat(flat))[:40],
        "texts_sample": sorted(texts_from_flat(flat))[:40],
        "screenshot": img.name if raw else None,
    }
    (out_dir / f"{slug}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    login_log.say(
        f"calib [{slug}] top={state.top or '?'} overlay={state.overlay or '-'} "
        f"pkg={rec['pkg']} shot={img.name if raw else '—'}")
    return rec


def _run_named(ctx, name: str) -> None:
    step = get_step(name)()
    res = step.run(ctx, {})
    login_log.say(f"calib krok {name} → {res.status} {res.payload or res.error or ''}")


def step_passed(actual: str | None, expected: set[str] | None) -> bool:
    """Czy identify.top jest na liście akceptowanej dla tego kroku."""
    if not expected:
        return True
    return (actual or "") in expected


def _shot_abs(walk: Path, rec: dict) -> str:
    name = rec.get("screenshot")
    if not name:
        return ""
    return str((walk / name).resolve())


def verify_or_ask(
    *,
    ctx,
    rec: dict,
    expected: set[str] | None,
    data_root: Path,
    alias: str,
    walk: Path,
    account: str,
) -> str:
    """Dump już jest. Pass / pytanie do agenta (ok=traktuj jako pass, retry=nowy dump)."""
    actual = (rec.get("identify") or {}).get("top")
    slug = rec.get("slug") or "?"
    while True:
        if step_passed(actual, expected):
            rec["verified"] = True
            calibrate_state.save(data_root, alias, {
                "status": "running",
                "step": slug,
                "actual": actual,
                "expected": sorted(expected) if expected else [],
                "account": account,
                "device_alias": alias,
            })
            return "ok"
        q = (
            f"Krok `{slug}` nie przeszedł. "
            f"Oczekiwałem {sorted(expected) if expected else 'cokolwiek'}, "
            f"identify dał `{actual or '?'}`. "
            f"Obejrzyj screen i powiedz: OK (to ten ekran), Pomiń, Ponów dump albo Przerwij."
        )
        login_log.say(f"calib FAIL {slug} actual={actual} — pytam agenta")
        calibrate_state.ask(
            data_root, alias, walk,
            step=slug,
            expected=sorted(expected) if expected else [],
            actual=actual,
            question=q,
            shot_path=_shot_abs(walk, rec),
            account=account,
        )
        reply = calibrate_state.wait_reply(data_root, alias)
        act = (reply.get("action") or "abort").lower()
        if act == "ok":
            rec["verified"] = True
            rec["agent_ok"] = True
            return "ok"
        if act == "skip":
            rec["verified"] = False
            rec["agent_skip"] = True
            return "skip"
        if act == "retry":
            rec = dump_step(ctx, walk, slug + "_retry", "ponowny dump po agencie")
            actual = (rec.get("identify") or {}).get("top")
            continue
        rec["verified"] = False
        rec["aborted"] = True
        return "abort"


def run_calibration(ctx, calib_root: Path | None = None,
                    data_root: Path | None = None) -> dict[str, Any]:
    """Obchodzi wake→unlock→IG→taby; przy failu screen + pytanie do agenta."""
    hw = ctx.account.device.get("_hardware") or {}
    model = normalize_model(hw.get("model") or "UNKNOWN") or "UNKNOWN"
    res = hw.get("resolution") or [1080, 2400]
    w, h = int(res[0]), int(res[1])
    alias = (ctx.account.device.get("device_alias") or "device").strip()
    account = ctx.account.account_id
    root = Path(calib_root or Path(__file__).resolve().parents[1] / "calibration")
    data = Path(data_root or Path(__file__).resolve().parents[1] / "data")
    walk = root / model / "walk"
    if walk.exists():
        shutil.rmtree(walk)
    walk.mkdir(parents=True, exist_ok=True)
    calibrate_state.save(data, alias, {
        "status": "running", "account": account, "device_alias": alias,
        "step": "start",
    })

    steps: list[dict] = []
    harvested: dict[str, dict] = {}
    aborted = False

    def snap(slug: str, note: str, expected: set[str] | None = None) -> dict:
        rec = dump_step(ctx, walk, f"{len(steps):02d}_{slug}", note)
        steps.append(rec)
        harvested.update(harvest_anchors(
            flatten_hierarchy(ctx.device.hierarchy(force=True))))
        verdict = verify_or_ask(
            ctx=ctx, rec=rec, expected=expected,
            data_root=data, alias=alias, walk=walk, account=account,
        )
        rec["verdict"] = verdict
        return rec

    snap("start", "stan wejściowy — zanim ruszamy gestami")
    if any(s.get("verdict") == "abort" for s in steps):
        aborted = True
    if not aborted:
        _run_named(ctx, "system.wake")
        snap("wake", "po wake")
        aborted = steps[-1].get("verdict") == "abort"
    if not aborted:
        _run_named(ctx, "system.unlock")
        snap("unlock", "po unlock / dismiss-keyguard")
        aborted = steps[-1].get("verdict") == "abort"
    if not aborted:
        _run_named(ctx, "system.ensure_ig_running")
        snap("ig", "po launch klona", IG_LANDING)
        aborted = steps[-1].get("verdict") == "abort"

    top = (steps[-1].get("identify") or {}).get("top") if steps else None
    if not aborted:
        flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if _dismiss_gms_passkey(ctx, flat):
            time.sleep(0.6)
            snap("gms", "po zamknięciu GMS Passkey", IG_LANDING)
            aborted = steps[-1].get("verdict") == "abort"
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
        if not aborted and _dismiss_help_sheet(ctx, flat):
            time.sleep(0.5)
            snap("help", "po sheet pomocy")
            aborted = steps[-1].get("verdict") == "abort"

    if not aborted:
        rec = snap("landing", "landing: login / signup / home / picker", IG_LANDING)
        aborted = rec.get("verdict") == "abort"
        top = (rec.get("identify") or {}).get("top")

    if not aborted and top in (
            "home_feed", "search_top", "reels_feed", "profile_self", "direct_inbox"):
        for slug, rid in (
            ("tab_feed", NAV_ANCHOR_IDS["nav.feed_tab"]),
            ("tab_search", NAV_ANCHOR_IDS["nav.search_tab"]),
            ("tab_reels", NAV_ANCHOR_IDS["nav.clips_tab"]),
            ("tab_direct", NAV_ANCHOR_IDS["nav.direct_tab"]),
            ("tab_profile", NAV_ANCHOR_IDS["nav.profile_tab"]),
        ):
            if aborted:
                break
            flat = flatten_hierarchy(ctx.device.hierarchy(force=True))
            node = next((n for n in flat if n.get("resource-id") == rid), None)
            if not node:
                rec = dump_step(ctx, walk, f"{len(steps):02d}_{slug}_missing",
                                f"brak kotwicy {rid}")
                steps.append(rec)
                rec["verdict"] = verify_or_ask(
                    ctx=ctx, rec=rec, expected=TAB_EXPECT.get(slug),
                    data_root=data, alias=alias, walk=walk, account=account,
                )
                aborted = rec.get("verdict") == "abort"
                continue
            xy = center(node["bounds"])
            ctx.ht.tap(*xy, pause_kind="tap")
            time.sleep(1.1)
            rec = snap(slug, f"po tap {rid.split('/')[-1]}", TAB_EXPECT.get(slug))
            aborted = rec.get("verdict") == "abort"

    dest = write_model_map(
        model=model, resolution=[w, h], harvested=harvested,
        dest=root / f"{model}_draft.json",
    )
    playbook = (
        f"# Kalibracja {model}\n\n"
        f"Rozdzielczość: {w}×{h}. Zrzuty: `{model}/walk/`.\n"
        f"Mapa: `{dest.name}`. Harvest kotwic: {len(harvested)}.\n"
        f"Start: `{_iso()}`. Przerwane: {aborted}.\n\n"
        "## Kroki\n\n"
        + "\n".join(
            f"- `{s['slug']}` — {s['note_pl']} → top="
            f"{(s.get('identify') or {}).get('top') or '?'} "
            f"[{s.get('verdict') or 'dump'}]"
            for s in steps
        )
        + "\n"
    )
    (walk / "PLAYBOOK.md").write_text(playbook, encoding="utf-8")
    (walk / "STEP_MAP.json").write_text(
        json.dumps({"model": model, "resolution": [w, h], "steps": steps},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    calibrate_state.save(data, alias, {
        "status": "failed" if aborted else "done",
        "account": account,
        "device_alias": alias,
        "step": "end",
        "map": dest.name,
        "harvested": sorted(harvested),
    })
    return {
        "model": model,
        "map": str(dest),
        "walk": str(walk),
        "harvested": sorted(harvested),
        "steps": [s["slug"] for s in steps],
        "last_top": top,
        "aborted": aborted,
    }
