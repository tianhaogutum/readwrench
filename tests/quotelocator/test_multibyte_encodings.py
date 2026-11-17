from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import json

import pytest

from readwrench.quotelocator.cli import main


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-32"])
def test_multibyte_source_offsets_remain_unicode_codepoints_through_cli(tmp_path, capsys, encoding):
    source = tmp_path / "book.txt"
    text = "😀 前文。\n\n核对原始文献。\n\n后文。"
    source.write_bytes(text.encode(encoding))
    review, anchor = tmp_path / "review.json", tmp_path / "anchor.json"
    assert (
        main(
            [
                "find",
                str(source),
                "--quote",
                "核对原始文献",
                "--encoding",
                encoding,
                "--output",
                str(review),
                "--json",
            ]
        )
        == 0
    )
    record = json.loads(capsys.readouterr().out)
    candidate = record["candidates"][0]
    assert candidate["start"] == text.index("核对原始文献")
    assert text[candidate["start"] : candidate["end"]] == "核对原始文献"
    assert (
        main(["confirm", str(source), str(review), "--candidate", "1", "--output", str(anchor)])
        == 0
    )
    assert main(["verify", str(source), str(anchor), "--json"]) == 0
    assert '"valid": true' in capsys.readouterr().out
