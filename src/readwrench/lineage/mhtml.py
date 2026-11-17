"""A tolerant MHTML (``multipart/related``) reader.

Replaces the original's ``mhtml-to-html`` ``parse()``: top-level headers (lower-cased
names), resources keyed by ``Content-Location`` (or ``cid:`` + ``Content-ID``), the main
document (the first part), base64 / quoted-printable transfer decoding and charset
decoding of text parts. Header lines without a name (as in some real Chrome captures)
are skipped silently, like the original parser. Recoverable structural problems are
reported as anomalies instead of failing: ``missing-final-boundary``,
``missing-boundary`` and ``index-synthesized`` (the main part has no location).
"""
from __future__ import annotations

import binascii
import quopri
import re

from .encoding import decode as decode_bytes
from .encoding import label_to_name


def _split_head(data: bytes) -> tuple[bytes, bytes]:
    match = re.search(rb"\r?\n\r?\n", data)
    if not match:
        return data, b""
    return data[:match.start()], data[match.end():]


def _headers(block: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    last = None
    for raw in re.split(rb"\r?\n", block):
        line = raw.decode("utf-8", "replace")
        if not line:
            continue
        if line[0] in " \t" and last is not None:
            headers[last] += line
            continue
        name, sep, value = line.partition(":")
        if not sep or not name.strip() or " " in name.strip():
            last = None
            continue
        key = name.strip().lower()
        if key not in headers:
            headers[key] = value.strip()
            last = key
        else:
            last = None
    return {key: value.strip() for key, value in headers.items()}


def _params(value: str) -> tuple[str, dict[str, str]]:
    kind, _, rest = value.partition(";")
    params: dict[str, str] = {}
    for match in re.finditer(r';?\s*([^=;\s]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]*)', ";" + rest):
        raw = match.group(2).strip()
        if raw.startswith('"'):
            raw = re.sub(r"\\(.)", r"\1", raw[1:-1])
        params.setdefault(match.group(1).lower(), raw)
    return kind.strip().lower(), params


def _body(data: bytes, headers: dict[str, str]):
    encoding = headers.get("content-transfer-encoding", "").lower()
    if encoding == "base64":
        try:
            data = binascii.a2b_base64(re.sub(rb"[^A-Za-z0-9+/=]", b"", data))
        except binascii.Error:
            data = b""
    elif encoding == "quoted-printable":
        data = quopri.decodestring(data)
    kind, params = _params(headers.get("content-type", "text/plain"))
    if kind.startswith("text/"):
        name = label_to_name(params.get("charset", "utf-8")) or "UTF-8"
        return decode_bytes(data, name)
    return data


def parse_mhtml(data: bytes) -> dict:
    """``{"headers", "index", "resources", "anomalies"}`` like mhtml-to-html's ``parse``."""
    anomalies: list[dict] = []
    head, body = _split_head(data)
    headers = _headers(head)
    kind, params = _params(headers.get("content-type", ""))
    parts: list[tuple[dict, bytes]] = []
    if kind.startswith("multipart/"):
        boundary = params.get("boundary")
        if not boundary:
            anomalies.append({"type": "missing-boundary"})
            parts.append(({"content-type": "text/html"}, body))
        else:
            delimiter = re.escape(b"--" + boundary.encode("utf-8", "replace"))
            pieces = re.split(rb"(?:^|\r?\n)" + delimiter + rb"(--)?[ \t]*(?:\r?\n|$)", body)
            # pieces: preamble, (close-flag, part)*
            closed = False
            for index in range(1, len(pieces), 2):
                if pieces[index] == b"--":
                    closed = True
                    break
                part = pieces[index + 1] if index + 1 < len(pieces) else b""
                part_head, part_body = _split_head(part)
                parts.append((_headers(part_head), part_body))
            if not closed:
                anomalies.append({"type": "missing-final-boundary"})
    else:
        parts.append((headers, body))
    resources: dict[str, dict] = {}
    index = None
    for position, (part_headers, part_body) in enumerate(parts):
        location = part_headers.get("content-location")
        content_id = part_headers.get("content-id", "").strip("<>")
        key = location or (f"cid:{content_id}" if content_id else None)
        if key is None:
            key = f"resource-{position + 1}"
            if position == 0:
                anomalies.append({"type": "index-synthesized"})
        if key in resources:
            continue
        resources[key] = {"contentType": part_headers.get("content-type", ""), "data": _body(part_body, part_headers),
                          "id": content_id or None}
        if position == 0:
            index = key
    return {"headers": headers, "index": index, "resources": resources, "anomalies": anomalies}
