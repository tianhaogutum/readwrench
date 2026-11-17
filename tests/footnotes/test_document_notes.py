from __future__ import annotations

import pytest

pytest.importorskip("lxml")
pytest.importorskip("ebooklib")



from readwrench.footnotes.audit import audit
from readwrench.footnotes.epub import Book, EpubError, parse_xml
from readwrench.footnotes.patch import make_patch
from tests.footnotes.books import DEFAULT_CHAPTER, DEFAULT_NOTES, approved, make_book


def test_document_only_forward_link_to_one_note_roundtrips(tmp_path):
    chapter = DEFAULT_CHAPTER.replace("notes.xhtml#note-1", "notes.xhtml")
    source = make_book(tmp_path / "document.epub", {"Text/chapter.xhtml": chapter})
    with Book(source) as book:
        fresh = audit(book)
        record = fresh.plan["references"][0]
        assert record["association_basis"] == "whole-document-single-note"
        assert record["status"] == "missing-return"
        patch = make_patch(book, fresh, approved(fresh.plan))
        assert b'href="notes.xhtml"' in patch.changes["EPUB/Text/chapter.xhtml"]
        candidate = tmp_path / "candidate.epub"
        book.write_candidate(candidate, patch.changes)
    with Book(candidate) as book:
        fresh = audit(book)
        assert fresh.plan["references"][0]["status"] == "exact-return"


def test_document_link_with_multiple_notes_is_not_guessed(tmp_path):
    chapter = DEFAULT_CHAPTER.replace("notes.xhtml#note-1", "notes.xhtml")
    notes = DEFAULT_NOTES.replace('</section>', '<aside epub:type="footnote" id="note-2"><p>Second note.</p></aside></section>')
    source = make_book(tmp_path / "ambiguous.epub", {"Text/chapter.xhtml": chapter}, notes)
    with Book(source) as book:
        fresh = audit(book)
        assert fresh.plan["references"][0]["status"] == "ambiguous-document-note"
        decisions = approved(fresh.plan)
        decisions["references"][0]["action"] = "add_backlink"
        with pytest.raises(EpubError, match="ambiguous-document-note"):
            make_patch(book, fresh, decisions)


def test_whole_untyped_note_file_uses_body(tmp_path):
    chapter = DEFAULT_CHAPTER.replace("notes.xhtml#note-1", "notes.xhtml")
    source = make_book(tmp_path / "body.epub", {"Text/chapter.xhtml": chapter}, '<h1>A separate note</h1><p>One explanation.</p>')
    with Book(source) as book:
        fresh = audit(book)
        ref = fresh.plan["references"][0]
        assert ref["association_basis"] == "whole-document-body"
        assert fresh.notes[ref["note_key"]][1].tag.endswith("}body")
        assert make_patch(book, fresh, approved(fresh.plan)).changes


def test_chapter_only_return_is_reported_as_ancestor_not_exact(tmp_path):
    notes = DEFAULT_NOTES.replace('</aside>', '<p><a href="chapter.xhtml">Chapter beginning</a></p></aside>')
    source = make_book(tmp_path / "return.epub", notes=notes)
    with Book(source) as book:
        ref = audit(book).plan["references"][0]
        assert ref["status"] == "ancestor-return"
        assert ref["returns"][0]["location"]["tag"] == "body"


@pytest.mark.parametrize("href", ["", "#", "chapter.xhtml"])
def test_empty_or_self_document_link_does_not_associate_to_an_unrelated_inline_note(tmp_path, href):
    source = make_book(tmp_path / "self.epub", {"Text/chapter.xhtml": DEFAULT_CHAPTER.replace("notes.xhtml#note-1", href) + DEFAULT_NOTES})
    with Book(source) as book:
        fresh = audit(book)
        assert fresh.plan["references"][0]["status"] == "self-reference"


def test_ordinary_chapter_link_is_not_inferred_as_a_note_from_a_single_aside(tmp_path):
    chapters = {
        "Text/chapter.xhtml": '<p><a href="later.xhtml">Next chapter</a></p>',
        "Text/later.xhtml": '<h1>Chapter two</h1><aside epub:type="footnote" id="n"><p>Inline note.</p></aside>',
    }
    source = make_book(tmp_path / "chapters.epub", chapters)
    with Book(source) as book:
        assert audit(book).plan["references"] == []


def test_node_guard_runs_before_lxml_tree_allocation(monkeypatch):
    monkeypatch.setattr("readwrench.footnotes.epub.MAX_XML_NODES", 10)
    monkeypatch.setattr("readwrench.footnotes.epub.etree.fromstring", lambda *args: (_ for _ in ()).throw(AssertionError("lxml must not run")))
    with pytest.raises(EpubError, match="node memory bound"):
        parse_xml(b'<root>' + b'<i/>' * 20 + b'</root>', "nodes.xml")


@pytest.mark.parametrize("data", [
    b'<root ' + b' '.join(f'a{i}="x"'.encode() for i in range(20)) + b'/>',
    b'<!--comment-->' * 20 + b'<root/>',
])
def test_node_guard_includes_attributes_and_prolog_comments(monkeypatch, data):
    monkeypatch.setattr("readwrench.footnotes.epub.MAX_XML_NODES", 10)
    with pytest.raises(EpubError, match="node memory bound"):
        parse_xml(data, "nodes.xml")


def test_xml_depth_is_bounded_before_tree_allocation():
    with pytest.raises(EpubError, match="256-level"):
        parse_xml(b'<r>' * 257 + b'</r>' * 257, "depth.xml")


def test_total_book_node_bound_is_enforced(tmp_path, monkeypatch):
    source = make_book(tmp_path / "source.epub")
    monkeypatch.setattr("readwrench.footnotes.epub.MAX_BOOK_NODES", 10)
    with pytest.raises(EpubError, match="total-node memory bound"):
        Book(source)
