from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import copy

import pytest

from readwrench.quotelocator import confirm, load_document, locate, verify


@pytest.fixture
def record(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("A quotation")
    doc = load_document(path)
    review = locate(doc, "A quotation")
    return doc, review, confirm(doc, review, 1)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("selection", "start", False),
        ("selection", "end", 11.0),
        ("document", "characters", 11.0),
        ("selector_status", "matching_contexts", True),
    ],
)
def test_number_boolean_and_float_tampering_is_not_equivalent(record, section, key, value):
    doc, _review, anchor = record
    broken = copy.deepcopy(anchor)
    broken[section][key] = value
    assert verify(doc, broken)["valid"] is False


def test_w3c_position_boolean_is_invalid(record):
    doc, _review, anchor = record
    anchor["annotation"]["target"]["selector"][1]["start"] = False
    assert verify(doc, anchor)["valid"] is False


def test_review_boolean_offset_tampering_fails(record):
    doc, review, _anchor = record
    review["candidates"][0]["start"] = False
    with pytest.raises(ValueError, match="modified"):
        confirm(doc, review, 1)


def test_conflicting_confirmed_review_flag_is_rejected(record):
    doc, review, _anchor = record
    review["confirmed"] = True
    with pytest.raises(ValueError, match="unconfirmed"):
        confirm(doc, review, 1)


def test_original_uri_is_a_typed_local_hint(record):
    doc, _review, anchor = record
    anchor["document"]["uri"] = []
    anchor["readium"][0]["href"] = []
    assert verify(doc, anchor)["valid"] is False


def test_modified_other_candidate_cannot_be_hidden_in_confirmed_review(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("A quotation.\n\nA quotation.")
    doc = load_document(path)
    review = locate(doc, "A quotation.")
    review["candidates"][1]["text"] = "fabricated second occurrence"
    with pytest.raises(ValueError, match="modified"):
        confirm(doc, review, 1)


def test_locator_identity_is_stable_but_distinct_annotation_bodies_have_distinct_ids(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("A ﬁne quotation.", encoding="utf-8")
    doc = load_document(path)
    first = confirm(doc, locate(doc, "fine"), 1)
    second = confirm(doc, locate(doc, "ﬁne"), 1)
    assert first["id"] == second["id"]
    assert first["annotation"]["id"] != second["annotation"]["id"]
    assert verify(doc, first)["valid"] and verify(doc, second)["valid"]
