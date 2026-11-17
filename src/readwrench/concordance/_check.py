"""``readwrench check`` adapter for ``readwrench concordance``.

Two read-only analyses that need no decision from the reader:

* ``concordance-epub``: runs the concordance EPUB intake (archive limits, unsafe paths,
  encrypted prose, EbookLib parsing, chapter decoding and text snapshots) on every EPUB
  and reports the ones ``readwrench concordance init`` would refuse.
* ``concordance-workspace``: re-verifies a saved concordance workspace (recognised by
  its ``manifest.json``): manifest and chapter snapshot hashes, the source EPUB editions
  it records, the Taguette project it reads, and decisions that would block an export.

Both only read files; the Taguette database is opened read-only through SQLite and
Taguette itself is never imported.
"""
from __future__ import annotations

from pathlib import Path

from readwrench.check.model import (ERROR, FIND, INFO, WARNING, Check, Finding, need, plural,
                                    short)

TOOL = "concordance"
EXTRA = "concordance"
EPUB_CHECK = "concordance-epub"
WORKSPACE_CHECK = "concordance-workspace"
WORKSPACE_KIND = "concordance-workspace"

EPUB_REQUIRES = ("ebooklib", "bs4", "html5lib", "lxml")
WORKSPACE_REQUIRES = (*EPUB_REQUIRES, "jsonschema")

# The workspace manifest written by ``readwrench concordance init`` (schema_version 1).
_MANIFEST_MARKERS = (b'"canonical_text_version"', b'"workspace_id"', b'"chapters"')

# Same limit as the intake: a larger file cannot be the recorded edition.
_MAX_EPUB_BYTES = 32 * 1024 * 1024


def classify(path: Path, head: bytes):
    """Recognise a concordance workspace by its ``manifest.json``."""
    try:
        if path.name != "manifest.json" or not head.lstrip().startswith(b"{"):
            return None
        if all(marker in head for marker in _MANIFEST_MARKERS):
            return WORKSPACE_KIND, "readwrench concordance workspace"
    except Exception:  # classify must never raise
        return None
    return None


def _epub_rule(message: str) -> str:
    text = message.lower()
    if "encrypt" in text or "drm" in text:
        return "encrypted-text"
    if "size limit" in text or "more than 5000" in text:
        return "over-limit"
    if "no searchable prose" in text:
        return "no-searchable-prose"
    if "encoding" in text:
        return "undecodable-chapter"
    return "unreadable"


def check_epub(source):
    need(*EPUB_REQUIRES)
    from .books import read_book
    from .errors import InputError

    hint = f"readwrench concordance init {source.display} --concepts CONCEPTS.json --out NEW_WORKSPACE"
    if source.path.suffix.lower() != ".epub":
        yield Finding(EPUB_CHECK, "not-epub-name", INFO,
                      "readwrench concordance only accepts EPUB files whose name ends in .epub",
                      file=source.display, hint="copy or rename the file to *.epub first")
        return
    try:
        read_book(source.path)
    except InputError as exc:
        yield Finding(EPUB_CHECK, _epub_rule(str(exc)), WARNING,
                      f"readwrench concordance cannot compare this EPUB: {short(exc)}",
                      file=source.display, hint=hint)


def _workspace_display(source) -> str:
    return str(Path(source.display).parent)


def _source_findings(manifest: dict, source, workspace: str):
    from .books import digest_file

    for index, book in enumerate(manifest["books"]):
        recorded = Path(book["path"])
        pointer = f"/books/{index}/path"
        hint = (f"readwrench concordance resolve {workspace} OCCURRENCE_ID "
                f"--source PATH/TO/{recorded.name}")
        try:
            present = recorded.is_file()
            size = recorded.stat().st_size if present else 0
        except OSError:
            present, size = False, 0
        if not present:
            yield Finding(WORKSPACE_CHECK, "source-missing", WARNING,
                          f"the source EPUB of {short(book['title'], 80)!r} is no longer at its recorded "
                          "path; citations can only be verified against that exact edition",
                          file=source.display, pointer=pointer, subject=book["title"], hint=hint)
            continue
        try:
            changed = size > _MAX_EPUB_BYTES or digest_file(recorded) != book["sha256"]
        except OSError as exc:
            yield Finding(WORKSPACE_CHECK, "source-unreadable", WARNING,
                          f"cannot read the recorded source EPUB: {short(exc)}",
                          file=source.display, pointer=pointer, subject=book["title"], hint=hint)
            continue
        if changed:
            yield Finding(WORKSPACE_CHECK, "source-changed", WARNING,
                          f"the file at the recorded path of {short(book['title'], 80)!r} is a different "
                          "edition (SHA-256 differs); resolve will refuse it",
                          file=source.display, pointer=pointer, subject=book["title"], hint=hint)


def check_workspace(source):
    need(*WORKSPACE_REQUIRES)
    from .errors import InputError
    from .workspace import collect, load_workspace

    root = source.path.parent
    workspace = _workspace_display(source)
    try:
        manifest, _ = load_workspace(root)
    except InputError as exc:
        yield Finding(WORKSPACE_CHECK, "invalid-workspace", ERROR,
                      f"workspace cannot be loaded: {short(exc)}", file=source.display,
                      hint=f"readwrench concordance check {workspace}")
        return
    yield from _source_findings(manifest, source, workspace)

    database = root / "project.sqlite3"
    if not database.is_file():
        yield Finding(WORKSPACE_CHECK, "database-missing", ERROR,
                      "the workspace's Taguette database project.sqlite3 is missing; "
                      "list, label and export need it", file=source.display,
                      hint=f"readwrench concordance export {workspace} --database REVIEWED.sqlite3 --out NEW_EXPORT")
        return
    try:
        _, _, hits = collect(root, strict_decisions=False)
    except InputError as exc:
        yield Finding(WORKSPACE_CHECK, "taguette-mismatch", ERROR,
                      f"the Taguette project no longer matches the workspace: {short(exc)}",
                      file=source.display, hint=f"readwrench concordance check {workspace}")
        return
    conflicted = sum(hit["status"] == "conflicted" for hit in hits)
    if conflicted:
        yield Finding(WORKSPACE_CHECK, "conflicting-decisions", WARNING,
                      f"{plural(conflicted, 'occurrence')} carry both sense/ and status/rejected tags "
                      "(or an empty sense/ tag); export refuses until each is resolved",
                      file=source.display,
                      hint=f"readwrench concordance list {workspace} --status conflicted")
    unreviewed = sum(hit["status"] == "unreviewed" for hit in hits)
    if unreviewed:
        yield Finding(WORKSPACE_CHECK, "unreviewed", INFO,
                      f"{unreviewed} of {plural(len(hits), 'candidate occurrence')} are still unreviewed",
                      file=source.display,
                      hint=f"readwrench concordance list {workspace} --status unreviewed")


CHECKS = (
    Check(
        name=EPUB_CHECK, area=FIND,
        summary="EPUBs that readwrench concordance cannot ingest (encrypted prose, size limits, "
                "malformed archives, undecodable chapters, no searchable prose)",
        kinds=frozenset({"epub"}), run=check_epub,
        rules=("encrypted-text", "over-limit", "no-searchable-prose", "undecodable-chapter",
               "unreadable", "not-epub-name"),
        requires=EPUB_REQUIRES, extra=EXTRA, tool=TOOL,
        note="Parses every spine chapter with html5lib, so large libraries take a while. "
             "EPUBs that pass are not listed.",
    ),
    Check(
        name=WORKSPACE_CHECK, area=FIND,
        summary="Saved concordance workspaces: snapshot hashes, recorded source editions, "
                "Taguette project consistency and decisions that block an export",
        kinds=frozenset({WORKSPACE_KIND}), run=check_workspace,
        rules=("invalid-workspace", "source-missing", "source-unreadable", "source-changed",
               "database-missing", "taguette-mismatch", "conflicting-decisions", "unreviewed"),
        requires=WORKSPACE_REQUIRES, extra=EXTRA, tool=TOOL,
        note="Reads the workspace's own project.sqlite3 read-only; a separate reviewed "
             "Taguette export is not looked for.",
    ),
)
