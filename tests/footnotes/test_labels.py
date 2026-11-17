from __future__ import annotations

import pytest

pytest.importorskip("lxml")
pytest.importorskip("ebooklib")


from lxml import etree

from readwrench.footnotes.audit import audit
from readwrench.footnotes.epub import Book, EpubError, NS, parse_xml
from readwrench.footnotes.patch import make_patch
from tests.footnotes.books import FIXTURES, approved, make_book


def test_localized_label_template_and_language_are_navigation_only(tmp_path):
    source = make_book(tmp_path / "source.epub")
    with Book(source) as book:
        fresh = audit(book, {"label_template": "返回引用 {number}（{chapter}）", "label_lang": "zh-Hans"})
        patch = make_patch(book, fresh, approved(fresh.plan))
        root = etree.fromstring(patch.changes["EPUB/Text/notes.xhtml"])
        link = root.xpath('//x:a[@class="fnrt-backlink"]', namespaces=NS)[0]
        assert link.text.startswith("返回引用 1")
        assert link.get("lang") == "zh-Hans"
        assert link.get("{http://www.w3.org/XML/1998/namespace}lang") == "zh-Hans"
        assert "返回引用" not in book.documents["EPUB/Text/notes.xhtml"].data.decode()


def test_epub2_language_uses_supported_xml_lang():
    with Book(FIXTURES / "sigil-linked-epub2.epub") as book:
        fresh = audit(book, {"label_template": "返回引用 {number}", "label_lang": "zh"})
        patch = make_patch(book, fresh, approved(fresh.plan))
        root = parse_xml(patch.changes["OEBPS/Text/endnotes.xhtml"], "epub2-notes")
        for link in root.xpath('//x:a[@class="fnrt-backlink"]', namespaces=NS):
            assert link.get("lang") is None
            assert link.get("{http://www.w3.org/XML/1998/namespace}lang") == "zh"


@pytest.mark.parametrize("template", ["Return", "{chapter}", "{number.__class__}", "{number:1000000000}", "{number!r}", "{number", [], "{unknown} {number}"])
def test_label_templates_do_not_accept_arbitrary_format_expressions(tmp_path, template):
    source = make_book(tmp_path / "source.epub")
    with Book(source) as book:
        with pytest.raises(EpubError, match="label|Return"):
            audit(book, {"label_template": template})


@pytest.mark.parametrize("language", ["", 'en" role="x', "中文", "x" * 64, [], 1])
def test_label_language_is_validated_before_xml(tmp_path, language):
    source = make_book(tmp_path / "source.epub")
    with Book(source) as book:
        with pytest.raises(EpubError, match="language tag"):
            audit(book, {"label_lang": language})
