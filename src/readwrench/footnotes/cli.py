"""readwrench footnotes: offline scan → per-item review → separate candidate + EPUBCheck evidence.

The lxml-based modules are imported inside the subcommands that need them, so
``--help`` and the terminal ``review`` step work without the ``footnotes`` extra.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from readwrench import __version__

from .base import DEFAULT_LABEL_TEMPLATE, EpubError

PROG = "readwrench footnotes"


def write_json(path: Path, data: dict, max_bytes: int | None = None) -> None:
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if max_bytes is not None and len(serialized.encode("utf-8")) > max_bytes:
        raise EpubError("Review output exceeds 16 MiB; split the book's review in an EPUB editor")
    path.write_text(serialized, encoding="utf-8")


def read_json(path: Path) -> dict:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise EpubError("Review file exceeds 16 MiB")
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise EpubError(f"Duplicate JSON field: {key}")
            result[key] = item
        return result

    def invalid_constant(value):
        raise EpubError(f"Invalid JSON constant: {value}")

    with path.open(encoding="utf-8-sig") as stream:
        value = json.load(stream, object_pairs_hook=unique, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise EpubError("Review file must contain a JSON object")
    return value


@contextmanager
def output_directory(path: Path):
    if path.exists() or path.is_symlink():
        raise EpubError(f"Output already exists; use a new directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".footnotes-", dir=path.parent) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        yield staging
        if path.exists() or path.is_symlink():
            raise EpubError(f"Output was created concurrently: {path}")
        staging.rename(path)


def scan(args) -> int:
    from .audit import audit
    from .epub import Book
    from .html import review_html

    options = {"all_links": args.all_links, "ref_xpath": args.ref_xpath, "note_xpath": args.note_xpath,
               "label_template": args.label_template, "label_lang": args.label_lang}
    with Book(args.epub) as book:
        result = audit(book, options)
        with output_directory(args.out) as output:
            write_json(output / "plan.json", result.plan, max_bytes=16 * 1024 * 1024)
            (output / "review.html").write_text(review_html(result.plan), encoding="utf-8")
        counts = Counter(r["status"] for r in result.plan["references"])
        print(json.dumps({"references": len(result.plan["references"]), "statuses": dict(counts),
                          "warnings": len(result.plan["warnings"]), "review": str(args.out / "review.html"),
                          "decisions": str(args.out / "plan.json")}, ensure_ascii=False))
    return 0


def review(args) -> int:
    plan = read_json(args.plan)
    if type(plan.get("schema_version")) is not int or plan.get("schema_version") != 1 or not isinstance(plan.get("references"), list):
        raise EpubError("Unsupported review plan")
    if args.out.exists() or args.out.is_symlink():
        raise EpubError(f"Output already exists: {args.out}")
    notes_list = plan.get("notes")
    if not isinstance(notes_list, list) or any(
        not isinstance(n, dict) or not isinstance(n.get("key"), str) or not isinstance(n.get("excerpt"), str)
        for n in notes_list
    ):
        raise EpubError("Review plan has invalid note excerpts; regenerate it with readwrench footnotes scan")
    for note in notes_list:
        if "links" in note and (not isinstance(note["links"], list) or any(not isinstance(link, dict) for link in note["links"])):
            raise EpubError("Review plan has an invalid note-link catalog")
    for record in plan["references"]:
        if not isinstance(record, dict) or type(record.get("number")) is not int or not isinstance(record.get("location"), dict):
            raise EpubError("Review plan has invalid reference locations; regenerate it with readwrench footnotes scan")
        if any(not isinstance(record.get(field), str) for field in ("key", "context", "href", "status")):
            raise EpubError("Review plan has invalid reference text; regenerate it with readwrench footnotes scan")
        if not isinstance(record["location"].get("path"), str) or "line" not in record["location"]:
            raise EpubError("Review plan has invalid source locations; regenerate it with readwrench footnotes scan")
        if record.get("note_key") is not None and not isinstance(record.get("note_key"), str):
            raise EpubError("Review plan has an invalid note association")
        if type(record.get("approved")) is not bool or not isinstance(record.get("action"), str) or record["action"] not in {"skip", "keep", "add_backlink"}:
            raise EpubError("Review plan has invalid approval/action fields")
    notes = {n["key"]: n for n in notes_list}
    print("Confirm each association. a=approve adding precise return, k=keep, s=skip, q=save and quit.")
    for record in plan["references"]:
        if record.get("approved"):
            continue
        note = notes.get(record.get("note_key"))
        print(f"\nReference {record['number']} at {record['location']['path']}:{record['location']['line']}")
        print(record["context"])
        print(f"Forward: {record['href']}\nStatus: {record['status']}")
        print("Note:", note["excerpt"] if note else "Unresolved; edit the book and rescan.")
        if note and isinstance(note.get("location"), dict):
            print("Note location:", note["location"].get("path"), "line", note["location"].get("line"))
        print("Proposed return label:", record.get("backlink_label", "(missing; rerun readwrench footnotes scan)"))
        print("Observed returns:", json.dumps(record.get("returns", []), ensure_ascii=False))
        if note and note.get("links"):
            print("All note links (first 10):", json.dumps(note["links"][:10], ensure_ascii=False))
            if len(note["links"]) > 10:
                print(f"{len(note['links']) - 10} more links are listed in the JSON/HTML note catalog.")
        while True:
            try:
                answer = input("Action [a/k/s/q, default s]: ").strip().lower() or "s"
            except (EOFError, KeyboardInterrupt):
                answer = "q"
            if answer in {"a", "k", "s", "q"}:
                if answer == "a" and record["status"] not in {"missing-return", "ancestor-return", "exact-return"}:
                    print("This unresolved path cannot receive a backlink. Keep/skip it, or edit the EPUB and rescan.")
                    continue
                break
            print("Enter a, k, s, or q.")
        if answer == "q":
            break
        record["approved"] = answer in {"a", "k"}
        record["action"] = {"a": "add_backlink", "k": "keep", "s": "skip"}[answer]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(plan, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    created = False
    try:
        with args.out.open("xb") as stream:
            created = True
            stream.write(payload)
    except BaseException:
        if created:
            args.out.unlink(missing_ok=True)
        raise
    print(f"Saved {args.out}")
    return 0


def candidate(args) -> int:
    from .audit import audit
    from .epub import Book, sha256_file
    from .html import preview_html
    from .patch import make_patch
    from .preserve import verify_preservation
    from .validate import epubcheck

    decisions = read_json(args.decisions)
    if not args.skip_epubcheck and (args.epubcheck_jar is None or not args.epubcheck_jar.is_file()):
        raise EpubError("Provide --epubcheck-jar PATH (or EPUBCHECK_JAR). Use --skip-epubcheck only for an explicitly unvalidated candidate.")
    with Book(args.epub) as book:
        fresh = audit(book, decisions.get("scan_options"))
        patch = make_patch(book, fresh, decisions)
        with output_directory(args.out) as output:
            candidate_path = output / "candidate.epub"
            book.write_candidate(candidate_path, patch.changes)
            patch.manifest["candidate_sha256"] = sha256_file(candidate_path)
            patch.manifest["preservation_check"] = verify_preservation(book, candidate_path, patch.manifest)
            (output / "changes.patch").write_text(patch.diff, encoding="utf-8")
            write_json(output / "decisions.json", decisions)
            if args.skip_epubcheck:
                patch.manifest["format_validation"] = {"status": "not_run", "reason": "Explicit --skip-epubcheck"}
            else:
                patch.manifest["source_format_validation"] = epubcheck(args.epub, args.epubcheck_jar, output, "source", args.check_timeout)
                patch.manifest["format_validation"] = epubcheck(candidate_path, args.epubcheck_jar, output, "candidate", args.check_timeout)
            book.verify_unchanged()
            write_json(output / "manifest.json", patch.manifest)
            (output / "preview.html").write_text(preview_html(patch.manifest, fresh.plan), encoding="utf-8")
        status = patch.manifest["format_validation"]["status"]
        print(json.dumps({"output": str(args.out), "changed_members": len(patch.changes),
                          "approved": patch.manifest["approved_references"], "format_validation": status,
                          "accessibility_status": "not_assessed"}))
        return 0 if status in {"passed", "not_run"} else 3


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog=PROG,
        description="Review existing EPUB footnote links one by one and build a separate "
                    "candidate EPUB with precise return links. Offline; the input is never modified.")
    root.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    scan_parser = commands.add_parser("scan", help="Audit existing note references; write a review plan and offline HTML")
    scan_parser.add_argument("epub", type=Path)
    scan_parser.add_argument("--out", type=Path, required=True, help="New review directory")
    scan_parser.add_argument("--all-links", action="store_true", help="Also review ordinary internal XHTML links")
    scan_parser.add_argument("--ref-xpath", action="append", default=[], help="Extra reference selector; namespaces: x, epub")
    scan_parser.add_argument("--note-xpath", action="append", default=[], help="Extra note-container selector; namespaces: x, epub")
    scan_parser.add_argument("--label-template", default=DEFAULT_LABEL_TEMPLATE, help="Return text with {number}, {chapter}, {label}; must include {number}")
    scan_parser.add_argument("--label-lang", help="Language of new link text, for example zh-Hans; otherwise inherit note language")
    scan_parser.set_defaults(handler=scan)
    review_parser = commands.add_parser("review", help="Confirm references one by one in a terminal")
    review_parser.add_argument("plan", type=Path)
    review_parser.add_argument("--out", type=Path, required=True, help="New decisions JSON file")
    review_parser.set_defaults(handler=review)
    candidate_parser = commands.add_parser("candidate", help="Build a separate EPUB with approved navigation insertions")
    candidate_parser.add_argument("epub", type=Path)
    candidate_parser.add_argument("decisions", type=Path)
    candidate_parser.add_argument("--out", type=Path, required=True, help="New candidate bundle directory")
    candidate_parser.add_argument("--epubcheck-jar", type=Path, default=os.environ.get("EPUBCHECK_JAR"))
    candidate_parser.add_argument("--skip-epubcheck", action="store_true")
    candidate_parser.add_argument("--check-timeout", type=int, choices=range(1, 61), default=45, metavar="1..60")
    candidate_parser.set_defaults(handler=candidate)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except (EpubError, OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(f"{PROG}: cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
