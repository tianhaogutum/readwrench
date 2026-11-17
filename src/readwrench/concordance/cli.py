"""Command line for ``readwrench concordance``.

Only the standard library is imported here, so ``--help`` works before the
optional dependencies (``readwrench[concordance]``) are installed. Each
subcommand imports the EPUB/Taguette machinery when it runs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from readwrench import __version__

from .errors import InputError

PROG = "readwrench concordance"


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog=PROG,
        description="Edition-aware EPUB concept matrices for Taguette. All processing is local.",
        epilog="Example: readwrench concordance init a.epub b.epub c.epub --concepts concepts.json --out ws")
    command.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = command.add_subparsers(dest="command", required=True)
    initialize = sub.add_parser("init", help="Create a Taguette project and immutable chapter snapshots")
    initialize.add_argument("books", nargs="+", type=Path)
    initialize.add_argument("--concepts", required=True, type=Path)
    initialize.add_argument("--out", required=True, type=Path)
    initialize.add_argument("--name", default="Concept comparison")
    initialize.add_argument("--max-hits", type=int, default=10000)
    initialize.add_argument("--include-nonlinear", action="store_true", help="Also search spine items marked linear=no")
    for name, help_text in [("list", "List candidate passages as JSON"), ("check", "Validate source snapshots and Taguette locations"),
                            ("export", "Export the concept matrix, reader and standard locators")]:
        child = sub.add_parser(name, help=help_text)
        child.add_argument("workspace", type=Path)
        child.add_argument("--database", type=Path, help="Read a Taguette exported project or local database without modifying it")
        child.add_argument("--project-id", type=int, help="Choose among multiple imported copies")
        if name == "list":
            child.add_argument("--concept", help="Filter by concept query ID")
            child.add_argument("--book", help="Filter by edition/book ID")
            child.add_argument("--chapter", help="Filter by exact EPUB or researcher-supplied chapter title")
            child.add_argument("--status", choices=["classified", "unreviewed", "rejected", "conflicted"])
            child.add_argument("--limit", type=int, default=20)
            child.add_argument("--offset", type=int, default=0)
        if name == "export":
            child.add_argument("--out", required=True, type=Path)
            child.add_argument("--context", type=int, default=160)
    label = sub.add_parser("label", help="Apply an explicit human decision using Taguette's models")
    label.add_argument("workspace", type=Path)
    label.add_argument("occurrence_id")
    label.add_argument("--sense", action="append", default=[], help="Researcher's category; repeat for multiple senses")
    label.add_argument("--reject", action="store_true")
    label.add_argument("--clear", action="store_true")
    label.add_argument("--project-id", type=int, help="Choose among matching projects in the workspace database")
    resolve = sub.add_parser("resolve", help="Verify an occurrence against the exact original EPUB edition")
    resolve.add_argument("workspace", type=Path)
    resolve.add_argument("occurrence_id")
    resolve.add_argument("--source", type=Path, required=True)
    resolve.add_argument("--database", type=Path, help="Read the same reviewed Taguette database used for export")
    resolve.add_argument("--project-id", type=int)
    return command


def _require_html5lib() -> None:
    # Beautiful Soup reports a missing tree builder as FeatureNotFound (a ValueError);
    # raise the import error instead so the dispatcher prints the install hint.
    import importlib.util
    if importlib.util.find_spec("html5lib") is None:
        raise ModuleNotFoundError("No module named 'html5lib'", name="html5lib")


def run(args) -> dict:
    _require_html5lib()
    from . import workspace

    if args.command == "init":
        manifest = workspace.initialize(args.books, args.concepts, args.out, name=args.name,
                                        max_hits=args.max_hits, include_nonlinear=args.include_nonlinear)
        return {"workspace": str(args.out.resolve()), "books": len(manifest["books"]),
                "chapters": len(manifest["chapters"]), "candidates": manifest["candidate_count"]}
    if args.command == "label":
        return workspace.label(args.workspace, args.occurrence_id, senses=args.sense, reject=args.reject,
                               clear=args.clear, project_id=args.project_id)
    if args.command == "resolve":
        return workspace.resolve(args.workspace, args.occurrence_id, args.source,
                                 database=args.database, project_id=args.project_id)
    if args.command == "export":
        from .exporting import export_workspace
        return export_workspace(args.workspace, args.out, database=args.database, project_id=args.project_id, context=args.context)
    manifest, _, hits = workspace.collect(args.workspace, database=args.database, project_id=args.project_id,
                                         strict_decisions=args.command != "list")
    if args.command == "check":
        return {"valid": True, "books": len(manifest["books"]), "chapters": len(manifest["chapters"]),
                "seeded_candidates": manifest["candidate_count"], "current_occurrences": len(hits),
                "status_counts": dict(Counter(hit["status"] for hit in hits))}
    if args.limit < 1 or args.limit > 10000 or args.offset < 0:
        raise InputError("Use --limit 1–10000 and a nonnegative --offset")
    if args.concept and args.concept not in {concept["id"] for concept in manifest["concepts"]}:
        raise InputError("Unknown concept ID")
    if args.book and args.book not in {book["id"] for book in manifest["books"]}:
        raise InputError("Unknown book ID")
    hits = [hit for hit in hits if (not args.concept or hit["concept_id"] == args.concept)
            and (not args.book or hit["book_id"] == args.book) and (not args.status or hit["status"] == args.status)
            and (not args.chapter or hit["chapter"] == args.chapter)]
    return {"total": len(hits), "offset": args.offset, "occurrences": hits[args.offset:args.offset + args.limit]}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except (InputError, OSError, ValueError) as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
