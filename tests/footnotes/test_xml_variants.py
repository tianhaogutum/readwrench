from __future__ import annotations

import pytest

pytest.importorskip("lxml")
pytest.importorskip("ebooklib")

import codecs
import copy
from zipfile import ZipFile

from lxml import etree

from readwrench.footnotes.audit import audit
from readwrench.footnotes.epub import Book, EPUB, XHTML, parse_xml, sha256_file
from readwrench.footnotes.patch import make_patch
from readwrench.footnotes.preserve import verify_preservation
from tests.footnotes.books import approved, make_book, rewrite


@pytest.mark.parametrize("encoding,bom", [("utf-8", False), ("utf-8", True), ("utf-16-le", True), ("utf-16-be", True)])
@pytest.mark.parametrize("prefix", [False, True])
@pytest.mark.parametrize("empty_reference", [False, True])
def test_expat_and_lxml_agree_on_source_bytes(tmp_path, encoding, bom, prefix, empty_reference):
    source = make_book(tmp_path / "source.epub")
    changes = {}
    with ZipFile(source) as z:
        for name in ("EPUB/Text/chapter.xhtml", "EPUB/Text/notes.xhtml"):
            original = parse_xml(z.read(name), name)
            root = etree.Element(f"{{{XHTML}}}html", nsmap={"x" if prefix else None: XHTML, "epub": EPUB})
            root.attrib.update(original.attrib)
            for child in original:
                root.append(copy.deepcopy(child))
            root.addprevious(etree.ProcessingInstruction("fixture", "preserve"))
            body = root.find(f"{{{XHTML}}}body")
            body.insert(0, etree.Comment("Original comment with < and > stays byte-for-byte"))
            anchor = root.find(f".//{{{XHTML}}}a")
            if anchor is not None:
                anchor.set("title", "literal > 你好 😀")
                if empty_reference:
                    anchor.text = None
            text = etree.tostring(root.getroottree(), encoding="unicode")
            # Literal '>' inside single quotes must not terminate the start tag.
            text = text.replace('title="literal &gt; 你好 😀"', "title='literal > 你好 😀'")
            declaration = "UTF-16" if encoding.startswith("utf-16") and bom else encoding
            text = f'<?xml version="1.0" encoding="{declaration}"?>\n' + text
            data = text.encode(encoding)
            if bom:
                data = {"utf-8": codecs.BOM_UTF8, "utf-16-le": codecs.BOM_UTF16_LE, "utf-16-be": codecs.BOM_UTF16_BE}[encoding] + data
            changes[name] = data
    variant = rewrite(source, changes)
    with Book(variant) as book:
        fresh = audit(book)
        patch = make_patch(book, fresh, approved(fresh.plan))
        candidate = tmp_path / "candidate.epub"
        book.write_candidate(candidate, patch.changes)
        patch.manifest["candidate_sha256"] = sha256_file(candidate)
        assert verify_preservation(book, candidate, patch.manifest)["status"] == "passed"
    with Book(candidate) as book:
        assert audit(book).plan["references"][0]["status"] == "exact-return"
