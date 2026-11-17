"""Offline command-line entry point for ``readwrench clues``."""

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from readwrench import __version__

from .capture import capture_index
from .importers import read_fragments
from .model import CluejoinError
from .search import search, validate_groups
from .store import build_index, open_index, source_unchanged_check

BIDI_CONTROLS = {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
TERMINAL_CONTROLS = {*range(127, 160), *BIDI_CONTROLS}
JSON_ESCAPES = {code: f"\\u{code:04x}" for code in TERMINAL_CONTROLS}
MAX_QUERY_BYTES = 1_250_000
PROG = "readwrench clues"


def json_text(value: object, *, indent: int | None = None) -> str:
    # Escaping is lossless JSON: parsed evidence still contains the original text.
    return json.dumps(value, ensure_ascii=False, indent=indent).translate(JSON_ESCAPES)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog=PROG,
        description="Find the material that contains two or three remembered phrases, "
        "with an original excerpt for every clue.",
    )
    root.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="atomically index local extracted text")
    build.add_argument("index", type=Path)
    build.add_argument("inputs", type=Path, nargs="+")
    build.add_argument("--format", choices=["text", "jsonl", "rga-json"], default="text")
    build.add_argument(
        "--replace", action="store_true", help="replace an existing index on success"
    )
    capture = commands.add_parser("capture", help="extract with rga and publish only on success")
    capture.add_argument("index", type=Path)
    capture.add_argument("inputs", type=Path, nargs="+")
    capture.add_argument("--rga", default="rga", help="ripgrep-all executable")
    capture.add_argument("--timeout", type=float, default=30, help="extraction timeout in seconds")
    capture.add_argument("--max-output-mib", type=int, default=128)
    capture.add_argument("--replace", action="store_true")
    find = commands.add_parser("search", help="join two or three required clue groups")
    find.add_argument("index", type=Path)
    clues = find.add_mutually_exclusive_group(required=True)
    clues.add_argument(
        "-g", "--group", action="append", nargs="+", metavar="LITERAL",
        help="repeat 2 or 3 times; alternatives within a group are OR; quote each phrase",
    )
    clues.add_argument("--query", type=Path, help="JSON file containing an array of clue arrays")
    find.add_argument("--limit", type=int, default=20)
    find.add_argument("--context", type=int, default=100, help="characters before/after each match")
    find.add_argument("--output", choices=["json", "text"], default="json")
    return root


def _safe_terminal(value: object, *, multiline: bool = False) -> str:
    """Keep document text from introducing terminal escape sequences."""
    text = str(value)
    result = []
    for char in text:
        code = ord(char)
        if multiline and char in "\n\t":
            result.append(char)
        elif code < 32 or code in TERMINAL_CONTROLS:
            result.append(f"\\x{code:02x}" if code < 256 else f"\\u{code:04x}")
        else:
            result.append(char)
    return "".join(result)


def print_text(result: dict) -> None:
    print(f"{result['matched_documents']} material(s) match every clue group.")
    for doc in result["documents"]:
        print(f"\n{_safe_terminal(doc['document_id'])}")
        print(f"  source: {_safe_terminal(doc['source'])}")
        print(f"  closest cover: {doc['distance']} {doc['distance_unit']} difference")
        for item in doc["evidence"]:
            locations = []
            for field in ("page", "paragraph", "line"):
                if item[field] is None:
                    continue
                value = str(item[field])
                if field == "line" and item["line_end"] != item["line"]:
                    value += f"-{item['line_end']}"
                locations.append(f"{field} {value}")
            coordinates = ", ".join(locations)
            print(f"  clue {item['group']}: {coordinates}")
            excerpt = item["context"]
            start, end = item["highlight_start"], item["highlight_end"]
            marked = excerpt[:start] + "⟦" + excerpt[start:end] + "⟧" + excerpt[end:]
            print("    " + _safe_terminal(marked, multiline=True).replace("\n", "\n    "))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "capture":
            stats = capture_index(
                args.index, args.inputs, executable=args.rga, timeout=args.timeout,
                max_output_mib=args.max_output_mib, replace=args.replace,
            )
            print(json_text({"index": str(args.index), **stats}), flush=True)
            return 0
        if args.command == "build":
            if any(args.index.resolve() == path.resolve() for path in args.inputs):
                raise CluejoinError("the output index must not replace an input document")
            stats = build_index(
                args.index, read_fragments(args.inputs, args.format), replace=args.replace,
                before_publish=source_unchanged_check(args.inputs),
            )
            print(json_text({"index": str(args.index), **stats}), flush=True)
            return 0
        if args.query:
            if not args.query.is_file():
                raise CluejoinError(f"query must be a regular file: {args.query}")
            with args.query.open("rb") as handle:
                data = handle.read(MAX_QUERY_BYTES + 1)
            if len(data) > MAX_QUERY_BYTES:
                raise CluejoinError(f"query file exceeds {MAX_QUERY_BYTES} bytes")
            groups = json.loads(data.decode("utf-8-sig"))
        else:
            groups = args.group
        groups = validate_groups(groups)
        with closing(open_index(args.index)) as connection:
            result = search(connection, groups, limit=args.limit, context=args.context)
        if args.output == "json":
            print(json_text(result, indent=2), flush=True)
        else:
            print_text(result)
            sys.stdout.flush()
        # Like grep: 0=matches, 1=no matches, 2=invalid input / I/O failure.
        return 0 if result["matched_documents"] else 1
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except BrokenPipeError:
            pass
        return 141
    except (CluejoinError, OSError, sqlite3.Error, ValueError, RecursionError) as exc:
        print(f"{PROG}: {_safe_terminal(exc)}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        if "symlink loop" not in str(exc).lower():
            raise
        print(f"{PROG}: {_safe_terminal(exc)}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
