from zipfile import ZipFile

import pytest

from .helpers import GUTENBERG, EPUB_DEPENDENCIES, require

require(*EPUB_DEPENDENCIES)

from readwrench.concordance.books import InputError, canonical_html, read_book


def test_blocks_inline_adjacency_entities_and_comments():
    text, headings, anchors = canonical_html('<h1 id="a">倫理 😀</h1><p>free<em>dom</em> &amp; law</p>tail<p>next</p><!-- invisible --> <script>power</script>')
    assert text == "倫理 😀 freedom & law tail next"
    assert headings == [{"start": 0, "title": "倫理 😀"}]
    assert anchors == [{"start": 0, "fragment": "a"}]


def test_real_writer_sections(make_book):
    path = make_book(content='<h1 id="one">One</h1><p>power</p><h2 id="two">Two</h2><p>自由 authority</p>')
    book = read_book(path)
    assert book.epub_version == "3.0"
    chapter, = book.chapters
    assert chapter.href == "text/chapter.xhtml"
    assert chapter.archive_path == "EPUB/text/chapter.xhtml"
    assert chapter.text == "One power Two 自由 authority"
    assert chapter.headings == [{"start": 0, "title": "One"}, {"start": 10, "title": "Two"}]


def test_real_gutenberg_epub2():
    from pathlib import Path
    book = read_book(GUTENBERG / "prince-epub2.epub")
    assert book.epub_version == "2.0"
    assert book.title == "The Prince"
    assert any("CHAPTER I." in chapter.text for chapter in book.chapters)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "iso-8859-1"])
def test_xml_declared_encoding_is_honored(encoding):
    source = f'<?xml version="1.0" encoding="{encoding}"?><html><body><p>liberté &amp; société</p></body></html>'
    assert canonical_html(source.encode(encoding))[0] == "liberté & société"


def test_spine_order_navigation_and_nonlinear(tmp_path):
    from ebooklib import epub
    book = epub.EpubBook()
    book.set_identifier("spine-order")
    book.set_title("Order")
    book.set_language("en")
    a = epub.EpubHtml(uid="a", file_name="a.xhtml", title="A", content="<p>First power.</p>")
    b = epub.EpubHtml(uid="b", file_name="b.xhtml", title="B", content="<p>Second power.</p>")
    c = epub.EpubHtml(uid="c", file_name="c.xhtml", title="C", content="<p>Notes power.</p>")
    for item in [a, b, c, epub.EpubNav(), epub.EpubNcx()]:
        book.add_item(item)
    book.spine = ["nav", b, a, (c, "no")]
    book.toc = [a, b, c]
    path = tmp_path / "ordered.epub"
    epub.write_epub(path, book)
    assert [c.href for c in read_book(path).chapters] == ["b.xhtml", "a.xhtml"]
    assert [c.href for c in read_book(path, include_nonlinear=True).chapters] == ["b.xhtml", "a.xhtml", "c.xhtml"]


def test_rtl_direction(make_book):
    assert read_book(make_book(direction="rtl")).chapters[0].direction == "RIGHT_TO_LEFT"


@pytest.mark.parametrize("member", ["../escape.xhtml", "/escape.xhtml", "a/../../escape.xhtml", "a\\escape.xhtml", "%2e%2e/escape.xhtml"])
def test_archive_paths_rejected(tmp_path, member):
    path = tmp_path / "unsafe.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr(member, "bad")
    with pytest.raises(InputError, match="Unsafe"):
        read_book(path)


def test_broken_archive(tmp_path):
    path = tmp_path / "bad.epub"
    path.write_text("not a ZIP")
    with pytest.raises(InputError, match="archive"):
        read_book(path)


def test_encrypted_prose_rejected(tmp_path):
    path = tmp_path / "drm.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr("META-INF/encryption.xml", '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes256-cbc"/></EncryptedData></encryption>')
    with pytest.raises(InputError, match="DRM/encrypted"):
        read_book(path)


def test_unmodified_gutenberg_books(real_books):
    parsed = [read_book(path) for path in real_books]
    assert [book.title for book in parsed] == ["The Prince", "Second Treatise of Government", "The Republic"]
    assert all(book.authors and book.identifiers and book.chapters for book in parsed)
    assert sum(len(book.chapters) for book in parsed) == 75
    assert all(c.text for b in parsed for c in b.chapters)
