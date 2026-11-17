from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import json
import os

import pytest

from readwrench.quotelocator.cli import _read_record, _write_json, main


@pytest.mark.parametrize("content", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "[]"])
def test_records_reject_ambiguous_or_nonstandard_json(tmp_path, content):
    path = tmp_path / "record.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        _read_record(path)


def test_terminal_controls_are_visible_while_json_preserves_text(tmp_path, capsys):
    path = tmp_path / "book.txt"
    text = "Before.\n\nA quote\x1b[2J\u202e.\n\nAfter."
    path.write_text(text, encoding="utf-8")
    assert main(["find", str(path), "--quote", "A quote"]) == 0
    human = capsys.readouterr().out
    assert "\x1b" not in human and "\u202e" not in human
    assert "\\u001b" in human and "\\u202e" in human
    assert main(["find", str(path), "--quote", "A quote", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["candidates"][0]["context"]["containing"][0]["text"] == "A quote\x1b[2J\u202e."


def test_quote_file_preserves_internal_crlf_and_accepts_utf8_bom(tmp_path, capsys):
    path = tmp_path / "book.txt"
    path.write_bytes(b"Before\r\nA multiline\r\nquotation.\r\nAfter")
    quote = tmp_path / "quote.txt"
    quote.write_bytes(b"\xef\xbb\xbfA multiline\r\nquotation.\r\n")
    assert (
        main(["find", str(path), "--quote-file", str(quote), "--profile", "strict", "--json"]) == 0
    )
    record = json.loads(capsys.readouterr().out)
    assert record["candidates"][0]["text"] == "A multiline\r\nquotation."


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named-pipe check")
def test_quote_file_cannot_block_on_a_named_pipe(tmp_path, capsys):
    source = tmp_path / "book.txt"
    source.write_text("A quote")
    pipe = tmp_path / "pipe"
    os.mkfifo(pipe)
    assert main(["find", str(source), "--quote-file", str(pipe)]) == 2
    assert "regular file" in capsys.readouterr().err


def test_atomic_output_failure_preserves_existing_file_and_cleans_temporary(tmp_path, monkeypatch):
    output = tmp_path / "review.json"
    output.write_text("original")

    def fail_replace(*args):
        raise OSError("Simulated full disk / replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        _write_json(output, {"new": "data"}, force=True, protected=[])
    assert output.read_text() == "original"
    assert list(tmp_path.glob(".quote-locator-*")) == []


def test_record_size_failure_is_atomic(tmp_path, monkeypatch):
    from readwrench.quotelocator import cli

    monkeypatch.setattr(cli, "MAX_RECORD_BYTES", 100)
    output = tmp_path / "review.json"
    with pytest.raises(ValueError, match="exceeds"):
        _write_json(output, {"data": "x" * 200}, force=False, protected=[])
    assert not output.exists()
    assert list(tmp_path.glob(".quote-locator-*")) == []


def test_existing_output_is_private_and_symlink_source_is_protected(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("A quote")
    output = tmp_path / "review.json"
    _write_json(output, {"data": "private"}, force=False, protected=[source])
    assert output.stat().st_mode & 0o077 == 0
    link = tmp_path / "link.json"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="overwrite"):
        _write_json(link, {}, force=True, protected=[source])
    assert source.read_text() == "A quote"
