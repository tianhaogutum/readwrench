"""Three deliberate steps: propose, review, then export approved correspondence.

PDF dependencies are imported inside :func:`run`, so ``--help`` and ``--version``
work before the ``pdf-pages`` extra is installed.
"""

import argparse
import json
import os
import signal
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from readwrench import __version__

from .mapping import BridgeError, approval_snapshot, read_plan, utc_now, validate_map

PROG = "readwrench pdf-pages"


@contextmanager
def new_directory(target: Path):
    """Commit all outputs together; preserve existing work and clean up failures."""
    target = target.resolve()
    if target.exists():
        raise BridgeError(f"Output already exists: {target}; choose a new directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target.name}-", dir=target.parent) as temporary:
        staging = Path(temporary) / "result"
        staging.mkdir()
        yield staging
        os.rename(staging, target)


def write_new_json(target: Path, value):
    # Write fully before publishing. A hard link is an atomic exclusive create,
    # unlike replace(), which could overwrite an existing approval or document.
    serialized = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=f".{target.name}-", dir=target.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parser():
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Review PDF page correspondence before comparing content.",
    )
    p.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    p.add_argument(
        "--timeout",
        type=int,
        default=180,
        metavar="SECONDS",
        help="Unix execution deadline (default: 180)",
    )
    sub = p.add_subparsers(dest="command", required=True)
    plan = sub.add_parser(
        "plan", help="Propose a page map and local HTML review (1-200 pages per PDF)"
    )
    plan.add_argument("original", type=Path)
    plan.add_argument("modified", type=Path)
    plan.add_argument("--out", type=Path, required=True, help="New output directory")
    plan.add_argument(
        "--min-score", type=float, default=0.62, help="Candidate floor, 0-1 (default: 0.62)"
    )
    confirm = sub.add_parser(
        "confirm", help="Assert you reviewed every pair in an edited mapping JSON"
    )
    confirm.add_argument("mapping", type=Path)
    confirm.add_argument("--reviewer", required=True, help="Name recorded with the approval")
    confirm.add_argument("--out", type=Path, required=True, help="New approved JSON file")
    export = sub.add_parser(
        "export", help="Export an approved map, difference PDF, and optional aligned PDFs"
    )
    export.add_argument("mapping", type=Path)
    export.add_argument("--out", type=Path, required=True, help="New output directory")
    export.add_argument(
        "--original", type=Path, help="Relocated original PDF; SHA-256 must still match"
    )
    export.add_argument(
        "--modified", type=Path, help="Relocated revised PDF; SHA-256 must still match"
    )
    export.add_argument(
        "--dpi",
        type=int,
        default=120,
        help="Raster comparison DPI, 36-200; long edge capped at 1400 pixels",
    )
    export.add_argument(
        "--pixel-threshold", type=int, default=24, help="Per-channel raster threshold, 0-254"
    )
    export.add_argument(
        "--aligned",
        action="store_true",
        help="Also output same-count, same-size pairs for diff-pdf",
    )
    export.add_argument(
        "--changed-only",
        action="store_true",
        help="Omit unchanged matched panels; keep the complete map and inserted/deleted pages",
    )
    return p


def run(args):
    if args.command == "plan":
        if not 0 <= args.min_score <= 1:
            raise BridgeError("--min-score must be between 0 and 1")
        from .core import make_plan
        from .review import write_review

        with new_directory(args.out) as output:
            assets = output / "assets"
            assets.mkdir()
            plan = make_plan(args.original, args.modified, assets, args.min_score)
            validate_map(plan)
            write_new_json(output / "mapping.json", plan)
            write_review(plan, output / "review.html")
        pending = sum(r["status"] == "pending" for r in plan["rows"])
        print(
            f"Proposed {len(plan['rows'])} rows; {pending} need attention. All require human approval."
        )
        print(f"Open locally: {(args.out / 'review.html').resolve()}")
        print(f"Editable map: {(args.out / 'mapping.json').resolve()}")
    elif args.command == "confirm":
        reviewer = args.reviewer.strip()
        if not reviewer or len(reviewer) > 120:
            raise BridgeError("--reviewer must contain 1-120 nonblank characters")
        data = read_plan(args.mapping)
        from .core import verify_sources

        verify_sources(data)
        # A user may have edited pair indices. Do not carry the old suggestion's
        # scores into a new approval as though they described the edited pair.
        data["rows"] = [
            {
                "original": row["original"],
                "modified": row["modified"],
                "confirmed": True,
                "status": "approved",
                "reasons": ["human_review"],
            }
            for row in data["rows"]
        ]
        data["review"] = {
            "reviewer": reviewer,
            "reviewed_at": utc_now(),
            "method": "cli-confirm",
            "approved_map": approval_snapshot(data),
        }
        validate_map(data, approved=True)
        write_new_json(args.out, data)
        print(f"Recorded approval by {reviewer}: {args.out.resolve()}")
    elif args.command == "export":
        if not 36 <= args.dpi <= 200:
            raise BridgeError("--dpi must be between 36 and 200")
        if not 0 <= args.pixel_threshold <= 254:
            raise BridgeError("--pixel-threshold must be between 0 and 254")
        if sys.version_info < (3, 12):
            # Py-PDF-Compare, which draws the word diff, supports Python 3.12+ only.
            raise BridgeError(
                "export needs Python 3.12 or newer (Py-PDF-Compare requires it); "
                "plan and confirm also work on Python 3.11"
            )
        from .export import export_comparison

        data = read_plan(args.mapping)
        with new_directory(args.out) as output:
            result = export_comparison(
                data,
                output,
                original=args.original,
                modified=args.modified,
                dpi=args.dpi,
                threshold=args.pixel_threshold,
                aligned=args.aligned,
                changed_only=args.changed_only,
            )
        print(json.dumps(result["counts"], sort_keys=True))
        print(f"Difference PDF: {(args.out / 'diff.pdf').resolve()}")


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.timeout <= 0:
        p.error("--timeout must be positive")
    previous_handler = None
    # SIGALRM handlers can only be installed from the main thread (and not on Windows).
    if hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread():

        def timed_out(signum, frame):
            raise BridgeError(
                f"Execution exceeded {args.timeout}s; use --timeout to allow more time"
            )

        previous_handler = signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(args.timeout)
    try:
        run(args)
        return 0
    except (BridgeError, OSError, RuntimeError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(f"{PROG}: cancelled; partial output removed", file=sys.stderr)
        return 130
    finally:
        if previous_handler is not None:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
