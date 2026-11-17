"""Command line for ``readwrench subset-proof``.

The parser needs only the standard library, so ``--help`` works without the optional
dependencies; :mod:`.proof` (fontTools, uharfbuzz, regex, brotli) is imported after
the arguments are parsed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from readwrench import __version__

PROG = "readwrench subset-proof"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog=PROG,
                                   description="Strict manuscript proof around fontTools and HarfBuzz")
    root.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in (("build", "Generate a fontTools subset and prove it against the manuscript"),
                            ("compare", "Prove a subset produced by another tool")):
        command = commands.add_parser(name, help=help_text, description=help_text)
        command.add_argument("font", type=Path, help="Original TTF, OTF, WOFF, WOFF2, or collection")
        if name == "compare":
            command.add_argument("candidate", type=Path, help="Subset font to prove against the original")
            command.add_argument("--candidate-index", type=int,
                                 help="Required zero-based face index for a candidate collection")
        command.add_argument("text", type=Path, help="UTF-8 manuscript, or --manifest JSON")
        command.add_argument("--out", type=Path, required=True, help="New output directory (must not exist)")
        command.add_argument("--manifest", action="store_true", help="Read an explicit shaping-runs JSON manifest")
        command.add_argument("--font-index", type=int, help="Required zero-based face index for a collection")
        command.add_argument("--features", default="", help="Shaping features, e.g. liga=1,kern=1,ss01=1")
        command.add_argument("--at", action="append", default=[], metavar="AXIS=VALUE,...",
                             help="Variable-font location; repeat to test multiple locations")
        command.add_argument("--direction", choices=["auto", "ltr", "rtl", "ttb", "btt"], default="auto")
        command.add_argument("--script", help="Four-letter script code, e.g. Latn or Arab")
        command.add_argument("--language", default="und", help="BCP47 language tag")
        if name == "build":
            command.add_argument("--keep-features", default="*", help="Retain these OpenType features; default *; empty drops all")
            command.add_argument("--flavor", choices=["sfnt", "woff", "woff2"], default="sfnt")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # Heavy optional dependencies; a missing one surfaces as ModuleNotFoundError,
    # which the readwrench dispatcher turns into an install hint.
    from . import proof

    try:
        report = proof.execute(args)
    except proof.EXPECTED_ERRORS as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    if not report["accepted"]:
        proof.rejection_diagnostics(report)
    print(json.dumps({"accepted": report["accepted"], "issues": report["issues"],
                      "report": str(args.out / "report.json"), "specimen": str(args.out / "specimen.html")}))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
