"""EventLogger — append-only zapis eventów sesji do JSONL.

Interfejs jest ten sam co planowany adapter Supabase — łatwo podmienić.
Każdy event ma: ts (ISO), session_id, account_id, kind, payload.
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EventLogger:
    account_id: str
    session_id: str
    data_root: Path
    _file_path: Path = field(init=False)

    def __post_init__(self):
        d = self.data_root / "accounts" / self.account_id
        d.mkdir(parents=True, exist_ok=True)
        self._file_path = d / "events.jsonl"

    @property
    def file_path(self) -> Path:
        return self._file_path

    def log(self, kind: str, **payload: Any) -> dict:
        rec = {
            "ts":         _now_iso(),
            "session_id": self.session_id,
            "account_id": self.account_id,
            "kind":       kind,
            "payload":    payload,
        }
        with self._file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read_all(self) -> list[dict]:
        if not self._file_path.exists():
            return []
        return [json.loads(l) for l in self._file_path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def session_events(self) -> list[dict]:
        """Zwraca eventy tylko z bieżącej sesji."""
        return [e for e in self.read_all() if e.get("session_id") == self.session_id]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def new_session_id() -> str:
    """Losowy krótki ID sesji: YYYYMMDD-HHMMSS-XXXXXXXX.

    Osiem znaków, nie cztery: przy czterech dwie sesje wystartowane w tej samej
    sekundzie zderzały się z prawdopodobieństwem rzędu procenta, a kolizja
    znaczy tu ciche zlanie zdarzeń dwóch sesji w jeden strumień
    (`session_events()` filtruje właśnie po tym polu).
    """
    import uuid
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
