"""Stan kalibracji telefonu — panel ↔ walker (jak kod 2FA).

Walker zapisuje `needs_agent` + pytanie + ścieżkę screena.
Panel / agent odpowiada `reply` (ok / skip / retry / abort).
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def state_path(data_root: Path, device_alias: str) -> Path:
    return Path(data_root) / "devices" / device_alias / "calibrate.json"


def ask_path(walk_dir: Path) -> Path:
    return Path(walk_dir) / "AGENT_ASK.md"


def load(data_root: Path, device_alias: str) -> dict:
    p = state_path(data_root, device_alias)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(data_root: Path, device_alias: str, doc: dict) -> dict:
    p = state_path(data_root, device_alias)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return doc


def public(doc: dict) -> dict:
    """Bez pełnych dumpów — do GET /api/devices."""
    if not doc:
        return {"status": "idle"}
    return {
        "status": doc.get("status") or "idle",
        "step": doc.get("step") or "",
        "expected": doc.get("expected") or [],
        "actual": doc.get("actual"),
        "question": doc.get("question") or "",
        "has_shot": bool(doc.get("shot_path")),
        "account": doc.get("account") or "",
        "note": doc.get("note") or "",
    }


def write_ask_md(walk_dir: Path, doc: dict) -> None:
    """Plik dla agenta Cursor — screen + pytanie, gdy krok nie przeszedł."""
    shot = doc.get("shot_path") or ""
    lines = [
        "# Agent: kalibracja utknęła",
        "",
        f"Urządzenie: `{doc.get('device_alias')}`",
        f"Krok: `{doc.get('step')}`",
        f"Oczekiwano: `{doc.get('expected')}`",
        f"Widzę (identify): `{doc.get('actual')}`",
        "",
        doc.get("question") or "Sprawdź screen i powiedz, co to za ekran.",
        "",
        f"Screenshot: `{shot}`",
        "",
        "Odpowiedź w panelu **Flota** (OK / Pomiń / Ponów / Przerwij)",
        "albo POST `/api/devices/{alias}/calibrate/reply`.",
        "",
    ]
    ask_path(walk_dir).write_text("\n".join(lines), encoding="utf-8")


def ask(data_root: Path, device_alias: str, walk_dir: Path, **fields) -> dict:
    """Ustaw needs_agent i wyczyść starą odpowiedź."""
    doc = load(data_root, device_alias)
    doc.update(fields)
    # Komentarz: kopia do data/ — panel serwuje stąd, nie z walk/
    src = Path(doc.get("shot_path") or "")
    if src.exists() and src.is_file():
        dest = Path(data_root) / "devices" / device_alias / f"calibrate{src.suffix or '.jpg'}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        doc["shot_path"] = str(dest)
    doc["status"] = "needs_agent"
    doc["device_alias"] = device_alias
    doc["reply"] = None
    doc["asked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save(data_root, device_alias, doc)
    write_ask_md(walk_dir, doc)
    return doc


def set_reply(data_root: Path, device_alias: str, action: str,
              note: str = "") -> dict:
    """Panel / agent — odblokuj czekający walker."""
    act = (action or "").strip().lower()
    if act not in ("ok", "skip", "retry", "abort"):
        raise ValueError("action: ok | skip | retry | abort")
    doc = load(data_root, device_alias)
    if not doc:
        raise FileNotFoundError("kalibracja nie czeka")
    doc["reply"] = {"action": act, "note": (note or "").strip()}
    doc["status"] = "running" if act != "abort" else "failed"
    save(data_root, device_alias, doc)
    return doc


def wait_reply(data_root: Path, device_alias: str,
               timeout_s: float = 1800.0, poll_s: float = 1.5) -> dict:
    """Blokuj aż panel/agent odpowie. Timeout → abort."""
    deadline = time.monotonic() + max(0.05, float(timeout_s))
    while time.monotonic() < deadline:
        doc = load(data_root, device_alias)
        reply = doc.get("reply") if isinstance(doc.get("reply"), dict) else None
        if reply and reply.get("action"):
            # Komentarz: skasuj reply, żeby retry nie wzięło starej odpowiedzi
            doc["reply"] = None
            save(data_root, device_alias, doc)
            return reply
        time.sleep(poll_s)
    return {"action": "abort", "note": "timeout — brak odpowiedzi agenta"}
