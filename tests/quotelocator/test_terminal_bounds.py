from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import json

from readwrench.quotelocator.cli import main


def test_long_source_and_diff_previews_are_explicit_and_json_remains_complete(tmp_path, capsys):
    path = tmp_path / "book.txt"
    text = "a" + "\u200b" * 6000 + " b"
    path.write_text(text, encoding="utf-8")
    assert main(["find", str(path), "--quote", "a b"]) == 0
    output = capsys.readouterr().out
    assert "Source preview clipped" in output and "full value in JSON" in output
    assert len(output) < 40_000
    assert main(["find", str(path), "--quote", "a b", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["candidates"][0]["text"] == text
    assert record["candidates"][0]["differences"][0]["source"] == "\u200b" * 6000


def test_many_diff_groups_are_summarized_without_discarding_json_edits(tmp_path, capsys):
    path = tmp_path / "book.txt"
    path.write_text("a  " * 100)
    quote = "a " * 100
    assert main(["find", str(path), "--quote", quote]) == 0
    assert "additional change groups retained in JSON" in capsys.readouterr().out
    assert main(["find", str(path), "--quote", quote, "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert len(record["candidates"][0]["differences"]) > 50
