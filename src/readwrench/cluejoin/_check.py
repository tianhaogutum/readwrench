"""``readwrench check`` adapter for ``readwrench clues``: re-verify saved clue indexes.

A clue index is a disposable snapshot of extracted text. This check opens each index
read-only, confirms it is intact and usable by this runtime, and re-reads the extracted
text, JSONL and rga JSON inputs it was built from (their absolute paths are stored in
the index) to report sources that are missing or have changed since the build. It
never rebuilds, writes or runs an extractor.
"""
from __future__ import annotations

from contextlib import closing
import shlex
import sqlite3
import unicodedata

from readwrench.check.model import ERROR, FIND, WARNING, Check, Finding, Source, plural, short

NAME = "clues"
KIND = "clues-index"
SQLITE_MAGIC = b"SQLite format 3\x00"
# Same value as ``readwrench.cluejoin.store.APPLICATION_ID`` ("CLUE"); repeated here so
# discovery stays a pure header test. The SQLite header stores it big-endian at offset 68.
APPLICATION_ID = 0x434C5545

RULES = ("corrupt-index", "unsupported-index", "unicode-mismatch", "source-missing",
         "stale-index")

# origin_kind stored per fragment -> the ``build --format`` that produced it.
FORMATS = {"extracted-text-line": "text", "jsonl-record": "jsonl", "rga-json-record": "rga-json"}
CAPTURED = "rga-extracted-line"
# Spell out the rebuild command only while it stays readable.
MAX_HINT_INPUTS = 4


def classify(path, head: bytes):
    if len(head) < 100 or not head.startswith(SQLITE_MAGIC):
        return None
    if int.from_bytes(head[68:72], "big") != APPLICATION_ID:
        return None
    return KIND, "readwrench clues index"


def _rebuild_hint(display: str, origins: list[tuple[str, str]]) -> str:
    kinds = {kind for _, kind in origins}
    paths = [origin for origin, _ in origins]
    inputs = (" ".join(shlex.quote(path) for path in paths)
              if paths and len(paths) <= MAX_HINT_INPUTS else "INPUT...")
    index = shlex.quote(display)
    if kinds == {CAPTURED}:
        return f"readwrench clues capture {index} {inputs} --replace"
    if len(kinds) == 1 and next(iter(kinds)) in FORMATS:
        fmt = FORMATS[next(iter(kinds))]
        option = "" if fmt == "text" else f" --format {fmt}"
        return f"readwrench clues build {index} {inputs}{option} --replace"
    return f"readwrench clues build {index} INPUT... --replace"


def _regenerated(origin: str, kind: str):
    """Fragments the importer yields today for one stored input, as build would keep them."""
    from pathlib import Path

    from .importers import from_jsonl, from_rga, from_text
    from .model import normalize

    reader = {"text": from_text, "jsonl": from_jsonl, "rga-json": from_rga}[FORMATS[kind]]
    for fragment in reader(Path(origin)):
        if normalize(fragment.text):
            yield (fragment.document_id, fragment.fragment_id, fragment.text,
                   fragment.page, fragment.paragraph, fragment.line)


def _first_difference(connection: sqlite3.Connection, origin: str, kind: str):
    """``None`` if the source still yields exactly the stored fragments, else a short reason."""
    from .model import CluejoinError

    stored = connection.execute(
        "SELECT document_id, fragment_id, text, page, paragraph, line FROM fragments "
        "WHERE origin=? ORDER BY id", (origin,),
    )
    fresh = _regenerated(origin, kind)
    count = 0
    try:
        while True:
            old = stored.fetchone()
            new = next(fresh, None)
            if old is None and new is None:
                return None
            count += 1
            if old is None:
                return (f"it now yields more than the {plural(count - 1, 'indexed fragment')}")
            if new is None:
                return (f"it now yields {plural(count - 1, 'fragment')}; the index holds more")
            if tuple(old) != new:
                return f"fragment {short(old[1], 80)!r} (number {count}) differs"
    except (CluejoinError, UnicodeDecodeError, OSError, ValueError) as exc:
        return f"it no longer imports cleanly: {short(exc, 160)}"


def check_index(source: Source):
    from pathlib import Path

    from .store import NORMALIZER, SCHEMA_VERSION

    file = source.display
    generic_hint = f"readwrench clues build {shlex.quote(file)} INPUT... --replace"

    def finding(rule, severity, message, *, subject=None, hint=generic_hint):
        return Finding(NAME, rule, severity, message, file=file, subject=subject, hint=hint)

    try:
        connection = sqlite3.connect(source.path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        yield finding("corrupt-index", ERROR, f"cannot open the index: {short(exc, 160)}")
        return
    with closing(connection):
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                yield finding("unsupported-index", WARNING,
                              f"index schema version {version} is not supported by this "
                              f"readwrench (expects {SCHEMA_VERSION}); search refuses it until "
                              "it is rebuilt")
                return
            checked = connection.execute("PRAGMA quick_check").fetchone()[0]
            if checked != "ok":
                yield finding("corrupt-index", ERROR,
                              f"SQLite integrity check failed: {short(checked, 160)}")
                return
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            origins = [tuple(row) for row in connection.execute(
                "SELECT DISTINCT origin, origin_kind FROM fragments ORDER BY origin")]
        except sqlite3.DatabaseError as exc:
            yield finding("corrupt-index", ERROR, f"index cannot be read: {short(exc, 160)}")
            return

        hint = _rebuild_hint(file, origins)
        if metadata.get("normalizer") != NORMALIZER:
            yield finding("unsupported-index", WARNING,
                          "index uses an unsupported text normalization; search refuses it "
                          "until it is rebuilt", hint=hint)
            return
        built = metadata.get("unicode_version")
        if built != unicodedata.unidata_version:
            yield finding("unicode-mismatch", WARNING,
                          f"index was built with Unicode {built}, this Python uses "
                          f"{unicodedata.unidata_version}; search refuses it until it is "
                          "rebuilt (casefold matches may differ)", hint=hint)

        for origin, kind in origins:
            path = Path(origin)
            if not path.is_file():
                yield finding("source-missing", WARNING,
                              "an input the index was built from is no longer a file at "
                              f"{origin}; its evidence cannot be re-checked",
                              subject=origin, hint=hint)
                continue
            if kind not in FORMATS:
                continue  # captured with rga: presence only, not re-extracted
            try:
                difference = _first_difference(connection, origin, kind)
            except sqlite3.DatabaseError as exc:
                yield finding("corrupt-index", ERROR,
                              f"index cannot be read: {short(exc, 160)}")
                return
            if difference is not None:
                yield finding("stale-index", WARNING,
                              f"input {origin} changed since the index was built: {difference}; "
                              "search results may not match the current text",
                              subject=origin, hint=hint)


CHECKS = (
    Check(
        name=NAME,
        area=FIND,
        summary="Saved clue indexes: intact, usable by this Python, and still matching "
                "the inputs they were built from",
        kinds=frozenset({KIND}),
        run=check_index,
        rules=RULES,
        tool="clues",
        note="Inputs captured with rga ('readwrench clues capture') are checked for presence "
             "only; they are not re-extracted.",
    ),
)
