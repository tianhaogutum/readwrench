from __future__ import annotations

from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfWriter

from readwrench.quotelocator import DocumentError, confirm, load_document, locate, verify

from ._writers import write_epub, write_pdf


def test_inline_html_entities_blocks_and_hidden_text(tmp_path):
    path = tmp_path / "article.html"
    path.write_text("""<!doctype html><html><head><title>Secret title</title></head><body>
      <p>Before paragraph.</p><p>A <em>fine</em> quote &amp; context.</p>
      <script>fabricated</script><p hidden>hidden sentence</p>
      <p aria-hidden="true">aria secret</p><p>After paragraph.</p></body></html>""")
    doc = load_document(path)
    review = locate(doc, "A fine quote & context.")
    assert review["status"] == "unique"
    assert review["candidates"][0]["context"]["before"]["text"] == "Before paragraph."
    assert review["candidates"][0]["context"]["after"]["text"] == "After paragraph."
    for excluded in ["Secret title", "fabricated", "hidden sentence", "aria secret"]:
        assert locate(doc, excluded)["status"] == "unmatched"


def test_real_pdf_writer_pages_and_cross_page_quote(tmp_path):
    path = tmp_path / "paper.pdf"
    write_pdf(path, ["A repeated quotation.", "A repeated quotation. Cross", "page passage."])
    doc = load_document(path)
    review = locate(doc, "A repeated quotation.")
    assert review["candidate_count"] == 2
    assert [c["locations"][0]["number"] for c in review["candidates"]] == [1, 2]
    cross = locate(doc, "Cross page passage.")
    assert cross["status"] == "unique"
    assert len(cross["candidates"][0]["locations"]) == 2
    anchor = confirm(doc, cross, 1)
    assert [loc["locations"]["fragments"] for loc in anchor["readium"]] == [["page=2"], ["page=3"]]
    assert verify(doc, anchor)["valid"]


@pytest.mark.parametrize("version", ["2.0", "3.0"])
def test_ebooklib_spine_order_and_resource_href(tmp_path, version):
    path = tmp_path / "book.epub"
    write_epub(path, version=version)
    doc = load_document(path)
    assert doc.text.startswith("Second chapter.")
    review = locate(doc, "A repeated quotation.")
    assert review["candidate_count"] == 2
    assert review["candidates"][0]["locations"][0]["href"] == "EPUB/Text/c2.xhtml"
    anchor = confirm(doc, review, 2)
    assert anchor["readium"][0]["href"] == "EPUB/Text/c1.xhtml"
    assert verify(doc, anchor)["valid"]


def test_pdf_empty_page_is_flagged(tmp_path):
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(600, 800)
    writer.write(path)
    doc = load_document(path)
    assert "no extractable text" in " ".join(doc.warnings)
    assert locate(doc, "an imaginary sentence")["status"] == "unmatched"


def test_encrypted_pdf_is_rejected(tmp_path):
    path = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(600, 800)
    writer.encrypt("password")
    writer.write(path)
    with pytest.raises(DocumentError, match="encrypted"):
        load_document(path)


def test_bad_encoding_and_unsupported_format(tmp_path):
    path = tmp_path / "legacy.txt"
    path.write_bytes("Café".encode("cp1252"))
    with pytest.raises(DocumentError, match="UnicodeDecodeError"):
        load_document(path)
    doc = load_document(path, encoding="cp1252")
    anchor = confirm(doc, locate(doc, "Café"), 1)
    assert verify(load_document(path, encoding="cp1252"), anchor)["valid"]
    other = tmp_path / "unknown.bin"
    other.write_bytes(b"not supported")
    with pytest.raises(DocumentError, match="Unsupported"):
        load_document(other)


def test_epub_expansion_budget(tmp_path):
    path = tmp_path / "oversized.epub"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        # Highly compressible, with expansion beyond the configured limit.
        archive.writestr("huge.txt", b"x" * (20 * 1024 * 1024 + 1))
    with pytest.raises(DocumentError, match="expanded"):
        load_document(path)
