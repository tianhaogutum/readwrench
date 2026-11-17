"""``readwrench check`` adapter for ``readwrench quote``.

Two read-only analyses that need no decision from the reader:

* ``quote``: every saved review (``quote-locator.review/v1``) and confirmed locator
  (``quote-locator.anchor/v1``) is re-verified against the document it names, exactly as
  ``readwrench quote verify`` / ``confirm`` would: same source bytes, same extracted text,
  same reader versions, untouched record. The document is looked for at the recorded
  ``file:`` URI, then next to the record under its recorded name.
* ``quote-text-layer``: PDFs (or pages) without extractable text, where a quotation can
  never be found because no OCR is run.

Nothing is written and nothing is fetched.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

from readwrench.check.model import (
    ERROR,
    FIND,
    INFO,
    WARNING,
    Check,
    Finding,
    Source,
    need,
    plural,
    short,
)

TOOL = "quote"
EXTRA = "quote"
RECORD_CHECK = "quote"
TEXT_CHECK = "quote-text-layer"
# Top-level import names: the matching engine and every document reader (EbookLib needs lxml).
REQUIRES = ("reanchor", "bs4", "pypdf", "ebooklib", "lxml", "regex", "rapidfuzz")
# Importing the document readers alone (no matching).
READER_REQUIRES = ("bs4", "pypdf", "ebooklib", "lxml")

KIND_REVIEW = "quote-review"
KIND_ANCHOR = "quote-anchor"
_LABELS = {
    "review": (KIND_REVIEW, "saved quote review (readwrench quote)"),
    "anchor": (KIND_ANCHOR, "confirmed quote locator (readwrench quote)"),
}
# Both record types are written with "schema" as their first key. An escaped copy inside
# a JSON string (\"schema\") does not match.
_SCHEMA = re.compile(rb'"schema"\s*:\s*"quote-locator\.(review|anchor)/v1"')


def classify(path: Path, head: bytes) -> tuple[str, str] | None:
    """Recognise saved reviews and confirmed locators by their schema id."""
    try:
        body = head.removeprefix(b"\xef\xbb\xbf").lstrip(b" \t\r\n")
        if not body.startswith(b"{"):
            return None
        match = _SCHEMA.search(body)
        return _LABELS[match.group(1).decode("ascii")] if match else None
    except Exception:  # noqa: BLE001 - discovery must never fail because of a classifier
        return None


def _q(value: object) -> str:
    return shlex.quote(str(value))


def _candidate_sources(source: Source, record: dict) -> tuple[list[Path], str]:
    """Existing regular files that may be the record's document, best first."""
    document = record.get("document")
    document = document if isinstance(document, dict) else {}
    name, uri = document.get("name"), document.get("uri")
    paths: list[Path] = []
    if isinstance(uri, str):
        parts = urlsplit(uri)
        if parts.scheme == "file" and parts.path:
            from urllib.request import url2pathname  # stdlib path conversion; no network

            paths.append(Path(url2pathname(parts.path)))
    if isinstance(name, str) and name not in ("", ".", "..") and not {"/", "\\"} & set(name):
        paths.append(source.path.parent / name)
    found, seen = [], set()
    record_real = os.path.realpath(source.path)
    for path in paths:
        try:
            real = os.path.realpath(path)
            if real in seen or real == record_real or not path.is_file():
                continue
        except (OSError, ValueError):
            continue
        seen.add(real)
        found.append(path)
    return found, name if isinstance(name, str) and name else "(unnamed)"


def _classify_reason(reason: str) -> tuple[str, str]:
    if reason.startswith("Source bytes changed"):
        return "source-changed", WARNING
    if reason.startswith(("Extracted text changed", "Extraction policy or reader versions")):
        return "extraction-changed", WARNING
    return "record-inconsistent", ERROR


def check_saved_record(source: Source):
    need(*REQUIRES)
    from .cli import _encoding, _read_record
    from .documents import DocumentError, load_document
    from .records import check_review, verify

    is_anchor = source.kind == KIND_ANCHOR
    what = "locator" if is_anchor else "review"
    try:
        record = _read_record(source.path)
        encoding = _encoding(record)
    except (ValueError, OSError, RecursionError) as exc:
        yield Finding(
            RECORD_CHECK, "invalid-record", ERROR,
            f"the saved {what} cannot be read: {short(exc, 160)}", source.display,
            hint=f"readwrench quote verify SOURCE {_q(source.display)}" if is_anchor
            else "save a fresh review with 'readwrench quote find SOURCE ... --output REVIEW.json'",
        )
        return
    paths, name = _candidate_sources(source, record)
    if not paths:
        yield Finding(
            RECORD_CHECK, "source-not-found", INFO,
            f"its document {short(name, 80)!r} is neither at the recorded path nor next to the "
            f"{what}; not re-verified", source.display, subject=short(name, 80),
            hint=f"readwrench quote verify SOURCE {_q(source.display)}" if is_anchor
            else f"readwrench quote confirm SOURCE {_q(source.display)} --candidate N --output FILE",
        )
        return
    problem = None
    for path in paths:
        try:
            document = load_document(path, encoding=encoding)
        except (DocumentError, ValueError, OSError, LookupError, RecursionError) as exc:
            found = ("source-unreadable", ERROR, f"its document cannot be read: {short(exc, 160)}")
        else:
            if is_anchor:
                result = verify(document, record)
                if result["valid"]:
                    return
                reason = str(result["reason"])
            else:
                try:
                    check_review(document, record)
                    return
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    reason = str(exc)
            rule, severity = _classify_reason(reason)
            found = (rule, severity, f"no longer matches its document: {short(reason, 160)}")
        problem = problem or (path, *found)
    path, rule, severity, message = problem
    if is_anchor:
        hint = f"readwrench quote verify {_q(path)} {_q(source.display)}"
        if rule != "record-inconsistent":
            hint += "; then run a fresh 'readwrench quote find' and confirm again"
    else:
        hint = (f"save a fresh review: readwrench quote find {_q(path)} --quote-file QUOTE.txt "
                "--output REVIEW.json")
    yield Finding(RECORD_CHECK, rule, severity, f"the saved {what} {message}", source.display,
                  subject=str(path), hint=hint)


def _page_ranges(pages: list[int], limit: int = 12) -> str:
    groups: list[str] = []
    start = previous = pages[0]
    for page in [*pages[1:], None]:
        if page is not None and page == previous + 1:
            previous = page
            continue
        groups.append(str(start) if start == previous else f"{start}-{previous}")
        if page is not None:
            start = previous = page
    shown = ", ".join(groups[:limit])
    return shown + (", …" if len(groups) > limit else "")


def check_text_layer(source: Source):
    need(*READER_REQUIRES)
    from .documents import DocumentError, load_document

    try:
        document = load_document(source.path)
    except DocumentError as exc:
        yield Finding(
            TEXT_CHECK, "not-searchable", INFO,
            f"readwrench quote cannot search this PDF: {short(exc, 160)}", source.display,
            hint=f"readwrench quote extract {_q(source.display)} shows the same error",
        )
        return
    pages = len(document.units)
    empty = [unit.number for unit in document.units
             if not document.text[unit.start:unit.end].strip()]
    if not document.text.strip():
        where = f"none of its {plural(pages, 'page')} has" if pages else "it has no pages with"
        yield Finding(
            TEXT_CHECK, "no-text-layer", WARNING,
            f"{where} extractable text (scanned images without OCR?); a quotation in it "
            "cannot be found", source.display,
            hint="add a text layer with an OCR tool, then use 'readwrench quote find'",
        )
    elif empty:
        yield Finding(
            TEXT_CHECK, "pages-without-text", INFO,
            f"{plural(len(empty), 'page')} of {pages} have no extractable text "
            f"(pages {_page_ranges(empty)}); a quotation on them cannot be found",
            source.display, pointer=f"page {empty[0]}",
            hint=f"readwrench quote extract {_q(source.display)} shows the searchable text",
        )


CHECKS = (
    Check(
        RECORD_CHECK, FIND,
        "Re-verify saved quote reviews and locators against the documents they name",
        frozenset({KIND_REVIEW, KIND_ANCHOR}),
        run=check_saved_record,
        rules=("invalid-record", "source-not-found", "source-unreadable", "source-changed",
               "extraction-changed", "record-inconsistent"),
        requires=REQUIRES, extra=EXTRA, tool=TOOL,
        note=("The document is looked for at the record's file: URI, then next to the record "
              "under its recorded name; a moved, byte-identical copy still verifies."),
    ),
    Check(
        TEXT_CHECK, FIND,
        "PDFs or pages without extractable text, where a quotation cannot be found",
        frozenset({"pdf"}),
        run=check_text_layer,
        rules=("no-text-layer", "pages-without-text", "not-searchable"),
        requires=READER_REQUIRES, extra=EXTRA, tool=TOOL,
        note=("Uses the same pypdf extraction as readwrench quote; no OCR is run. PDFs over "
              "20 MiB or 2,000 pages are reported as not searchable."),
    ),
)
