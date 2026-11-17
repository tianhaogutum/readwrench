"""Taguette intake, read-only export, and explicit human coding."""

from __future__ import annotations

from contextlib import contextmanager
import html
from itertools import islice
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from urllib.parse import quote
import uuid

from bs4 import BeautifulSoup, NavigableString

from .books import InputError, digest_text, read_book, stable_id
from .concepts import candidates, load_concepts
from .schema import validate_manifest
from .jsonio import read_json, read_text
from .positions import to_characters, to_utf8


SCHEMA_VERSION = 1
QUERY_PREFIX = "query/"
SENSE_PREFIX = "sense/"
CHAPTER_PREFIX = "chapter/"
REJECT_TAG = "status/rejected"
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


def document_marker(workspace_id: str, chapter_id: str) -> str:
    # Stored in Taguette document descriptions; existing workspaces depend on it.
    return f"concept-concordance:v1:{workspace_id}:{chapter_id}"


def plain_html(text: str) -> str:
    return f"<p>{html.escape(text)}</p>"


@contextmanager
def taguette_session(path: Path):
    # Lazy import: help and book validation do not load the QDA application.
    from taguette import database

    factory = database.connect("sqlite:///" + str(path.resolve()))
    try:
        with factory() as session:
            yield session
    finally:
        factory.kw["bind"].dispose()


def initialize(books: list[Path], concepts_path: Path, out: Path, *,
               name: str = "Concept comparison", max_hits: int = 10000,
               include_nonlinear: bool = False) -> dict:
    from taguette import database as d

    if not 1 <= len(books) <= 5:
        raise InputError("Provide 1–5 EPUBs (3–5 are recommended for comparison)")
    if not isinstance(max_hits, int) or not 1 <= max_hits <= 50000:
        raise InputError("max_hits must be between 1 and 50000")
    if not name.strip() or len(name) > 50:
        raise InputError("Project name must contain 1–50 characters")
    if out.exists():
        raise InputError(f"Output already exists; choose a new workspace: {out}")
    concepts = load_concepts(concepts_path)
    parsed = [read_book(path, include_nonlinear=include_nonlinear) for path in books]
    if len({book.sha256 for book in parsed}) != len(parsed):
        raise InputError("The same EPUB bytes were supplied more than once")
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    manifest = {"schema_version": SCHEMA_VERSION, "canonical_text_version": 1,
                "workspace_id": str(uuid.uuid4()), "name": name,
                "concepts": concepts, "books": [b.metadata() for b in parsed],
                "chapters": [], "candidate_count": 0,
                "include_nonlinear": include_nonlinear}
    try:
        (staging / "text").mkdir()
        with taguette_session(staging / "project.sqlite3") as db:
            user = d.User(login="admin")
            project = d.Project(name=name, description=(
                f'Concept Concordance workspace {manifest["workspace_id"]}. '
                "query/ tags are search candidates, not meanings. Add sense/ tags manually; "
                "use status/rejected for false positives. Keep document identity markers."
            ))
            db.add_all([user, project])
            db.flush()
            db.add(d.ProjectMember(project=project, user=user, privileges=d.Privileges.ADMIN))
            db.add(d.Command.project_meta(user.login, project.id, project.name, project.description))
            query_tags = {}
            for concept in concepts:
                tag = d.Tag(project=project, path=QUERY_PREFIX + concept["id"],
                            description=f'Candidate search for {concept["label"]}; not a semantic classification.')
                db.add(tag)
                db.flush()
                db.add(d.Command.tag_add(user.login, tag))
                query_tags[concept["id"]] = tag
            reject = d.Tag(project=project, path=REJECT_TAG,
                           description="Human decision: this search hit is not a relevant concept use")
            db.add(reject)
            db.flush()
            db.add(d.Command.tag_add(user.login, reject))
            for book in parsed:
                for chapter in book.chapters:
                    (staging / "text" / f"{chapter.id}.txt").write_text(chapter.text, encoding="utf-8")
                    metadata = chapter.metadata()
                    manifest["chapters"].append(metadata)
                    doc = d.Document(
                        project=project,
                        name=f"{book.title[:20]} · {chapter.spine_index}: {chapter.title}"[:50],
                        filename=f"{chapter.id}.html",
                        description=document_marker(manifest["workspace_id"], chapter.id),
                        contents=plain_html(chapter.text),
                        text_direction=d.TextDirection[chapter.direction],
                    )
                    db.add(doc)
                    db.flush()
                    db.add(d.Command.document_add(user.login, doc))
                    remaining = max_hits - manifest["candidate_count"]
                    matches = list(islice(candidates(chapter.text, concepts), remaining + 1))
                    if len(matches) > remaining:
                        raise InputError(f"More than {max_hits} candidates; narrow the query or raise --max-hits")
                    manifest["candidate_count"] += len(matches)
                    boundaries = to_utf8(chapter.text, (pos for _, start, end in matches for pos in (start, end)))
                    for concept_id, start, end in matches:
                        tag = query_tags[concept_id]
                        highlight = d.Highlight(
                            document=doc, start_offset=boundaries[start], end_offset=boundaries[end],
                            snippet=plain_html(chapter.text[start:end]), tags=[tag],
                        )
                        db.add(highlight)
                        db.flush()
                        db.add(d.Command.highlight_add(user.login, doc, highlight, [tag.id]))
                    db.flush()
            db.commit()
        validate_manifest(manifest)
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Never expose a partially imported workspace on conversion failure.
        if out.exists():
            raise InputError(f"Output appeared during import: {out}")
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def load_workspace(path: Path) -> tuple[dict, dict[str, str]]:
    try:
        manifest = read_json(path / "manifest.json", name="workspace manifest")
        validate_manifest(manifest)
        if manifest["schema_version"] != SCHEMA_VERSION or manifest["canonical_text_version"] != 1:
            raise InputError("Unsupported workspace/canonical text version")
        uuid.UUID(manifest["workspace_id"])
        if not isinstance(manifest["chapters"], list) or len(manifest["chapters"]) > 5000:
            raise InputError("Invalid chapter list")
        texts = {}
        for chapter in manifest["chapters"]:
            ident = chapter["id"]
            if not re.fullmatch(r"[0-9a-f]{24}", ident) or ident in texts:
                raise InputError("Invalid/duplicate chapter identity")
            expected = f"text/{ident}.txt"
            if chapter["text_file"] != expected:
                raise InputError("Unexpected snapshot path in manifest")
            snapshot = path / expected
            if snapshot.is_symlink() or not snapshot.resolve().is_relative_to(path.resolve()):
                raise InputError("Invalid canonical snapshot path")
            # A valid UTF-16 chapter may expand when stored as UTF-8. The
            # archive-member byte limit is not a canonical-snapshot byte limit.
            text = read_text(snapshot, maximum=MAX_SNAPSHOT_BYTES, name="canonical text snapshot")
            if digest_text(text) != chapter["text_sha256"] or len(text) != chapter["text_length"]:
                raise InputError(f"Canonical snapshot changed: {ident}")
            texts[ident] = text
        return manifest, texts
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(f"Invalid workspace: {exc}") from exc


@contextmanager
def readonly_database(path: Path):
    if not path.is_file():
        raise InputError(f"Taguette database does not exist: {path}")
    if path.stat().st_size > 512 * 1024 * 1024:
        raise InputError("Taguette database exceeds the 512 MiB size limit")
    connection = None
    try:
        connection = sqlite3.connect("file:" + quote(str(path.resolve()), safe="/") + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024 * 1024)
        deadline = time.monotonic() + 30
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA mmap_size=0")
        connection.execute("BEGIN")
        check = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
        if check != "ok":
            raise InputError(f"Invalid SQLite database: {check}")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if revision is None or revision[0] != "db5e31a0233d":
            raise InputError("Expected a Taguette 1.5.2 database schema; no migration was attempted")
        yield connection
    except sqlite3.Error as exc:
        raise InputError(f"Cannot read Taguette database: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def collect(path: Path, *, database: Path | None = None, project_id: int | None = None,
            context: int = 160, strict_decisions: bool = True) -> tuple[dict, dict[str, str], list[dict]]:
    if not 0 <= context <= 2000:
        raise InputError("Context must be between 0 and 2000 characters")
    manifest, texts = load_workspace(path)
    chapters = {c["id"]: c for c in manifest["chapters"]}
    books = {b["id"]: b for b in manifest["books"]}
    concepts = {c["id"]: c for c in manifest["concepts"]}
    markers = {document_marker(manifest["workspace_id"], c): c for c in chapters}
    occurrences = []
    manual_chapters = []
    with readonly_database(database or path / "project.sqlite3") as db:
        documents = []
        marker_values = list(markers)
        # Filter before fetching contents: an existing Taguette library may
        # contain unrelated books much larger than this comparison project.
        for beginning in range(0, len(marker_values), 400):
            values = marker_values[beginning:beginning + 400]
            placeholders = ",".join("?" for _ in values)
            documents.extend(dict(row) for row in db.execute(
                f"SELECT id, project_id, description FROM documents WHERE description IN ({placeholders})", values))
        projects = {doc["project_id"] for doc in documents}
        if project_id is None:
            if len(projects) != 1:
                raise InputError("Expected one matching Taguette project; use --project-id when multiple imported copies exist")
            project_id = projects.pop()
        documents = [doc for doc in documents if doc["project_id"] == project_id]
        mapped = [markers[doc["description"]] for doc in documents]
        if len(set(mapped)) != len(mapped) or set(mapped) != set(chapters):
            raise InputError("Missing/duplicate chapter markers in Taguette; retain document descriptions and all source chapters")
        codebook = [dict(row) for row in db.execute("SELECT id, path, description FROM tags WHERE project_id=? ORDER BY path", (project_id,))]
        if any(not isinstance(tag["path"], str) or not isinstance(tag["description"], str) for tag in codebook):
            raise InputError("Invalid Taguette codebook text")
        tags = {row["id"]: row["path"] for row in codebook}
        # View metadata only; the persisted edition manifest is never mutated.
        manifest = dict(manifest, codebook=codebook, taguette_project_id=project_id)
        highlight_tags: dict[int, list[str]] = {}
        for row in db.execute("SELECT ht.highlight_id, ht.tag_id FROM highlight_tags ht JOIN highlights h ON h.id=ht.highlight_id JOIN documents d ON d.id=h.document_id WHERE d.project_id=?", (project_id,)):
            if row["tag_id"] not in tags:
                raise InputError("Highlight references a tag outside its project")
            highlight_tags.setdefault(row["highlight_id"], []).append(tags[row["tag_id"]])
        for doc in documents:
            chapter_id = markers[doc["description"]]
            chapter = chapters[chapter_id]
            text = texts[chapter_id]
            contents = db.execute("SELECT contents FROM documents WHERE id=?", (doc["id"],)).fetchone()[0]
            if not isinstance(contents, str):
                raise InputError(f"Taguette document text changed: {chapter_id}; source positions cannot be trusted")
            # Taguette's find_pos counts text-node bytes without synthesizing
            # spaces for block tags. Render-equivalent HTML is not sufficient
            # to preserve that coordinate system (comments/head text count too).
            soup = BeautifulSoup(contents, "html5lib")
            flattened = "".join(str(node) for node in soup.descendants if isinstance(node, NavigableString))
            if flattened != text:
                raise InputError(f"Taguette document text changed: {chapter_id}; source positions cannot be trusted")
            highlights = list(db.execute("SELECT id, start_offset, end_offset FROM highlights WHERE document_id=? ORDER BY start_offset, end_offset, id", (doc["id"],)))
            boundaries = to_characters(text, (pos for highlight in highlights for pos in (highlight["start_offset"], highlight["end_offset"])))
            chapter_ranges = []
            for highlight in highlights:
                titles = [tag[len(CHAPTER_PREFIX):] for tag in highlight_tags.get(highlight["id"], []) if tag.startswith(CHAPTER_PREFIX)]
                if not titles:
                    continue
                if len(titles) != 1 or not titles[0].strip():
                    raise InputError("Each manual chapter range needs one non-empty chapter/ tag")
                start, end = boundaries[highlight["start_offset"]], boundaries[highlight["end_offset"]]
                if start >= end:
                    raise InputError("Manual chapter range must select non-empty source text")
                chapter_ranges.append({"resource_id": chapter_id, "book_id": chapter["book_id"],
                                       "title": titles[0], "start": start, "end": end,
                                       "exact": text[start:end], "taguette_highlight_id": highlight["id"]})
            chapter_ranges.sort(key=lambda item: (item["start"], item["end"]))
            if any(left["end"] > right["start"] for left, right in zip(chapter_ranges, chapter_ranges[1:])):
                raise InputError("Manual chapter ranges overlap; keep one chapter level per source resource")
            manual_chapters.extend(chapter_ranges)
            seen = set()
            for highlight in highlights:
                assigned = sorted(highlight_tags.get(highlight["id"], []))
                queries = [tag[len(QUERY_PREFIX):] for tag in assigned if tag.startswith(QUERY_PREFIX)]
                if not queries:
                    continue
                if any(q not in concepts for q in queries):
                    raise InputError("Unknown query/ tag; retain the original concept IDs")
                senses = [tag[len(SENSE_PREFIX):] for tag in assigned if tag.startswith(SENSE_PREFIX)]
                if strict_decisions and any(not sense.strip() for sense in senses):
                    raise InputError("Empty sense tag")
                rejected = REJECT_TAG in assigned
                conflicted = bool(rejected and senses) or any(not sense.strip() for sense in senses)
                if strict_decisions and rejected and senses:
                    raise InputError("A highlight has both sense/ and status/rejected tags; choose one decision")
                start = boundaries[highlight["start_offset"]]
                end = boundaries[highlight["end_offset"]]
                if start >= end:
                    raise InputError("Highlight must have a non-empty forward text range")
                heading = next((h for h in reversed(chapter["headings"]) if h["start"] <= start), None)
                manual = next((section for section in chapter_ranges if section["start"] <= start and end <= section["end"]), None)
                section = manual["title"] if manual else (heading["title"] if heading else chapter["title"])
                fragment = next((a["fragment"] for a in reversed(chapter["anchors"]) if a["start"] <= start), None)
                for query in queries:
                    key = (query, start, end)
                    if key in seen:
                        raise InputError("Duplicate query highlights at identical offsets; merge or remove one in Taguette")
                    seen.add(key)
                    ident = stable_id(chapter_id, query, str(start), str(end))
                    occurrences.append({
                        "id": ident, "concept_id": query, "concept": concepts[query]["label"],
                        "book_id": chapter["book_id"], "book": books[chapter["book_id"]]["title"],
                        "edition_sha256": books[chapter["book_id"]]["sha256"],
                        "chapter_id": chapter_id, "chapter": section, "href": chapter["href"],
                        "chapter_source": "manual_range" if manual else ("epub_heading" if heading else "resource"),
                        "archive_path": chapter["archive_path"], "fragment": fragment,
                        "start": start, "end": end, "exact": text[start:end],
                        "before": text[max(0, start - context):start], "after": text[end:end + context],
                        "status": "conflicted" if conflicted else ("rejected" if rejected else ("classified" if senses else "unreviewed")),
                        "senses": senses, "tags": assigned,
                        "taguette_highlight_id": highlight["id"], "taguette_document_id": doc["id"],
                    })
    occurrences.sort(key=lambda hit: (list(books).index(hit["book_id"]),
                                     chapters[hit["chapter_id"]]["spine_index"], hit["start"], hit["concept_id"]))
    manifest["manual_chapters"] = manual_chapters
    return manifest, texts, occurrences


def label(path: Path, occurrence_id: str, *, senses: list[str], reject: bool = False,
          clear: bool = False, project_id: int | None = None) -> dict:
    from taguette import database as d
    from taguette import validate

    if sum([bool(senses), reject, clear]) != 1:
        raise InputError("Choose --sense (repeatable), --reject, or --clear")
    for sense in senses:
        if not sense.strip() or sense != sense.strip() or "\x00" in sense:
            raise InputError("Sense labels must be non-empty, trimmed text without NUL")
        validate.tag_path(SENSE_PREFIX + sense)
    _, _, occurrences = collect(path, strict_decisions=False, project_id=project_id)
    matches = [hit for hit in occurrences if hit["id"] == occurrence_id]
    if len(matches) != 1:
        raise InputError("Occurrence ID was not found; use the full ID from list/export")
    hit = matches[0]
    with taguette_session(path / "project.sqlite3") as db:
        highlight = db.query(d.Highlight).get(hit["taguette_highlight_id"])
        doc = highlight.document
        retained = [tag for tag in highlight.tags if not tag.path.startswith(SENSE_PREFIX) and tag.path != REJECT_TAG]
        paths = [SENSE_PREFIX + sense for sense in dict.fromkeys(senses)] + ([REJECT_TAG] if reject else [])
        for tag_path in paths:
            tag = db.query(d.Tag).filter_by(project_id=doc.project_id, path=tag_path).one_or_none()
            if tag is None:
                tag = d.Tag(project_id=doc.project_id, path=tag_path, description="User-supplied concept classification")
                db.add(tag)
                db.flush()
                db.add(d.Command.tag_add("admin", tag))
            retained.append(tag)
        highlight.tags = retained
        db.add(d.Command.highlight_add("admin", doc, highlight, [tag.id for tag in retained]))
        db.commit()
    return {"id": occurrence_id, "status": "rejected" if reject else ("classified" if senses else "unreviewed"),
            "senses": list(dict.fromkeys(senses))}


def resolve(path: Path, occurrence_id: str, source: Path, *, database: Path | None = None,
            project_id: int | None = None) -> dict:
    from taguette import extract

    manifest, texts, occurrences = collect(path, database=database, project_id=project_id)
    hit = next((hit for hit in occurrences if hit["id"] == occurrence_id), None)
    if hit is None:
        raise InputError("Occurrence ID was not found")
    original = read_book(source, include_nonlinear=manifest["include_nonlinear"])
    if original.sha256 != hit["edition_sha256"]:
        raise InputError("Source edition SHA-256 mismatch; positions were not applied to a different edition")
    chapter = next((c for c in original.chapters if c.id == hit["chapter_id"]), None)
    if chapter is None or chapter.text != texts[hit["chapter_id"]]:
        raise InputError("Source chapter does not reproduce the preserved canonical text")
    start_byte = len(chapter.text[:hit["start"]].encode("utf-8"))
    end_byte = start_byte + len(hit["exact"].encode("utf-8"))
    snippet = extract.extract(plain_html(chapter.text), start_byte, end_byte)
    return dict(hit, verified_source=str(source.resolve()), taguette_verified_snippet=snippet)
