#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dekoder/enkoder binarnego AndroidManifest.xml (AXML) ↔ tekst XML.

apktool 3 z `--no-res` kopiuje surowy AXML. Patch klona potrzebuje tekstu.
`apktool b` w tym trybie nie kompiluje tekstu — po patchu kodujemy z powrotem
do AXML, żeby raw trafił do APK. Nie ruszamy apktool.jar.
"""
from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
import xml.sax.saxutils
from pathlib import Path

# Komentarz: magia ResXML (type=0x0003, headerSize=0x0008)
AXML_TYPE = 0x0003
RES_STRING_POOL = 0x0001
RES_XML_RESOURCE_MAP = 0x0180
RES_XML_START_NAMESPACE = 0x0100
RES_XML_END_NAMESPACE = 0x0101
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
RES_XML_CDATA = 0x0104

TYPE_NULL = 0x00
TYPE_REFERENCE = 0x01
TYPE_ATTRIBUTE = 0x02
TYPE_STRING = 0x03
TYPE_FLOAT = 0x04
TYPE_DIMENSION = 0x05
TYPE_FRACTION = 0x06
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12
TYPE_INT_COLOR_ARGB8 = 0x1C
TYPE_INT_COLOR_RGB8 = 0x1D
TYPE_INT_COLOR_ARGB4 = 0x1E
TYPE_INT_COLOR_RGB4 = 0x1F

UTF8_FLAG = 1 << 8


class AxmlError(RuntimeError):
    """Błędny albo niekompletny bufor AXML."""


def is_binary_axml(data: bytes) -> bool:
    """True gdy plik to binarny AXML, nie `<?xml`."""
    if len(data) < 8:
        return False
    if data.lstrip().startswith(b"<?xml") or data.lstrip().startswith(b"<manifest"):
        return False
    typ, hdr = struct.unpack_from("<HH", data, 0)
    return typ == AXML_TYPE and hdr >= 8


def _u32(data: bytes, off: int) -> int:
    if off + 4 > len(data):
        raise AxmlError(f"odczyt u32 poza buforem @{off}")
    return struct.unpack_from("<I", data, off)[0]


def _i32(data: bytes, off: int) -> int:
    if off + 4 > len(data):
        raise AxmlError(f"odczyt i32 poza buforem @{off}")
    return struct.unpack_from("<i", data, off)[0]


def _u16(data: bytes, off: int) -> int:
    if off + 2 > len(data):
        raise AxmlError(f"odczyt u16 poza buforem @{off}")
    return struct.unpack_from("<H", data, off)[0]


def _utf8_len(data: bytes, off: int) -> tuple[int, int]:
    """Długość UTF-8 w puli stringów Android (char-len + byte-len). Zwraca (nowy_off, n_bytes)."""
    b0 = data[off]
    off += 1
    if b0 & 0x80:
        b0 = ((b0 & 0x7F) << 8) | data[off]
        off += 1
    b1 = data[off]
    off += 1
    if b1 & 0x80:
        b1 = ((b1 & 0x7F) << 8) | data[off]
        off += 1
    return off, b1


def _utf16_len(data: bytes, off: int) -> tuple[int, int]:
    """Długość UTF-16 w puli (w jednostkach 16-bit)."""
    n = _u16(data, off)
    off += 2
    if n & 0x8000:
        n = ((n & 0x7FFF) << 16) | _u16(data, off)
        off += 2
    return off, n


class _StringPool:
    """Pula stringów z chunku RES_STRING_POOL."""

    def __init__(self, data: bytes, chunk_off: int) -> None:
        hdr_size = _u16(data, chunk_off + 2)
        chunk_size = _u32(data, chunk_off + 4)
        self._end = chunk_off + chunk_size
        body = chunk_off + 8
        string_count = _u32(data, body)
        _style_count = _u32(data, body + 4)
        flags = _u32(data, body + 8)
        strings_start = _u32(data, body + 12)
        self._utf8 = bool(flags & UTF8_FLAG)
        self._data = data
        self._base = chunk_off + strings_start
        off_table = chunk_off + hdr_size
        self._offsets = [
            _u32(data, off_table + i * 4) for i in range(string_count)
        ]

    def get(self, idx: int) -> str:
        if idx < 0 or idx >= len(self._offsets):
            return ""
        off = self._base + self._offsets[idx]
        if self._utf8:
            off, nbytes = _utf8_len(self._data, off)
            raw = self._data[off : off + nbytes]
            return raw.decode("utf-8", errors="replace")
        off, nchars = _utf16_len(self._data, off)
        raw = self._data[off : off + nchars * 2]
        return raw.decode("utf-16le", errors="replace")


def _fmt_typed(pool: _StringPool, raw_str: int, datatype: int, data: int) -> str:
    """Wartość atrybutu jak w zdekodowanym manifeście apktool."""
    if datatype == TYPE_STRING:
        return pool.get(raw_str if raw_str >= 0 else data)
    if datatype == TYPE_INT_BOOLEAN:
        return "true" if data else "false"
    if datatype == TYPE_INT_HEX:
        return f"0x{data:x}"
    if datatype == TYPE_INT_DEC:
        return str(data if data < 0x80000000 else data - 0x100000000)
    if datatype == TYPE_REFERENCE:
        return f"@{data:08x}"
    if datatype == TYPE_ATTRIBUTE:
        return f"?{data:08x}"
    if datatype == TYPE_FLOAT:
        return str(struct.unpack("<f", struct.pack("<I", data))[0])
    if datatype == TYPE_NULL:
        return ""
    if datatype in (
        TYPE_INT_COLOR_ARGB8,
        TYPE_INT_COLOR_RGB8,
        TYPE_INT_COLOR_ARGB4,
        TYPE_INT_COLOR_RGB4,
    ):
        return f"#{data:08x}"
    if datatype == TYPE_DIMENSION:
        return str(data)
    if datatype == TYPE_FRACTION:
        return str(data)
    return str(data)


def _esc(val: str) -> str:
    return xml.sax.saxutils.escape(val, {'"': "&quot;"})


# Komentarz: PM czyta android:value / android:resource po ID z resource map,
# nie po nazwie w puli. Złe ID → INSTALL_PARSE_FAILED_MANIFEST_MALFORMED
# („<meta-data> requires an android:value or android:resource”).
# 0x01010024 = value, 0x01010025 = resource (android.R.attr / public.xml).
# Reszta: resource map stock IG + publiczne attr manifestu bez kolizji ID.
ANDROID_ATTR_VALUE = 0x01010024
ANDROID_ATTR_RESOURCE = 0x01010025
_ANDROID_ATTR = {
    0x01010000: "theme",
    0x01010001: "label",
    0x01010002: "icon",
    0x01010003: "name",
    0x01010005: "allowClearUserData",
    0x01010006: "permission",
    0x01010007: "readPermission",
    0x01010008: "writePermission",
    0x01010009: "protectionLevel",
    0x0101000b: "sharedUserId",
    0x0101000c: "hasCode",
    0x0101000d: "persistent",
    0x0101000e: "enabled",
    0x0101000f: "debuggable",
    0x01010010: "exported",
    0x01010011: "process",
    0x01010012: "taskAffinity",
    0x01010013: "multiprocess",
    0x01010015: "clearTaskOnLaunch",
    0x01010016: "stateNotNeeded",
    0x01010017: "excludeFromRecents",
    0x01010018: "authorities",
    0x0101001b: "grantUriPermissions",
    0x0101001c: "priority",
    0x0101001d: "launchMode",
    0x0101001e: "screenOrientation",
    0x0101001f: "configChanges",
    0x01010020: "description",
    0x01010024: "value",
    0x01010025: "resource",
    0x01010026: "mimeType",
    0x01010027: "scheme",
    0x01010028: "host",
    0x01010029: "port",
    0x0101002a: "path",
    0x0101002b: "pathPrefix",
    0x0101002c: "pathPattern",
    0x010100dd: "fitsSystemWindows",
    0x01010202: "targetActivity",
    0x01010203: "alwaysRetainTaskState",
    0x01010204: "allowTaskReparenting",
    0x0101020c: "minSdkVersion",
    0x0101021b: "versionCode",
    0x0101021c: "versionName",
    0x0101022b: "windowSoftInputMode",
    0x0101022d: "noHistory",
    0x0101026c: "anyDensity",
    0x01010270: "targetSdkVersion",
    0x01010271: "maxSdkVersion",
    0x01010272: "testOnly",
    0x0101027f: "backupAgent",
    0x01010280: "allowBackup",
    0x01010281: "glEsVersion",
    0x01010284: "smallScreens",
    0x01010285: "normalScreens",
    0x01010286: "largeScreens",
    0x0101028e: "required",
    0x010102b4: "vmSafeMode",
    0x010102b7: "installLocation",
    0x010102bf: "xlargeScreens",
    0x010102d3: "hardwareAccelerated",
    0x01010303: "parentActivityName",
    0x0101031f: "uiOptions",
    0x0101033a: "fontFamily",
    0x01010354: "logo",
    0x0101035a: "largeHeap",
    0x0101036a: "stopWithTask",
    0x010103a9: "isolatedProcess",
    0x010103af: "supportsRtl",
    0x010103c9: "showOnLockScreen",
    0x010103e8: "documentLaunchMode",
    0x010103e9: "maxRecents",
    0x010103ea: "autoRemoveFromRecents",
    0x010103eb: "relinquishTaskIdentity",
    0x010103f3: "resumeOnTaskLaunch",
    0x0101040d: "permissionFlags",
    0x0101044d: "enableVrMode",
    0x0101045a: "roundIcon",
    0x01010473: "fullBackupOnly",
    0x010104ea: "extractNativeLibs",
    0x010104eb: "fullBackupContent",
    0x010104ee: "autoVerify",
    0x010104f6: "resizeableActivity",
    0x010104f7: "supportsPictureInPicture",
    0x01010505: "directBootAware",
    0x01010527: "networkSecurityConfig",
    0x01010531: "visibleToInstantApps",
    0x0101054e: "forceQueryable",
    0x01010569: "showWhenLocked",
    0x0101056a: "turnScreenOn",
    0x01010572: "compileSdkVersion",
    0x01010573: "compileSdkVersionCodename",
    0x0101057a: "appComponentFactory",
    0x0101057e: "allowNativeHeapPointerTagging",
    0x0101057f: "allowAudioPlaybackCapture",
    0x01010587: "requestForegroundServiceExemption",
    0x01010593: "localeConfig",
    0x01010597: "useAppZygote",
    0x01010599: "foregroundServiceType",
    0x0101059a: "knownActivityEmbeddingCerts",
    0x010105b5: "enableOnBackInvokedCallback",
    0x01010603: "requestLegacyExternalStorage",
    0x0101063e: "dataExtractionRules",
    0x01010644: "usesPermissionFlags",
    0x0101064e: "requiredSplitTypes",
    0x0101064f: "splitTypes",
    0x010106a1: "allowCrossUidActivitySwitchFromBelow",
}


def _android_attr_name(res_id: int) -> str:
    """Nazwa atrybutu frameworku albo placeholder z ID."""
    return _ANDROID_ATTR.get(res_id, f"attr_{res_id:08x}")


# Komentarz: URI frameworku — atrybuty android:* i mapa zasobów
ANDROID_NS_URI = "http://schemas.android.com/apk/res/android"
XMLNS_URI = "http://www.w3.org/2000/xmlns/"
# Komentarz: xmlns:foo="..." w pierwszym tagu (ET gubi zduplikowane URI)
_NS_DECL_RE = re.compile(r'\sxmlns(?::([A-Za-z_][\w.-]*))?="([^"]*)"')
_ATTR_PLACEHOLDER_RE = re.compile(r"^attr_([0-9a-fA-F]{8})$")
# Komentarz: nazwa atrybutu → android.R.attr (pierwszy ID wygrywa)
_ATTR_TO_ID: dict[str, int] = {}
for _rid, _name in _ANDROID_ATTR.items():
    _ATTR_TO_ID.setdefault(_name, _rid)


def _pad4(blob: bytes) -> bytes:
    """Dopełnij do granicy 4 bajtów (chunki AXML)."""
    n = (-len(blob)) % 4
    return blob if n == 0 else blob + (b"\x00" * n)


def _encode_utf16_pool_string(s: str) -> bytes:
    """Wpis puli UTF-16 (długość + dane + terminator)."""
    nchars = len(s.encode("utf-16-le")) // 2
    if nchars > 0x7FFF:
        hdr = struct.pack("<HH", ((nchars >> 16) & 0x7FFF) | 0x8000, nchars & 0xFFFF)
    else:
        hdr = struct.pack("<H", nchars)
    return hdr + s.encode("utf-16-le") + b"\x00\x00"


def _attr_res_id(name: str) -> int:
    """ID android.R.attr albo 0; placeholder attr_XXXXXXXX z dekodera."""
    m = _ATTR_PLACEHOLDER_RE.match(name)
    if m:
        return int(m.group(1), 16)
    return _ATTR_TO_ID.get(name, 0)


def _split_clark(key: str) -> tuple[str, str]:
    """Clark notation `{uri}local` → (uri, local)."""
    if key.startswith("{") and "}" in key:
        uri, local = key[1:].split("}", 1)
        return uri, local
    if ":" in key:
        return "", key
    return "", key


def _collect_ns_decls(xml: str) -> list[tuple[str, str]]:
    """Prefix+URI z pierwszego tagu — kolejność jak w tekście."""
    start = xml.find("<manifest")
    if start < 0:
        start = xml.find("<")
    if start < 0:
        return []
    gt = xml.find(">", start)
    head = xml[start:gt] if gt > start else xml[start : start + 4000]
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for m in _NS_DECL_RE.finditer(head):
        prefix = m.group(1) or ""
        uri = m.group(2)
        pair = (prefix, uri)
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    return out


def _typed_nonstring(val: str) -> tuple[int, int] | None:
    """Typ+dane AXML albo None gdy zostaje TYPE_STRING."""
    if val in ("true", "false"):
        return TYPE_INT_BOOLEAN, (1 if val == "true" else 0)
    if val.startswith(("0x", "0X")):
        try:
            return TYPE_INT_HEX, int(val, 16) & 0xFFFFFFFF
        except ValueError:
            return None
    if val.startswith("@") and re.fullmatch(r"@[0-9a-fA-F]+", val):
        return TYPE_REFERENCE, int(val[1:], 16) & 0xFFFFFFFF
    if val.startswith("?") and re.fullmatch(r"\?[0-9a-fA-F]+", val):
        return TYPE_ATTRIBUTE, int(val[1:], 16) & 0xFFFFFFFF
    if re.fullmatch(r"-?\d+", val):
        return TYPE_INT_DEC, int(val) & 0xFFFFFFFF
    return None


class _PoolBuilder:
    """Pula stringów: najpierw nazwy atrybutów z ID (resource map)."""

    def __init__(self) -> None:
        self.strings: list[str] = []
        self._index: dict[str, int] = {}
        self.res_ids: list[int] = []

    def add(self, s: str) -> int:
        """Dodaj string; ten sam tekst → ten sam indeks."""
        if s in self._index:
            return self._index[s]
        idx = len(self.strings)
        self.strings.append(s)
        self._index[s] = idx
        return idx

    def intern_attr_names(self, names: list[str]) -> None:
        """Wstaw nazwy atrybutów z android.R.attr na początek puli."""
        seen: set[str] = set()
        for name in names:
            if not name or name in seen:
                continue
            rid = _attr_res_id(name)
            if not rid:
                continue
            seen.add(name)
            self.add(name)
            self.res_ids.append(rid)


def _build_string_pool(strings: list[str]) -> bytes:
    """Chunk RES_STRING_POOL w UTF-16 (lepiej tolerowany przez PM)."""
    encoded = [_encode_utf16_pool_string(s) for s in strings]
    offsets: list[int] = []
    pos = 0
    parts: list[bytes] = []
    for rec in encoded:
        offsets.append(pos)
        parts.append(rec)
        pos += len(rec)
    data = _pad4(b"".join(parts))
    header_size = 28
    count = len(strings)
    strings_start = header_size + 4 * count
    size = strings_start + len(data)
    header = struct.pack(
        "<HHIIIIII",
        RES_STRING_POOL,
        header_size,
        size,
        count,
        0,
        0,
        strings_start,
        0,
    )
    off_tbl = b"".join(struct.pack("<I", o) for o in offsets)
    return header + off_tbl + data


def _xml_node(typ: int, extra: bytes, line: int = 1) -> bytes:
    """Nagłówek ResXMLTree_node + ciało (ns / element)."""
    body = struct.pack("<I i", line, -1) + extra
    size = 8 + len(body)
    return struct.pack("<HHI", typ, 16, size) + body


def encode_axml(xml: str) -> bytes:
    """Zamień tekstowy AndroidManifest na binarny AXML (pod apktool b / ZIP).

    Resource map musi mieć oficjalne android.R.attr (value=0x01010024).
    """
    text = xml.lstrip()
    if not text:
        raise AxmlError("pusty manifest XML")
    root = ET.fromstring(xml)
    ns_decls = _collect_ns_decls(xml)
    uri_to_prefix: dict[str, str] = {}
    for prefix, uri in ns_decls:
        if uri and uri not in uri_to_prefix:
            uri_to_prefix[uri] = prefix
        elif uri and prefix == "android":
            uri_to_prefix[uri] = prefix

    attr_names: list[str] = []

    def walk_collect(el: ET.Element) -> None:
        attr_names.append(_split_clark(el.tag)[1])
        for key in el.attrib:
            uri, local = _split_clark(key)
            if uri == XMLNS_URI or local.startswith("xmlns"):
                continue
            attr_names.append(local)
        for child in list(el):
            walk_collect(child)

    walk_collect(root)

    pool = _PoolBuilder()
    pool.intern_attr_names(attr_names)
    for prefix, uri in ns_decls:
        if prefix:
            pool.add(prefix)
        if uri:
            pool.add(uri)

    def intern_tree(el: ET.Element) -> None:
        pool.add(_split_clark(el.tag)[1])
        for key, val in el.attrib.items():
            uri, local = _split_clark(key)
            if uri == XMLNS_URI or local.startswith("xmlns"):
                continue
            pool.add(local)
            if _typed_nonstring(val) is None:
                pool.add(val)
        for child in list(el):
            intern_tree(child)

    intern_tree(root)

    chunks: list[bytes] = [_build_string_pool(pool.strings)]
    if pool.res_ids:
        n = len(pool.res_ids)
        chunks.append(
            struct.pack("<HHI", RES_XML_RESOURCE_MAP, 8, 8 + 4 * n)
            + b"".join(struct.pack("<I", rid) for rid in pool.res_ids)
        )

    for prefix, uri in ns_decls:
        extra = struct.pack(
            "<ii",
            pool.add(prefix) if prefix else -1,
            pool.add(uri) if uri else -1,
        )
        chunks.append(_xml_node(RES_XML_START_NAMESPACE, extra))

    def emit_element(el: ET.Element) -> None:
        tag = _split_clark(el.tag)[1]
        tag_ns = _split_clark(el.tag)[0]
        attrs: list[tuple[str, str, str]] = []
        for key, val in el.attrib.items():
            uri, local = _split_clark(key)
            if uri == XMLNS_URI or key == "xmlns" or key.startswith("xmlns:"):
                continue
            attrs.append((uri, local, val))
        id_idx = class_idx = style_idx = 0
        attr_blob = b""
        for i, (uri, local, val) in enumerate(attrs):
            if local == "id":
                id_idx = i + 1
            elif local == "class":
                class_idx = i + 1
            elif local == "style":
                style_idx = i + 1
            ns_idx = pool.add(uri) if uri else -1
            name_idx = pool.add(local)
            typed = _typed_nonstring(val)
            if typed is None:
                sidx = pool.add(val)
                raw, datatype, data = sidx, TYPE_STRING, sidx
            else:
                raw, (datatype, data) = -1, typed
            attr_blob += struct.pack(
                "<iii HBB I",
                ns_idx,
                name_idx,
                raw,
                8,
                0,
                datatype,
                data,
            )
        ext = struct.pack(
            "<ii HHHHHH",
            pool.add(tag_ns) if tag_ns else -1,
            pool.add(tag),
            20,
            20,
            len(attrs),
            id_idx,
            class_idx,
            style_idx,
        )
        chunks.append(_xml_node(RES_XML_START_ELEMENT, ext + attr_blob))
        for child in list(el):
            emit_element(child)
        end_extra = struct.pack(
            "<ii",
            pool.add(tag_ns) if tag_ns else -1,
            pool.add(tag),
        )
        chunks.append(_xml_node(RES_XML_END_ELEMENT, end_extra))

    emit_element(root)
    for prefix, uri in reversed(ns_decls):
        extra = struct.pack(
            "<ii",
            pool.add(prefix) if prefix else -1,
            pool.add(uri) if uri else -1,
        )
        chunks.append(_xml_node(RES_XML_END_NAMESPACE, extra))

    body = b"".join(chunks)
    return struct.pack("<HHI", AXML_TYPE, 8, 8 + len(body)) + body


def axml_resource_map(data: bytes) -> list[tuple[str, int]]:
    """Pary (nazwa z puli, android.R.attr) z RES_XML_RESOURCE_MAP."""
    if not is_binary_axml(data):
        raise AxmlError("to nie jest binarny AXML")
    file_size = min(_u32(data, 4), len(data))
    pool: _StringPool | None = None
    res_map: list[int] = []
    pos = 8
    while pos + 8 <= file_size:
        typ = _u16(data, pos)
        size = _u32(data, pos + 4)
        if size < 8 or pos + size > len(data):
            break
        if typ == RES_STRING_POOL:
            pool = _StringPool(data, pos)
        elif typ == RES_XML_RESOURCE_MAP:
            n = (size - 8) // 4
            res_map = [_u32(data, pos + 8 + i * 4) for i in range(n)]
        pos += size
    if pool is None:
        return []
    return [(pool.get(i), rid) for i, rid in enumerate(res_map)]


def decode_axml(data: bytes) -> str:
    """Zamień bufor AXML na UTF-8 XML (AndroidManifest)."""
    if not is_binary_axml(data):
        raise AxmlError("to nie jest binarny AXML")
    file_size = _u32(data, 4)
    if file_size > len(data):
        file_size = len(data)

    res_map: list[int] = []
    pool: _StringPool | None = None
    ns_stack: list[tuple[str, str]] = []
    # prefix → uri, aktualne
    ns_by_prefix: dict[str, str] = {}
    uri_to_prefix: dict[str, str] = {}
    root_ns: list[tuple[str, str]] = []
    out: list[str] = ['<?xml version="1.0" encoding="utf-8"?>\n']
    indent = 0
    pos = 8

    def pool_get(idx: int) -> str:
        if pool is None or idx < 0:
            return ""
        return pool.get(idx)

    while pos + 8 <= file_size:
        typ = _u16(data, pos)
        _hdr = _u16(data, pos + 2)
        size = _u32(data, pos + 4)
        if size < 8 or pos + size > len(data):
            break
        if typ == RES_STRING_POOL:
            pool = _StringPool(data, pos)
        elif typ == RES_XML_RESOURCE_MAP:
            n = (size - 8) // 4
            res_map = [_u32(data, pos + 8 + i * 4) for i in range(n)]
        elif typ == RES_XML_START_NAMESPACE:
            prefix = pool_get(_i32(data, pos + 16))
            uri = pool_get(_i32(data, pos + 20))
            ns_stack.append((prefix, uri))
            ns_by_prefix[prefix] = uri
            uri_to_prefix[uri] = prefix
            if (prefix, uri) not in root_ns:
                root_ns.append((prefix, uri))
        elif typ == RES_XML_END_NAMESPACE:
            if ns_stack:
                prefix, uri = ns_stack.pop()
                ns_by_prefix.pop(prefix, None)
                uri_to_prefix.pop(uri, None)
        elif typ == RES_XML_START_ELEMENT:
            ns_idx = _i32(data, pos + 16)
            name_idx = _i32(data, pos + 20)
            name = pool_get(name_idx)
            ns_uri = pool_get(ns_idx)
            attr_start = _u16(data, pos + 24)
            attr_size = _u16(data, pos + 26)
            attr_count = _u16(data, pos + 28)
            # Komentarz: attrStart liczy się od ResXMLTree_attrExt (pos+16), nie od chunka
            attr_off = pos + 16 + attr_start
            attrs: list[str] = []
            if indent == 0:
                for pfx, uri in root_ns:
                    if pfx:
                        attrs.append(f'xmlns:{pfx}="{_esc(uri)}"')
                    else:
                        attrs.append(f'xmlns="{_esc(uri)}"')
            for i in range(attr_count):
                a = attr_off + i * max(attr_size, 20)
                a_ns = _i32(data, a)
                a_name = _i32(data, a + 4)
                a_raw = _i32(data, a + 8)
                packed = _u32(data, a + 12)
                a_data = _u32(data, a + 16)
                datatype = (packed >> 24) & 0xFF
                key = pool_get(a_name)
                if not key and 0 <= a_name < len(res_map):
                    key = _android_attr_name(res_map[a_name])
                prefix = uri_to_prefix.get(pool_get(a_ns), "")
                qname = f"{prefix}:{key}" if prefix and key else key
                val = _fmt_typed(pool, a_raw, datatype, a_data) if pool else ""
                attrs.append(f'{qname}="{_esc(val)}"')
            pad = "    " * indent
            attr_s = (" " + " ".join(attrs)) if attrs else ""
            # Komentarz: puste elementy i tak zamykamy osobnym END_ELEMENT
            out.append(f"{pad}<{name}{attr_s}>\n")
            indent += 1
        elif typ == RES_XML_END_ELEMENT:
            indent = max(0, indent - 1)
            name = pool_get(_i32(data, pos + 20))
            pad = "    " * indent
            out.append(f"{pad}</{name}>\n")
        elif typ == RES_XML_CDATA:
            pass
        pos += size

    return "".join(out)


def ensure_text_manifest(path) -> bool:
    """Jeśli `path` to AXML, nadpisz go tekstem XML. True gdy dekodowano."""
    p = Path(path)
    data = p.read_bytes()
    if not is_binary_axml(data):
        return False
    xml = decode_axml(data)
    p.write_text(xml, encoding="utf-8")
    return True


def ensure_binary_manifest(path) -> bytes:
    """Tekstowy manifest → binarny AXML (nadpisuje `path`). Zwraca bajty."""
    p = Path(path)
    data = p.read_bytes()
    if is_binary_axml(data):
        return data
    axml = encode_axml(data.decode("utf-8"))
    p.write_bytes(axml)
    return axml
