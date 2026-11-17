from __future__ import annotations

import pytest

pytest.importorskip("lxml")
pytest.importorskip("ebooklib")

from zipfile import ZipFile

from lxml import etree

from readwrench.footnotes.audit import audit
from readwrench.footnotes.epub import Book, EpubError, NS, relative_href, resolve_href
from readwrench.footnotes.html import review_html
from readwrench.footnotes.patch import make_patch
from tests.footnotes.books import DEFAULT_CHAPTER, DEFAULT_NOTES, FIXTURES, approved, make_book, rewrite

CHAPTER = "EPUB/Text/chapter.xhtml"
NOTES = "EPUB/Text/notes.xhtml"


@pytest.fixture
def book_path(tmp_path):
    return make_book(tmp_path / "book.epub")


def test_real_ebooklib_epub_is_audited_in_spine_order(book_path):
    with Book(book_path) as book:
        result = audit(book)
        assert len(result.plan["references"]) == 1
        ref = result.plan["references"][0]
        assert ref["status"] == "missing-return"
        assert ref["approved"] is False
        assert ref["location"]["path"] == CHAPTER
        assert result.plan["notes"][0]["location"]["tag"] == "aside"
        assert not any(r["location"]["path"] in book.nav_paths for r in result.plan["references"])


def test_candidate_is_additive_and_roundtrips(book_path, tmp_path):
    with Book(book_path) as book:
        fresh = audit(book)
        patch = make_patch(book, fresh, approved(fresh.plan))
        assert set(patch.changes) == {CHAPTER, NOTES}
        for member, changed in patch.changes.items():
            edits = [e for e in patch.manifest["edits"] if e["path"] == member]
            cursor, offset, recovered = 0, 0, []
            for edit in edits:
                end = edit["byte_offset"] + offset
                recovered.append(changed[cursor:end])
                cursor = end + edit["inserted_bytes"]
                offset += edit["inserted_bytes"]
            recovered.append(changed[cursor:])
            assert b"".join(recovered) == book.documents[member].data
        candidate = tmp_path / "candidate.epub"
        book.write_candidate(candidate, patch.changes)
    with ZipFile(book_path) as before, ZipFile(candidate) as after:
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if name not in patch.changes:
                assert before.read(name) == after.read(name)
    with Book(candidate) as book:
        fresh = audit(book)
        assert fresh.plan["references"][0]["status"] == "exact-return"
        assert make_patch(book, fresh, approved(fresh.plan)).changes == {}


def test_unapproved_and_stale_reviews_are_rejected(book_path):
    with Book(book_path) as book:
        fresh = audit(book)
        with pytest.raises(EpubError, match="No approved"):
            make_patch(book, fresh, fresh.plan)
        decisions = approved(fresh.plan)
        decisions["book_sha256"] = "0" * 64
        with pytest.raises(EpubError, match="changed since review"):
            make_patch(book, fresh, decisions)


def test_many_references_to_one_note_get_distinct_returns(tmp_path):
    chapters = {
        "Text/chapter.xhtml": DEFAULT_CHAPTER,
        "Text/later.xhtml": '<h1>Later</h1><p>A second use <a role="doc-noteref" href="notes.xhtml#note-1">again</a>.</p>',
    }
    source = make_book(tmp_path / "shared.epub", chapters)
    with Book(source) as book:
        fresh = audit(book)
        assert [r["number"] for r in fresh.plan["references"]] == [1, 2]
        patch = make_patch(book, fresh, approved(fresh.plan))
        root = etree.fromstring(patch.changes[NOTES])
        links = root.xpath('//x:a[@role="doc-backlink"]', namespaces=NS)
        assert len(links) == 2
        assert len({a.get("href") for a in links}) == 2
        assert len({a.text for a in links}) == 2
        output = tmp_path / "candidate.epub"
        book.write_candidate(output, patch.changes)
    with Book(output) as book:
        assert [r["status"] for r in audit(book).plan["references"]] == ["exact-return", "exact-return"]


def test_existing_exact_return_is_kept(tmp_path):
    chapter = DEFAULT_CHAPTER.replace('<a epub:type=', '<a id="original-ref" epub:type=')
    notes = DEFAULT_NOTES.replace('</aside>', '<p><a href="chapter.xhtml#original-ref">Original return label</a></p></aside>')
    path = make_book(tmp_path / "exact.epub", {"Text/chapter.xhtml": chapter}, notes)
    with Book(path) as book:
        fresh = audit(book)
        assert fresh.plan["references"][0]["status"] == "exact-return"
        decisions = approved(fresh.plan)
        decisions["references"][0]["action"] = "add_backlink"
        patch = make_patch(book, fresh, decisions)
        assert patch.changes == {}
        assert patch.manifest["paths"][0]["result"] == "kept-existing"
        candidate = tmp_path / "unchanged-copy.epub"
        book.write_candidate(candidate, patch.changes)
        assert candidate.read_bytes() == path.read_bytes()


def test_ancestor_return_is_reported_and_preserved(tmp_path):
    chapter = DEFAULT_CHAPTER.replace('<p>', '<p id="paragraph">')
    notes = DEFAULT_NOTES.replace('</aside>', '<p><a href="chapter.xhtml#paragraph">Original return</a></p></aside>')
    path = make_book(tmp_path / "ancestor.epub", {"Text/chapter.xhtml": chapter}, notes)
    with Book(path) as book:
        fresh = audit(book)
        assert fresh.plan["references"][0]["status"] == "ancestor-return"
        patch = make_patch(book, fresh, approved(fresh.plan))
        assert b'Original return</a>' in patch.changes[NOTES]
        assert b'href="chapter.xhtml#paragraph"' in patch.changes[NOTES]
        assert b'role="doc-backlink"' in patch.changes[NOTES]


@pytest.mark.parametrize("href,error", [
    ("notes.xhtml#absent", "missing-target-id"),
    ("https://example.org/#note-1", "external-link"),
    ("notes.xhtml#epubcfi(/6/2)", "epub-cfi-unsupported"),
    ("../../../elsewhere.xhtml#n", "link-escapes-archive"),
    ("notes.xhtml?x=1#note-1", "query-in-link"),
    ("notes.xhtml#bad%xx", "invalid-percent-escape"),
])
def test_invalid_forward_destinations_cannot_be_patched(tmp_path, href, error):
    path = make_book(tmp_path / "broken.epub", {"Text/chapter.xhtml": DEFAULT_CHAPTER.replace("notes.xhtml#note-1", href)})
    with Book(path) as book:
        fresh = audit(book)
        ref = fresh.plan["references"][0]
        assert ref["status"] == error
        decisions = approved(fresh.plan)
        decisions["references"][0]["action"] = "add_backlink"
        with pytest.raises(EpubError, match="Cannot add"):
            make_patch(book, fresh, decisions)


def test_duplicate_target_is_ambiguous(tmp_path):
    path = make_book(tmp_path / "duplicate.epub", notes=DEFAULT_NOTES.replace('</section>', '<p id="note-1">Another note</p></section>'))
    with Book(path) as book:
        fresh = audit(book)
        assert fresh.plan["references"][0]["status"] == "duplicate-target-id"
        assert any(w["code"] == "invalid-or-duplicate-id" for w in fresh.plan["warnings"])


def test_tampered_association_is_not_trusted(book_path):
    with Book(book_path) as book:
        fresh = audit(book)
        decisions = approved(fresh.plan)
        decisions["references"][0]["note_key"] = "another-note"
        with pytest.raises(EpubError, match="Association differs"):
            make_patch(book, fresh, decisions)


def test_custom_xpath_and_plain_links(tmp_path):
    chapter = '<p>Legacy <a class="special" href="notes.xhtml#note-1">N</a>.</p>'
    notes = '<div class="special-note" id="note-1"><p>Original note.</p></div>'
    path = make_book(tmp_path / "legacy.epub", {"Text/chapter.xhtml": chapter}, notes)
    with Book(path) as book:
        assert len(audit(book).plan["references"]) == 0
        result = audit(book, {"ref_xpath": ['//x:a[@class="special"]'], "note_xpath": ['//x:div[@class="special-note"]']})
        assert len(result.plan["references"]) == 1
        assert make_patch(book, result, approved(result.plan)).changes
        with pytest.raises(EpubError, match="must select"):
            audit(book, {"ref_xpath": ["count(//x:a)"]})


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be"])
def test_source_positions_preserve_unicode_quotes_comments_and_cdata(tmp_path, encoding):
    path = make_book(tmp_path / "encoding.epub")
    changes = {}
    with ZipFile(path) as archive:
        for name in (CHAPTER, NOTES):
            text = archive.read(name).decode().replace('encoding=\'utf-8\'', f'encoding="{encoding}"')
            text = text.replace('encoding="utf-8"', f'encoding="{encoding}"')
            text = text.replace('<body>', '<body><!-- preserved --><p title="é &gt; 你好"><![CDATA[Original < text]]></p>')
            changes[name] = text.encode(encoding)
    variant = rewrite(path, changes)
    with Book(variant) as book:
        fresh = audit(book)
        decisions = approved(fresh.plan)
        decisions["references"][0]["backlink_label"] = "返回引用一 — 你好"
        patch = make_patch(book, fresh, decisions)
        assert patch.changes
        assert "返回引用一" in patch.changes[NOTES].decode(encoding)
        assert "<![CDATA[Original < text]]>" in patch.changes[NOTES].decode(encoding)


@pytest.mark.parametrize("source,target,fragment", [
    ("EPUB/Text/章.xhtml", "EPUB/notes & more.xhtml", "注:1/2?"),
    ("chapter.xhtml", "notes.xhtml", "note-1"),
    ("EPUB/one.xhtml", "EPUB/one.xhtml", "n#1"),
])
def test_url_composition_roundtrip(source, target, fragment):
    href = relative_href(source, target, fragment)
    resolved = resolve_href(source, href)
    assert (resolved.path, resolved.fragment, resolved.error) == (target, fragment, None)


def test_review_html_escapes_book_markup(book_path):
    with Book(book_path) as book:
        plan = audit(book).plan
        plan["book_title"] = '</script><script>alert("bad")</script>'
        plan["references"][0]["context"] = '<img src="https://example.org/tracker">'
        output = review_html(plan)
        assert '<img src="https://' not in output
        assert '</script><script>alert' not in output
        assert '\\u003c/script\\u003e' in output
        assert "connect-src &#x27;none&#x27;" in output


def test_published_sigil_unlinked_markers_are_reported():
    with Book(FIXTURES / "upstream/FNLTest.epub") as book:
        result = audit(book)
        assert result.plan["references"] == []
        assert len([w for w in result.plan["warnings"] if w["code"] == "unlinked-note-marker"]) == 4


@pytest.mark.parametrize("name,count,status", [("wasteland.epub", 50, "ancestor-return"), ("moby-dick.epub", 1, "missing-return")])
def test_published_idpf_books(name, count, status, tmp_path):
    with Book(FIXTURES / "upstream" / name) as book:
        result = audit(book)
        assert len(result.plan["references"]) == count
        assert {r["status"] for r in result.plan["references"]} == {status}
        patch = make_patch(book, result, approved(result.plan))
        candidate = tmp_path / "candidate.epub"
        book.write_candidate(candidate, patch.changes)
    with Book(candidate) as book:
        assert {r["status"] for r in audit(book).plan["references"]} == {"exact-return"}


def test_real_epub2_package_named_entities_and_legacy_links(tmp_path):
    with Book(FIXTURES / "sigil-linked-epub2.epub") as book:
        assert book.version == "2.0"
        result = audit(book)
        assert len(result.plan["references"]) == 4
        patch = make_patch(book, result, approved(result.plan))
        assert b'&nbsp;&mdash;' in patch.changes["OEBPS/Text/endnotes.xhtml"]
        assert b'role="doc-backlink"' not in patch.changes["OEBPS/Text/endnotes.xhtml"]
        candidate = tmp_path / "epub2.epub"
        book.write_candidate(candidate, patch.changes)
    with Book(candidate) as book:
        result = audit(book)
        assert [r["status"] for r in result.plan["references"]] == ["exact-return"] * 4


def test_unicode_archive_path_and_fragment_work_as_actual_epub(tmp_path):
    chapter_name = "Text/章节 & one.xhtml"
    chapter = '<h1>原文</h1><p>继续阅读 <a epub:type="noteref" href="notes.xhtml#%E6%B3%A8%E4%B8%80">一</a>。</p>'
    notes = '<aside epub:type="footnote" id="注一"><p>中文注释。</p></aside>'
    path = make_book(tmp_path / "unicode.epub", {chapter_name: chapter}, notes)
    with Book(path) as book:
        result = audit(book)
        patch = make_patch(book, result, approved(result.plan))
        href = patch.manifest["paths"][0]["return_href"]
        assert "%E7%AB%A0%E8%8A%82%20%26%20one.xhtml#" in href
        candidate = tmp_path / "candidate.epub"
        book.write_candidate(candidate, patch.changes)
    with Book(candidate) as book:
        assert audit(book).plan["references"][0]["status"] == "exact-return"
