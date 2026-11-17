"""Recover a lost quotation in a local TXT, Markdown, HTML, PDF or EPUB file, offline.
Review every occurrence first; confirming one is a separate, explicit operation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unicodedata
from pathlib import Path

from readwrench import __version__

# The document readers and matching engine import optional dependencies (reanchor,
# Beautiful Soup, pypdf, EbookLib, regex, RapidFuzz); they are imported inside main()
# so that --help and --version work without them.

PROG = "readwrench quote"
MAX_RECORD_BYTES = 24 * 1024 * 1024
MAX_TERMINAL_VALUE = 2000
MAX_TERMINAL_DIFFS = 50


def _safe_terminal(value: object) -> str:
    text = str(value)
    escaped = []
    for char in text:
        if (
            unicodedata.category(char) in {"Cc", "Cs"}
            and char not in {"\n", "\t"}
            or char in "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
        ):
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    return "".join(escaped)


def _human(*values: object) -> None:
    print(" ".join(_safe_terminal(value) for value in values))


def _paragraph(label: str, paragraph: dict) -> None:
    before = "[context clipped before] " if paragraph["clipped_before"] else ""
    after = " [context clipped after]" if paragraph["clipped_after"] else ""
    _human(label, before + paragraph["text"] + after)


def _diff_value(text: str) -> str:
    shown = repr(text[:MAX_TERMINAL_VALUE])
    if len(text) > MAX_TERMINAL_VALUE:
        shown += f" [{len(text) - MAX_TERMINAL_VALUE} code points omitted; full value in JSON]"
    return shown


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str):
    raise ValueError(f"Non-standard JSON constant: {value}")


def _read_record(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
        raise ValueError("Record must be a regular JSON file under 24 MiB")
    with path.open("rb") as stream:
        content = stream.read(MAX_RECORD_BYTES + 1)
    if len(content) > MAX_RECORD_BYTES:
        raise ValueError("Record grew beyond the 24 MiB limit while reading")
    result = json.loads(
        content.decode("utf-8-sig"),
        object_pairs_hook=_object_without_duplicates,
        parse_constant=_invalid_constant,
    )
    if not isinstance(result, dict):
        raise ValueError("Record must be a JSON object")
    return result


def _encoding(record: dict) -> str:
    document = record.get("document")
    extraction = document.get("extraction") if isinstance(document, dict) else None
    encoding = extraction.get("encoding") if isinstance(extraction, dict) else None
    if encoding is not None and not isinstance(encoding, str):
        raise ValueError("Invalid source encoding in record")
    return encoding or "utf-8"


def _write_json(path: Path, value: dict, *, force: bool, protected: list[Path]) -> None:
    path = path.expanduser().absolute()
    if path.resolve() in {entry.expanduser().resolve() for entry in protected}:
        raise ValueError("Output must not overwrite a source or input record")
    if not path.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {path.parent}")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".quote-locator-", delete=False
        ) as stream:
            temp_name = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            if stream.tell() > MAX_RECORD_BYTES:
                raise ValueError("Record exceeds 24 MiB; use a smaller --limit or a shorter quote")
            os.fsync(stream.fileno())
        if force:
            os.replace(temp_name, path)
        else:
            # Atomic create without overwriting another process's output.
            os.link(temp_name, path)
    except FileExistsError as exc:
        raise ValueError(f"Output exists: {path}; use --force to replace it") from exc
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def _display(review: dict) -> None:
    _human(f"{review['status'].upper()}: {review['candidate_count']} candidate(s); unconfirmed")
    for warning in review["document"]["warnings"]:
        _human(f"Note: {warning}")
    if review["truncated"]:
        _human(
            f"Showing {len(review['candidates'])} candidates "
            f"from offset {review['window']['offset']}. "
            f"Next offset: {review['window']['next_offset']}; use --offset to continue."
        )
    for candidate in review["candidates"]:
        labels = ", ".join(location["label"] for location in candidate["locations"])
        _human(
            f"\n[{candidate['index']}] {candidate['method']} | {labels} | "
            f"code points {candidate['start']}..{candidate['end']}"
        )
        _human(f"ID: {candidate['id']}")
        evidence = candidate["context_evidence"]
        if evidence["agrees"] is not None:
            _human(
                "Saved context under this profile:",
                "agrees" if evidence["agrees"] else "differs",
                f"(prefix={evidence['prefix_matches']}, suffix={evidence['suffix_matches']})",
            )
        context = candidate["context"]
        if context["before"]:
            _paragraph("Previous paragraph:", context["before"])
        text = candidate["text"]
        _human("Matching source:", text[:MAX_TERMINAL_VALUE])
        if len(text) > MAX_TERMINAL_VALUE:
            _human(
                f"[Source preview clipped; {len(text) - MAX_TERMINAL_VALUE} "
                "more code points in JSON/extract]"
            )
        for paragraph in context["containing"]:
            _paragraph("Containing paragraph:", paragraph)
        if context["after"]:
            _paragraph("Next paragraph:", context["after"])
        for change in candidate["differences"][:MAX_TERMINAL_DIFFS]:
            if change.get("granularity") == "whole-span":
                _human(
                    "Difference uses a complete whole-span replacement to bound alignment memory."
                )
            _human(
                f"Difference ({change['operation']}): "
                f"{_diff_value(change['query'])} -> {_diff_value(change['source'])}"
            )
        remaining = len(candidate["differences"]) - MAX_TERMINAL_DIFFS
        if remaining > 0:
            _human(f"[{remaining} additional change groups retained in JSON]")
    if not review["candidate_count"]:
        _human("No supported textual occurrence found. No quotation or page number was invented.")
    elif not review["candidates"]:
        _human("No candidates in this window. Use a smaller --offset.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__)
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    find = sub.add_parser("find", help="Find all exact/normalized candidates, without confirming")
    find.add_argument("source", type=Path)
    quotes = find.add_mutually_exclusive_group(required=True)
    quotes.add_argument("--quote", help="Quotation as a literal argument")
    quotes.add_argument(
        "--quote-file", type=Path, help="UTF-8 file; one terminal newline is removed"
    )
    quotes.add_argument(
        "--selector",
        type=Path,
        help="Local W3C selector/Annotation or Readium locator JSON; source URLs are not fetched",
    )
    find.add_argument(
        "--profile", choices=["strict", "typographic", "loose"], default="typographic"
    )
    find.add_argument(
        "--encoding", default="utf-8", help="Encoding for TXT/MD inputs (default UTF-8)"
    )
    find.add_argument("--offset", type=int, default=0, help="Skip this many candidates (0-based)")
    find.add_argument("--limit", type=int, default=1000, help="Candidates in this window (1..1000)")
    find.add_argument("--json", action="store_true", help="Print machine-readable review to stdout")
    find.add_argument("--output", type=Path, help="Save a review for the confirm command")
    find.add_argument("--force", action="store_true", help="Replace an existing output file")
    select = sub.add_parser(
        "confirm", help="Explicitly select one reviewed candidate and export its locator"
    )
    select.add_argument("source", type=Path)
    select.add_argument("review", type=Path)
    select.add_argument(
        "--candidate", required=True, help="Displayed 1-based index or full candidate ID"
    )
    select.add_argument("--output", required=True, type=Path)
    select.add_argument("--force", action="store_true")
    check = sub.add_parser(
        "verify", help="Check a confirmed locator against exactly the same source bytes"
    )
    check.add_argument("source", type=Path)
    check.add_argument("anchor", type=Path)
    check.add_argument("--json", action="store_true")
    extract = sub.add_parser(
        "extract", help="Write the exact text representation targeted by offsets"
    )
    extract.add_argument("source", type=Path)
    extract.add_argument("--encoding", default="utf-8")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from .documents import DocumentError, load_document
    from .matching import MAX_QUOTE_CHARS, locate
    from .records import confirm, verify
    from .selectors import import_selector

    try:
        if args.command == "find":
            if args.selector:
                quote = import_selector(_read_record(args.selector))
            elif args.quote_file:
                if not args.quote_file.is_file():
                    raise ValueError("Quotation file must be a regular file")
                with args.quote_file.open("rb") as stream:
                    content = stream.read(MAX_QUOTE_CHARS * 4 + 5)
                if len(content) > MAX_QUOTE_CHARS * 4 + 4:
                    raise ValueError("Quotation file exceeds the size limit")
                quote = content.decode("utf-8-sig").removesuffix("\n").removesuffix("\r")
            else:
                quote = args.quote
            document = load_document(args.source, encoding=args.encoding)
            result = locate(
                document, quote, profile=args.profile, offset=args.offset, limit=args.limit
            )
            if args.output:
                protected = [args.source] + ([args.quote_file] if args.quote_file else [])
                if args.selector:
                    protected.append(args.selector)
                _write_json(args.output, result, force=args.force, protected=protected)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                _display(result)
                if args.output:
                    _human(
                        f"\nSaved review to {args.output}. Confirm one with "
                        f"'{PROG} confirm SOURCE REVIEW --candidate INDEX_OR_ID --output FILE'."
                    )
            return 1 if result["status"] == "unmatched" else 0
        if args.command == "confirm":
            review = _read_record(args.review)
            document = load_document(args.source, encoding=_encoding(review))
            candidate = int(args.candidate) if args.candidate.isdecimal() else args.candidate
            anchor = confirm(document, review, candidate)
            _write_json(args.output, anchor, force=args.force, protected=[args.source, args.review])
            _human(f"Confirmed candidate {anchor['selection']['index']}; saved {args.output}")
            return 0
        if args.command == "verify":
            anchor = _read_record(args.anchor)
            document = load_document(args.source, encoding=_encoding(anchor))
            result = verify(document, anchor)
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                _human(f"{'VALID' if result['valid'] else 'INVALID'}: {result['reason']}")
            return 0 if result["valid"] else 1
        if args.command == "extract":
            sys.stdout.write(load_document(args.source, encoding=args.encoding).text)
            return 0
    except BrokenPipeError:
        return 0
    except (DocumentError, ValueError, OSError, LookupError, RecursionError) as exc:
        print(_safe_terminal(f"{PROG}: {exc}"), file=sys.stderr)
        return 2
    return 2
