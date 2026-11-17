"""Test helpers: small real EPUBs written by upstream EbookLib (not a ZIP facsimile).

``make_book`` needs the test-only dependency EbookLib; ``rewrite`` and ``approved``
are standard library only. Committed fixtures live in ``fixtures/``.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

FIXTURES = Path(__file__).resolve().parent / "fixtures"

DEFAULT_CHAPTER = ('<h1>Chapter one</h1><p>Original prose '
                   '<a epub:type="noteref" role="doc-noteref" href="notes.xhtml#note-1">1</a> continues.</p>')
DEFAULT_NOTES = ('<section epub:type="endnotes" role="doc-endnotes"><h1>Notes</h1>'
                 '<aside id="note-1" epub:type="footnote" role="doc-footnote">'
                 '<p>An original note without a return link.</p></aside></section>')


def make_book(path: Path, chapters: dict[str, str] | None = None, notes: str = DEFAULT_NOTES) -> Path:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("urn:readwrench:footnotes:" + path.stem)
    book.set_title("Upstream-generated note sample")
    book.set_language("en")
    book.add_author("readwrench contributors")
    book.add_metadata("DC", "rights", "Original test prose; MIT licensed with epub-footback.")
    chapters = chapters or {"Text/chapter.xhtml": DEFAULT_CHAPTER}
    content = []
    for index, (name, html) in enumerate(chapters.items(), 1):
        item = epub.EpubHtml(title=f"Chapter {index}", file_name=name, lang="en")
        item.content = html
        book.add_item(item)
        content.append(item)
    note = epub.EpubHtml(title="Notes", file_name="Text/notes.xhtml", lang="en")
    note.content = notes
    book.add_item(note)
    book.toc = tuple(content + [note])
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *content, note]
    epub.write_epub(str(path), book, {"mtime": datetime(2020, 1, 1, tzinfo=timezone.utc)})
    return path


def rewrite(path: Path, changes: dict[str, bytes], destination: Path | None = None) -> Path:
    destination = destination or path.with_name(path.stem + "-variant.epub")
    with ZipFile(path) as source, ZipFile(destination, "w") as output:
        output.comment = source.comment
        for info in source.infolist():
            output.writestr(info, changes.get(info.filename, source.read(info)))
    return destination


def approved(plan):
    result = copy.deepcopy(plan)
    for record in result["references"]:
        record["approved"] = True
    return result
