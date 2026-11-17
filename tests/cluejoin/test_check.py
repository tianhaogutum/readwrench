"""The ``readwrench check`` adapter for saved clue indexes."""
import sqlite3
from pathlib import Path

import pytest

from readwrench.check.model import HEAD_BYTES, Source
from readwrench.cluejoin import _check
from readwrench.cluejoin.cli import main
from readwrench.cluejoin.store import APPLICATION_ID, build_index

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "clues"


def run(index: Path):
    source = Source(_check.KIND, index, index.name, "readwrench clues index")
    return list(_check.check_index(source))


def snapshot(directory: Path):
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()}


@pytest.fixture
def text_index(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("alpha line\nwrapped\n\nomega paragraph\n", encoding="utf-8")
    index = tmp_path / "notes.sqlite"
    assert main(["build", str(index), str(notes)]) == 0
    return index, notes


@pytest.fixture
def jsonl_index(tmp_path):
    fragments = tmp_path / "fragments.jsonl"
    fragments.write_bytes((EXAMPLES / "fragments.jsonl").read_bytes())
    index = tmp_path / "fragments.sqlite"
    assert main(["build", str(index), str(fragments), "--format", "jsonl"]) == 0
    return index, fragments


def test_application_id_matches_store():
    assert _check.APPLICATION_ID == APPLICATION_ID


def test_classify_recognises_only_clue_indexes(tmp_path, text_index, capsys):
    index, _ = text_index
    head = index.read_bytes()[:HEAD_BYTES]
    assert _check.classify(index, head) == (_check.KIND, "readwrench clues index")
    other = tmp_path / "other.sqlite"
    with sqlite3.connect(other) as connection:
        connection.execute("CREATE TABLE private(x)")
    assert _check.classify(other, other.read_bytes()[:HEAD_BYTES]) is None
    assert _check.classify(tmp_path / "x.txt", b"SQLite format 3\x00") is None
    assert _check.classify(tmp_path / "x.txt", b"plain text" * 20) is None


def test_fresh_indexes_have_no_findings_and_nothing_is_written(tmp_path, text_index, jsonl_index):
    before = snapshot(tmp_path)
    assert run(text_index[0]) == []
    assert run(jsonl_index[0]) == []
    assert snapshot(tmp_path) == before


def test_changed_text_source_is_stale_with_rebuild_hint(text_index):
    index, notes = text_index
    notes.write_text("alpha line\nwrapped\n\nomega changed\n", encoding="utf-8")
    findings = run(index)
    assert [finding.rule for finding in findings] == ["stale-index"]
    finding = findings[0]
    assert finding.severity == "warning"
    assert finding.file == index.name
    assert "paragraph:2" in finding.message
    assert finding.hint.startswith("readwrench clues build notes.sqlite ")
    assert finding.hint.endswith(" --replace")
    assert "--format" not in finding.hint


def test_missing_source_is_reported(text_index):
    index, notes = text_index
    notes.unlink()
    findings = run(index)
    assert [finding.rule for finding in findings] == ["source-missing"]
    assert findings[0].subject == str(notes.resolve())


def test_appended_jsonl_record_is_stale(jsonl_index):
    index, fragments = jsonl_index
    with fragments.open("a", encoding="utf-8") as handle:
        handle.write('{"document_id":"new","page":1,"text":"more words"}\n')
    findings = run(index)
    assert [finding.rule for finding in findings] == ["stale-index"]
    assert "--format jsonl --replace" in findings[0].hint


def test_source_that_no_longer_imports_is_stale(jsonl_index):
    index, fragments = jsonl_index
    fragments.write_text('{"document_id": "broken"}\n', encoding="utf-8")
    findings = run(index)
    assert [finding.rule for finding in findings] == ["stale-index"]
    assert "no longer imports" in findings[0].message


def test_unicode_version_mismatch(jsonl_index):
    index, _ = jsonl_index
    with sqlite3.connect(index) as connection:
        connection.execute("UPDATE metadata SET value='0.0.0' WHERE key='unicode_version'")
    findings = run(index)
    assert [finding.rule for finding in findings] == ["unicode-mismatch"]


def test_unsupported_schema_and_normalizer(tmp_path):
    for name, statement in [("schema", "PRAGMA user_version=2"),
                            ("normalizer", "UPDATE metadata SET value='new' WHERE key='normalizer'")]:
        index = tmp_path / f"{name}.sqlite"
        build_index(index, [_fragment()])
        with sqlite3.connect(index) as connection:
            connection.execute(statement)
        findings = run(index)
        assert [finding.rule for finding in findings] == ["unsupported-index"], name


def test_corrupt_index_is_an_error(tmp_path):
    index = tmp_path / "good.sqlite"
    build_index(index, [_fragment()])
    data = bytearray(index.read_bytes())
    data[4096:8192] = b"\xff" * 4096
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(bytes(data))
    findings = run(corrupt)
    assert [finding.rule for finding in findings] == ["corrupt-index"]
    assert findings[0].severity == "error"


def test_check_record():
    (check,) = _check.CHECKS
    assert check.name == "clues" and check.tool == "clues"
    assert check.kinds == frozenset({_check.KIND})
    assert check.requires == () and check.extra is None
    assert set(check.rules) == set(_check.RULES)


def _fragment():
    from readwrench.cluejoin.model import Fragment

    return Fragment("book", "book.pdf", "p1", "alpha omega", page=2)
