"""``readwrench check [PATH ...]``: one read-only pass over a folder of books, papers, fonts and captures.

Exit status: 0 no finding at the --fail-on threshold, 1 findings at or above
it, 2 usage or input error (unknown option or check, missing path).
Read-only: no file is written, nothing is executed, no network is used.
"""
from __future__ import annotations

import argparse
import os
import sys

from readwrench import __version__

from .discover import DiscoveryError, discover
from .model import AREAS
from .registry import CHECKS, CLASSIFIERS, run_checks, select
from .report import RENDERERS, Run, render_text

PROG = "readwrench check"
FAIL_ON = {"error": "error", "warning": "warning", "never": None}

EPILOG = """\
examples:
  readwrench check                                   scan the current folder
  readwrench check ~/Reading --fail-on warning
  readwrench check books/ --only footnotes
  readwrench check . --format markdown > reading-check.md
  readwrench check . --format sarif > readwrench.sarif
  readwrench check --list-checks

exit status: 0 nothing at the --fail-on level, 1 findings at or above it,
2 usage or input error. Nothing is written, executed or sent anywhere.
Docs: https://github.com/tianhaogutum/readwrench/blob/main/docs/check.md
"""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # usage errors exit 2, like the other commands
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _names(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise argparse.ArgumentTypeError("expected one or more check names, comma separated")
    return names


def parser() -> argparse.ArgumentParser:
    p = Parser(
        prog=PROG, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
        description="Find EPUBs, PDFs, fonts, saved web pages and readwrench review files under PATH by their "
                    "content, and run every readwrench analysis that needs no decision from you: what is broken, "
                    "stale or missing, and the command that shows the details.")
    p.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    p.add_argument("paths", nargs="*", metavar="PATH", help="folders or files to scan (default: .)")
    p.add_argument("--format", choices=("text", "json", "sarif", "markdown"), default="text",
                   help="report format (default: text); sarif is SARIF 2.1.0 for code-scanning viewers")
    p.add_argument("--fail-on", choices=tuple(FAIL_ON), default="error",
                   help="exit 1 when a finding is at least this severe (default: error)")
    p.add_argument("--only", type=_names, action="append", default=[], metavar="CHECK[,CHECK]",
                   help="run only these checks (repeatable)")
    p.add_argument("--skip", type=_names, action="append", default=[], metavar="CHECK[,CHECK]",
                   help="do not run these checks (repeatable)")
    p.add_argument("--list-checks", action="store_true", help="list the available checks and exit")
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                   help="colour text output (default: auto; honours NO_COLOR)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="text: show a hint under each finding and list inputs without findings")
    return p


def list_checks() -> str:
    width = max(len(check.name) for check in CHECKS) + 2
    lines = ["Checks (select with --only/--skip):"]
    for area in AREAS:
        group = [check for check in CHECKS if check.area == area]
        if not group:
            continue
        lines += ["", f"{area}:"]
        for check in group:
            lines.append(f"  {check.name.ljust(width)}{check.summary}")
            notes = ["built in" if check.tool == "-" else f"uses: readwrench {check.tool}"]
            if check.extra:
                notes.append(f"needs readwrench[{check.extra}]")
            if check.rules:
                notes.append(f"rules: {', '.join(check.rules)}")
            pad = "  " + "".ljust(width)
            lines.append(pad + "; ".join(notes))
            if check.note:
                lines.append(pad + check.note)
    return "\n".join(lines) + "\n"


def _use_color(choice: str) -> bool:
    if choice != "auto":
        return choice == "always"
    try:
        tty = sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False
    return tty and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.list_checks:
        sys.stdout.write(list_checks())
        return 0
    try:
        checks = select([name for group in args.only for name in group],
                        [name for group in args.skip for name in group])
        if not checks:
            raise ValueError("--only/--skip leave no check to run")
        paths = args.paths or ["."]
        discovery = discover(paths, CLASSIFIERS)
    except (ValueError, DiscoveryError) as exc:
        sys.stderr.write(f"{PROG}: error: {exc}\n")
        return 2
    findings = run_checks(discovery, checks)
    run = Run(paths, discovery.sources, discovery.scanned, checks, findings, FAIL_ON[args.fail_on])
    if args.format == "text":
        output = render_text(run, color=_use_color(args.color), verbose=args.verbose)
    else:
        output = RENDERERS[args.format](run)
    try:
        sys.stdout.write(output)
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
    return 1 if run.failed else 0


__all__ = ["main", "parser"]

if __name__ == "__main__":
    raise SystemExit(main())
