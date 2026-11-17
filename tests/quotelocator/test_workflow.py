from __future__ import annotations

from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import copy
import json

import pytest

from readwrench.quotelocator import confirm, load_document, locate, verify
from readwrench.quotelocator.cli import main


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text(
        "Before first.\n\nA fine quote.\n\nAfter first.\n\n"
        "Before second.\n\nA ﬁne quote.\n\nAfter second.",
        encoding="utf-8",
    )
    return path


def test_exact_and_normalized_candidates_are_both_kept(source):
    review = locate(load_document(source), "A fine quote.")
    assert review["status"] == "ambiguous"
    assert [item["method"] for item in review["candidates"]] == ["exact", "normalized"]
    second = review["candidates"][1]
    assert second["context"]["before"]["text"] == "Before second."
    assert second["context"]["after"]["text"] == "After second."
    assert len(second["differences"]) == 1
    change = second["differences"][0]
    assert (change["operation"], change["query"], change["source"]) == ("replace", "fi", "ﬁ")
    assert change["query_start"] == change["source_start"] == 2


def test_confirmation_requires_explicit_selection_and_rechecks_source(source):
    document = load_document(source)
    review = locate(document, "A fine quote.")
    assert review["confirmed"] is False
    anchor = confirm(document, review, 2)
    assert anchor["confirmed"] is True
    assert anchor["selection"]["text"] == "A ﬁne quote."
    assert verify(document, anchor)["valid"] is True
    source.write_text(source.read_text() + "\nnew revision", encoding="utf-8")
    assert verify(load_document(source), anchor)["valid"] is False
    with pytest.raises(ValueError, match="Source bytes changed"):
        confirm(load_document(source), review, 2)


def test_tampered_review_and_anchor_fail(source):
    document = load_document(source)
    review = locate(document, "A fine quote.")
    broken = copy.deepcopy(review)
    broken["candidates"][0]["text"] = "invented"
    with pytest.raises(ValueError, match="modified"):
        confirm(document, broken, 1)
    anchor = confirm(document, review, 1)
    for section in ["selection", "annotation"]:
        broken_anchor = copy.deepcopy(anchor)
        broken_anchor[section]["start"] = 999
        assert not verify(document, broken_anchor)["valid"]


def test_no_fuzzy_numeric_claim_or_invented_anchor(tmp_path):
    path = tmp_path / "paper.txt"
    path.write_text("The evidence says 10 percent.")
    doc = load_document(path)
    review = locate(doc, "The evidence says 20 percent.")
    assert review["status"] == "unmatched"
    with pytest.raises(ValueError, match="Candidate does not exist"):
        confirm(doc, review, 1)


def test_strict_case_and_accents_are_explicit(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("Café ﬁne\n\nquote.", encoding="utf-8")
    doc = load_document(path)
    assert locate(doc, "Cafe fine quote.")["status"] == "unmatched"
    assert locate(doc, "cafe fine quote.", profile="loose")["status"] == "unique"
    assert locate(doc, "Café fine quote.", profile="strict")["status"] == "unmatched"


def test_partial_compatibility_expansion_is_not_a_quote(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("ﬁ", encoding="utf-8")
    doc = load_document(path)
    assert locate(doc, "f")["status"] == "unmatched"
    assert locate(doc, "fi")["candidates"][0]["text"] == "ﬁ"


def test_cli_roundtrip_and_no_overwrite(source, tmp_path, capsys):
    review_path, anchor_path = tmp_path / "review.json", tmp_path / "anchor.json"
    assert (
        main(["find", str(source), "--quote", "A fine quote.", "--output", str(review_path)]) == 0
    )
    assert (
        main(
            [
                "confirm",
                str(source),
                str(review_path),
                "--candidate",
                "2",
                "--output",
                str(anchor_path),
            ]
        )
        == 0
    )
    assert main(["verify", str(source), str(anchor_path), "--json"]) == 0
    assert '"valid": true' in capsys.readouterr().out
    before = anchor_path.read_bytes()
    assert (
        main(
            [
                "confirm",
                str(source),
                str(review_path),
                "--candidate",
                "1",
                "--output",
                str(anchor_path),
            ]
        )
        == 2
    )
    assert anchor_path.read_bytes() == before
    assert (
        main(["find", str(source), "--quote", "A fine quote.", "--output", str(source), "--force"])
        == 2
    )


def test_moved_identical_source_verifies(source, tmp_path):
    doc = load_document(source)
    anchor = confirm(doc, locate(doc, "A fine quote."), 1)
    moved = tmp_path / "moved.txt"
    moved.write_bytes(source.read_bytes())
    assert verify(load_document(moved), anchor)["valid"] is True


@pytest.mark.parametrize("quote", ["", "  \n\t", "x" * 10_001])
def test_invalid_quotes(source, quote):
    with pytest.raises(ValueError):
        locate(load_document(source), quote)


def test_malformed_json_error(source, tmp_path, capsys):
    record = tmp_path / "bad.json"
    record.write_text("[]")
    assert main(["verify", str(source), str(record)]) == 2
    assert "JSON object" in capsys.readouterr().err


def test_overlapping_matches_and_emoji_codepoints(tmp_path):
    path = tmp_path / "emoji.txt"
    path.write_text("😀 ababa", encoding="utf-8")
    result = locate(load_document(path), "aba")
    assert [(c["start"], c["end"]) for c in result["candidates"]] == [(2, 5), (4, 7)]


def test_anchor_is_json_roundtrip_stable(source):
    doc = load_document(source)
    anchor = confirm(doc, locate(doc, "A fine quote."), 2)
    assert verify(doc, json.loads(json.dumps(anchor)))["valid"]
