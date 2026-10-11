"""Kolejka publikacji — pliki z tożsamością (konto + typ + nazwa kanoniczna).

Blob: `data/accounts/<alias>/publish/files/<item_id>.*`
Meta: `data/accounts/<alias>/publish/queue.json` (lista rekordów).

Nazwa kanoniczna (nigdy surowa z dysku operatora):

    {account}__{item_id}__{slug}.{ext}

Przykład: `jobsaver__p7f3a2__sunset.jpg`

Plugin czyta TYLKO kolejkę `ctx.account` — zakaz „weź najnowszy z DCIM”.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import subprocess
import time
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path
from typing import Callable, Iterable

# Typ i status — v1, bez karuzel / rolek-jako-post.
KINDS = ("post", "story")
STATUSES = ("ready", "on_device", "publishing", "published", "failed")
# Po opublikowaniu nie ruszamy kind/caption (audit).
LOCKED_STATUSES = ("published",)
# Rozszerzenia, które IG ogarnie przez ACTION_SEND.
ALLOWED_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "3gp": "video/3gpp",
}

# Folder na telefonie — nawet gdy klony dzielą /sdcard.
# adb push lubi /sdcard; MediaStore na A11 wymaga /storage/emulated/0
# (live technature699: content insert z /sdcard padł IllegalArgumentException).
PHONE_ROOT = "/sdcard/Pictures/bot_v2"
EMULATED_ROOT = "/storage/emulated/0/Pictures/bot_v2"

AdbRunner = Callable[[list[str], float], object]


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict | None]:
    """Pola tekstowe + opcjonalny plik — bez `python-multipart` (Starlette).

    Zwraca `({name: value}, {filename, mime, blob} | None)`.
    """
    fields: dict[str, str] = {}
    upload: dict | None = None
    if not body:
        return fields, None
    # BytesParser chce pełny RFC822 (nagłówki + puste + body).
    raw = b"Content-Type: " + content_type.encode("utf-8", "replace") + b"\r\n\r\n" + body
    try:
        msg = BytesParser(policy=HTTP).parsebytes(raw)
    except Exception:
        return fields, None
    parts = msg.iter_parts() if msg.is_multipart() else []
    for part in parts:
        disp = part.get_content_disposition() or ""
        if disp != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = b""
        if filename:
            upload = {
                "filename": filename,
                "mime": part.get_content_type() or None,
                "blob": payload if isinstance(payload, (bytes, bytearray)) else bytes(payload),
            }
        else:
            fields[str(name)] = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
    return fields, upload


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _atomic_write_json(path: Path, data) -> None:
    """Zapis JSON z tmp+replace — nie zostawia półpliku przy crashu."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def slugify(name: str) -> str:
    """Krótki ASCII slug z nazwy operatora — bez spacji i ogonków."""
    stem = Path(name or "").stem or "plik"
    # NFKD jest zbędny — i tak tniemy do [a-z0-9].
    raw = stem.encode("ascii", "ignore").decode("ascii") or stem
    slug = re.sub(r"[^a-zA-Z0-9]+", "", raw).lower()
    return (slug[:32] or "plik")


def ext_of(filename: str, mime: str | None = None) -> str:
    """Rozszerzenie z nazwy albo MIME; domyślnie jpg."""
    suf = Path(filename or "").suffix.lower().lstrip(".")
    if suf == "jpeg":
        suf = "jpg"
    if suf in ALLOWED_EXT:
        return "jpg" if suf == "jpeg" else suf
    if mime:
        for ext, m in ALLOWED_EXT.items():
            if m == mime:
                return "jpg" if ext == "jpeg" else ext
    return "jpg"


def mime_of(ext: str, fallback: str | None = None) -> str:
    e = (ext or "").lower().lstrip(".")
    if e == "jpeg":
        e = "jpg"
    return ALLOWED_EXT.get(e) or fallback or "application/octet-stream"


def canonical_name(account: str, item_id: str, slug: str, ext: str) -> str:
    """`{account}__{item_id}__{slug}.{ext}` — tożsamość pliku."""
    e = (ext or "jpg").lower().lstrip(".")
    if e == "jpeg":
        e = "jpg"
    return f"{account}__{item_id}__{slugify(slug)}.{e}"


def new_item_id() -> str:
    """Krótki id w stylu p7f3a2 — unikalność pilnuje store (cały drzewo)."""
    return "p" + secrets.token_hex(3)


def phone_dir(account: str, kind: str) -> str:
    """`/sdcard/Pictures/bot_v2/<alias>/<kind>/` — bez końcowego slasha w join."""
    k = kind if kind in KINDS else "post"
    return f"{PHONE_ROOT}/{account}/{k}"


def phone_path_for(account: str, kind: str, name: str) -> str:
    return f"{phone_dir(account, kind)}/{name}"


def emulated_fs_path(path: str) -> str:
    """ /sdcard/… → /storage/emulated/0/… pod content insert."""
    p = path or ""
    if p.startswith("/sdcard/"):
        return "/storage/emulated/0/" + p[len("/sdcard/"):]
    return p


def _queue_path(data_root: Path, account: str) -> Path:
    return Path(data_root) / "accounts" / account / "publish" / "queue.json"


def _files_dir(data_root: Path, account: str) -> Path:
    return Path(data_root) / "accounts" / account / "publish" / "files"


def _read_queue(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [x for x in data["items"] if isinstance(x, dict)]
    return []


def all_item_ids(data_root: Path) -> set[str]:
    """Id we wszystkich kolejkach — `item_id` ma być unikalny w całym store."""
    out: set[str] = set()
    root = Path(data_root) / "accounts"
    if not root.exists():
        return out
    for q in root.glob("*/publish/queue.json"):
        for rec in _read_queue(q):
            i = rec.get("id")
            if i:
                out.add(str(i))
    return out


def alloc_item_id(data_root: Path) -> str:
    """Nowy id, który nie koliduje z żadnym kontem."""
    taken = all_item_ids(data_root)
    for _ in range(32):
        nid = new_item_id()
        if nid not in taken:
            return nid
    # ekstremalnie rzadkie — dokładamy więcej hex
    return "p" + secrets.token_hex(6)


class PublishStore:
    """Kolejka jednego aliasu — blob + queue.json."""

    def __init__(self, account_id: str, data_root: Path):
        self.account_id = account_id
        self.data_root = Path(data_root)
        self._dir = self.data_root / "accounts" / account_id / "publish"
        self._files = self._dir / "files"
        self._path = self._dir / "queue.json"

    @property
    def queue_path(self) -> Path:
        return self._path

    @property
    def files_dir(self) -> Path:
        return self._files

    def read_all(self) -> list[dict]:
        return _read_queue(self._path)

    def get(self, item_id: str) -> dict | None:
        for rec in self.read_all():
            if rec.get("id") == item_id:
                return rec
        return None

    def _write(self, items: list[dict]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._path, items)

    def add(self, *, kind: str, filename: str, blob: bytes,
            mime: str | None = None, caption: str = "",
            device_alias: str | None = None) -> dict:
        """Nowy rekord + blob. Nazwa kanoniczna od razu, nie surowa."""
        if kind not in KINDS:
            raise ValueError(f"kind musi być post|story, dostano {kind!r}")
        if not blob:
            raise ValueError("pusty plik")
        ext = ext_of(filename, mime)
        mime = mime_of(ext, mime)
        item_id = alloc_item_id(self.data_root)
        slug = slugify(filename)
        cname = canonical_name(self.account_id, item_id, slug, ext)
        self._files.mkdir(parents=True, exist_ok=True)
        dest = self._files / f"{item_id}.{ext}"
        dest.write_bytes(blob)
        rec = {
            "id": item_id,
            "account": self.account_id,
            "kind": kind,
            "files": [{
                "canonical_name": cname,
                "local_name": dest.name,
                "mime": mime,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "bytes": len(blob),
            }],
            "caption": caption or "",
            "status": "ready",
            "phone_path": None,
            "device_alias": device_alias,
            "error": None,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        items = self.read_all()
        items.append(rec)
        self._write(items)
        return rec

    def update(self, item_id: str, **fields) -> dict:
        """Aktualizacja pól. kind/caption zablokowane po published."""
        items = self.read_all()
        found = None
        for i, rec in enumerate(items):
            if rec.get("id") != item_id:
                continue
            st = rec.get("status")
            if st in LOCKED_STATUSES:
                banned = {"kind", "caption"} & set(fields)
                if banned:
                    raise ValueError(
                        f"nie można zmienić {', '.join(sorted(banned))} "
                        f"gdy status={st}"
                    )
            if "kind" in fields and fields["kind"] not in KINDS:
                raise ValueError("kind musi być post|story")
            if "status" in fields and fields["status"] not in STATUSES:
                raise ValueError(f"nieznany status: {fields['status']}")
            rec = dict(rec)
            rec.update(fields)
            rec["updated_at"] = _now_iso()
            items[i] = rec
            found = rec
            break
        if found is None:
            raise KeyError(item_id)
        self._write(items)
        return found

    def delete(self, item_id: str) -> dict:
        """Kasuje rekord i blob na PC. Zwraca skasowany rekord (pod rm na telefonie)."""
        items = self.read_all()
        found = None
        keep = []
        for rec in items:
            if rec.get("id") == item_id:
                found = rec
            else:
                keep.append(rec)
        if found is None:
            raise KeyError(item_id)
        self._write(keep)
        for f in found.get("files") or []:
            local = f.get("local_name")
            if local:
                p = self._files / local
                if p.exists():
                    p.unlink()
            # stary układ: <id>.*
            for leftover in self._files.glob(f"{item_id}.*"):
                leftover.unlink(missing_ok=True)
        return found

    def blob_path(self, item: dict) -> Path | None:
        """Ścieżka bloba na PC."""
        files = item.get("files") or []
        if not files:
            return None
        local = files[0].get("local_name")
        if local:
            p = self._files / local
            if p.exists():
                return p
        iid = item.get("id")
        if iid:
            hits = list(self._files.glob(f"{iid}.*"))
            return hits[0] if hits else None
        return None

    def pending_for_plugin(self, kind: str = "both") -> list[dict]:
        """Pozycje tego aliasu gotowe do publikacji — nigdy cudze konto.

        `on_device` pierwsze, potem `ready` (push w locie).
        kind: post | story | both
        """
        want = set(KINDS) if kind in ("both", "", None) else {kind}
        ready, on_dev = [], []
        for rec in self.read_all():
            if rec.get("kind") not in want:
                continue
            st = rec.get("status")
            if st == "on_device":
                on_dev.append(rec)
            elif st == "ready":
                ready.append(rec)
        return on_dev + ready


def list_accounts_with_publish(data_root: Path) -> list[str]:
    root = Path(data_root) / "accounts"
    if not root.exists():
        return []
    out = []
    for q in sorted(root.glob("*/publish/queue.json")):
        out.append(q.parent.parent.name)
    return out


def list_publish(
    data_root: Path,
    *,
    account: str | None = None,
    device: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    device_of: Callable[[str], str | None] | None = None,
) -> list[dict]:
    """Lista z filtrami. `device_of(alias)` — device_alias z configu konta."""
    accounts: Iterable[str]
    if account:
        accounts = [account]
    else:
        root = Path(data_root) / "accounts"
        accounts = [p.name for p in sorted(root.iterdir())] if root.exists() else []
    out: list[dict] = []
    for acc in accounts:
        if not acc or acc.startswith("."):
            continue
        recs = PublishStore(acc, data_root).read_all()
        for rec in recs:
            row = dict(rec)
            row.setdefault("account", acc)
            # device z rekordu albo z configu — filtr „cały telefon”
            dev = row.get("device_alias") or (device_of(acc) if device_of else None)
            if device_of and not row.get("device_alias"):
                row["device_alias"] = dev
            if device and dev != device:
                continue
            if kind and row.get("kind") != kind:
                continue
            if status and row.get("status") != status:
                continue
            out.append(row)
    return out


def _run_adb(args: list[str], timeout: float = 30.0):
    """Domyślny runner — `adb …`. Testy podmieniają ten symbol."""
    return subprocess.run(
        ["adb", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def push_to_phone(
    item: dict,
    *,
    serial: str,
    blob_path: Path,
    run_adb: AdbRunner | None = None,
) -> dict:
    """`adb push` do folderu konta + skan MediaStore (A50 = Android 11).

    Zwraca `{phone_path, content_uri}` do zapisania na rekordzie.
    """
    runner = run_adb or _run_adb
    account = item.get("account") or ""
    kind = item.get("kind") or "post"
    files = item.get("files") or [{}]
    cname = (files[0] or {}).get("canonical_name")
    if not cname:
        raise ValueError("brak canonical_name — nie pushuję sieroty")
    if not serial:
        raise ValueError("brak seriala ADB")
    dest_dir = phone_dir(account, kind)
    dest = phone_path_for(account, kind, cname)
    emu = emulated_fs_path(dest)
    mkdir = runner(["-s", serial, "shell", "mkdir", "-p", dest_dir], 15.0)
    if getattr(mkdir, "returncode", 0) not in (0, None):
        err = (getattr(mkdir, "stderr", "") or getattr(mkdir, "stdout", "") or "").strip()
        raise RuntimeError(f"adb mkdir: {err or 'fail'}")
    pushed = runner(["-s", serial, "push", str(blob_path), dest], 60.0)
    if getattr(pushed, "returncode", 0) not in (0, None):
        err = (getattr(pushed, "stderr", "") or getattr(pushed, "stdout", "") or "").strip()
        raise RuntimeError(f"adb push: {err or 'fail'}")
    # Android 11: broadcast + insert po ścieżce emulated (nie /sdcard).
    scan_uri = f"file://{emu}"
    runner(
        ["-s", serial, "shell", "am", "broadcast",
         "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
         "-d", scan_uri],
        15.0,
    )
    mime = (files[0] or {}).get("mime") or "image/jpeg"
    media_uri = (
        "content://media/external/images/media"
        if str(mime).startswith("image/")
        else "content://media/external/video/media"
    )
    inserted = runner(
        ["-s", serial, "shell", "content", "insert",
         "--uri", media_uri,
         "--bind", f"_data:s:{emu}",
         "--bind", f"mime_type:s:{mime}"],
        15.0,
    )
    insert_out = (
        (getattr(inserted, "stdout", "") or "")
        + (getattr(inserted, "stderr", "") or "")
    )
    from_insert = _uri_from_insert_out(insert_out, media_uri)
    content_uri = (
        from_insert
        or _query_media_uri(runner, serial, emu, media_uri)
        or scan_uri
    )
    return {"phone_path": dest, "content_uri": content_uri}


def _uri_from_insert_out(out: str, media_uri: str) -> str | None:
    """`content insert` na A11 często wypisuje nowy content://…/id."""
    m = re.search(r"(content://media/external/(?:images|video)/media/\d+)", out)
    if m:
        return m.group(1)
    m = re.search(r"_id=(\d+)", out)
    if m:
        return f"{media_uri}/{m.group(1)}"
    return None


def _query_media_uri(runner: AdbRunner, serial: str, emu_path: str,
                     media_uri: str) -> str | None:
    """content://…/id — intent SHARE chce to, nie file://.

    A11 `content query --where` nie bierze LIKE ani `_data='/…'`
    (IllegalArgumentException: Invalid token storage). Szukamy po
    `_display_name` = nazwa kanoniczna.
    """
    name = emu_path.rstrip("/").split("/")[-1]
    if not name:
        return None
    # Jeden string do `adb shell` — inaczej A11 tnie `.jpg` / apostrofy.
    q = runner(
        ["-s", serial, "shell",
         f"content query --uri {media_uri} --projection _id:_data "
         f"--where \"_display_name='{name}'\""],
        15.0,
    )
    out = (getattr(q, "stdout", "") or "") + (getattr(q, "stderr", "") or "")
    m = re.search(r"_id=(\d+)", out)
    if not m:
        return None
    return f"{media_uri}/{m.group(1)}"


def delete_on_phone(
    item: dict,
    *,
    serial: str,
    run_adb: AdbRunner | None = None,
) -> None:
    """Best-effort rm na telefonie — brak pliku nie jest błędem."""
    path = item.get("phone_path")
    if not path or not serial:
        return
    runner = run_adb or _run_adb
    try:
        runner(["-s", serial, "shell", "rm", "-f", path], 15.0)
    except Exception:
        pass
