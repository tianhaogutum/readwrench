"""Top-level ``readwrench`` command: dispatches to one tool per subcommand.

Each tool lives in its own subpackage exposing ``cli.main(argv) -> int``. Tools
are imported lazily, so ``readwrench --help`` stays fast and a tool that is not
shipped in this build reports itself instead of failing with a traceback. Tools
with heavy third-party dependencies declare them in ``requires``; when one is not
installed the command prints a one-line install hint for its extra instead of a
traceback.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import importlib
import importlib.util
import sys

from . import __version__

PROG = "readwrench"
REPO_URL = "https://github.com/tianhaogutum/readwrench"


@dataclass(frozen=True)
class Command:
    name: str
    # Dotted path of the module exposing ``main(argv)``.
    module: str
    summary: str
    group: str
    # Top-level import names of optional dependencies, and the extra that installs them
    # (exactly as written inside ``readwrench[...]``).
    requires: tuple[str, ...] = ()
    extra: str | None = None
    # Oldest Python the command's dependencies install on (the package itself needs 3.11).
    python: tuple[int, int] = (3, 11)


GROUPS = ("Check", "Find & compare", "PDF", "EPUB & fonts", "Web captures")

COMMANDS: tuple[Command, ...] = (
    Command("check", "readwrench.check.cli",
            "Read-only scan of a reading folder: what each readwrench tool finds, in one report",
            "Check"),
    Command("quote", "readwrench.quotelocator.cli",
            "Recover a lost quotation in a local book or paper; review every occurrence in context",
            "Find & compare",
            requires=("reanchor", "bs4", "pypdf", "ebooklib", "lxml", "regex", "rapidfuzz"), extra="quote"),
    Command("clues", "readwrench.cluejoin.cli",
            "Find the material that contains two or three remembered phrases, with excerpts",
            "Find & compare"),
    Command("concordance", "readwrench.concordance.cli",
            "Compare how a term is used across three to five EPUB books (Taguette concept matrix)",
            "Find & compare",
            requires=("taguette", "pkg_resources", "ebooklib", "bs4", "html5lib", "lxml", "jinja2", "markupsafe",
                      "jsonschema"),
            extra="concordance"),
    Command("pdf-pages", "readwrench.pagebridge.cli",
            "Match corresponding pages of two PDF versions before comparing them",
            "PDF",
            requires=("pdf_compare", "pymupdf", "fitz", "PIL", "numpy", "scipy", "rapidfuzz", "dhash", "reportlab"),
            extra="pdf-pages"),
    Command("reanchor", "readwrench.reanchor.cli",
            "Carry PDF annotations from an old version to a revised one, after review",
            "PDF",
            requires=("pymupdf", "fitz", "pdfanno", "pydantic", "rapidfuzz", "regex"), extra="reanchor",
            python=(3, 12)),
    Command("footnotes", "readwrench.footnotes.cli",
            "Review EPUB footnote links and build a copy with precise return links",
            "EPUB & fonts",
            requires=("lxml",), extra="footnotes"),
    Command("subset-proof", "readwrench.subsetproof.cli",
            "Prove a subset font still covers the manuscript text and OpenType features",
            "EPUB & fonts",
            requires=("fontTools", "uharfbuzz", "regex", "brotli"), extra="subset-proof"),
    Command("lineage", "readwrench.lineage.cli",
            "Turn several saved captures of one web page into an offline paragraph timeline",
            "Web captures"),
)
BY_NAME = {command.name: command for command in COMMANDS}

# Former standalone tool names, used only to point users at the new subcommand.
# (subset-proof kept its name.)
LEGACY = {
    "concept-concordance": "concordance",
    "quote-locator": "quote",
    "page-lineage": "lineage",
    "epub-footback": "footnotes",
    "cluejoin": "clues",
    "pdf-page-bridge": "pdf-pages",
    "pdf-reanchor": "reanchor",
}


def _present(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _installed(command: Command) -> bool:
    # Locating the cli module imports only the (light) subpackage __init__, not the tool.
    return _present(command.module.rpartition(".")[0]) and _present(command.module)


def missing_dependency_message(command: Command, dependency: str) -> str:
    if sys.version_info[:2] < command.python:
        return (f"{PROG} {command.name} needs {dependency}, which needs Python "
                f"{command.python[0]}.{command.python[1]} or newer (this is Python "
                f"{sys.version_info[0]}.{sys.version_info[1]})\n")
    extra = f"{PROG}[{command.extra}]"
    return (f"{PROG} {command.name} needs {dependency}: pipx install '{extra}' "
            f"(or pip install '{extra}')\n")


def _missing_requirement(command: Command, exc: ModuleNotFoundError) -> str | None:
    """The optional dependency behind ``exc``, if it is one this command declares."""
    root = (exc.name or "").partition(".")[0]
    return root if root and root in command.requires else None


def overview() -> str:
    width = max(len(command.name) for command in COMMANDS) + 2
    lines = [
        f"{PROG} {__version__} - offline tools for reading and annotating books, papers, PDFs, EPUBs and web pages",
        "",
        f"usage: {PROG} <command> [args...]",
        f"       {PROG} <command> --help",
    ]
    for group in GROUPS:
        lines += ["", f"{group}:"]
        for command in COMMANDS:
            if command.group != group:
                continue
            note = "" if _installed(command) else "  (coming soon)"
            lines.append(f"  {command.name.ljust(width)}{command.summary}{note}")
    extras = [f"{PROG}[{c.extra}]" + ("" if c.extra == c.name else f" ({c.name})") for c in COMMANDS if c.extra]
    lines += [
        "",
        "options:",
        "  -h, --help     show this overview",
        "  -V, --version  show the version and exit",
        "",
        "check, clues and lineage need no extra. Optional extras:",
        *("  " + ", ".join(extras[i:i + 3]) + "," for i in range(0, len(extras), 3)),
        f"  or {PROG}[all] for every command.",
        "",
        f"Docs and issues: {REPO_URL}",
    ]
    return "\n".join(lines) + "\n"


def _unknown(name: str) -> int:
    message = f"{PROG}: unknown command {name!r}"
    if name in LEGACY:
        message += f"; {name} is now '{PROG} {LEGACY[name]}'"
    else:
        match = difflib.get_close_matches(name, [*BY_NAME, *LEGACY], n=1, cutoff=0.6)
        if match:
            suggestion = LEGACY.get(match[0], match[0])
            message += f"; did you mean '{suggestion}'?"
    sys.stderr.write(message + f"\nRun '{PROG} --help' to list commands.\n")
    return 2


def _not_available(command: Command) -> int:
    sys.stderr.write(f"{PROG}: command {command.name!r} is not available in "
                     f"{PROG} {__version__} yet; see {REPO_URL}\n")
    return 2


def run(command: Command, argv: list[str]) -> int:
    if not _installed(command):
        return _not_available(command)
    try:
        module = importlib.import_module(command.module)
        result = module.main(argv)
    except ModuleNotFoundError as exc:
        # A subpackage that exists but does not ship its cli module yet.
        if exc.name == command.module:
            return _not_available(command)
        dependency = _missing_requirement(command, exc)
        if dependency is None:
            raise
        sys.stderr.write(missing_dependency_message(command, dependency))
        return 2
    return 0 if result is None else result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write(overview())
        return 0
    first, rest = args[0], args[1:]
    if first in ("-V", "--version"):
        sys.stdout.write(f"{PROG} {__version__}\n")
        return 0
    if first == "help":
        if not rest:
            sys.stdout.write(overview())
            return 0
        first, rest = rest[0], ["--help"]
    if first.startswith("-"):
        sys.stderr.write(f"{PROG}: unknown option {first!r}\nRun '{PROG} --help' for usage.\n")
        return 2
    command = BY_NAME.get(first)
    if command is None:
        return _unknown(first)
    return run(command, rest)


if __name__ == "__main__":
    raise SystemExit(main())
