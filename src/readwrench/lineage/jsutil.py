"""Small helpers that keep JavaScript string semantics where saved output depends on them.

The original tool measured and clipped strings in UTF-16 code units, trimmed with
ECMAScript's whitespace set and serialized with ``JSON.stringify(value, null, 2)``.
Reports and JSON written by this port stay byte-compatible with those rules.
"""
from __future__ import annotations

import json
import re

# ECMAScript WhiteSpace + LineTerminator (what ``\\s`` and ``String.prototype.trim`` use).
WS_CHARS = "\t\n\u000b\u000c\r    -     　﻿"
WS = f"[{WS_CHARS}]"
WS_RUN = re.compile(f"{WS}+")
_TRIM = re.compile(f"^{WS}+|{WS}+$")
_SURROGATE = re.compile("[\ud800-\udfff]")


def js_len(text: str) -> int:
    """``text.length`` in JavaScript: UTF-16 code units."""
    if text.isascii():
        return len(text)
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def js_slice(text: str, end: int) -> str:
    """``text.slice(0, end)`` in UTF-16 code units.

    A cut through a surrogate pair keeps the lone high surrogate, as JavaScript does;
    :func:`to_utf8` and :func:`stringify` then render it exactly as Node.js would.
    """
    if text.isascii() or end <= 0:
        return text[:max(end, 0)]
    units = text.encode("utf-16-le", "surrogatepass")
    if len(units) // 2 <= end:
        return text
    head = units[:end * 2]
    last = int.from_bytes(head[-2:], "little")
    if 0xD800 <= last <= 0xDBFF:
        return head[:-2].decode("utf-16-le", "surrogatepass") + chr(last)
    return head.decode("utf-16-le", "surrogatepass")


def js_trim(text: str) -> str:
    return _TRIM.sub("", text)


def to_utf8(text: str) -> bytes:
    """Encode like Node.js writing a string as UTF-8 (lone surrogates become U+FFFD)."""
    return _SURROGATE.sub("�", text).encode("utf-8")


def stringify(value) -> str:
    """``JSON.stringify(value, null, 2)``."""
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    return _SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", text)


def number(value: float):
    """A JavaScript number as Python would need to serialize it (integral floats as int)."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value
