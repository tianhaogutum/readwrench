import json
import sqlite3
from urllib.parse import quote
from zipfile import ZipFile

from .helpers import require

require()

from lxml import etree
import pytest

from readwrench.concordance.books import InputError, canonical_html, read_book
from readwrench.concordance.exporting import export_workspace
from readwrench.concordance.workspace import collect, initialize, label


def test_conflicting_decision_can_be_cleared(simple_workspace):
    path, _ = simple_workspace
    hit = collect(path)[2][0]
    label(path, hit["id"], senses=["capacity"])
    with sqlite3.connect(path / "project.sqlite3") as db:
        tag_id = db.execute("SELECT id FROM tags WHERE path='status/rejected'").fetchone()[0]
        db.execute("INSERT INTO highlight_tags VALUES (?, ?)", (hit["taguette_highlight_id"], tag_id))
    with pytest.raises(InputError, match="both sense/"):
        collect(path)
    # Recovery must remain available when export is intentionally blocked.
    label(path, hit["id"], senses=[], clear=True)
    assert collect(path)[2][0]["status"] == "unreviewed"


@pytest.mark.parametrize("change", ["unknown_book", "bad_headings", "duplicate_concept", "bad_path", "missing_id"])
def test_corrupt_manifest_is_a_friendly_input_error(simple_workspace, change):
    path, _ = simple_workspace
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if change == "unknown_book":
        manifest["chapters"][0]["book_id"] = "f" * 24
    elif change == "bad_headings":
        manifest["chapters"][0]["headings"] = [{"start": "oops", "title": []}]
    elif change == "duplicate_concept":
        manifest["concepts"].append(manifest["concepts"][0])
    elif change == "bad_path":
        manifest["chapters"][0]["href"] = "../outside"
    else:
        del manifest["books"][0]["id"]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(InputError):
        collect(path)


def test_invalid_declared_utf8_is_not_guessed_as_windows1252():
    with pytest.raises(InputError, match="encoding"):
        canonical_html(b'<?xml version="1.0" encoding="utf-8"?><html><body><p>libert\xe9</p></body></html>')


@pytest.mark.parametrize("filename", ["text/chapter one.xhtml", "text/自由.xhtml", "text/chapter#1?.xhtml", "text/100%law.xhtml"])
def test_epub_manifest_uri_encoding_preserves_actual_archive_member(tmp_path, make_book, concepts_file, filename):
    source = make_book(filename=filename)
    # EbookLib's writer emits raw hrefs; patch the OPF with lxml to create a
    # standards-shaped URI-encoded manifest while keeping actual ZIP names.
    with ZipFile(source) as archive:
        members = [(item, archive.read(item)) for item in archive.infolist()]
    with ZipFile(source, "w") as archive:
        for item, data in members:
            if item.filename.endswith(".opf"):
                root = etree.fromstring(data)
                for entry in root.findall("{*}manifest/{*}item"):
                    entry.set("href", quote(entry.get("href"), safe="/"))
                data = etree.tostring(root, xml_declaration=True, encoding="utf-8")
            archive.writestr(item, data)
    book = read_book(source)
    chapter, = book.chapters
    assert chapter.archive_path == "EPUB/" + filename
    assert chapter.href == quote(filename, safe="/")
    assert read_book(source).chapters[0].id == chapter.id
    workspace = tmp_path / "encoded"
    initialize([source], concepts_file(), workspace)
    out = tmp_path / "encoded-export"
    export_workspace(workspace, out)
    hit = json.loads((out / "occurrences.json").read_text())["occurrences"][0]
    assert hit["readium_locator"]["href"] == quote(filename, safe="/")


def test_source_markup_escaped_and_no_active_content(tmp_path, make_book, concepts_file):
    source = make_book(title='<img src=x onerror="alert(1)">', content='<h1>&lt;script&gt;danger&lt;/script&gt;</h1><p>power &lt;img src=x onerror=alert(1)&gt;</p>')
    workspace = tmp_path / "escaped"
    initialize([source], concepts_file([{"id": "a", "label": '<script>alert("x")</script>', "aliases": ["power"]}]), workspace)
    hit = collect(workspace)[2][0]
    label(workspace, hit["id"], senses=['<script>alert("y")</script>'])
    out = tmp_path / "escaped-export"
    export_workspace(workspace, out)
    from bs4 import BeautifulSoup
    for page in out.rglob("*.html"):
        soup = BeautifulSoup(page.read_text(), "html5lib")
        assert not soup.find("script")
        assert not soup.find("img")
        assert not soup.find(attrs={"onerror": True})


def test_render_equivalent_html_does_not_hide_changed_taguette_offsets(simple_workspace):
    path, _ = simple_workspace
    with sqlite3.connect(path / "project.sqlite3") as db:
        contents = db.execute("SELECT contents FROM documents").fetchone()[0]
        # Canonical block normalization produces the same reading text, but
        # Taguette counts actual text-node bytes and this removes one byte.
        changed = contents.replace("Power and", "Power</p><p>and")
        assert changed != contents
        db.execute("UPDATE documents SET contents=?", (changed,))
    with pytest.raises(InputError, match="document text changed"):
        collect(path)


def test_markup_only_changes_with_identical_text_node_bytes_are_supported(simple_workspace):
    path, _ = simple_workspace
    original = collect(path)[2]
    with sqlite3.connect(path / "project.sqlite3") as db:
        contents = db.execute("SELECT contents FROM documents").fetchone()[0]
        db.execute("UPDATE documents SET contents=?", (contents.replace("Power", "<em>Power</em>"),))
    assert [h["id"] for h in collect(path)[2]] == [h["id"] for h in original]
