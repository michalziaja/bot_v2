#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge `resources.arsc` + `res/` ze splitów Play (density) do jednego APK.

Play trzyma splash (`R.drawable.instagram` = 0x7f082060 → res/s8z.png) wyłącznie
w `split_config.xxhdpi`. `pm path` + pull samego base.apk zostawia dziurę w tabeli:
TYPE SPEC ma slot, ale nie ma wartości. Klon jako pojedynczy APK pada
Resources$NotFoundException na IgSplashScreenActivity.

Nie wołamy zewnętrznego APKEditor — doklejamy pulę stringów i chunki TYPE
z overlayu (indeksy STRING przemapowane). Feature-splity bez arsc pomijamy.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

# Komentarz: splash IgSplashScreenActivity — drawable „instagram”
SPLASH_DRAWABLE_ID = 0x7F082060

RES_TABLE = 0x0002
RES_STRING_POOL = 0x0001
RES_TABLE_PACKAGE = 0x0200
RES_TABLE_TYPE = 0x0201
RES_TABLE_TYPE_SPEC = 0x0202

TYPE_STRING = 0x03
FLAG_COMPLEX = 0x0001
FLAG_COMPACT = 0x0008
NO_ENTRY = 0xFFFFFFFF
UTF8_FLAG = 1 << 8

# Komentarz: pliki split-only, których nie kopiujemy do standalone APK
_SKIP_ZIP_NAMES = frozenset({
    "AndroidManifest.xml",
    "resources.arsc",
    "stamp-cert-sha256",
})


class ArscError(RuntimeError):
    """Uszkodzony albo nieobsługiwany resources.arsc."""


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _utf8_len_pair(data: bytes, off: int) -> tuple[int, int, int]:
    """Zwraca (pozycja_bajtów, liczba_bajtów, nowy_off_po_nagłówku_długości)."""
    b0 = data[off]
    off += 1
    if b0 & 0x80:
        off += 1
    b1 = data[off]
    off += 1
    if b1 & 0x80:
        b1 = ((b1 & 0x7F) << 8) | data[off]
        off += 1
    return off, b1, off


def _decode_pool_string(data: bytes, off: int, utf8: bool) -> str:
    """Jedna pozycja puli Android (UTF-8 albo UTF-16)."""
    if utf8:
        pos, nbytes, _ = _utf8_len_pair(data, off)
        return data[pos : pos + nbytes].decode("utf-8", errors="replace")
    n = _u16(data, off)
    off += 2
    if n & 0x8000:
        n = ((n & 0x7FFF) << 16) | _u16(data, off)
        off += 2
    return data[off : off + n * 2].decode("utf-16-le", errors="replace")


def _encode_utf8_count(n: int) -> bytes:
    """Długość w formacie puli UTF-8 (1 albo 2 bajty)."""
    if n < 0x80:
        return bytes([n])
    return bytes([0x80 | ((n >> 8) & 0x7F), n & 0xFF])


def _encode_utf8_string(s: str) -> bytes:
    """Wpis puli UTF-8: char-len, byte-len, bajty, NUL."""
    raw = s.encode("utf-8")
    # Komentarz: pierwsza długość to liczba jednostek UTF-16, nie codepointów Pythona
    utf16_chars = len(s.encode("utf-16-le")) // 2
    return _encode_utf8_count(utf16_chars) + _encode_utf8_count(len(raw)) + raw + b"\x00"


class StringPool:
    """Pula stringów z chunku 0x0001."""

    def __init__(self, data: bytes, off: int) -> None:
        if _u16(data, off) != RES_STRING_POOL:
            raise ArscError(f"oczekiwano STRING_POOL @{off}")
        self.off = off
        self.header_size = _u16(data, off + 2)
        self.chunk_size = _u32(data, off + 4)
        self.count = _u32(data, off + 8)
        self.style_count = _u32(data, off + 12)
        self.flags = _u32(data, off + 16)
        self.strings_start = _u32(data, off + 20)
        self.utf8 = bool(self.flags & UTF8_FLAG)
        self._data = data
        self.offsets = [
            _u32(data, off + self.header_size + i * 4) for i in range(self.count)
        ]

    def get(self, idx: int) -> str:
        if idx < 0 or idx >= self.count:
            return ""
        return _decode_pool_string(
            self._data,
            self.off + self.strings_start + self.offsets[idx],
            self.utf8,
        )

    def all(self) -> list[str]:
        """Wszystkie stringi w kolejności indeksów."""
        return [self.get(i) for i in range(self.count)]


def parse_global_pool(data: bytes) -> StringPool:
    """Pierwsza pula globalna zaraz po nagłówku ResTable."""
    if len(data) < 12 or _u16(data, 0) != RES_TABLE:
        raise ArscError("to nie jest ResTable")
    pos = _u16(data, 2)
    end = min(_u32(data, 4), len(data))
    while pos + 8 <= end:
        typ = _u16(data, pos)
        size = _u32(data, pos + 4)
        if typ == RES_STRING_POOL:
            return StringPool(data, pos)
        if size < 8:
            break
        pos += size
    raise ArscError("brak globalnej puli stringów")


def _iter_packages(data: bytes):
    """Offsety chunków PACKAGE w tabeli."""
    pos = _u16(data, 2)
    end = min(_u32(data, 4), len(data))
    while pos + 8 <= end:
        typ = _u16(data, pos)
        size = _u32(data, pos + 4)
        if size < 8 or pos + size > len(data):
            break
        if typ == RES_TABLE_PACKAGE:
            yield pos, size
        pos += size


def arsc_entry_values(data: bytes, resid: int) -> list[dict]:
    """Wartości wpisu (jeśli są) — pusta lista = ID bez konkretu w tej tabeli."""
    pkg_id = (resid >> 24) & 0xFF
    type_id = (resid >> 16) & 0xFF
    entry_id = resid & 0xFFFF
    found: list[dict] = []
    gpool = parse_global_pool(data)
    for pkg_off, pkg_size in _iter_packages(data):
        if _u32(data, pkg_off + 8) != pkg_id:
            continue
        hdr = _u16(data, pkg_off + 2)
        ip = pkg_off + hdr
        end = pkg_off + pkg_size
        while ip + 8 <= end:
            it = _u16(data, ip)
            ih = _u16(data, ip + 2)
            isz = _u32(data, ip + 4)
            if isz < 8 or ip + isz > end:
                break
            if it == RES_TABLE_TYPE:
                tid = data[ip + 8]
                flags = data[ip + 9]
                entry_count = _u32(data, ip + 12)
                entries_start = _u32(data, ip + 16)
                if tid == type_id and not (flags & 1) and entry_id < entry_count:
                    eoff = _u32(data, ip + ih + entry_id * 4)
                    if eoff != NO_ENTRY:
                        ep = ip + entries_start + eoff
                        eflags = _u16(data, ep + 2)
                        val: dict = {"flags": eflags, "chunk": ip}
                        if not (eflags & FLAG_COMPLEX) and not (eflags & FLAG_COMPACT):
                            vtype = data[ep + 11]
                            vdata = _u32(data, ep + 12)
                            val["data_type"] = vtype
                            val["data"] = vdata
                            if vtype == TYPE_STRING:
                                val["string"] = gpool.get(vdata)
                        found.append(val)
            ip += isz
    return found


def arsc_has_entry_value(data: bytes, resid: int) -> bool:
    """True gdy tabela ma konkretną wartość dla ID (nie sam TYPE SPEC)."""
    return bool(arsc_entry_values(data, resid))


def build_utf8_string_pool(strings: list[str]) -> bytes:
    """Chunk puli UTF-8 zgodny z ResStringPool_header (styles=0)."""
    encoded = [_encode_utf8_string(s) for s in strings]
    header_size = 0x1C
    blob = bytearray()
    offsets: list[int] = []
    for raw in encoded:
        offsets.append(len(blob))
        blob.extend(raw)
    while len(blob) % 4:
        blob.append(0)
    strings_start = header_size + 4 * len(strings)
    chunk_size = strings_start + len(blob)
    parts = [
        struct.pack("<HH", RES_STRING_POOL, header_size),
        struct.pack("<I", chunk_size),
        struct.pack("<I", len(strings)),
        struct.pack("<I", 0),
        struct.pack("<I", UTF8_FLAG),
        struct.pack("<I", strings_start),
        struct.pack("<I", 0),
    ]
    out = bytearray(b"".join(parts))
    for off in offsets:
        out += struct.pack("<I", off)
    out += blob
    if len(out) != chunk_size:
        raise ArscError(f"pula: size {len(out)} != {chunk_size}")
    return bytes(out)


def _remap_res_value(buf: bytearray, off: int, remap: list[int]) -> None:
    """Jeśli Res_value to STRING — podmień indeks puli."""
    if off + 8 > len(buf):
        return
    if buf[off + 3] != TYPE_STRING:
        return
    old = struct.unpack_from("<I", buf, off + 4)[0]
    if 0 <= old < len(remap):
        struct.pack_into("<I", buf, off + 4, remap[old])


def remap_type_chunk_strings(chunk: bytes, remap: list[int]) -> bytes:
    """Przepisz indeksy TYPE_STRING w chunku TYPE (0x0201)."""
    buf = bytearray(chunk)
    ih = _u16(buf, 2)
    flags = buf[9]
    entry_count = _u32(buf, 12)
    entries_start = _u32(buf, 16)
    if flags & 1:
        # Komentarz: sparse — rzadkie w IG density split; zostaw bez remap
        return bytes(buf)
    for i in range(entry_count):
        eoff = _u32(buf, ih + i * 4)
        if eoff == NO_ENTRY:
            continue
        ep = entries_start + eoff
        if ep + 8 > len(buf):
            continue
        eflags = _u16(buf, ep + 2)
        if eflags & FLAG_COMPACT:
            continue
        if eflags & FLAG_COMPLEX:
            count = _u32(buf, ep + 12)
            pos = ep + 16
            for _ in range(count):
                if pos + 12 > len(buf):
                    break
                _remap_res_value(buf, pos + 4, remap)
                pos += 12
        else:
            _remap_res_value(buf, ep + 8, remap)
    return bytes(buf)


def collect_type_chunks(data: bytes, pkg_off: int, pkg_size: int) -> list[bytes]:
    """Surowe chunki TYPE z pakietu (bez SPEC / puli nazw)."""
    hdr = _u16(data, pkg_off + 2)
    ip = pkg_off + hdr
    end = pkg_off + pkg_size
    out: list[bytes] = []
    while ip + 8 <= end:
        isz = _u32(data, ip + 4)
        if isz < 8 or ip + isz > end:
            break
        if _u16(data, ip) == RES_TABLE_TYPE:
            out.append(data[ip : ip + isz])
        ip += isz
    return out


def merge_arsc_tables(base: bytes, overlay: bytes) -> bytes:
    """Doklej TYPE-y overlayu do tabeli base; stringi overlayu dopisane na końcu puli."""
    if _u16(base, 0) != RES_TABLE or _u16(overlay, 0) != RES_TABLE:
        raise ArscError("merge: oba bufory muszą być ResTable")
    base_pool = parse_global_pool(base)
    over_pool = parse_global_pool(overlay)
    if not base_pool.utf8:
        raise ArscError("merge: baza nie-UTF8 — nieobsługiwane")
    strings = base_pool.all()
    index_of = {s: i for i, s in enumerate(strings)}
    remap: list[int] = []
    for i in range(over_pool.count):
        s = over_pool.get(i)
        if s in index_of:
            remap.append(index_of[s])
        else:
            remap.append(len(strings))
            index_of[s] = len(strings)
            strings.append(s)
    new_pool = build_utf8_string_pool(strings)

    extra: list[bytes] = []
    for pkg_off, pkg_size in _iter_packages(overlay):
        for raw in collect_type_chunks(overlay, pkg_off, pkg_size):
            extra.append(remap_type_chunk_strings(raw, remap))
    extra_blob = b"".join(extra)

    pkgs = list(_iter_packages(base))
    if not pkgs:
        raise ArscError("merge: baza bez PACKAGE")
    pkg_off, pkg_size = pkgs[0]
    # Komentarz: powiększ size pierwszego pakietu i doklej TYPE-y na końcu
    new_pkg = bytearray(base[pkg_off : pkg_off + pkg_size])
    struct.pack_into("<I", new_pkg, 4, pkg_size + len(extra_blob))
    new_pkg += extra_blob

    # Komentarz: pula globalna zwykle zaraz po nagłówku — wstawiamy nową w to samo miejsce
    old_pool_end = base_pool.off + base_pool.chunk_size
    header_size = _u16(base, 2)
    before_pool = base[header_size : base_pool.off]
    between = base[old_pool_end:pkg_off]
    after = base[pkg_off + pkg_size :]

    body = before_pool + new_pool + between + bytes(new_pkg) + after
    pkg_count = _u32(base, 8)
    total = header_size + len(body)
    header = struct.pack("<HHII", RES_TABLE, header_size, total, pkg_count)
    out = header + body
    if len(out) != total:
        raise ArscError(f"merge: size {len(out)} != {total}")
    return out


def apk_has_resources_arsc(apk: Path) -> bool:
    """True gdy ZIP zawiera resources.arsc (base / density, nie feature helium)."""
    if not apk.is_file():
        return False
    try:
        with zipfile.ZipFile(apk) as zf:
            return "resources.arsc" in zf.namelist()
    except zipfile.BadZipFile:
        return False


def read_apk_arsc(apk: Path) -> bytes:
    """resources.arsc z APK."""
    with zipfile.ZipFile(apk) as zf:
        return zf.read("resources.arsc")


def merge_resource_split_apks(split_paths: list[Path], dest: Path) -> Path:
    """Złóż standalone APK: pierwszy z arsc to baza, reszta dokleja res/ + TYPE.

    Feature-splity bez `resources.arsc` są pomijane (helium ~147 MB nie jest
    potrzebny do splash / zasobów).
    """
    resource_apks = [p for p in split_paths if apk_has_resources_arsc(p)]
    if not resource_apks:
        raise ArscError("brak APK z resources.arsc do merge")
    # Komentarz: base.apk pierwszy, potem density — nazwa „base” wygrywa
    def _rank(p: Path) -> tuple[int, int]:
        name = p.name.lower()
        if name == "base.apk" or name.startswith("base"):
            return (0, 0)
        if "split_config" in name:
            return (1, 0)
        return (2, -p.stat().st_size)

    resource_apks = sorted(resource_apks, key=_rank)
    base = resource_apks[0]
    overlays = resource_apks[1:]
    merged_arsc = read_apk_arsc(base)
    extra_res: dict[str, bytes] = {}
    for over in overlays:
        merged_arsc = merge_arsc_tables(merged_arsc, read_apk_arsc(over))
        with zipfile.ZipFile(over) as zf:
            for name in zf.namelist():
                if name.endswith("/") or name in _SKIP_ZIP_NAMES:
                    continue
                if name.startswith("META-INF/"):
                    continue
                extra_res[name] = zf.read(name)

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".merge-tmp")
    try:
        with zipfile.ZipFile(base, "r") as src, zipfile.ZipFile(tmp, "w") as out:
            for item in src.infolist():
                if item.filename == "resources.arsc":
                    info = zipfile.ZipInfo("resources.arsc")
                    info.compress_type = zipfile.ZIP_STORED
                    info.date_time = item.date_time
                    out.writestr(info, merged_arsc)
                    continue
                if item.filename in extra_res:
                    out.writestr(item, extra_res.pop(item.filename))
                    continue
                out.writestr(item, src.read(item.filename))
            for name, blob in extra_res.items():
                info = zipfile.ZipInfo(name)
                info.compress_type = (
                    zipfile.ZIP_STORED
                    if name.startswith("res/") or name.endswith(".png")
                    else zipfile.ZIP_DEFLATED
                )
                out.writestr(info, blob)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return dest


def overlay_splits_onto_apk(target: Path, split_paths: list[Path]) -> bool:
    """Doklej brakujące zasoby density do już zbudowanego klona (bez apktool).

    Zwraca True gdy coś dopisano. Manifest / dex klona zostają.
    """
    if not target.is_file():
        raise ArscError(f"brak APK: {target}")
    overlays = [
        p for p in split_paths
        if p.is_file() and apk_has_resources_arsc(p) and p.resolve() != target.resolve()
    ]
    if not overlays:
        return False
    current = read_apk_arsc(target)
    if arsc_has_entry_value(current, SPLASH_DRAWABLE_ID):
        return False
    merged = current
    extra_res: dict[str, bytes] = {}
    for over in overlays:
        over_arsc = read_apk_arsc(over)
        if over_arsc == current:
            continue
        # Komentarz: pomiń bazę (ta sama tabela) — tylko splity z nowymi TYPE
        if not arsc_has_entry_value(over_arsc, SPLASH_DRAWABLE_ID) and (
            len(over_arsc) >= len(current)
        ):
            # duża baza bez splash — to ten sam base, nie overlay
            continue
        merged = merge_arsc_tables(merged, over_arsc)
        with zipfile.ZipFile(over) as zf:
            for name in zf.namelist():
                if not name.startswith("res/") or name.endswith("/"):
                    continue
                extra_res[name] = zf.read(name)
    if merged == current and not extra_res:
        return False
    tmp = target.with_name(target.name + ".res-tmp")
    try:
        with zipfile.ZipFile(target, "r") as src, zipfile.ZipFile(tmp, "w") as out:
            wrote_arsc = False
            existing = set(src.namelist())
            for item in src.infolist():
                if item.filename == "resources.arsc":
                    info = zipfile.ZipInfo("resources.arsc")
                    info.compress_type = zipfile.ZIP_STORED
                    info.date_time = item.date_time
                    out.writestr(info, merged)
                    wrote_arsc = True
                    continue
                if item.filename in extra_res:
                    out.writestr(item, extra_res.pop(item.filename))
                    continue
                out.writestr(item, src.read(item.filename))
            if not wrote_arsc:
                info = zipfile.ZipInfo("resources.arsc")
                info.compress_type = zipfile.ZIP_STORED
                out.writestr(info, merged)
            for name, blob in extra_res.items():
                if name in existing:
                    continue
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_STORED
                out.writestr(info, blob)
        tmp.replace(target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True


def find_split_apks(source_apk: Path) -> list[Path]:
    """Katalog `*-splits` obok źródła (pull zapisuje tam base + split_config.*)."""
    found: list[Path] = []
    parent = source_apk.parent
    candidates = [
        parent / f"{source_apk.stem}-splits",
        parent / "apks" / f"{source_apk.stem}-splits",
    ]
    # Komentarz: instagram.apk → apks/instagram-<ver>-splits
    apks = parent / "apks"
    if apks.is_dir():
        candidates.extend(sorted(apks.glob("instagram-*-splits")))
    seen: set[Path] = set()
    for d in candidates:
        if not d.is_dir():
            continue
        key = d.resolve()
        if key in seen:
            continue
        seen.add(key)
        for p in sorted(d.glob("*.apk")):
            found.append(p)
    return found


def parse_pm_paths(path_out: str) -> list[str]:
    """Wszystkie `package:` z `pm path` — base i splity Play."""
    out: list[str] = []
    for line in (path_out or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            remote = line.split(":", 1)[1].strip()
            if remote:
                out.append(remote)
    return out
