"""Adapt already-extracted text and upstream JSON; never extract binary formats."""

import base64
import binascii
import json
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from .model import MAX_RECORD, MAX_TEXT, CluejoinError, Fragment, positive_int, string

FIELDS = {"document_id", "source", "fragment_id", "text", "page", "paragraph", "line"}
RGA_SUFFIXES = {".pdf", ".txt", ".md", ".log", ".csv", ".tsv", ".asciipagebreaks"}
PAGE_PREFIX = re.compile(r"^Page ([1-9][0-9]*): ?")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"{value} is not valid JSON")


def json_records(path: Path) -> Iterator[tuple[int, object]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        number = 0
        while line := handle.readline(MAX_RECORD + 1):
            number += 1
            if len(line) > MAX_RECORD:
                raise CluejoinError(f"{path}:{number}: JSON record exceeds {MAX_RECORD} characters")
            if not line.strip():
                continue
            try:
                yield number, json.loads(
                    line, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
                )
            except (ValueError, RecursionError) as exc:
                raise CluejoinError(f"{path}:{number}: invalid JSON: {exc}") from exc


def from_jsonl(path: Path) -> Iterator[Fragment]:
    for number, record in json_records(path):
        try:
            if not isinstance(record, dict):
                raise CluejoinError("a fragment record must be an object")
            if extra := set(record) - FIELDS:
                raise CluejoinError(f"unknown field(s): {', '.join(sorted(extra))}")
            document_id = string(record.get("document_id"), "document_id")
            source = string(record.get("source", document_id), "source")
            text = string(record.get("text"), "text", maximum=MAX_TEXT)
            page = positive_int(record.get("page"), "page")
            paragraph = positive_int(record.get("paragraph"), "paragraph")
            if page is not None and "\f" in text:
                raise CluejoinError("split form-feed pages into separate JSONL records")
            if page is None and paragraph is None:
                raise CluejoinError("provide page or paragraph to locate this fragment")
            yield Fragment(
                document_id=document_id,
                source=source,
                fragment_id=string(
                    record.get("fragment_id", f"{path.resolve()}:{number}"), "fragment_id"
                ),
                text=text,
                page=page,
                paragraph=paragraph,
                line=positive_int(record.get("line"), "line"),
                origin=str(path.resolve()),
                record=number,
                origin_kind="jsonl-record",
            )
        except CluejoinError as exc:
            raise CluejoinError(f"{path}:{number}: {exc}") from exc


def from_text(path: Path) -> Iterator[Fragment]:
    """Split blank-line paragraphs and Poppler form feeds without losing content."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        has_pages = False
        while chunk := handle.read(65_536):
            has_pages |= "\f" in chunk
        handle.seek(0)
        source = str(path.resolve())
        paragraph = 0
        page = 1
        line_number = 0
        first_line = 1
        last_line = 1
        parts: list[str] = []
        size = 0

        def flush() -> Fragment | None:
            nonlocal paragraph, size
            text = "".join(parts).rstrip("\r\n")
            parts.clear()
            size = 0
            if not text.strip():
                return None
            paragraph += 1
            return Fragment(
                source, source, f"paragraph:{paragraph}", text,
                page if has_pages else None, paragraph, first_line, source, first_line,
                last_line,
                "extracted-text-line",
            )

        while line := handle.readline(MAX_TEXT + 1):
            line_number += 1
            if len(line) > MAX_TEXT:
                raise CluejoinError(f"{path}:{line_number}: line exceeds {MAX_TEXT} characters")
            chunks = line.split("\f")
            for i, part in enumerate(chunks):
                if i:
                    if fragment := flush():
                        yield fragment
                    page += 1
                if not part.strip():
                    if fragment := flush():
                        yield fragment
                    continue
                if not parts:
                    first_line = line_number
                size += len(part)
                if size > MAX_TEXT:
                    raise CluejoinError(
                        f"{path}:{first_line}: paragraph exceeds {MAX_TEXT} characters; "
                        "split it into JSONL fragments"
                    )
                if "\x00" in part:
                    raise CluejoinError(f"{path}:{line_number}: binary NUL in extracted text")
                parts.append(part)
                last_line = line_number
        if fragment := flush():
            yield fragment


def _rga_text(value: object, name: str) -> str:
    if not isinstance(value, dict):
        raise CluejoinError(f"rga {name} must contain text or base64 bytes")
    if "text" in value:
        result = value["text"]
        if not isinstance(result, str):
            raise CluejoinError(f"rga {name}.text must be a string")
        return result
    try:
        return base64.b64decode(value["bytes"], validate=True).decode("utf-8")
    except (KeyError, ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise CluejoinError(f"rga {name}.bytes must be base64-encoded UTF-8") from exc


def _lf_lines(text: str) -> Iterator[str]:
    """Split ripgrep's LF records while retaining exact LF/CRLF separators."""
    parts = text.split("\n")
    for index, part in enumerate(parts):
        if index < len(parts) - 1:
            yield part + "\n"
        elif part:
            yield part


def _rga_content(text: str) -> str:
    if not text.strip():
        if len(text) > MAX_TEXT:
            raise CluejoinError(f"rga line exceeds {MAX_TEXT} characters")
        return text
    if text.rstrip("\r\n") == "[rga: binary data]":
        raise CluejoinError("rga emitted a binary-data placeholder; extract UTF-8 text upstream")
    return string(text, "lines", maximum=MAX_TEXT)


def _rga_lines(path: Path) -> Iterator[Fragment]:
    """Read rga's actual per-line content, before joining proven adjacent lines."""
    for number, record in json_records(path):
        try:
            if not isinstance(record, dict) or not isinstance(record.get("type"), str):
                raise CluejoinError("expected a ripgrep JSON event object")
            kind = record["type"]
            if kind in {"begin", "end", "summary"}:
                continue
            if kind not in {"match", "context"}:
                raise CluejoinError(f"unsupported ripgrep event type: {kind}")
            data = record.get("data")
            if not isinstance(data, dict):
                raise CluejoinError("rga event data must be an object")
            source = string(_rga_text(data.get("path"), "path"), "path")
            suffix = Path(source).suffix.lower()
            if suffix not in RGA_SUFFIXES:
                raise CluejoinError(
                    f"unsupported rga source {source!r}: import only PDFs or plain text; "
                    "archive member prefixes are ambiguous, so use explicit JSONL IDs"
                )
            text = _rga_text(data.get("lines"), "lines")
            line = positive_int(data.get("line_number"), "line_number")
            if suffix in {".pdf", ".asciipagebreaks"}:
                if not text.strip():
                    continue  # rga may append an unlocated, empty final line.
                marker = PAGE_PREFIX.match(text)
                if marker is None:
                    raise CluejoinError("PDF rga lines must begin with 'Page N: '")
                # A multiline event can cross a page boundary. Preserve each line separately.
                for offset, part in enumerate(_lf_lines(text)):
                    marker = PAGE_PREFIX.match(part)
                    if marker is None:
                        raise CluejoinError("each PDF line must carry its own Page N: prefix")
                    part = part[marker.end():]
                    yield Fragment(
                        source, source, f"{path.resolve()}:{number}:{offset}",
                        _rga_content(part),
                        positive_int(int(marker[1]), "page"), None,
                        positive_int(line + offset, "line_number") if line else None,
                        str(path.resolve()), number,
                        origin_kind="rga-json-record",
                    )
                continue
            # Sparse line streams do not establish original paragraph numbers.
            if line is None:
                raise CluejoinError("plain-text rga import requires --line-number (-n)")
            for offset, part in enumerate(_lf_lines(text)):
                yield Fragment(
                    source, source, f"{path.resolve()}:{number}:{offset}",
                    _rga_content(part), None, None,
                    positive_int(line + offset, "line_number"), str(path.resolve()), number,
                    origin_kind="rga-json-record",
                )
        except CluejoinError as exc:
            raise CluejoinError(f"{path}:{number}: {exc}") from exc


def from_rga(path: Path) -> Iterator[Fragment]:
    """Join only adjacent numbered lines from one source and one physical page."""
    pending: Fragment | None = None
    parts: list[str] = []
    size = 0
    last_line: int | None = None
    last_record = 0

    def flush() -> Fragment | None:
        nonlocal pending, size
        if pending is None:
            return None
        text = "".join(parts).rstrip("\r\n")
        result = replace(pending, text=text, record_end=last_record) if text.strip() else None
        pending = None
        parts.clear()
        size = 0
        return result

    for fragment in _rga_lines(path):
        adjacent = (
            pending is not None and pending.document_id == fragment.document_id
            and pending.page == fragment.page and last_line is not None
            and fragment.line == last_line + 1 and parts[-1].endswith("\n")
        )
        if not adjacent:
            if result := flush():
                yield result
            pending = fragment
        parts.append(fragment.text)
        size += len(fragment.text)
        if size > MAX_TEXT:
            raise CluejoinError(
                f"{path}:{fragment.record}: contiguous rga block exceeds {MAX_TEXT} characters; "
                "import a narrower upstream search or explicit located fragments"
            )
        last_line = fragment.line
        last_record = fragment.record
    if result := flush():
        yield result


def read_fragments(paths: list[Path], format: str) -> Iterator[Fragment]:
    reader = {"text": from_text, "jsonl": from_jsonl, "rga-json": from_rga}[format]
    for path in paths:
        try:
            yield from reader(path)
        except UnicodeDecodeError as exc:
            raise CluejoinError(f"{path}: expected UTF-8 extracted text: {exc}") from exc
