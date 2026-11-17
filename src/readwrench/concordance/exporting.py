"""A portable, static matrix around Taguette's human classifications."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import html
import json
from pathlib import Path
import shutil
import tempfile
import uuid
from urllib.parse import quote

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from .books import InputError
from .workspace import collect


def canonical_uri(chapter: dict, book: dict) -> str:
    # The "concept-concordance" name is part of the stable source URN; keep it.
    key = f'concept-concordance:canonical-text:v1:{book["sha256"]}:{chapter["href"]}'
    return "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def located_hit(hit: dict, chapters: dict, books: dict) -> dict:
    chapter = chapters[hit["chapter_id"]]
    locations = {"fragments": [quote(hit["fragment"], safe="")]} if hit["fragment"] else {}
    locator = {"href": hit["href"], "type": chapter["media_type"], "title": hit["chapter"],
               "text": {"before": hit["before"], "highlight": hit["exact"], "after": hit["after"]}}
    if locations:
        locator["locations"] = locations
    return dict(hit, source_snapshot=chapter["text_file"],
                canonical_source=canonical_uri(chapter, books[hit["book_id"]]),
                reader_link=f'readers/{hit["chapter_id"]}.html#hit-{hit["id"]}',
                readium_locator=locator)


def annotation(hit: dict, workspace_id: str) -> dict:
    bodies = [{"type": "TextualBody", "value": f'Query candidate: {hit["concept"]}', "purpose": "describing"}]
    bodies.extend({"type": "TextualBody", "value": sense, "purpose": "tagging"} for sense in hit["senses"])
    if hit["status"] == "rejected":
        bodies.append({"type": "TextualBody", "value": "Rejected by researcher", "purpose": "tagging"})
    return {
        # The textual occurrence is stable across projects; a researcher's
        # interpretation is a separate annotation in each workspace.
        "id": "urn:uuid:" + str(uuid.uuid5(uuid.UUID(workspace_id), hit["id"])),
        "type": "Annotation", "motivation": "tagging" if hit["senses"] or hit["status"] == "rejected" else "describing",
        "body": bodies,
        "target": {"type": "SpecificResource", "source": {"id": hit["canonical_source"], "format": "text/plain"},
                   "selector": [
                       {"type": "TextPositionSelector", "start": hit["start"], "end": hit["end"]},
                       {"type": "TextQuoteSelector", "exact": hit["exact"], "prefix": hit["before"], "suffix": hit["after"]},
                   ]},
    }


def matrix_rows(manifest: dict, hits: list[dict]) -> list[dict]:
    buckets = defaultdict(list)
    for hit in hits:
        categories = [("classified", sense) for sense in hit["senses"]] or [(hit["status"], "")]
        for status, sense in categories:
            buckets[(hit["concept_id"], status, sense, hit["book_id"])].append(hit)
    rows = []
    for concept in manifest["concepts"]:
        # Empty query rows make absence visible instead of silently dropping it.
        categories = {("unreviewed", ""), ("rejected", "")}
        categories.update((status, sense) for query, status, sense, _ in buckets if query == concept["id"])
        for status, sense in sorted(categories, key=lambda x: ({"classified": 0, "unreviewed": 1, "rejected": 2}[x[0]], x[1])):
            cells = []
            for book in manifest["books"]:
                matches = buckets[(concept["id"], status, sense, book["id"])]
                cells.append({"book_id": book["id"], "count": len(matches), "occurrences": matches})
            rows.append({"concept_id": concept["id"], "concept": concept["label"],
                         "status": status, "sense": sense, "cells": cells})
    return rows


def highlighted_text(text: str, hits: list[dict]) -> Markup:
    """Escape source prose, then add anchors; handle overlapping annotations."""
    starts = defaultdict(list)
    ends = Counter()
    for hit in hits:
        starts[hit["start"]].append(hit["id"])
        ends[hit["end"]] += 1
    positions = sorted({0, len(text), *starts, *ends})
    active = 0
    previous = 0
    result = []
    for position in positions:
        segment = html.escape(text[previous:position])
        result.append(f"<mark>{segment}</mark>" if active and segment else segment)
        active -= ends[position]
        for ident in starts[position]:
            result.append(f'<a id="hit-{ident}" class="hit-anchor"></a>')
            active += 1
        previous = position
    return Markup("".join(result))


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def csv_value(value):
    # CSV is the human spreadsheet view. Keep canonical values in JSON, and
    # guard formula-like cells with a tab inside a fully quoted CSV field.
    # See OWASP CSV Injection; spreadsheet applications differ on re-import.
    if isinstance(value, str) and value.lstrip("\ufeff \t\r\n").startswith(tuple("=+-@＝＋－＠")):
        return "\t" + value
    return value


def export_workspace(path: Path, out: Path, *, database: Path | None = None,
                     project_id: int | None = None, context: int = 160) -> dict:
    if out.exists():
        raise InputError(f"Export output already exists; choose a new directory: {out}")
    manifest, texts, raw_hits = collect(path, database=database, project_id=project_id, context=context)
    chapters = {chapter["id"]: chapter for chapter in manifest["chapters"]}
    books = {book["id"]: book for book in manifest["books"]}
    hits = [located_hit(hit, chapters, books) for hit in raw_hits]
    rows = matrix_rows(manifest, hits)
    counts = dict(Counter(hit["status"] for hit in hits))
    summary = {"occurrences": len(hits), "seeded_candidates": manifest["candidate_count"],
               "status_counts": {status: counts.get(status, 0) for status in ["classified", "unreviewed", "rejected"]},
               "books": len(books), "chapters": len(chapters), "concepts": len(manifest["concepts"]),
               "manual_chapter_ranges": len(manifest["manual_chapters"])}
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        (staging / "text").mkdir()
        (staging / "readers").mkdir()
        _write_json(staging / "occurrences.json", {"schema_version": 1, "summary": summary,
                    "books": manifest["books"], "concepts": manifest["concepts"], "codebook": manifest["codebook"], "occurrences": hits})
        _write_json(staging / "matrix.json", {"schema_version": 1, "summary": summary,
                                              "books": manifest["books"], "rows": rows})
        _write_json(staging / "annotations.jsonld", {"@context": "http://www.w3.org/ns/anno.jsonld",
                    "id": "urn:uuid:" + manifest["workspace_id"], "type": "AnnotationPage",
                    "items": [annotation(hit, manifest["workspace_id"]) for hit in hits]})
        _write_json(staging / "codebook.json", manifest["codebook"])
        _write_json(staging / "manual-chapters.json", manifest["manual_chapters"])
        sources = []
        for chapter in chapters.values():
            (staging / chapter["text_file"]).write_text(texts[chapter["id"]], encoding="utf-8")
            sources.append({"id": canonical_uri(chapter, books[chapter["book_id"]]),
                            "snapshot": chapter["text_file"], "text_sha256": chapter["text_sha256"],
                            "edition_sha256": books[chapter["book_id"]]["sha256"],
                            "epub_href": chapter["href"], "archive_path": chapter["archive_path"],
                            "normalization": "canonical-text-v1; Unicode code-point offsets"})
        _write_json(staging / "sources.json", sources)
        with (staging / "matrix.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
            writer.writerow([csv_value(value) for value in (["concept_id", "concept", "status", "sense"] + [f'{book["title"]} [{book["sha256"][:12]}]' for book in books.values()])])
            for row in rows:
                writer.writerow([csv_value(value) for value in ([row["concept_id"], row["concept"], row["status"], row["sense"]] +
                                ["; ".join(hit["id"] for hit in cell["occurrences"]) for cell in row["cells"]])])
        with (staging / "occurrences.csv").open("w", newline="", encoding="utf-8") as stream:
            fields = ["id", "concept_id", "concept", "status", "senses", "book", "edition_sha256", "chapter", "chapter_source", "href", "archive_path", "fragment", "start", "end", "before", "exact", "after", "reader_link"]
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", quoting=csv.QUOTE_ALL)
            writer.writeheader()
            for hit in hits:
                writer.writerow({key: csv_value(value) for key, value in dict(hit, senses=json.dumps(hit["senses"], ensure_ascii=False)).items()})
        environment = Environment(loader=PackageLoader(__package__, "templates"),
                                  autoescape=select_autoescape(["html"]))
        (staging / "index.html").write_text(environment.get_template("matrix.html").render(
            name=manifest["name"], books=list(books.values()), concepts=manifest["concepts"], rows=rows, summary=summary,
            codebook=manifest["codebook"]), encoding="utf-8")
        per_chapter = defaultdict(list)
        for hit in hits:
            per_chapter[hit["chapter_id"]].append(hit)
        reader_template = environment.get_template("reader.html")
        for chapter in chapters.values():
            (staging / "readers" / f'{chapter["id"]}.html').write_text(reader_template.render(
                book=books[chapter["book_id"]], chapter=chapter, hits=per_chapter[chapter["id"]],
                prose=highlighted_text(texts[chapter["id"]], per_chapter[chapter["id"]])), encoding="utf-8")
        if out.exists():
            raise InputError(f"Output appeared during export: {out}")
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return dict(summary, output=str(out))
