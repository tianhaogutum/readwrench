"""Regenerate ``fixtures/sigil-linked-epub2.epub`` from the published Sigil sample.

This is an explicit test adaptation, not a claimed run of the Sigil FootnoteLinker
plugin. It keeps the real EPUB 2 package/NCX/DTD structure of
``fixtures/upstream/FNLTest.epub`` and adds forward links and note IDs through lxml.
The upstream sample itself stays unchanged. Run it by hand from the repository root:

    python -m tests.footnotes.make_epub2_fixture
"""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from readwrench.footnotes.epub import NS, XHTML, parse_xml
from tests.footnotes.books import FIXTURES, rewrite


def make_epub2(destination: Path) -> Path:
    source = FIXTURES / "upstream" / "FNLTest.epub"
    changes = {}
    with ZipFile(source) as archive:
        for name in ("OEBPS/Text/chapter.xhtml", "OEBPS/Text/endnotes.xhtml"):
            root = parse_xml(archive.read(name), name)
            tree = root.getroottree()
            if "chapter" in name:
                for index, marker in enumerate(root.xpath('//x:sup[@class="noteanchor"]', namespaces=NS), 1):
                    anchor = etree.SubElement(marker, f"{{{XHTML}}}a")
                    anchor.set("href", f"endnotes.xhtml#note-{index}")
                    anchor.text = marker.text
                    marker.text = None
            else:
                for index, note in enumerate(root.xpath('//x:p[@class="note"]', namespaces=NS), 1):
                    note.set("id", f"note-{index}")
                root.xpath('//x:p[@class="note"]', namespaces=NS)[0].text += " — entity variant"
            data = etree.tostring(tree, xml_declaration=True, encoding="utf-8", doctype=tree.docinfo.doctype)
            # Real XHTML 1.1 DTD named entities; parser positions must preserve
            # these bytes instead of expanding/reformatting the source document.
            data = data.replace(" ".encode(), b"&nbsp;").replace("—".encode(), b"&mdash;")
            changes[name] = data
    return rewrite(source, changes, destination)


if __name__ == "__main__":
    print(make_epub2(FIXTURES / "sigil-linked-epub2.epub"))
