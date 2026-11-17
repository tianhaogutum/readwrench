from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import copy
import json

import pytest

from readwrench.quotelocator import confirm, load_document, locate, verify
from readwrench.quotelocator.cli import main


def test_every_ambiguous_candidate_is_reachable_and_confirmable(tmp_path):
    path = tmp_path / "repeated.txt"
    path.write_text("A repeated quotation.\n\n" * 17)
    doc = load_document(path)
    seen = []
    offset = 0
    while offset is not None:
        review = locate(doc, "A repeated quotation.", offset=offset, limit=4)
        assert review["status"] == "ambiguous"
        assert review["candidate_count"] == 17
        assert review["truncated"]
        seen.extend(c["index"] for c in review["candidates"])
        for candidate in review["candidates"]:
            anchor = confirm(doc, review, candidate["id"])
            assert verify(doc, anchor)["valid"]
        offset = review["window"]["next_offset"]
    assert seen == list(range(1, 18))


def test_payload_budget_paginates_without_hiding_total_count(tmp_path, monkeypatch):
    from readwrench.quotelocator import matching

    monkeypatch.setattr(matching, "MAX_REVIEW_PAYLOAD", 3000)
    path = tmp_path / "book.txt"
    path.write_text("Paragraph before.\n\nA repeated quotation.\n\nParagraph after.\n\n" * 30)
    doc = load_document(path)
    review = locate(doc, "A repeated quotation.")
    assert review["candidate_count"] == 30
    assert 1 <= len(review["candidates"]) < 30
    assert review["window"]["payload_limited"]
    next_page = locate(doc, "A repeated quotation.", offset=review["window"]["next_offset"])
    assert next_page["candidates"][0]["index"] == len(review["candidates"]) + 1


@pytest.mark.parametrize("offset,limit", [(-1, 2), (True, 2), (0, 0), (0, 1001), (0, False)])
def test_invalid_window(tmp_path, offset, limit):
    path = tmp_path / "book.txt"
    path.write_text("A quote")
    with pytest.raises(ValueError):
        locate(load_document(path), "quote", offset=offset, limit=limit)


def test_cli_window_keeps_global_candidate_indices(tmp_path, capsys):
    path = tmp_path / "book.txt"
    path.write_text("quote " * 12)
    assert (
        main(["find", str(path), "--quote", "quote", "--offset", "10", "--limit", "1", "--json"])
        == 0
    )
    review = json.loads(capsys.readouterr().out)
    assert review["candidates"][0]["index"] == 11
    anchor = confirm(load_document(path), review, 11)
    assert verify(load_document(path), anchor)["valid"]
    broken = copy.deepcopy(anchor)
    broken["review_summary"]["window"]["offset"] = 0
    assert not verify(load_document(path), broken)["valid"]


def test_empty_window_is_not_reported_as_unmatched(tmp_path, capsys):
    path = tmp_path / "book.txt"
    path.write_text("quote")
    assert main(["find", str(path), "--quote", "quote", "--offset", "10"]) == 0
    output = capsys.readouterr().out
    assert "UNIQUE" in output and "smaller --offset" in output
    assert "No supported textual occurrence" not in output
