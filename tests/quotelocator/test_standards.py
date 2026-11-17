from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import json
from pathlib import Path
from urllib.parse import unquote
from zipfile import ZipFile

import pytest

# Development-only standards validators (the W3C JSON-LD context and the Readium schema).
pytest.importorskip("jsonschema")
pytest.importorskip("pyld")
pytest.importorskip("rfc3986_validator")

from ebooklib import epub  # noqa: E402
from jsonschema import Draft7Validator, FormatChecker  # noqa: E402
from pyld import jsonld  # noqa: E402

from readwrench.quotelocator import confirm, load_document, locate  # noqa: E402

from ._writers import write_epub, write_pdf  # noqa: E402

STANDARDS = Path(__file__).parent / "fixtures" / "standards"


def test_annotation_expands_with_the_real_w3c_context_and_no_network(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("😀 Before. A ﬁne quotation. After.", encoding="utf-8")
    doc = load_document(path)
    anchor = confirm(doc, locate(doc, "A fine quotation."), 1)
    context = json.loads((STANDARDS / "anno.jsonld").read_text())
    requested = []

    def offline_loader(url, options=None):
        requested.append(url)
        assert url == "http://www.w3.org/ns/anno.jsonld"
        return {"contextUrl": None, "documentUrl": url, "document": context}

    expanded = jsonld.expand(anchor["annotation"], options={"documentLoader": offline_loader})
    oa = "http://www.w3.org/ns/oa#"
    assert expanded[0]["@type"] == [oa + "Annotation"]
    target = expanded[0][oa + "hasTarget"][0]
    assert target[oa + "hasSource"][0]["@id"] == "urn:sha256:" + doc.text_sha256
    selectors = {s["@type"][0]: s for s in target[oa + "hasSelector"]}
    position = selectors[oa + "TextPositionSelector"]
    start, end = position[oa + "start"][0], position[oa + "end"][0]
    assert start["@type"] == end["@type"] == "http://www.w3.org/2001/XMLSchema#nonNegativeInteger"
    assert (
        doc.text[start["@value"] : end["@value"]]
        == selectors[oa + "TextQuoteSelector"][oa + "exact"][0]["@value"]
    )
    assert requested == ["http://www.w3.org/ns/anno.jsonld"]


@pytest.mark.parametrize("format", ["txt", "html", "pdf", "epub"])
def test_exported_readium_locators_validate_against_upstream_schema(tmp_path, format):
    path = tmp_path / ("source." + format)
    if format == "pdf":
        write_pdf(path, ["A quotation."])
    elif format == "epub":
        write_epub(path)
    else:
        path.write_text("<p>A quotation.</p>" if format == "html" else "A quotation.")
    doc = load_document(path)
    quote = "A repeated quotation." if format == "epub" else "A quotation."
    anchor = confirm(doc, locate(doc, quote), 1)
    schema = json.loads((STANDARDS / "locator.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    assert "uri-reference" in validator.format_checker.checkers
    for locator in anchor["readium"]:
        validator.validate(locator)


def test_epub_resource_hrefs_resolve_from_container_root_and_escape_uri_characters(tmp_path):
    path = tmp_path / "unicode.epub"
    book = epub.EpubBook()
    book.set_identifier("unicode-resource")
    book.set_title("Unicode resource")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="章节", file_name="Text/章节 一.xhtml")
    chapter.content = "<p>核对原始文献。</p>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [chapter]
    epub.write_epub(path, book)
    doc = load_document(path)
    anchor = confirm(doc, locate(doc, "核对原始文献"), 1)
    locator = anchor["readium"][0]
    assert "%20" in locator["href"] and " " not in locator["href"]
    with ZipFile(path) as archive:
        assert unquote(locator["href"]) in archive.namelist()
    assert unquote(locator["href"]) == doc.units[0].href
