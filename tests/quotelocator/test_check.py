"""The ``readwrench check`` adapter of ``readwrench quote`` (re-verifying saved records)."""

import json
import re
from pathlib import Path

import pytest

from readwrench.check.model import ERROR, HEAD_BYTES, INFO, WARNING, Source
from readwrench.quotelocator._check import (
    CHECKS,
    KIND_ANCHOR,
    KIND_REVIEW,
    check_saved_record,
    check_text_layer,
    classify,
)


def test_classify_saved_records_by_schema_id():
    anchor = b'\xef\xbb\xbf{\n  "schema": "quote-locator.anchor/v1",\n  "id": "urn:sha256:00"'
    assert classify(Path("anything.json"), anchor)[0] == KIND_ANCHOR
    assert classify(Path("r"), b'{"schema":"quote-locator.review/v1"}')[0] == KIND_REVIEW
    # An escaped copy inside a string value, other JSON and binary data are not records.
    escaped = b'{"note": "\\"schema\\": \\"quote-locator.review/v1\\""}'
    assert classify(Path("x.json"), escaped) is None
    assert classify(Path("x.json"), b'[{"schema": "quote-locator.review/v1"}]') is None
    assert classify(Path("x.json"), b'{"schema": "quote-locator.review/v2"}') is None
    assert classify(Path("x.pdf"), b"%PDF-1.7\n\xff\xfe") is None


def test_checks_declare_tool_extra_and_rules():
    assert {check.name for check in CHECKS} == {"quote", "quote-text-layer"}
    for check in CHECKS:
        assert check.tool == "quote" and check.extra == "quote"
        assert check.requires and check.run is not None
        assert all(re.fullmatch(r"[a-z]+(-[a-z]+)*", rule) for rule in check.rules)


@pytest.fixture
def saved(tmp_path):
    for name in ("reanchor", "bs4", "pypdf", "ebooklib", "lxml", "regex", "rapidfuzz"):
        pytest.importorskip(name)
    from readwrench.quotelocator.cli import main

    book = tmp_path / "book.txt"
    book.write_text("Before.\n\nA quotation.\n\nAfter.", encoding="utf-8")
    review, anchor = tmp_path / "review.json", tmp_path / "anchor.json"
    assert main(["find", str(book), "--quote", "A quotation.", "--output", str(review)]) == 0
    assert main(["confirm", str(book), str(review), "--candidate", "1", "--output", str(anchor)]) == 0
    return book, review, anchor


def source(path: Path) -> Source:
    with path.open("rb") as stream:
        kind, label = classify(path, stream.read(HEAD_BYTES))
    return Source(kind, path, path.name, label)


def findings(path: Path):
    return list(check_saved_record(source(path)))


def test_fresh_review_and_locator_have_no_findings(saved):
    _book, review, anchor = saved
    assert findings(review) == []
    assert findings(anchor) == []


def test_changed_source_makes_saved_records_stale(saved):
    book, review, anchor = saved
    book.write_text(book.read_text(encoding="utf-8") + "\n\nA new paragraph.", encoding="utf-8")
    for record in (review, anchor):
        [finding] = findings(record)
        assert (finding.rule, finding.severity) == ("source-changed", WARNING)
        assert finding.file == record.name
        assert "readwrench quote" in finding.hint


def test_moved_folder_is_verified_by_recorded_name(saved, tmp_path):
    moved = tmp_path / "moved"
    moved.mkdir()
    for path in saved:
        path.rename(moved / path.name)
    assert findings(moved / "anchor.json") == []
    assert findings(moved / "review.json") == []


def test_missing_source_is_a_notice_not_an_error(saved):
    book, _review, anchor = saved
    book.unlink()
    [finding] = findings(anchor)
    assert (finding.rule, finding.severity) == ("source-not-found", INFO)
    assert finding.subject == "book.txt"


def test_edited_records_are_inconsistent(saved):
    _book, review, anchor = saved
    data = json.loads(anchor.read_text(encoding="utf-8"))
    data["selection"]["end"] -= 1
    anchor.write_text(json.dumps(data), encoding="utf-8")
    [finding] = findings(anchor)
    assert (finding.rule, finding.severity) == ("record-inconsistent", ERROR)
    data = json.loads(review.read_text(encoding="utf-8"))
    data["candidates"][0]["text"] = "invented"
    review.write_text(json.dumps(data), encoding="utf-8")
    [finding] = findings(review)
    assert (finding.rule, finding.severity) == ("record-inconsistent", ERROR)


def test_unreadable_record_is_reported(saved):
    _book, _review, anchor = saved
    anchor.write_text('{"schema": "quote-locator.anchor/v1", "id": 1, "id": 2}')
    [finding] = findings(anchor)
    assert (finding.rule, finding.severity) == ("invalid-record", ERROR)


def test_pdf_text_layer(saved, tmp_path):
    from pypdf import PdfWriter

    from ._writers import write_pdf

    def run(path):
        return list(check_text_layer(Source("pdf", path, path.name, "PDF document")))

    text = tmp_path / "text.pdf"
    write_pdf(text, ["A quotation.", "Another page."])
    assert run(text) == []
    partial = tmp_path / "partial.pdf"
    write_pdf(partial, ["A quotation.", "", ""])
    [finding] = run(partial)
    assert (finding.rule, finding.severity, finding.pointer) == ("pages-without-text", INFO, "page 2")
    assert "pages 2-3" in finding.message
    scan = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(600, 800)
    writer.write(scan)
    [finding] = run(scan)
    assert (finding.rule, finding.severity) == ("no-text-layer", WARNING)
    locked = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(600, 800)
    writer.encrypt("password")
    writer.write(locked)
    [finding] = run(locked)
    assert (finding.rule, finding.severity) == ("not-searchable", INFO)
