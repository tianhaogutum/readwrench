from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from urllib.parse import unquote, urlsplit

from .helpers import EXAMPLES, require

require()

from bs4 import BeautifulSoup
import pytest

from readwrench.concordance.books import InputError
from readwrench.concordance.cli import main
from readwrench.concordance.exporting import export_workspace
from readwrench.concordance.workspace import collect, initialize, label, resolve, taguette_session


def test_manual_decisions_preserve_query_and_have_audit_events(simple_workspace):
    path, _ = simple_workspace
    _, _, before = collect(path)
    hit = before[0]
    label(path, hit["id"], senses=["institutional", "coercive", "institutional"])
    _, _, after = collect(path)
    changed = next(h for h in after if h["id"] == hit["id"])
    assert changed["senses"] == ["coercive", "institutional"]
    assert changed["status"] == "classified"
    assert "query/power" in changed["tags"]
    label(path, hit["id"], senses=[], reject=True)
    assert collect(path)[2][0]["status"] == "rejected"
    label(path, hit["id"], senses=[], clear=True)
    assert collect(path)[2][0]["status"] == "unreviewed"
    with sqlite3.connect(path / "project.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM commands WHERE json_extract(payload,'$.type')='highlight_add'").fetchone()[0] == len(before) + 3


def test_unicode_offsets_repeat_quotes_and_resolve(tmp_path, make_book, concepts_file):
    source = make_book(content='<h1 id="heading">倫理😀</h1><p>自由 &amp; power. 自由 power.</p>')
    path = tmp_path / "unicode"
    initialize([source], concepts_file([{"id": "a", "aliases": ["自由", "power"], "boundary": "substring"}]), path)
    manifest, texts, hits = collect(path)
    assert [hit["exact"] for hit in hits] == ["自由", "power", "自由", "power"]
    assert len({hit["id"] for hit in hits}) == 4
    assert all(texts[hit["chapter_id"]][hit["start"]:hit["end"]] == hit["exact"] for hit in hits)
    for hit in hits:
        result = resolve(path, hit["id"], source)
        assert BeautifulSoup(result["taguette_verified_snippet"], "html5lib").get_text() == hit["exact"]
    assert hits[0]["fragment"] == "heading"


def test_export_standard_selectors_links_and_manual_matrix(simple_workspace, tmp_path):
    path, _ = simple_workspace
    _, _, hits = collect(path)
    label(path, hits[0]["id"], senses=["command", "capacity"])
    label(path, hits[1]["id"], senses=[], reject=True)
    out = tmp_path / "export"
    summary = export_workspace(path, out)
    assert summary["status_counts"] == {"classified": 1, "unreviewed": 2, "rejected": 1}
    exported = json.loads((out / "occurrences.json").read_text())
    page = json.loads((out / "annotations.jsonld").read_text())
    sources = {s["id"]: s for s in json.loads((out / "sources.json").read_text())}
    assert len(page["items"]) == len(hits)
    for annotation in page["items"]:
        target = annotation["target"]
        text = (out / sources[target["source"]["id"]]["snapshot"]).read_text()
        position, quote = target["selector"]
        assert text[position["start"]:position["end"]] == quote["exact"]
        assert text[:position["start"]].endswith(quote["prefix"])
        assert text[position["end"]:].startswith(quote["suffix"])
    for hit in exported["occurrences"]:
        parsed = urlsplit(hit["reader_link"])
        reader = out / unquote(parsed.path)
        soup = BeautifulSoup(reader.read_text(), "html5lib")
        assert soup.find(id=parsed.fragment) is not None
        assert not soup.find("script")
        assert "#" not in hit["readium_locator"]["href"]
    rows = json.loads((out / "matrix.json").read_text())["rows"]
    assert {row["sense"] for row in rows if row["status"] == "classified"} == {"command", "capacity"}


def test_tagging_import_roundtrip_survives_id_remapping(simple_workspace, tmp_path):
    from taguette import database as d
    path, _ = simple_workspace
    hits = collect(path)[2]
    label(path, hits[0]["id"], senses=["political"])
    external = tmp_path / "taguette-export.sqlite3"
    with taguette_session(external) as dest:
        dest.add(d.User(login="admin"))
        dummy = d.Project(name="Existing project", description="Force numeric ID remapping")
        dest.add(dummy)
        dest.flush()
        dest.add(d.Document(project=dummy, name="Existing", description="", filename="old.html", contents="<p>old</p>", text_direction=d.TextDirection.LEFT_TO_RIGHT))
        dest.commit()
        with taguette_session(path / "project.sqlite3") as src:
            imported = d.copy_project(src, dest, 1, "admin")
    before = sha256(external.read_bytes()).hexdigest()
    imported_hits = collect(path, database=external)[2]
    assert imported == 2
    assert imported_hits[0]["taguette_document_id"] != hits[0]["taguette_document_id"]
    assert [h["id"] for h in imported_hits] == [h["id"] for h in hits]
    assert imported_hits[0]["senses"] == ["political"]
    export_workspace(path, tmp_path / "from-taguette", database=external)
    assert sha256(external.read_bytes()).hexdigest() == before


def test_different_edition_rejected_even_same_identifier_quote(simple_workspace, make_book):
    path, source = simple_workspace
    changed = make_book(name="different-edition", title="test")
    # A ZIP comment changes edition bytes while preserving all document text,
    # identifiers and metadata, without depending on wall-clock ZIP timestamps.
    from zipfile import ZipFile
    with ZipFile(changed, "a") as archive:
        archive.comment = b"different-edition-bytes"
    assert source.read_bytes() != changed.read_bytes()
    with pytest.raises(InputError, match="SHA-256 mismatch"):
        resolve(path, collect(path)[2][0]["id"], changed)


def test_modified_document_cannot_silently_reanchor(simple_workspace):
    path, _ = simple_workspace
    with sqlite3.connect(path / "project.sqlite3") as db:
        db.execute("UPDATE documents SET contents='<p>Different power</p>'")
    with pytest.raises(InputError, match="document text changed"):
        collect(path)


def test_inside_utf8_offset_rejected(tmp_path, make_book, concepts_file):
    path = tmp_path / "unicode"
    initialize([make_book(content="<p>自由 power</p>")], concepts_file(), path)
    with sqlite3.connect(path / "project.sqlite3") as db:
        db.execute("UPDATE highlights SET start_offset=1")
    with pytest.raises(InputError, match="UTF-8"):
        collect(path)


def test_failed_import_and_export_do_not_leave_partial_output(tmp_path, make_book, concepts_file, simple_workspace):
    out = tmp_path / "too-many"
    with pytest.raises(InputError, match="More than 1 candidates"):
        initialize([make_book(name="too-many")], concepts_file(), out, max_hits=1)
    assert not out.exists()
    assert not list(tmp_path.glob(".too-many-*"))
    path, _ = simple_workspace
    target = tmp_path / "existing"
    target.mkdir()
    (target / "retain").write_text("retained")
    with pytest.raises(InputError, match="already exists"):
        export_workspace(path, target)
    assert (target / "retain").read_text() == "retained"


def test_duplicate_bytes_not_deduplicated_silently(tmp_path, make_book, concepts_file):
    source = make_book()
    with pytest.raises(InputError, match="same EPUB bytes"):
        initialize([source, source], concepts_file(), tmp_path / "duplicate")


def test_snapshot_tampering_detected(simple_workspace):
    path, _ = simple_workspace
    next((path / "text").iterdir()).write_text("tampered")
    with pytest.raises(InputError, match="snapshot changed"):
        collect(path)


def test_empty_match_matrix_keeps_zero_rows(tmp_path, make_book, concepts_file):
    path = tmp_path / "no-hits"
    initialize([make_book(content="<p>Other prose.</p>")], concepts_file(), path)
    out = tmp_path / "no-hits-export"
    assert export_workspace(path, out)["occurrences"] == 0
    rows = json.loads((out / "matrix.json").read_text())["rows"]
    assert len(rows) == 4
    assert all(cell["count"] == 0 for row in rows for cell in row["cells"])


def test_cli_friendly_invalid_input(tmp_path, capsys):
    assert main(["list", str(tmp_path / "missing")]) == 2
    assert "workspace" in capsys.readouterr().err


def test_real_three_book_end_to_end(tmp_path, real_books):
    path = tmp_path / "real-workspace"
    manifest = initialize(real_books, EXAMPLES / "concepts.json", path)
    assert manifest["candidate_count"] == 1349
    _, _, hits = collect(path)
    # The first liberty hit is a proper-name institution, illustrating why
    # automatic lexical matching cannot replace human concept classification.
    proper_name = next(h for h in hits if h["concept_id"] == "liberty")
    assert "Ten of " in proper_name["before"] and proper_name["after"].startswith(" and Peace")
    label(path, proper_name["id"], senses=[], reject=True)
    source_book = next(book for book in manifest["books"] if book["id"] == proper_name["book_id"])
    assert resolve(path, proper_name["id"], Path(source_book["path"]))["status"] == "rejected"
    out = tmp_path / "real-export"
    assert export_workspace(path, out)["status_counts"]["rejected"] == 1
