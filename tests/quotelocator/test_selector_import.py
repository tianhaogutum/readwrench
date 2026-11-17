from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import copy
import json

import pytest
from reanchor import TextQuoteSelector

from readwrench.quotelocator import confirm, import_selector, load_document, locate, verify
from readwrench.quotelocator.cli import main


def test_w3c_quote_context_keeps_all_occurrences(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("Introduction: A quotation. Early.\n\nBody: A quotation. Later.")
    selector = import_selector(
        {
            "type": "TextQuoteSelector",
            "exact": "A quotation.",
            "prefix": "Body: ",
            "suffix": " Later.",
        }
    )
    doc = load_document(path)
    review = locate(doc, selector)
    assert review["candidate_count"] == 2 and review["status"] == "ambiguous"
    assert [c["context_evidence"]["agrees"] for c in review["candidates"]] == [False, True]
    anchor = confirm(doc, review, 2)
    assert anchor["query"]["prefix"] == "Body: "
    assert verify(doc, anchor)["valid"]


@pytest.mark.parametrize("selector_shape", ["object", "array"])
def test_w3c_annotation_ignores_stale_positions_and_does_not_fetch_source(tmp_path, selector_shape):
    path = tmp_path / "book.txt"
    path.write_text("Before. A quotation. After.")
    selectors = [
        {"type": "TextPositionSelector", "start": 99999, "end": 100000},
        {"type": "TextQuoteSelector", "exact": "A quotation.", "prefix": "Before. "},
    ]
    record = {
        "type": "Annotation",
        "target": {
            "source": "https://invalid.example/not-fetched",
            "selector": selectors if selector_shape == "array" else selectors[1],
        },
    }
    review = locate(load_document(path), import_selector(record))
    assert review["candidate_count"] == 1
    assert review["candidates"][0]["start"] == 8


def test_readium_text_locator_import(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("Before. A quotation. After.")
    selector = import_selector(
        {
            "href": "Text/chapter.xhtml",
            "type": "application/xhtml+xml",
            "locations": {"position": 900},
            "text": {"before": "Before. ", "highlight": "A quotation.", "after": " After."},
        }
    )
    review = locate(load_document(path), selector)
    assert review["candidates"][0]["context_evidence"]["agrees"]


def test_own_exported_w3c_annotation_roundtrip(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("Before. A ﬁne quotation. After.", encoding="utf-8")
    doc = load_document(path)
    anchor = confirm(doc, locate(doc, "A fine quotation."), 1)
    imported = import_selector(anchor["annotation"])
    review = locate(doc, imported)
    assert review["candidates"][0]["context_evidence"]["agrees"]
    assert review["candidates"][0]["text"] == anchor["selection"]["text"]


def test_saved_context_mismatch_is_not_hidden_or_converted_to_unmatched(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("The source has A quotation. with different context.")
    selector = TextQuoteSelector(exact="A quotation.", prefix="Missing context")
    doc = load_document(path)
    review = locate(doc, selector)
    assert review["status"] == "unique"
    assert review["candidates"][0]["context_evidence"]["agrees"] is False
    anchor = confirm(doc, review, 1)
    assert verify(doc, anchor)["valid"]


def test_context_uses_the_chosen_profile_and_unicode_offsets(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("😀 Café\n\nA ﬁne quotation.\n\nAfter.", encoding="utf-8")
    doc = load_document(path)
    selector = TextQuoteSelector(exact="A fine quotation.", prefix="cafe ", suffix=" after.")
    assert locate(doc, selector)["candidates"][0]["context_evidence"]["agrees"] is False
    loose = locate(doc, selector, profile="loose")
    assert loose["candidates"][0]["context_evidence"]["agrees"] is True


@pytest.mark.parametrize(
    "record",
    [
        {"type": "TextQuoteSelector", "exact": "quote", "prefix": None},
        {"type": "TextQuoteSelector", "exact": ""},
        {"type": "Annotation", "target": []},
        {
            "type": "Annotation",
            "target": [
                {
                    "selector": [
                        {"type": "TextQuoteSelector", "exact": "one"},
                        {"type": "TextQuoteSelector", "exact": "two"},
                    ]
                }
            ],
        },
        {"href": "book", "text": {"highlight": 42}},
        {"type": "TextPositionSelector", "start": 0, "end": 3},
    ],
)
def test_invalid_and_compound_selector_inputs_fail_without_guessing(record):
    with pytest.raises(ValueError):
        import_selector(record)


def test_cli_selector_input_and_input_file_protection(tmp_path, capsys):
    source = tmp_path / "book.txt"
    source.write_text("Before. A quotation. After.")
    selector = tmp_path / "selector.json"
    selector.write_text(
        json.dumps({"type": "TextQuoteSelector", "exact": "A quotation.", "prefix": "Before. "})
    )
    assert main(["find", str(source), "--selector", str(selector), "--json"]) == 0
    review = json.loads(capsys.readouterr().out)
    assert review["candidates"][0]["context_evidence"]["agrees"]
    assert (
        main(
            ["find", str(source), "--selector", str(selector), "--output", str(selector), "--force"]
        )
        == 2
    )
    assert json.loads(selector.read_text())["type"] == "TextQuoteSelector"


def test_context_metadata_tampering_is_rejected(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("Before. A quotation. After.")
    doc = load_document(source)
    review = locate(doc, TextQuoteSelector(exact="A quotation.", prefix="Before. "))
    anchor = confirm(doc, review, 1)
    broken = copy.deepcopy(anchor)
    broken["selection"]["context_evidence"]["agrees"] = False
    assert not verify(doc, broken)["valid"]


def test_invalid_api_context_type_or_size_is_rejected(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("A quotation.")
    doc = load_document(source)
    for prefix in [False, "x" * 4097]:
        with pytest.raises(ValueError):
            locate(doc, TextQuoteSelector(exact="A quotation.", prefix=prefix))


def test_literal_context_survives_cross_boundary_dehyphenation(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("A hyphen-\nated word.")
    review = locate(load_document(source), TextQuoteSelector(exact="ated", prefix="hyphen-\n"))
    assert review["candidates"][0]["context_evidence"]["prefix_matches"] is True


def test_compound_targets_with_one_position_only_fragment_are_not_silently_dropped():
    record = {
        "type": "Annotation",
        "target": [
            {"selector": {"type": "TextQuoteSelector", "exact": "one"}},
            {"selector": {"type": "TextPositionSelector", "start": 10, "end": 20}},
        ],
    }
    with pytest.raises(ValueError, match="exactly one target"):
        import_selector(record)
