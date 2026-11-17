"""A disposable, atomically published SQLite FTS5 index."""

import os
import sqlite3
import stat
import tempfile
import unicodedata
from collections.abc import Callable, Iterable
from contextlib import closing
from pathlib import Path

from .model import CluejoinError, Fragment, line_break_ends, normalize

SCHEMA_VERSION = 3
APPLICATION_ID = 0x434C5545
NORMALIZER = "unicode-casefold-whitespace-v1"
SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE documents (
    document_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    axis TEXT NOT NULL DEFAULT 'page'
);
CREATE TABLE fragments (
    id INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    fragment_id TEXT NOT NULL,
    page INTEGER,
    paragraph INTEGER,
    line INTEGER,
    position INTEGER NOT NULL DEFAULT 0,
    text_offset INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    norm TEXT NOT NULL,
    origin TEXT NOT NULL,
    record INTEGER NOT NULL,
    record_end INTEGER NOT NULL,
    origin_kind TEXT NOT NULL,
    UNIQUE(document_id, fragment_id)
);
CREATE INDEX fragment_document ON fragments(document_id, page, paragraph, line, id);
CREATE VIRTUAL TABLE fragment_fts USING fts5(
    norm, content='fragments', content_rowid='id', tokenize='trigram case_sensitive 1'
);
CREATE TRIGGER fragment_insert AFTER INSERT ON fragments BEGIN
    INSERT INTO fragment_fts(rowid, norm) VALUES (new.id, new.norm);
END;
"""


def _configure(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA cache_size=-8192")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute("PRAGMA busy_timeout=3000")


def source_unchanged_check(paths: Iterable[Path]) -> Callable[[], None]:
    """Record file identity/size/timestamps and return a pre-publication check."""
    def state(path):
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise CluejoinError(f"input must be a regular file: {path}")
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    expected = {path: state(path) for path in paths}

    def check():
        for path, original in expected.items():
            try:
                same = state(path) == original
            except OSError as exc:
                raise CluejoinError(f"source changed during import/extraction: {path}") from exc
            if not same:
                raise CluejoinError(f"source changed during import/extraction: {path}; retry it")

    return check


def _finish_coordinates(connection: sqlite3.Connection) -> None:
    documents = connection.execute(
        "SELECT document_id, count(*), count(page), count(paragraph), count(line) "
        "FROM fragments GROUP BY document_id"
    )
    for document_id, count, pages, paragraphs, lines in documents:
        if pages == count:
            axis = "page"
        elif paragraphs == count:
            axis = "paragraph"
        elif lines == count:
            axis = "line"
        else:
            raise CluejoinError(
                f"document {document_id!r}: all fragments must share a complete coordinate "
                "(page, global paragraph, or original line)"
            )
        connection.execute("UPDATE documents SET axis=? WHERE document_id=?", (axis, document_id))
        order = {
            "page": "page, paragraph, line, id", "paragraph": "paragraph, line, id",
            "line": "line, id",
        }[axis]
        offset = 0
        for position, (row_id, length) in enumerate(connection.execute(
            "SELECT id, length(text) FROM fragments WHERE document_id=? ORDER BY " + order,
            (document_id,),
        )):
            connection.execute(
                "UPDATE fragments SET position=?, text_offset=? WHERE id=?",
                (position, offset, row_id),
            )
            offset += length + 2
        if axis == "line":
            previous_end = 0
            for line, text, kind in connection.execute(
                "SELECT line, text, origin_kind FROM fragments "
                "WHERE document_id=? ORDER BY position",
                (document_id,),
            ):
                if line <= previous_end:
                    raise CluejoinError(
                        f"document {document_id!r}: overlapping original line ranges; "
                        "deduplicate overlapping rga captures before importing"
                    )
                breaks = line_break_ends(text, kind)
                previous_end = line + len(breaks) - bool(breaks and breaks[-1] == len(text))
    # Known coordinates cannot invert each other, even when other records omit
    # one of the coordinates. Missing annotations must not bypass validation.
    for first, second in [("page", "paragraph"), ("page", "line"), ("paragraph", "line")]:
        previous_doc = None
        previous_max = None
        rows = connection.execute(
            f"SELECT document_id, min({second}), max({second}) FROM fragments "
            f"WHERE {first} IS NOT NULL AND {second} IS NOT NULL "
            f"GROUP BY document_id, {first} ORDER BY document_id, {first}"
        )
        for document_id, minimum, maximum in rows:
            if document_id == previous_doc and minimum < previous_max:
                raise CluejoinError(
                    f"document {document_id!r}: {first} and global {second} coordinates "
                    "contradict each other; omit page-local paragraph/line numbers"
                )
            previous_doc, previous_max = document_id, maximum


def build_index(
    path: Path, fragments: Iterable[Fragment], *, replace: bool = False,
    before_publish: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Build beside the destination and publish only after every input validates."""
    if path.exists() and not replace:
        raise CluejoinError(f"index already exists: {path}; use --replace to rebuild it")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise CluejoinError(f"index destination must be a regular file: {path}")
    # Do not create directories implicitly: a typo must not create an unexpected tree.
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            _configure(connection)
            try:
                connection.executescript(SCHEMA)
            except sqlite3.OperationalError as exc:
                if "no such module: fts5" in str(exc) or "no such tokenizer: trigram" in str(exc):
                    raise CluejoinError(
                        "SQLite with FTS5 and its trigram tokenizer is required (SQLite >=3.34)"
                    ) from exc
                raise
            with connection:
                total = 0
                for fragment in fragments:
                    norm = normalize(fragment.text)
                    if not norm:
                        continue
                    connection.execute(
                        "INSERT OR IGNORE INTO documents(document_id, source) VALUES (?,?)",
                        (fragment.document_id, fragment.source),
                    )
                    (source,) = connection.execute(
                        "SELECT source FROM documents WHERE document_id=?", (fragment.document_id,)
                    ).fetchone()
                    if source != fragment.source:
                        raise CluejoinError(
                            f"document {fragment.document_id!r}: conflicting source paths "
                            f"{source!r} and {fragment.source!r}"
                        )
                    try:
                        connection.execute(
                            "INSERT INTO fragments(document_id,fragment_id,page,paragraph,line,"
                            "text,norm,origin,record,record_end,origin_kind) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (fragment.document_id, fragment.fragment_id, fragment.page,
                             fragment.paragraph, fragment.line, fragment.text, norm,
                             fragment.origin, fragment.record,
                             fragment.record_end if fragment.record_end is not None
                             else fragment.record, fragment.origin_kind),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise CluejoinError(
                            f"duplicate fragment_id {fragment.fragment_id!r} in "
                            f"document {fragment.document_id!r}"
                        ) from exc
                    total += 1
                if not total:
                    raise CluejoinError("no nonempty extracted text fragments were found")
                _finish_coordinates(connection)
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.executemany("INSERT INTO metadata VALUES (?,?)", [
                    ("normalizer", NORMALIZER), ("unicode_version", unicodedata.unidata_version),
                ])
                connection.execute("INSERT INTO fragment_fts(fragment_fts) VALUES ('optimize')")
                connection.execute("ANALYZE")
                documents = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
            checked = connection.execute("PRAGMA quick_check").fetchone()[0]
            if checked != "ok":
                raise CluejoinError(f"SQLite integrity check failed: {checked}")
        if before_publish is not None:
            before_publish()
        if replace:
            os.replace(temporary, path)
        else:
            # Unlike replace(), link() cannot overwrite a concurrently created destination.
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise CluejoinError(f"index was created concurrently: {path}") from exc
        return {"documents": documents, "fragments": total, "bytes": path.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)
        Path(f"{temporary}-journal").unlink(missing_ok=True)


def open_index(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise CluejoinError(f"index must exist and be a regular file: {path}")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        _configure(connection)
        app = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if app != APPLICATION_ID or version != SCHEMA_VERSION:
            raise CluejoinError(
                f"{path}: not a supported readwrench clues index; rebuild it with "
                "'readwrench clues build INDEX INPUT... --replace'"
            )
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        if metadata.get("normalizer") != NORMALIZER:
            raise CluejoinError(f"{path}: unsupported text normalization; rebuild the index")
        if metadata.get("unicode_version") != unicodedata.unidata_version:
            raise CluejoinError(
                f"{path}: index uses Unicode {metadata.get('unicode_version')}, runtime uses "
                f"{unicodedata.unidata_version}; rebuild to avoid missing changed casefold matches"
            )
        connection.row_factory = sqlite3.Row
        return connection
    except BaseException:
        connection.close()
        raise
