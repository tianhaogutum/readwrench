"""Command-line entry point for ``readwrench lineage``."""
from __future__ import annotations

import argparse
import os
import stat
import sys

from readwrench import __version__

from .fileio import LIMITS, InputError, write_outputs
from .jsutil import stringify
from .urls import normalize_url

PROG = "readwrench lineage"

DESCRIPTION = """Offline paragraph history for local web captures.

Files are read locally. No network, capture browser, service or AI is used.
Capture times come from explicit manifest values, SingleFile metadata, MHTML
Date, or the selected WARC-Date. Missing times remain unknown; file modification
times are never used. Edit captures.json to add timezone-qualified capturedAt,
labels or selectors."""

EPILOG = """examples:
  readwrench lineage init --url URL --out captures.json FILE...
  readwrench lineage build captures.json --out timeline.html [--json timeline.json]
  readwrench lineage archivebox --out captures.json [--url URL] INDEX.json|INDEX.jsonl...

Exit status: 0 on success, 1 on invalid input, extraction failure or write failure."""


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(f"{message} (see '{self.prog} --help')")


def _common(command: argparse.ArgumentParser) -> None:
    command.add_argument("--force", action="store_true",
                         help="replace existing outputs; original inputs are always protected")
    command.add_argument("--quiet", action="store_true", help="suppress progress and warnings; errors still go to stderr")


def build_parser() -> argparse.ArgumentParser:
    root = _Parser(prog=PROG, description=DESCRIPTION, epilog=EPILOG,
                   formatter_class=argparse.RawDescriptionHelpFormatter)
    root.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    commands = root.add_subparsers(dest="command", metavar="{init,build,archivebox}")
    init = commands.add_parser("init", help="write a manifest for local capture files",
                               description="Write a lineage manifest listing local HTML, MHTML or WARC captures "
                                           "(paths relative to the manifest).")
    init.add_argument("files", nargs="+", metavar="FILE", help="capture files (1-100)")
    init.add_argument("--url", required=True, help="original HTTP(S) source URL")
    init.add_argument("--out", required=True, help="manifest to write (relative to the current directory)")
    _common(init)
    build = commands.add_parser("build", help="build the HTML timeline (and JSON) from a manifest",
                                description="Extract every capture named in a manifest and write the offline HTML "
                                            "timeline, plus structured JSON with --json.")
    build.add_argument("manifest", help="lineage manifest (captures.json)")
    build.add_argument("--out", required=True, help="HTML report to write")
    build.add_argument("--json", dest="json_out", metavar="FILE", help="also write the timeline as JSON")
    _common(build)
    archivebox = commands.add_parser("archivebox", help="import per-snapshot ArchiveBox metadata into a manifest",
                                     description="Import per-snapshot ArchiveBox index.json / index.jsonl files "
                                                 "into a lineage manifest.")
    archivebox.add_argument("indexes", nargs="+", metavar="INDEX", help="per-snapshot index.json or index.jsonl")
    archivebox.add_argument("--out", required=True, help="manifest to write")
    archivebox.add_argument("--url", help="expected HTTP(S) source URL")
    _common(archivebox)
    return root


def _write_json(value) -> str:
    return stringify(value) + "\n"


def _run(args, log) -> None:
    if args.command == "init":
        if len(args.files) > LIMITS["captures"]:
            raise InputError("init requires 1–100 local capture files")
        for file in args.files:
            try:
                info = os.stat(file)
            except OSError as error:
                raise InputError(f"Cannot inspect capture {file}: {error.strerror or error}") from None
            if not stat.S_ISREG(info.st_mode):
                raise InputError(f"Not a regular capture file: {file}")
            if info.st_size > LIMITS["captureBytes"]:
                raise InputError(f"Capture exceeds 10 MiB limit: {file}")
        root = os.path.dirname(os.path.abspath(args.out))
        manifest = {"version": 1, "url": normalize_url(args.url),
                    "captures": [{"file": os.path.relpath(os.path.abspath(file), root)} for file in args.files]}
        write_outputs([{"filename": args.out, "data": _write_json(manifest)}], force=args.force,
                      protected_files=args.files)
        log(f"Wrote {args.out}. Review ordering and add capture times where known.")
    elif args.command == "archivebox":
        from .archivebox import import_archivebox
        result = import_archivebox(args.indexes, args.out, args.url)
        write_outputs([{"filename": args.out, "data": _write_json(result["manifest"])}], force=args.force,
                      protected_files=result["protectedFiles"])
        for warning in result["warnings"]:
            log(f"Warning: {warning}")
        log(f"Imported {len(result['manifest']['captures'])} snapshots into {args.out}.")
    else:
        from .render import render_html
        from .timeline import build_timeline
        built = build_timeline(args.manifest, on_progress=log)
        timeline = built["timeline"]
        outputs = [{"filename": args.out, "data": render_html(timeline)}]
        if args.json_out:
            outputs.append({"filename": args.json_out, "data": _write_json(timeline)})
        write_outputs(outputs, force=args.force, protected_files=built["protectedFiles"])
        for warning in [*timeline["warnings"], *(w for c in timeline["captures"] for w in c["warnings"]),
                        *(w for t in timeline["transitions"] for w in t["warnings"])]:
            log(f"Warning: {warning}")
        also = f" and {args.json_out}" if args.json_out else ""
        log(f"Wrote {args.out}{also}: {len(timeline['captures'])} captures, {len(timeline['lineages'])} paragraph "
            "histories.")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    try:
        args = parser.parse_args(argv)
        if args.command is None:
            raise _UsageError(f"a command is required: init, build or archivebox (see '{PROG} --help')")
    except _UsageError as error:
        sys.stderr.write(f"{PROG}: {error}\n")
        return 1
    quiet = args.quiet

    def log(message: str) -> None:
        if not quiet:
            sys.stderr.write(message + "\n")

    try:
        _run(args, log)
    except InputError as error:
        sys.stderr.write(f"{PROG}: {error}\n")
        return 1
    except Exception as error:  # noqa: BLE001 - every failure is reported as exit status 1
        sys.stderr.write(f"{PROG}: {error}\nUnexpected failure. No capture scripts or remote resources were "
                         "executed.\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
