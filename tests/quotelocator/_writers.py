"""Real format writers shared by the ``readwrench quote`` tests (pypdf and EbookLib)."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from ebooklib import epub
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def write_pdf(path: Path, pages: list[str]) -> None:
    """Use the actual pypdf writer and PDF text operators, not a fake extraction fixture."""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        content = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content.set_data(f"BT /F1 12 Tf 30 700 Td ({escaped}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(path)


def write_epub(path: Path, *, version: str = "3.0") -> None:
    book = epub.EpubBook()
    book.set_identifier("quote-locator-test")
    book.set_title("Test book")
    book.set_language("en")
    chapters = []
    for number, text in enumerate(
        ["First chapter. A repeated quotation.", "Second chapter. A repeated quotation."], 1
    ):
        chapter = epub.EpubHtml(title=f"Chapter {number}", file_name=f"Text/c{number}.xhtml")
        chapter.content = f"<p>{text}</p><p>After chapter {number}.</p>"
        book.add_item(chapter)
        chapters.append(chapter)
    # Manifest insertion order is deliberately different from reading order.
    book.spine = [chapters[1], chapters[0]]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(path, book)
    if version == "2.0":
        # EbookLib writes EPUB3; preserve the real archive and switch the package
        # version to exercise its EPUB2 reader (with NCX already present).
        with ZipFile(path) as archive:
            entries = [(info, archive.read(info)) for info in archive.infolist()]
        with ZipFile(path, "w") as archive:
            for info, data in entries:
                if info.filename.endswith(".opf"):
                    data = data.replace(b'version="3.0"', b'version="2.0"')
                archive.writestr(info, data)
