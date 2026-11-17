"""HTML byte decoding following the WHATWG Encoding Standard and HTML encoding sniffing.

Replaces the original's ``html-encoding-sniffer`` (BOM, transport-layer label, ``<meta>``
prescan of the first 1024 bytes, Windows-1252 default) and ``@exodus/bytes`` label
lookup and legacy decoding. Labels and canonical names come from the Encoding Standard;
decoding uses Python's codecs (with WHATWG's C1 pass-through for Windows-1252).
"""
from __future__ import annotations

import codecs

# Canonical name -> (Python codec, labels). From the WHATWG Encoding Standard.
_ENCODINGS = {
    "UTF-8": ("utf-8", "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8"),
    "IBM866": ("cp866", "866 cp866 csibm866 ibm866"),
    "ISO-8859-2": ("iso8859_2", "csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 "
                                "iso_8859-2:1987 l2 latin2"),
    "ISO-8859-3": ("iso8859_3", "csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 "
                                "iso_8859-3:1988 l3 latin3"),
    "ISO-8859-4": ("iso8859_4", "csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 "
                                "iso_8859-4:1988 l4 latin4"),
    "ISO-8859-5": ("iso8859_5", "csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 "
                                "iso_8859-5 iso_8859-5:1988"),
    "ISO-8859-6": ("iso8859_6", "arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 "
                                "iso-8859-6 iso-8859-6-e iso-8859-6-i iso-ir-127 iso8859-6 iso88596 "
                                "iso_8859-6 iso_8859-6:1987"),
    "ISO-8859-7": ("iso8859_7", "csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 "
                                "iso8859-7 iso88597 iso_8859-7 iso_8859-7:1987 sun_eu_greek"),
    "ISO-8859-8": ("iso8859_8", "csiso88598e csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-ir-138 "
                                "iso8859-8 iso88598 iso_8859-8 iso_8859-8:1988 visual"),
    "ISO-8859-8-I": ("iso8859_8", "csiso88598i iso-8859-8-i logical"),
    "ISO-8859-10": ("iso8859_10", "csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6"),
    "ISO-8859-13": ("iso8859_13", "iso-8859-13 iso8859-13 iso885913"),
    "ISO-8859-14": ("iso8859_14", "iso-8859-14 iso8859-14 iso885914"),
    "ISO-8859-15": ("iso8859_15", "csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9"),
    "ISO-8859-16": ("iso8859_16", "iso-8859-16"),
    "KOI8-R": ("koi8_r", "cskoi8r koi koi8 koi8-r koi8_r"),
    "KOI8-U": ("koi8_u", "koi8-ru koi8-u"),
    "macintosh": ("mac_roman", "csmacintosh mac macintosh x-mac-roman"),
    "windows-874": ("cp874", "dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874"),
    "windows-1250": ("cp1250", "cp1250 windows-1250 x-cp1250"),
    "windows-1251": ("cp1251", "cp1251 windows-1251 x-cp1251"),
    "windows-1252": ("cp1252", "ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 iso-ir-100 "
                               "iso8859-1 iso88591 iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii "
                               "windows-1252 x-cp1252"),
    "windows-1253": ("cp1253", "cp1253 windows-1253 x-cp1253"),
    "windows-1254": ("cp1254", "cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 "
                               "iso_8859-9:1989 l5 latin5 windows-1254 x-cp1254"),
    "windows-1255": ("cp1255", "cp1255 windows-1255 x-cp1255"),
    "windows-1256": ("cp1256", "cp1256 windows-1256 x-cp1256"),
    "windows-1257": ("cp1257", "cp1257 windows-1257 x-cp1257"),
    "windows-1258": ("cp1258", "cp1258 windows-1258 x-cp1258"),
    "x-mac-cyrillic": ("mac_cyrillic", "x-mac-cyrillic x-mac-ukrainian"),
    "GBK": ("gb18030", "chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk"),
    "gb18030": ("gb18030", "gb18030"),
    "Big5": ("big5hkscs", "big5 big5-hkscs cn-big5 csbig5 x-x-big5"),
    "EUC-JP": ("euc_jp", "cseucpkdfmtjapanese euc-jp x-euc-jp"),
    "ISO-2022-JP": ("iso2022_jp", "csiso2022jp iso-2022-jp"),
    "Shift_JIS": ("cp932", "csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis"),
    "EUC-KR": ("cp949", "cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 "
                        "ksc5601 ksc_5601 windows-949"),
    "replacement": ("", "csiso2022kr hz-gb-2312 iso-2022-cn iso-2022-cn-ext iso-2022-kr replacement"),
    "UTF-16BE": ("utf-16-be", "unicodefffe utf-16be"),
    "UTF-16LE": ("utf-16-le", "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff utf-16 utf-16le"),
    "x-user-defined": ("", "x-user-defined"),
}
_LABELS = {label: name for name, (_, labels) in _ENCODINGS.items() for label in labels.split()}
_ASCII_WS = b"\t\n\f\r "


def label_to_name(label) -> str | None:
    """The canonical encoding name for a WHATWG label, or None."""
    if not isinstance(label, str):
        return None
    return _LABELS.get(label.strip("\t\n\f\r ").lower())


def bom_encoding(data: bytes) -> str | None:
    if data.startswith(b"\xef\xbb\xbf"):
        return "UTF-8"
    if data.startswith(b"\xfe\xff"):
        return "UTF-16BE"
    if data.startswith(b"\xff\xfe"):
        return "UTF-16LE"
    return None


def _c1_passthrough(error: UnicodeDecodeError):
    return "".join(chr(byte) for byte in error.object[error.start:error.end]), error.end


codecs.register_error("lineage-c1", _c1_passthrough)


def decode(data: bytes, name: str) -> str:
    """Decode with the BOM taking precedence over ``name`` (WHATWG "decode")."""
    bom = bom_encoding(data)
    if bom:
        data = data[3:] if bom == "UTF-8" else data[2:]
        name = bom
    if name == "replacement":
        return "�" if data else ""
    if name == "x-user-defined":
        return "".join(chr(byte) if byte < 0x80 else chr(0xF780 + byte - 0x80) for byte in data)
    codec = _ENCODINGS[name][0]
    if name == "windows-1252":
        return data.decode(codec, "lineage-c1")
    return data.decode(codec, "replace")


# --- prescan (HTML Standard "prescan a byte stream to determine its encoding") --------

def _attribute(data: bytes, pos: int):
    """``(name, value, position)`` or ``(None, None, position)``."""
    length = len(data)
    while pos < length and (data[pos] in _ASCII_WS or data[pos] == 0x2F):
        pos += 1
    if pos >= length or data[pos] == 0x3E:
        return None, None, pos
    name = bytearray()
    value = bytearray()
    while pos < length:
        byte = data[pos]
        if byte == 0x3D and name:
            pos += 1
            break
        if byte in _ASCII_WS:
            while pos < length and data[pos] in _ASCII_WS:
                pos += 1
            if pos >= length or data[pos] != 0x3D:
                return bytes(name).decode("latin-1"), "", pos
            pos += 1
            break
        if byte in (0x2F, 0x3E):
            return bytes(name).decode("latin-1"), "", pos
        name.append(byte + 0x20 if 0x41 <= byte <= 0x5A else byte)
        pos += 1
    else:
        return bytes(name).decode("latin-1"), "", pos
    while pos < length and data[pos] in _ASCII_WS:
        pos += 1
    if pos >= length:
        return bytes(name).decode("latin-1"), "", pos
    byte = data[pos]
    if byte in (0x22, 0x27):
        quote = byte
        pos += 1
        while pos < length and data[pos] != quote:
            value.append(data[pos] + 0x20 if 0x41 <= data[pos] <= 0x5A else data[pos])
            pos += 1
        pos += 1
        return bytes(name).decode("latin-1"), bytes(value).decode("latin-1"), pos
    if byte == 0x3E:
        return bytes(name).decode("latin-1"), "", pos
    while pos < length and data[pos] not in _ASCII_WS and data[pos] != 0x3E:
        value.append(data[pos] + 0x20 if 0x41 <= data[pos] <= 0x5A else data[pos])
        pos += 1
    return bytes(name).decode("latin-1"), bytes(value).decode("latin-1"), pos


def _charset_from_content(text: str) -> str | None:
    position = 0
    lowered = text.lower()
    while True:
        position = lowered.find("charset", position)
        if position < 0:
            return None
        position += 7
        while position < len(text) and text[position] in "\t\n\f\r ":
            position += 1
        if position < len(text) and text[position] == "=":
            break
    position += 1
    while position < len(text) and text[position] in "\t\n\f\r ":
        position += 1
    if position >= len(text):
        return None
    if text[position] in "\"'":
        quote = text[position]
        end = text.find(quote, position + 1)
        return None if end < 0 else text[position + 1:end]
    end = position
    while end < len(text) and text[end] not in "\t\n\f\r ;":
        end += 1
    return text[position:end] or None


def prescan(data: bytes) -> str | None:
    data = data[:1024]
    length = len(data)
    pos = 0
    while pos < length:
        if data.startswith(b"<!--", pos):
            end = data.find(b"-->", pos + 2)
            if end < 0:
                return None
            pos = end + 3
            continue
        if data[pos:pos + 5].lower() == b"<meta" and pos + 5 < length and data[pos + 5] in b"\t\n\f\r /":
            pos += 6
            seen: set[str] = set()
            got_pragma = False
            need_pragma = None
            charset = None
            while True:
                name, value, pos = _attribute(data, pos)
                if name is None:
                    break
                if name in seen:
                    continue
                seen.add(name)
                if name == "http-equiv" and value == "content-type":
                    got_pragma = True
                elif name == "content" and charset is None:
                    found = _charset_from_content(value)
                    if found is not None:
                        charset, need_pragma = found, True
                elif name == "charset":
                    charset, need_pragma = value, False
            if need_pragma is not None and not (need_pragma and not got_pragma) and charset is not None:
                encoding = label_to_name(charset)
                if encoding is not None:
                    if encoding in ("UTF-16BE", "UTF-16LE"):
                        encoding = "UTF-8"
                    if encoding == "x-user-defined":
                        encoding = "windows-1252"
                    return encoding
            continue
        if data[pos] == 0x3C and pos + 1 < length and (
                chr(data[pos + 1]).isascii() and chr(data[pos + 1]).isalpha()
                or (data[pos + 1] == 0x2F and pos + 2 < length and chr(data[pos + 2]).isascii()
                    and chr(data[pos + 2]).isalpha())):
            while pos < length and data[pos] not in _ASCII_WS and data[pos] != 0x3E:
                pos += 1
            while True:
                name, _, pos = _attribute(data, pos)
                if name is None:
                    break
            pos += 1
            continue
        if data.startswith((b"<!", b"</", b"<?"), pos):
            end = data.find(b">", pos + 2)
            if end < 0:
                return None
            pos = end + 1
            continue
        pos += 1
    return None


def sniff(data: bytes, transport_label: str | None = None, default: str = "windows-1252") -> str:
    """The encoding a browser would use for these HTML bytes."""
    bom = bom_encoding(data)
    if bom:
        return bom
    if transport_label:
        name = label_to_name(transport_label)
        if name:
            return name
    return prescan(data) or default
