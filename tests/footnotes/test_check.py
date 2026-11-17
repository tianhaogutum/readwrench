"""The read-only ``readwrench check`` adapter for footnote links."""

from __future__ import annotations

import pytest

pytest.importorskip("lxml")
pytest.importorskip("ebooklib")

from readwrench.check.model import ERROR, INFO, WARNING, Source
from readwrench.footnotes._check import CHECKS, check_footnotes
from tests.footnotes.books import DEFAULT_CHAPTER, DEFAULT_NOTES, FIXTURES, make_book


def run(path):
    source = Source(kind="epub", path=path, display=path.name, label="EPUB")
    return list(check_footnotes(source))


def by_rule(findings):
    return {finding.rule: finding for finding in findings}


def test_check_is_registered_for_epub_inputs():
    (check,) = CHECKS
    assert check.name == "footnotes"
    assert check.kinds == frozenset({"epub"})
    assert check.requires == ("lxml",)
    assert check.extra == "footnotes"


def test_missing_return_link_is_a_warning_with_a_scan_hint(tmp_path):
    path = make_book(tmp_path / "book.epub")
    before = sorted(tmp_path.iterdir())
    findings = run(path)
    assert [f.rule for f in findings] == ["no-return-link"]
    finding = findings[0]
    assert finding.severity == WARNING
    assert finding.file == "book.epub"
    assert finding.pointer.startswith("EPUB/Text/chapter.xhtml")
    assert finding.hint.startswith("readwrench footnotes scan book.epub --out ")
    # Read-only: no plan, review page or candidate is written.
    assert sorted(tmp_path.iterdir()) == before


def test_exact_return_produces_no_finding(tmp_path):
    chapter = DEFAULT_CHAPTER.replace('<a epub:type=', '<a id="original-ref" epub:type=')
    notes = DEFAULT_NOTES.replace('</aside>', '<p><a href="chapter.xhtml#original-ref">Back</a></p></aside>')
    assert run(make_book(tmp_path / "exact.epub", {"Text/chapter.xhtml": chapter}, notes)) == []


def test_broken_and_ambiguous_note_links(tmp_path):
    broken = make_book(tmp_path / "broken.epub",
                       {"Text/chapter.xhtml": DEFAULT_CHAPTER.replace("notes.xhtml#note-1", "notes.xhtml#absent")})
    assert by_rule(run(broken))["broken-note-link"].severity == ERROR
    duplicate = make_book(tmp_path / "duplicate.epub",
                          notes=DEFAULT_NOTES.replace('</section>', '<p id="note-1">Another note</p></section>'))
    rules = by_rule(run(duplicate))
    assert rules["ambiguous-note-link"].severity == WARNING
    assert rules["duplicate-id"].severity == WARNING


def test_broken_backlink_is_reported(tmp_path):
    notes = DEFAULT_NOTES.replace('</aside>', '<p><a role="doc-backlink" href="chapter.xhtml#gone">Back</a></p></aside>')
    rules = by_rule(run(make_book(tmp_path / "backlink.epub", notes=notes)))
    assert rules["broken-return-link"].severity == WARNING
    assert "no-return-link" in rules


def test_unlinked_sigil_markers_are_info():
    findings = run(FIXTURES / "upstream" / "FNLTest.epub")
    assert [(f.rule, f.severity) for f in findings] == [("unlinked-note-marker", INFO)]
    assert findings[0].message.startswith("4 superscript note markers")


def test_unreadable_epub_is_one_info_finding(tmp_path):
    path = tmp_path / "not-a-book.epub"
    path.write_bytes(b"PK\x03\x04 truncated")
    findings = run(path)
    assert [(f.rule, f.severity) for f in findings] == [("not-inspected", INFO)]
