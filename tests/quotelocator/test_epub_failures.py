from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from ebooklib import epub
from ._writers import write_epub

from readwrench.quotelocator import DocumentError, load_document


def rewrite_archive(path, replacements):
    with ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    with ZipFile(path, "w") as archive:
        names = set()
        for info, content in entries:
            names.add(info.filename)
            archive.writestr(info, replacements.get(info.filename, content))
        for name, content in replacements.items():
            if name not in names:
                archive.writestr(name, content)


def test_multiple_renditions_are_an_explicit_error_not_a_partial_unmatched_search(tmp_path):
    path = tmp_path / "book.epub"
    write_epub(path)
    with ZipFile(path) as archive:
        container = BeautifulSoup(archive.read("META-INF/container.xml"), "xml")
    other = container.new_tag("rootfile")
    other["full-path"] = "Another/package.opf"
    other["media-type"] = "application/oebps-package+xml"
    container.rootfiles.append(other)
    rewrite_archive(path, {"META-INF/container.xml": container.encode("utf-8")})
    with pytest.raises(DocumentError, match="multiple package renditions"):
        load_document(path)


def test_text_disguised_with_a_font_extension_does_not_bypass_encryption_check(tmp_path):
    path = tmp_path / "book.epub"
    book = epub.EpubBook()
    book.set_identifier("encrypted-content")
    book.set_title("Encrypted content")
    book.set_language("en")
    chapter = epub.EpubHtml(title="Chapter", file_name="Text/chapter.otf")
    chapter.content = "<p>A quotation that must not be searched as decrypted text.</p>"
    book.add_item(chapter)
    book.spine = [chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(path, book)
    with ZipFile(path) as archive:
        opf_path = next(name for name in archive.namelist() if name.endswith(".opf"))
        package = BeautifulSoup(archive.read(opf_path), "xml")
    package.manifest.find("item", attrs={"href": "Text/chapter.otf"})["media-type"] = (
        "application/xhtml+xml"
    )
    encryption = b"""<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
      xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>
      <enc:CipherData><enc:CipherReference URI="EPUB/Text/chapter.otf"/></enc:CipherData>
      </enc:EncryptedData></encryption>"""
    rewrite_archive(
        path, {"META-INF/encryption.xml": encryption, opf_path: package.encode("utf-8")}
    )
    with pytest.raises(DocumentError, match="spine content is encrypted"):
        load_document(path)


def test_missing_spine_resource_fails_without_inventing_text(tmp_path):
    path = tmp_path / "book.epub"
    write_epub(path)
    with ZipFile(path) as archive:
        opf_path = next(name for name in archive.namelist() if name.endswith(".opf"))
        package = BeautifulSoup(archive.read(opf_path), "xml")
    package.spine.itemref["idref"] = "missing-resource"
    rewrite_archive(path, {opf_path: package.encode("utf-8")})
    with pytest.raises(DocumentError, match="missing"):
        load_document(path)
