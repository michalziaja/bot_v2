"""Ludzkie komunikaty logowania (stdout / panel) — prefix `login`.

Events.jsonl zbiera surowe dane; tu tylko to, co operator czyta na żywo.
"""
from __future__ import annotations
import sys

_PREFIX = "login"


def say(msg: str) -> None:
    """Jedna linia statusu — zawsze z prefixem login."""
    if not (msg or "").strip():
        print(flush=True)
        return
    try:
        print(f"  {_PREFIX} {msg}", flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        line = f"  {_PREFIX} {msg}\n"
        sys.stdout.buffer.write(line.encode(enc, errors="replace"))
        sys.stdout.flush()
