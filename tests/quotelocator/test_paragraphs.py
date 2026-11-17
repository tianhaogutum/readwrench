from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import pytest

from readwrench.quotelocator import load_document, locate


@pytest.mark.parametrize(
    "separator",
    ["\n\n", "\r\n\r\n", "\r\r", "\u2029", "\f", "\u2028\u2028", "\n \t\n\n", "\n\u3000\n"],
)
def test_original_paragraphs_survive_line_ending_variants(tmp_path, separator):
    path = tmp_path / "book.txt"
    path.write_bytes(separator.join(["Before.", "A quotation.", "After."]).encode("utf-8"))
    doc = load_document(path)
    review = locate(doc, "A quotation.")
    context = review["candidates"][0]["context"]
    assert context["before"]["text"] == "Before."
    assert context["containing"][0]["text"] == "A quotation."
    assert context["after"]["text"] == "After."


def test_single_crlf_line_wrap_is_not_a_paragraph_boundary(tmp_path):
    path = tmp_path / "book.txt"
    text = "Before.\r\n\r\nA wrapped\r\nquotation.\r\n\r\nAfter."
    path.write_bytes(text.encode())
    context = locate(load_document(path), "A wrapped quotation.")["candidates"][0]["context"]
    assert context["containing"][0]["text"] == "A wrapped\r\nquotation."
    assert context["before"]["text"] == "Before."
    assert context["after"]["text"] == "After."


def test_leading_separator_in_a_literal_quote_does_not_mislabel_previous_paragraph(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("Before.\n\nA quotation.\n\nAfter.")
    context = locate(load_document(path), "\nA quotation.", profile="strict")["candidates"][0][
        "context"
    ]
    assert context["before"]["text"] == "Before."
    assert [p["text"] for p in context["containing"]] == ["A quotation."]
    assert context["after"]["text"] == "After."
