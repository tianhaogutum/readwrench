"""Select one self-contained, successful HTML response from a WARC file.

Replaces the original's use of Webrecorder warcio.js: WARC 1.0/1.1 records in plain or
gzip (multi-member) containers, HTTP header parsing, chunked transfer decoding and
gzip/deflate content decoding, all bounded. Revisit records are never resolved.
"""
from __future__ import annotations

import hashlib
import re
import zlib

from .fileio import LIMITS, InputError
from .times import iso_time
from .urls import normalize_url

EXPANDED_LIMIT = 50 * 1024 * 1024
DECODED_LIMIT = 50 * 1024 * 1024
_STEP = 64 * 1024
_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_QUOTED_OK = re.compile(r"^[\t\x20-\x7e\x80-\xff]*$")
_HTTP_WS = "\t\n\r "


class MimeType:
    """A WHATWG MIME type (what Node.js ``util.MIMEType`` parses and serializes)."""

    def __init__(self, essence: str, params: dict[str, str]):
        self.essence = essence
        self.params = params

    def __str__(self):
        text = self.essence
        for name, value in self.params.items():
            if not value or not _TOKEN.match(value):
                value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
            text += f";{name}={value}"
        return text


def parse_mime(text: str) -> MimeType | None:
    text = text.strip(_HTTP_WS)
    slash = text.find("/")
    if slash <= 0:
        return None
    kind = text[:slash]
    end = text.find(";", slash)
    subtype = (text[slash + 1:] if end < 0 else text[slash + 1:end]).rstrip(_HTTP_WS)
    if not _TOKEN.match(kind) or not subtype or not _TOKEN.match(subtype):
        return None
    params: dict[str, str] = {}
    position = len(text) if end < 0 else end
    while position < len(text):
        position += 1  # skip ';'
        while position < len(text) and text[position] in _HTTP_WS:
            position += 1
        start = position
        while position < len(text) and text[position] not in ";=":
            position += 1
        name = text[start:position].lower()
        if position < len(text) and text[position] == ";":
            continue
        position += 1  # skip '='
        if position > len(text):
            break
        if position < len(text) and text[position] == '"':
            position += 1
            value = []
            while position < len(text):
                char = text[position]
                if char == "\\" and position + 1 < len(text):
                    value.append(text[position + 1])
                    position += 2
                    continue
                if char == "\\":
                    value.append("\\")
                    position += 1
                    continue
                if char == '"':
                    position += 1
                    break
                value.append(char)
                position += 1
            value = "".join(value)
            while position < len(text) and text[position] != ";":
                position += 1
        else:
            start = position
            while position < len(text) and text[position] != ";":
                position += 1
            value = text[start:position].rstrip(_HTTP_WS)
            if not value:
                continue
        if name and _TOKEN.match(name) and _QUOTED_OK.match(value) and name not in params:
            params[name] = value
    return MimeType(f"{kind}/{subtype}".lower(), params)


def _expand(data: bytes) -> bytes:
    """Gunzip every member, bounding the expansion."""
    output = bytearray()
    remaining = data
    try:
        while remaining:
            if not remaining.startswith(b"\x1f\x8b"):
                if output:
                    break  # trailing bytes after the last member
                raise InputError("Cannot parse WARC: incorrect header check")
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            pending = remaining
            while True:
                chunk = inflater.decompress(pending, _STEP)
                output += chunk
                if len(output) > EXPANDED_LIMIT:
                    raise InputError("WARC expanded container exceeds 50 MiB limit")
                pending = inflater.unconsumed_tail
                if inflater.eof or (not pending and not chunk):
                    break
            if not inflater.eof:
                raise InputError("Cannot parse WARC: unexpected end of file")
            remaining = inflater.unused_data
    except zlib.error as error:
        raise InputError(f"Cannot parse WARC: {error}") from error
    return bytes(output)


def _parse_headers(lines: list[bytes]) -> dict[str, str]:
    headers: dict[str, str] = {}
    last = None
    for raw in lines:
        line = raw.decode("utf-8", "replace")
        if line[:1] in (" ", "\t") and last is not None:
            headers[last] += " " + line.strip()
            continue
        name, sep, value = line.partition(":")
        if not sep:
            continue
        key = name.strip().lower()
        value = value.strip()
        if key in headers:
            headers[key] += ", " + value
        else:
            headers[key] = value
        last = key
    return headers


def _read_line(data: bytes, pos: int, limit: int | None = None) -> tuple[bytes, int]:
    limit = len(data) if limit is None else limit
    end = data.find(b"\n", pos, limit)
    if end < 0:
        return data[pos:limit], limit
    return data[pos:end + 1], end + 1


def _header_block(data: bytes, pos: int, end: int) -> tuple[list[bytes], int]:
    lines = []
    while pos < end:
        line, pos = _read_line(data, pos, end)
        stripped = line.rstrip(b"\r\n")
        if not stripped:
            break
        lines.append(stripped)
    return lines, pos


def _js_hex_prefix(text: bytes):
    match = re.match(rb"^\s*([0-9a-fA-F]+)", text)
    return int(match.group(1), 16) if match else None


def _dechunk(body: bytes) -> tuple[bytes, bool]:
    """``(payload, errored)``; a body that does not start like a chunk is returned raw."""
    pos = 0
    first = True
    out = bytearray()
    while True:
        line, pos = _read_line(body, pos)
        if not line:
            return bytes(out), False
        size = _js_hex_prefix(line)
        if size is None or size > 2 ** 32:
            if first:
                return body, False
            return bytes(out), True
        if size == 0:
            return bytes(out), False
        chunk = body[pos:pos + size]
        pos += size
        if len(chunk) != size:
            if first:
                return body, False
            out += chunk
            return bytes(out), True
        out += chunk
        if body[pos:pos + 2] == b"\r\n":
            pos += 2
        elif body[pos:pos + 1] == b"\n":
            pos += 1
        elif pos < len(body):
            return bytes(out), True
        first = False


def _inflate(payload: bytes, wbits: int, limit: int) -> bytes | None:
    """Decoded bytes, None if the data is not in this format at all; raises past ``limit``."""
    inflater = zlib.decompressobj(wbits)
    output = bytearray()
    pending = payload
    try:
        while pending:
            chunk = inflater.decompress(pending, _STEP)
            output += chunk
            if len(output) > limit:
                raise InputError("Selected WARC HTML exceeds 10 MiB limit")
            pending = inflater.unconsumed_tail
            if inflater.eof or not chunk and not pending:
                break
    except zlib.error as error:
        if not output:
            return None
        raise InputError(f"Cannot parse WARC: {error}") from error
    return bytes(output)


def _decode_content(payload: bytes, encoding: str) -> tuple[bytes, bool]:
    """``(decoded, fell_back_to_raw)``."""
    limit = LIMITS["captureBytes"]
    if encoding in ("gzip", "x-gzip"):
        decoded = _inflate(payload, 16 + zlib.MAX_WBITS, limit)
    elif encoding == "deflate":
        decoded = _inflate(payload, zlib.MAX_WBITS, limit)
        if decoded is None:
            decoded = _inflate(payload, -zlib.MAX_WBITS, limit)
    else:
        return payload, False
    if decoded is None:
        return payload, True
    return decoded, False


def _status(statusline: str):
    parts = statusline.split(" ")
    if len(parts) < 2:
        return None
    match = re.match(r"^\s*([+-]?[0-9]+)", parts[1])
    return int(match.group(1)) if match else None


def extract_warc(data: bytes, source_url: str, record_id: str | None = None) -> dict:
    expanded = _expand(data) if data[:2] == b"\x1f\x8b" else data
    if len(expanded) > EXPANDED_LIMIT:
        raise InputError("WARC expanded container exceeds 50 MiB limit")
    chosen = None
    records = decoded_total = matching_revisits = 0
    warnings: list[str] = []
    pos = 0
    length = len(expanded)
    while True:
        # Skip blank separator lines between records.
        while pos < length:
            line, after = _read_line(expanded, pos)
            if line.strip():
                break
            pos = after
        if pos >= length:
            break
        status_raw, pos = _read_line(expanded, pos)
        statusline = status_raw.decode("utf-8", "replace").rstrip("\r\n")
        header_lines, pos = _header_block(expanded, pos, length)
        headers = _parse_headers(header_lines)
        records += 1
        if records > 5000:
            raise InputError("WARC exceeds 5,000 record limit")
        if not re.fullmatch(r"WARC/1\.[01]", statusline):
            raise InputError("Unsupported or malformed WARC version")
        declared = headers.get("content-length", "")
        if not re.fullmatch(r"[0-9]+", declared) or int(declared) > EXPANDED_LIMIT:
            raise InputError("Invalid or excessive WARC Content-Length")
        content_length = int(declared)
        block = expanded[pos:pos + content_length]
        pos = min(pos + content_length, length)
        truncated = len(block) < content_length
        warc_type = headers.get("warc-type")
        target_uri = headers.get("warc-target-uri")
        same_url = False
        try:
            same_url = bool(target_uri) and normalize_url(target_uri) == source_url
        except InputError:
            pass  # Other records can be non-HTTP resources.
        identifier = headers.get("warc-record-id")
        if record_id and identifier == record_id and not same_url:
            raise InputError(f"Selected WARC record belongs to a different URL: {target_uri}")
        if same_url and warc_type == "revisit":
            matching_revisits += 1
        http_headers = None
        status = None
        payload = b""
        if warc_type in ("response", "request", "revisit") and block and (
                "application/http" in headers.get("content-type", "").lower() or block.startswith(b"HTTP/")):
            http_status_raw, offset = _read_line(block, 0)
            http_lines, offset = _header_block(block, offset, len(block))
            http_headers = _parse_headers(http_lines)
            status = _status(http_status_raw.decode("utf-8", "replace").strip())
            payload = block[offset:]
        mime = parse_mime((http_headers or {}).get("content-type") or "application/octet-stream")
        select = (same_url and warc_type == "response" and status is not None and 200 <= status < 300
                  and mime is not None and mime.essence == "text/html" and (not record_id or identifier == record_id))
        if select and chosen:
            raise InputError("WARC contains multiple successful HTML responses for this URL; set recordId to "
                             "select the capture explicitly")
        if select:
            content_encoding = http_headers.get("content-encoding", "").strip().lower()
            transfer_encoding = http_headers.get("transfer-encoding", "").strip().lower()
            if content_encoding not in ("", "identity", "gzip", "x-gzip", "deflate"):
                raise InputError(f"Unsupported WARC HTTP Content-Encoding: {content_encoding[:100]}")
            if transfer_encoding not in ("", "identity", "chunked"):
                raise InputError(f"Unsupported WARC HTTP Transfer-Encoding: {transfer_encoding[:100]}")
            errored = False
            if transfer_encoding == "chunked":
                payload, errored = _dechunk(payload)
            html, fell_back = _decode_content(payload, content_encoding)
            decoded_total += len(html)
            if decoded_total > DECODED_LIMIT:
                raise InputError("WARC decoded payloads exceed 50 MiB limit")
            if len(html) > LIMITS["captureBytes"]:
                raise InputError("Selected WARC HTML exceeds 10 MiB limit")
            if errored:
                raise InputError("Malformed HTTP chunked transfer in selected WARC response")
            if fell_back:
                warnings.append("The WARC content decoder fell back to raw bytes after a Content-Encoding "
                                "mismatch; verify the original capture.")
        if truncated:
            raise InputError("Truncated WARC record: declared content length exceeds available bytes")
        if not select:
            continue
        if not html:
            raise InputError("Selected WARC response has an empty HTML payload")
        if not identifier:
            raise InputError("Selected WARC record lacks WARC-Record-ID")
        if len(identifier) > 500:
            raise InputError("Selected WARC-Record-ID exceeds 500 characters")
        warc_date = headers.get("warc-date")
        at = iso_time(warc_date, "WARC-Date")
        content_type = str(mime)
        if len(content_type) > 1000:
            warnings.append("WARC Content-Type metadata exceeded 1,000 characters and was truncated.")
        last_modified = http_headers.get("last-modified")
        http_date = http_headers.get("date")
        chosen = {
            "html": html, "encoding": mime.params.get("charset"),
            "time": {"at": at, "source": "WARC response WARC-Date", "raw": warc_date},
            "record": {
                "id": identifier, "targetURI": target_uri, "date": warc_date, "httpStatus": status,
                "contentType": content_type[:1000] + "… [truncated]" if len(content_type) > 1000 else content_type,
                "httpLastModified": last_modified[:300] if last_modified else None,
                "httpDate": http_date[:300] if http_date else None,
                "payloadSHA256": hashlib.sha256(html).hexdigest(),
                "digestVerification": "Original WARC digests are not verified; this SHA-256 identifies the decoded "
                                      "HTML used by this report.",
            },
        }
    if not chosen:
        suffix = f" with recordId {record_id}" if record_id else ""
        raise InputError(f"No self-contained 2xx HTML response for {source_url}{suffix}. Revisit records and "
                         "redirects are not article captures.")
    if matching_revisits:
        warnings.append(f"{matching_revisits} matching WARC revisit record(s) were not treated as additional "
                        "captures because they do not contain a self-contained response body.")
    return {**chosen, "warnings": warnings}
