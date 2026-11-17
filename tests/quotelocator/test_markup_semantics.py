from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import logging

import pytest

from readwrench.quotelocator import load_document, locate
from readwrench.quotelocator.cli import main
from readwrench.quotelocator.documents import _html_text, _pdf_messages


@pytest.mark.parametrize(
    "markup",
    [
        "<!doctype html><p>Actual quotation.</p>",
        '<?xml version="1.0" encoding="UTF-8"?><p>Actual quotation.</p>',
        "<!doctype html><!-- comment --><?instruction hidden?><p>Actual quotation.</p>",
    ],
)
def test_declarations_are_not_citable_text(markup):
    assert _html_text(markup.encode()) == "Actual quotation."


@pytest.mark.parametrize("prefix", ["", "x:"])
def test_ruby_annotations_do_not_interrupt_the_base_quotation(prefix):
    markup = (
        f"<{prefix}p>山路を<{prefix}ruby>登<{prefix}rp>(</{prefix}rp>"
        f"<{prefix}rt>のぼ</{prefix}rt><{prefix}rp>)</{prefix}rp></{prefix}ruby>"
        f"りながら、こう考えた。</{prefix}p>"
    )
    assert _html_text(markup.encode()) == "山路を登りながら、こう考えた。"


def test_hidden_ancestors_and_adjacent_table_cells():
    markup = b"<div hidden><p>Not citable</p></div><table><tr><td>10</td><td>20</td></tr></table>"
    assert _html_text(markup) == "10 20"


def test_pdf_recovery_warnings_are_retained_and_handlers_cleaned_up():
    logger = logging.getLogger("pypdf")
    before = list(logger.handlers)
    with _pdf_messages() as messages:
        logging.getLogger("pypdf.test").warning("Further form content skipped")
    assert messages == ["PDF extraction warning: Further form content skipped"]
    assert logger.handlers == before


def test_clipped_context_is_explicit_in_human_output(tmp_path, capsys):
    path = tmp_path / "book.txt"
    path.write_text(
        "Before paragraph.\n\n"
        + "prefix " * 100
        + "quotation"
        + " suffix" * 100
        + "\n\nAfter paragraph."
    )
    assert main(["find", str(path), "--quote", "quotation"]) == 0
    output = capsys.readouterr().out
    assert "context clipped before" in output and "context clipped after" in output
    review = locate(load_document(path), "quotation")
    context = review["candidates"][0]["context"]["containing"][0]
    assert context["text"] == load_document(path).text[context["start"] : context["end"]]
