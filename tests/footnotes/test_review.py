from __future__ import annotations

import pytest

pytest.importorskip("lxml")
pytest.importorskip("ebooklib")

import json

from lxml import etree

from readwrench.footnotes.audit import audit
from readwrench.footnotes.cli import main, read_json, write_json
from readwrench.footnotes.epub import Book, EpubError, excerpt, parse_xml
from readwrench.footnotes.html import preview_html, review_html
from readwrench.footnotes.patch import make_patch
from tests.footnotes.books import DEFAULT_CHAPTER, DEFAULT_NOTES, approved, make_book


def test_kept_return_preview_uses_actual_label_and_language(tmp_path):
    chapter = DEFAULT_CHAPTER.replace('<a epub:type=', '<a id="original-ref" epub:type=')
    notes = DEFAULT_NOTES.replace('</aside>', '<p><a href="chapter.xhtml#original-ref" lang="fr">Retour original</a></p></aside>')
    path = make_book(tmp_path / "source.epub", {"Text/chapter.xhtml": chapter}, notes)
    with Book(path) as book:
        fresh = audit(book, {"label_template": "New proposed label {number}", "label_lang": "en"})
        patch = make_patch(book, fresh, approved(fresh.plan))
        assert patch.changes == {}
        route = patch.manifest["paths"][0]
        assert route["label"] == "Retour original"
        assert route["return_language"] == "fr"
        assert route["return_href"] == "chapter.xhtml#original-ref"
        output = preview_html(patch.manifest, fresh.plan)
        assert 'lang="fr">Retour original</a>' in output
        assert "New proposed label" not in output


def test_kept_ancestor_return_remains_navigable_and_is_identified_as_surrounding(tmp_path):
    chapter = DEFAULT_CHAPTER.replace('<p>', '<p id="paragraph">')
    notes = DEFAULT_NOTES.replace('</aside>', '<p><a href="chapter.xhtml#paragraph">Original nearby return</a></p></aside>')
    source = make_book(tmp_path / "source.epub", {"Text/chapter.xhtml": chapter}, notes)
    with Book(source) as book:
        fresh = audit(book)
        decisions = approved(fresh.plan)
        decisions["references"][0]["action"] = "keep"
        patch = make_patch(book, fresh, decisions)
        assert patch.changes == {}
        assert patch.manifest["paths"][0]["result"] == "kept-ancestor-return"
        output = preview_html(patch.manifest, fresh.plan)
        assert 'Original nearby return</a>' in output
        assert 'surrounding element' in output


def test_catalog_exposes_wrong_and_broken_existing_returns(tmp_path):
    chapter = DEFAULT_CHAPTER + '<p id="unrelated">Somewhere unrelated.</p>'
    notes = DEFAULT_NOTES.replace('</aside>', '<p><a role="doc-backlink" href="chapter.xhtml#unrelated">Wrong return</a> <a href="missing.xhtml#n">Broken unmarked link</a></p></aside>')
    source = make_book(tmp_path / "catalog.epub", {"Text/chapter.xhtml": chapter}, notes)
    with Book(source) as book:
        fresh = audit(book)
        assert fresh.plan["references"][0]["status"] == "missing-return"
        note = next(n for n in fresh.plan["notes"] if n["links"])
        assert len(note["links"]) == 2
        assert note["links"][0]["destination"]["id"] == "unrelated"
        assert note["links"][1]["target"]["error"] == "missing-target-file"
        html = review_html(fresh.plan)
        assert "Wrong return" in html and "Broken unmarked link" in html
        assert 'href="missing.xhtml' not in html
        patch = make_patch(book, fresh, approved(fresh.plan))
        assert b'Wrong return</a>' in patch.changes["EPUB/Text/notes.xhtml"]


def test_catalog_is_rendered_once_for_a_shared_note(tmp_path):
    chapter = DEFAULT_CHAPTER + DEFAULT_CHAPTER
    notes = DEFAULT_NOTES.replace('</aside>', '<p><a href="chapter.xhtml#unrelated">Unique catalog label</a></p></aside>')
    source = make_book(tmp_path / "shared.epub", {"Text/chapter.xhtml": chapter}, notes)
    with Book(source) as book:
        plan = audit(book).plan
    tree = etree.HTML(review_html(plan))
    # One outgoing-link catalog section; row previews may include note prose.
    catalogs = tree.xpath('//section[starts-with(@id,"catalog-")]')
    assert len(catalogs) == 1
    assert len(catalogs[0].xpath('./ul[1]/li')) == 1
    assert len(catalogs[0].xpath('./ul[2]/li/a')) == 2
    assert len(tree.xpath('//article')) == 2


def test_named_entity_excerpt_does_not_double_decode_literal_prose():
    data = b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body><p>Literal &amp;nbsp; / &nbsp;&mdash;<!--hidden--> tail</p></body></html>'
    root = parse_xml(data, "entities.xhtml")
    p = root.find('.//{http://www.w3.org/1999/xhtml}p')
    assert excerpt(p) == "Literal &nbsp; / — tail"


def test_review_marks_context_and_new_link_languages(tmp_path):
    chapter = '<div lang="zh-Hans">' + DEFAULT_CHAPTER + '</div>'
    source = make_book(tmp_path / "source.epub", {"Text/chapter.xhtml": chapter})
    with Book(source) as book:
        plan = audit(book, {"label_template": "返回 {number}", "label_lang": "zh-Hans"}).plan
    tree = etree.HTML(review_html(plan))
    assert tree.xpath('//article//blockquote')[0].get("lang") == "zh-Hans"
    assert tree.xpath('//input[@class="label"]')[0].get("lang") == "zh-Hans"


def test_source_fingerprint_covers_the_loading_interval(tmp_path, monkeypatch):
    path = make_book(tmp_path / "source.epub")
    original_read = Book.read
    changed = False

    def read(self, name):
        nonlocal changed
        result = original_read(self, name)
        if name.endswith("notes.xhtml") and not changed:
            with self.path.open("ab") as output:
                output.write(b"changed during loading")
            changed = True
        return result

    monkeypatch.setattr(Book, "read", read)
    with pytest.raises(EpubError, match="changed during processing"):
        Book(path)


@pytest.mark.parametrize("options", [[], "", False, 0])
def test_empty_nonobject_scan_options_are_rejected(tmp_path, options):
    source = make_book(tmp_path / "source.epub")
    with Book(source) as book, pytest.raises(EpubError, match="Invalid scan options"):
        audit(book, options)


def test_utf8_bom_review_json_is_accepted(tmp_path):
    path = tmp_path / "review.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8-sig")
    assert read_json(path)["schema_version"] == 1


def test_review_output_size_limit_is_enforced_before_writing(tmp_path):
    output = tmp_path / "plan.json"
    with pytest.raises(EpubError, match="Review output exceeds"):
        write_json(output, {"text": "long text"}, max_bytes=5)
    assert not output.exists()


def test_terminal_review_refuses_to_approve_an_unresolved_add(tmp_path, monkeypatch):
    source = make_book(tmp_path / "source.epub", {"Text/chapter.xhtml": DEFAULT_CHAPTER.replace("#note-1", "#missing")})
    with Book(source) as book:
        plan = audit(book).plan
    path, output = tmp_path / "plan.json", tmp_path / "decisions.json"
    path.write_text(json.dumps(plan))
    answers = iter(["a", "s"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert main(["review", str(path), "--out", str(output)]) == 0
    assert json.loads(output.read_text())["references"][0]["action"] == "skip"
