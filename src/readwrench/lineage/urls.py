"""Source-URL identity: the parts of WHATWG URL parsing that ``new URL(value).href`` applies
to an absolute http(s) URL (scheme and host case, default ports, IPv4 forms, dot segments,
percent-encoding of path and query), with the fragment dropped.

Internationalized hosts use Python's IDNA 2003 codec rather than UTS #46; the two differ
only for rare code points. Hosts that the codec rejects make the URL invalid.
"""
from __future__ import annotations

import re

from .fileio import InputError

_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+\-.]*):")
_FORBIDDEN_HOST = set("\x00\t\n\r #/:<>?@[\\]^|%") | {chr(c) for c in range(0x20)} | {"\x7f"}
_PATH_SET = set(' "#<>?^`{}')
_QUERY_SET = set(' "#<>\'')
_DEFAULT_PORT = {"http": "80", "https": "443"}


class URLError(ValueError):
    pass


def _encode(text: str, extra: set[str]) -> str:
    out = []
    for char in text:
        code = ord(char)
        if code < 0x21 or code > 0x7E or char in extra:
            out.extend(f"%{byte:02X}" for byte in char.encode("utf-8", "surrogatepass"))
        else:
            out.append(char)
    return "".join(out)


def _percent_decode(text: str) -> bytes:
    data = text.encode("utf-8", "surrogatepass")
    return re.sub(rb"%([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), data)


def _ipv4_number(part: str) -> int | None:
    if not part:
        return None
    base = 10
    if part[:2].lower() == "0x":
        base, part = 16, part[2:]
    elif len(part) > 1 and part[0] == "0":
        base, part = 8, part[1:]
    if part == "":
        return 0
    try:
        return int(part, base) if re.fullmatch(r"[0-9A-Fa-f]+", part) else None
    except ValueError:
        return None


def _host(text: str) -> str:
    if text.startswith("["):
        if not text.endswith("]") or not re.fullmatch(r"\[[0-9A-Fa-f:.]+\]", text):
            raise URLError("invalid IPv6 host")
        return text.lower()
    try:
        domain = _percent_decode(text).decode("utf-8")
    except UnicodeDecodeError as error:
        raise URLError("invalid host encoding") from error
    if not domain.isascii():
        try:
            domain = domain.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise URLError("invalid internationalized host") from error
    domain = domain.lower()
    if not domain or any(char in _FORBIDDEN_HOST for char in domain):
        raise URLError("invalid host")
    labels = domain.split(".")
    if labels[-1] == "" and len(labels) > 1:
        labels = labels[:-1]
    last = labels[-1]
    if re.fullmatch(r"[0-9]+", last) or re.fullmatch(r"0[xX][0-9A-Fa-f]*", last):
        if len(labels) > 4:
            raise URLError("invalid IPv4 host")
        numbers = [_ipv4_number(label) for label in labels]
        if any(number is None for number in numbers):
            raise URLError("invalid IPv4 host")
        if any(number > 255 for number in numbers[:-1]) or numbers[-1] >= 256 ** (5 - len(numbers)):
            raise URLError("invalid IPv4 host")
        address = numbers[-1]
        for index, number in enumerate(numbers[:-1]):
            address += number * 256 ** (3 - index)
        return ".".join(str((address >> shift) & 255) for shift in (24, 16, 8, 0))
    return domain


def _path(text: str) -> str:
    segments = re.split(r"[/\\]", text)
    if segments and segments[0] == "":
        segments = segments[1:]
    output: list[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        lowered = segment.lower()
        if lowered in ("..", ".%2e", "%2e.", "%2e%2e"):
            if output:
                output.pop()
            if last:
                output.append("")
        elif lowered in (".", "%2e"):
            if last:
                output.append("")
        else:
            output.append(_encode(segment, _PATH_SET))
    return "/" + "/".join(output)


def parse_http_url(value: str) -> tuple[str, bool]:
    """``(href without fragment, has credentials)``, or raise :class:`URLError`.

    Non-http(s) schemes raise ``URLError("scheme")`` after a successful scheme parse.
    """
    text = value.strip("".join(chr(c) for c in range(0x21)))
    text = re.sub(r"[\t\n\r]", "", text)
    match = _SCHEME.match(text)
    if not match:
        raise URLError("missing scheme")
    scheme = match.group(1).lower()
    if scheme not in _DEFAULT_PORT:
        raise URLError("scheme")
    rest = text[match.end():]
    rest = re.sub(r"^[/\\]*", "", rest)
    end = len(rest)
    for position, char in enumerate(rest):
        if char in "/\\?#":
            end = position
            break
    authority, rest = rest[:end], rest[end:]
    credentials = False
    if "@" in authority:
        userinfo, authority = authority.rsplit("@", 1)
        user, _, password = userinfo.partition(":")
        credentials = bool(user or password)
    if authority.startswith("["):
        close = authority.find("]")
        host_text, port_text = (authority, "") if close < 0 else (authority[:close + 1], authority[close + 1:])
        if port_text and not port_text.startswith(":"):
            raise URLError("invalid port")
        port_text = port_text[1:]
    else:
        host_text, _, port_text = authority.partition(":")
    if not host_text:
        raise URLError("empty host")
    host = _host(host_text)
    port = ""
    if port_text:
        if not re.fullmatch(r"[0-9]+", port_text) or int(port_text) > 65535:
            raise URLError("invalid port")
        port = str(int(port_text))
        if port == _DEFAULT_PORT[scheme]:
            port = ""
    fragment_at = rest.find("#")
    if fragment_at >= 0:
        rest = rest[:fragment_at]
    query = None
    query_at = rest.find("?")
    if query_at >= 0:
        rest, query = rest[:query_at], rest[query_at + 1:]
    href = f"{scheme}://{host}{':' + port if port else ''}{_path(rest)}"
    if query is not None:
        href += "?" + _encode(query, _QUERY_SET)
    return href, credentials


def normalize_url(value) -> str:
    if not isinstance(value, str) or len(value) > 8192:
        raise InputError("Source URL must be a string of at most 8,192 characters")
    try:
        href, credentials = parse_http_url(value)
    except URLError as error:
        if str(error) == "scheme":
            raise InputError("Source URL must be HTTP(S), without embedded credentials") from None
        raise InputError(f"Invalid source URL: {value}") from None
    if credentials:
        raise InputError("Source URL must be HTTP(S), without embedded credentials")
    return href
