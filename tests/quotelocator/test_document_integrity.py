from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import copy

import pytest
from pypdf import PdfReader, PdfWriter, get_configuration
from reanchor import find_exact
from ._writers import write_pdf

from readwrench.quotelocator import DocumentError, confirm, load_document, locate, verify
from readwrench.quotelocator.matching import differences, occurrences


def test_source_changes_during_extraction_are_rejected(tmp_path, monkeypatch):
    from readwrench.quotelocator import documents

    path = tmp_path / "article.html"
    path.write_text("<p>Original quotation.</p>")
    real_extract = documents._html_text

    def changing_extract(content):
        text = real_extract(content)
        path.write_text("<p>Edited while extraction was happening.</p>")
        return text

    monkeypatch.setattr(documents, "_html_text", changing_extract)
    with pytest.raises(DocumentError, match="changed during extraction"):
        load_document(path)


def test_document_metadata_does_not_alias_the_review(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("A quotation")
    doc = load_document(path)
    review = locate(doc, "quotation")
    review["document"]["extraction"]["offset_unit"] = "wrong"
    assert doc.extraction["offset_unit"] == "unicode-code-point"
    with pytest.raises(ValueError, match="policy"):
        confirm(doc, review, 1)


@pytest.mark.parametrize(
    "key,value",
    [("characters", 1), ("media_type", "made/up"), ("units", []), ("warnings", ["edited"])],
)
def test_mutated_document_metadata_is_not_verified(tmp_path, key, value):
    path = tmp_path / "book.txt"
    path.write_text("A quotation")
    doc = load_document(path)
    anchor = confirm(doc, locate(doc, "quotation"), 1)
    anchor["document"][key] = value
    assert not verify(doc, anchor)["valid"]


def test_long_pattern_search_keeps_every_overlap_and_rejects_prefix_only_hits():
    for pattern in ["a" * 128, "ab" * 80, "a" * 130 + "b"]:
        text = "a" * 600 + "ab" * 200 + pattern + pattern[:32]
        assert list(occurrences(text, pattern)) == find_exact(text, pattern)


def test_long_normalized_span_uses_bounded_complete_diff():
    quote = "a" * 10_000
    source = ("a" + "\u200b" * 20) * 10_000
    changes = differences(quote, source)
    assert changes == [
        {
            "operation": "replace",
            "query": quote,
            "source": source,
            "query_start": 0,
            "query_end": len(quote),
            "source_start": 0,
            "source_end": len(source),
            "granularity": "whole-span",
        }
    ]


def test_pdf_decompression_limit_uses_upstream_configuration_and_restores_it(tmp_path, monkeypatch):
    from readwrench.quotelocator import documents

    path = tmp_path / "large-stream.pdf"
    write_pdf(path, ["a" * 5000])
    reader = PdfReader(path)
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    writer.pages[0].compress_content_streams()
    writer.write(path)
    previous = get_configuration()
    monkeypatch.setitem(documents.PDF_LIMITS, "zlib_maximum_output_length", 1024)
    with pytest.raises(DocumentError, match="[Ll]imit"):
        load_document(path)
    assert get_configuration() == previous


def test_xhtml_prefixes_preserve_paragraphs_and_exclude_head(tmp_path):
    path = tmp_path / "article.xhtml"
    path.write_text("""<x:html xmlns:x="http://www.w3.org/1999/xhtml"><x:head>
    <x:title>Invisible title</x:title></x:head><x:body><x:p>Before</x:p>
    <x:p>A <x:em>fine</x:em> quotation.</x:p><x:p>After</x:p></x:body></x:html>""")
    doc = load_document(path)
    review = locate(doc, "A fine quotation.")
    assert review["candidate_count"] == 1
    assert "Invisible" not in doc.text
    assert review["candidates"][0]["context"]["before"]["text"] == "Before"


def test_oversized_html_is_rejected_before_parsing(tmp_path, monkeypatch):
    from readwrench.quotelocator import documents

    path = tmp_path / "large.html"
    path.write_text("<p>hello</p>" * 100)
    monkeypatch.setattr(documents, "MAX_HTML_BYTES", 100)
    with pytest.raises(DocumentError, match="HTML/XHTML"):
        load_document(path)


def test_repeated_selector_reports_need_for_hash_bound_position(tmp_path):
    path = tmp_path / "repetition.txt"
    path.write_text("a" * 3000)
    doc = load_document(path)
    review = locate(doc, "a" * 200, offset=1000, limit=1)
    anchor = confirm(doc, review, 1001)
    assert anchor["selector_status"]["text_quote_unique"] is False
    assert anchor["selector_status"]["position_required"] is True
    assert anchor["selector_status"]["matching_contexts"] > 1
    assert verify(doc, anchor)["valid"]
    broken = copy.deepcopy(anchor)
    broken["selector_status"]["text_quote_unique"] = True
    assert not verify(doc, broken)["valid"]
