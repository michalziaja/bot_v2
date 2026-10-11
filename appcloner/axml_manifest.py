# -*- coding: utf-8 -*-
"""Patch binarnego AndroidManifest.xml (AXML) bez dekodowania resources.
apktool `d --no-res` kopiuje surowy AXML. Czytanie go jako UTF-8 exploduje
(`invalid continuation byte`). `--force-manifest` daje tekst, ale `apktool b`
wtedy pakuje zepsuty manifest. Zostawiamy binarny plik i przepisujemy pulę
stringów (te same indeksy, ewentualnie inna długość).
"""
from __future__ import annotations
import struct
from collections.abc import Callable, Sequence
# Komentarz: typy chunków z AOSP ResourceTypes.h
RES_XML_TYPE = 0x0003
RES_STRING_POOL_TYPE = 0x0001
RES_XML_START_ELEMENT_TYPE = 0x0102
RES_XML_END_ELEMENT_TYPE = 0x0103
RES_XML_RESOURCE_MAP_TYPE = 0x0180
UTF8_FLAG = 1 << 8
TYPE_STRING = 0x03
NO_ENTRY = 0xFFFFFFFF
# Komentarz: android.R.attr.* — fallback gdy pula nie ma literału "authorities"/"name"
ATTR_NAME = 0x01010003
ATTR_AUTHORITIES = 0x01010018
class AxmlError(ValueError):
    """Uszkodzony albo nieobsługiwany AXML."""
def is_binary_axml(data: bytes) -> bool:
    """True gdy plik zaczyna się nagłówkiem RES_XML_TYPE (typ 0x0003)."""
    # Komentarz: pozycja 5 z błędem 0xc9 to bajt pola size — klasyczny surowy AXML
    if len(data) < 8:
        return False
    typ, header_size = struct.unpack_from("<HH", data, 0)
    return typ == RES_XML_TYPE and header_size >= 8
def _pad4(blob: bytes) -> bytes:
    """Dopełnij chunk/dane stringów do granicy 4 bajtów."""
    n = (-len(blob)) % 4
    return blob if n == 0 else blob + (b"\x00" * n)
def _encode_utf8_len(n: int) -> bytes:
    """Długość UTF-8 w AOSP: 1 bajt albo 2 z bitem 0x80."""
    if n > 0x7FFF:
        raise AxmlError(f"zbyt długi string UTF-8 AXML ({n})")
    if n > 0x7F:
        return bytes([((n >> 8) & 0x7F) | 0x80, n & 0xFF])
    return bytes([n])
def _encode_utf16_len(n: int) -> bytes:
    """Długość UTF-16: 1× uint16 albo 2 z bitem 0x8000."""
    if n > 0x7FFFFFFF:
        raise AxmlError(f"zbyt długi string UTF-16 AXML ({n})")
    if n > 0x7FFF:
        return struct.pack("<HH", ((n >> 16) & 0x7FFF) | 0x8000, n & 0xFFFF)
    return struct.pack("<H", n)
def encode_pool_string(s: str, utf8: bool) -> bytes:
    """Zakoduj jeden wpis puli (z terminatorami)."""
    if utf8:
        raw = s.encode("utf-8")
        utf16_units = len(s.encode("utf-16-le")) // 2
        return _encode_utf8_len(utf16_units) + _encode_utf8_len(len(raw)) + raw + b"\x00"
    raw = s.encode("utf-16-le")
    return _encode_utf16_len(len(raw) // 2) + raw + b"\x00\x00"
def _decode_len(buf: bytes, offset: int, char_size: int) -> tuple[int, int]:
    """Zwraca (length, ile bajtów zjadło). char_size=1 → UTF-8, 2 → UTF-16."""
    if offset + char_size * 2 > len(buf):
        if offset + char_size > len(buf):
            raise AxmlError("obcięta długość stringa w puli AXML")
        fmt = "<B" if char_size == 1 else "<H"
        return struct.unpack_from(fmt, buf, offset)[0], char_size
    fmt = "<BB" if char_size == 1 else "<HH"
    a, b = struct.unpack_from(fmt, buf, offset)
    high = 0x80 if char_size == 1 else 0x8000
    if a & high:
        length = ((a & ~high) << (8 * char_size)) | b
        return length, char_size * 2
    return a, char_size
def decode_pool_string(buf: bytes, offset: int, utf8: bool) -> str:
    """Odczytaj string z puli od danego offsetu (względem bloku danych)."""
    if utf8:
        _chars, skip1 = _decode_len(buf, offset, 1)
        offset += skip1
        nbytes, skip2 = _decode_len(buf, offset, 1)
        offset += skip2
        raw = buf[offset : offset + nbytes]
        return raw.decode("utf-8", errors="replace")
    nchars, skip = _decode_len(buf, offset, 2)
    offset += skip
    raw = buf[offset : offset + nchars * 2]
    return raw.decode("utf-16-le", errors="replace")
def iter_chunks(data: bytes) -> list[bytes]:
    """Chunki po nagłówku RES_XML (zwykle 8 bajtów)."""
    if not is_binary_axml(data):
        raise AxmlError("to nie jest binarny AXML")
    header_size = struct.unpack_from("<H", data, 2)[0]
    declared = struct.unpack_from("<I", data, 4)[0]
    end = min(len(data), declared) if declared >= header_size else len(data)
    pos = header_size
    chunks: list[bytes] = []
    while pos + 8 <= end:
        _typ, _hsz, size = struct.unpack_from("<HHI", data, pos)
        if size < 8 or pos + size > len(data):
            raise AxmlError(f"uszkodzony chunk AXML @ {pos} size={size}")
        chunks.append(data[pos : pos + size])
        pos += size
    return chunks
def pack_axml(chunks: Sequence[bytes]) -> bytes:
    """Złóż plik AXML: nagłówek RES_XML + chunki, zaktualizowane size."""
    body = b"".join(chunks)
    total = 8 + len(body)
    return struct.pack("<HHI", RES_XML_TYPE, 8, total) + body
def parse_string_pool(chunk: bytes) -> tuple[list[str], int, bytes]:
    """Zwraca (stringi, flags, surowe dane stylów albo b'')."""
    if len(chunk) < 28:
        raise AxmlError("za krótki string pool AXML")
    typ, header_size, size, count, style_count, flags, strings_start, styles_start = (
        struct.unpack_from("<HHIIIIII", chunk, 0)
    )
    if typ != RES_STRING_POOL_TYPE:
        raise AxmlError(f"oczekiwano STRING_POOL, jest 0x{typ:04x}")
    utf8 = (flags & UTF8_FLAG) != 0
    off_base = header_size
    offsets: list[int] = []
    for i in range(count):
        offsets.append(struct.unpack_from("<I", chunk, off_base + i * 4)[0])
    # Komentarz: dane stringów kończą się na stylesStart albo na końcu chunka
    data_end = styles_start if (style_count and styles_start and styles_start < size) else size
    string_data = chunk[strings_start:data_end]
    style_blob = chunk[styles_start:size] if (style_count and styles_start) else b""
    strings: list[str] = []
    for rel in offsets:
        if rel >= len(string_data):
            strings.append("")
            continue
        strings.append(decode_pool_string(string_data, rel, utf8))
    return strings, flags, style_blob
def build_string_pool(strings: Sequence[str], flags: int, style_blob: bytes = b"") -> bytes:
    """Zbuduj chunk STRING_POOL; indeksy stringów bez zmian kolejności.
    style_blob jest ignorowany — AndroidManifest IG ma styleCount=0.
    """
    del style_blob
    utf8 = (flags & UTF8_FLAG) != 0
    encoded: list[bytes] = [encode_pool_string(s, utf8) for s in strings]
    offsets: list[int] = []
    pos = 0
    parts: list[bytes] = []
    for rec in encoded:
        offsets.append(pos)
        parts.append(rec)
        pos += len(rec)
    data = _pad4(b"".join(parts))
    count = len(strings)
    header_size = 28
    strings_start = header_size + 4 * count
    size = strings_start + len(data)
    header = struct.pack(
        "<HHIIIIII",
        RES_STRING_POOL_TYPE,
        header_size,
        size,
        count,
        0,
        flags,
        strings_start,
        0,
    )
    off_tbl = b"".join(struct.pack("<I", o) for o in offsets)
    return header + off_tbl + data
def _safe_str(strings: Sequence[str], idx: int) -> str:
    """Pusty string przy 0xFFFFFFFF / poza zakresem."""
    if idx == NO_ENTRY or idx < 0 or idx >= len(strings):
        return ""
    return strings[idx]
def _start_element_name_idx(chunk: bytes) -> int:
    """Indeks nazwy tagu w START_ELEMENT."""
    _typ, header_size, _size = struct.unpack_from("<HHI", chunk, 0)
    _ns, name = struct.unpack_from("<II", chunk, header_size)
    return name
