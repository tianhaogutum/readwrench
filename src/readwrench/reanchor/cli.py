"""Small CLI composing pdfanno anchors/scoring with offline confirmation.

Heavy dependencies (PyMuPDF, pdfanno, pydantic, RapidFuzz, regex) are imported only
after the arguments are parsed, so ``--help`` and ``--version`` work without them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from readwrench import __version__

PROG = "readwrench reanchor"


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog=PROG,
        description="Propose and explicitly confirm native PDF annotation migration, locally.",
    )
    cli.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    commands = cli.add_subparsers(dest="command", required=True)
    proposal = commands.add_parser(
        "propose", help="Create proposal.json, screenshots and offline review.html"
    )
    proposal.add_argument("old_pdf", type=Path)
    proposal.add_argument("new_pdf", type=Path)
    proposal.add_argument(
        "--out", required=True, type=Path, help="New output directory (never overwritten)"
    )
    proposal.add_argument(
        "--candidates", type=int, default=5, choices=range(1, 21), metavar="1..20"
    )
    proposal.add_argument(
        "--minimum-text",
        type=float,
        default=0.78,
        help="Fuzzy text similarity floor, 0.50..1.00 (default .78)",
    )
    migrate = commands.add_parser(
        "apply", help="Write only explicitly accepted choices, plus an unmatched archive"
    )
    migrate.add_argument("bundle", type=Path)
    migrate.add_argument("decisions", type=Path)
    migrate.add_argument("--out", required=True, type=Path, help="New output directory")
    migrate.add_argument(
        "--old-pdf", type=Path, help="Moved original, required to have identical SHA-256"
    )
    migrate.add_argument(
        "--new-pdf", type=Path, help="Moved revision, required to have identical SHA-256"
    )
    return cli


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    # Imported after parsing so that --help works without the optional dependencies.
    # A missing dependency propagates as ModuleNotFoundError; the readwrench dispatcher
    # turns it into an install hint for the ``reanchor`` extra.
    import pymupdf
    from pydantic import ValidationError

    from .workflow import ReviewError, apply, propose

    try:
        # Keep stdout a single JSON document even when MuPDF diagnoses a
        # malformed source annotation while rendering its preserved evidence.
        pymupdf.set_messages(stream=sys.stderr)
        if args.command == "propose":
            if not 0.50 <= args.minimum_text <= 1:
                raise ReviewError("--minimum-text must be between 0.50 and 1.00")
            result = propose(
                args.old_pdf,
                args.new_pdf,
                args.out,
                candidates=args.candidates,
                minimum_text=args.minimum_text,
            )
            print(
                json.dumps(
                    {
                        "proposal_id": result.proposal_id,
                        "annotations": len(result.annotations),
                        "with_candidates": sum(bool(a.candidates) for a in result.annotations),
                        "review": str(args.out.resolve() / "review.html"),
                        "warnings": result.warnings,
                    },
                    indent=2,
                )
            )
        else:
            result = apply(
                args.bundle, args.decisions, args.out, old=args.old_pdf, new=args.new_pdf
            )
            print(
                json.dumps(
                    {
                        "output": str(args.out.resolve()),
                        "migrated": result["migrated_count"],
                        "unmatched": result["unmatched_count"],
                    },
                    indent=2,
                )
            )
        return 0
    except (
        ReviewError,
        OSError,
        ValueError,
        ValidationError,
        pymupdf.FileDataError,
        RuntimeError,
    ) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
