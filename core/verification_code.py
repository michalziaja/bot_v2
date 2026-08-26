"""Kanał panel→bot do wpisania kodu weryfikacyjnego (email/SMS) w trakcie
logowania.

Ten sam wzorzec co plik `STOP` (`plugins/base.py:stop_path_for`): krok
czekający na kod zapisuje `CODE_REQUEST` (sygnał dla panelu — „proszę o kod,
oto kontekst z ekranu"), panel zapisuje odpowiedź do `CODE_RESPONSE`, krok ją
odczytuje i kasuje oba pliki. Ustalone z użytkownikiem 2026-08-02 — jedyny
etap 2FA/checkpointu, który bot obsługuje; captcha/suspicious login nadal
kończy się ręcznie na telefonie.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

REQUEST_FILENAME = "CODE_REQUEST"
RESPONSE_FILENAME = "CODE_RESPONSE"


def _account_dir(data_root, account_id: str) -> Path:
    return Path(data_root) / "accounts" / account_id


def request_path(data_root, account_id: str) -> Path:
    return _account_dir(data_root, account_id) / REQUEST_FILENAME


def response_path(data_root, account_id: str) -> Path:
    return _account_dir(data_root, account_id) / RESPONSE_FILENAME


def request_code(data_root, account_id: str, prompt: str) -> None:
    """Krok woła to, gdy widzi ekran 'wprowadź kod' — sygnał dla panelu."""
    p = request_path(data_root, account_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"prompt": prompt,
                             "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                            ensure_ascii=False), encoding="utf-8")


def clear_request(data_root, account_id: str) -> None:
    p = request_path(data_root, account_id)
    if p.exists():
        p.unlink()


def get_request(data_root, account_id: str) -> dict | None:
    """Panel woła to, żeby wiedzieć czy/o co bot prosi — bez ujawniania kodu."""
    p = request_path(data_root, account_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def submit_code(data_root, account_id: str, code: str) -> None:
    """Panel woła to po tym, jak użytkownik wpisał kod w dashboardzie."""
    p = response_path(data_root, account_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code.strip(), encoding="utf-8")


def take_code(data_root, account_id: str) -> str | None:
    """Odczytaj i skasuj kod, jeśli panel go już zapisał. `None` = jeszcze nic."""
    p = response_path(data_root, account_id)
    if not p.exists():
        return None
    code = p.read_text(encoding="utf-8").strip()
    p.unlink()
    return code or None


def wait_for_code(data_root, account_id: str, timeout_s: float,
                  poll_s: float = 2.0) -> str | None:
    """Polluj plik odpowiedzi do timeoutu. `None` = timeout bez kodu."""
    deadline = time.monotonic() + timeout_s
    while True:
        code = take_code(data_root, account_id)
        if code:
            return code
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_s)
