import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from readwrench.cluejoin.cli import MAX_QUERY_BYTES, main
from readwrench.cluejoin.model import CluejoinError, Fragment
from readwrench.cluejoin.store import build_index, open_index, source_unchanged_check

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "clues"


def valid_fragments():
    return [Fragment("book", "book.pdf", "p1", "alpha omega", page=2)]


def test_atomic_rebuild_preserves_good_index_on_invalid_input(tmp_path):
    path = tmp_path / "index.sqlite"
    build_index(path, valid_fragments())
    before = path.read_bytes()

    def fail_late():
        yield from valid_fragments()
        raise CluejoinError("late malformed record")

    with pytest.raises(CluejoinError, match="late malformed"):
        build_index(path, fail_late(), replace=True)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp*"))


def test_source_identity_change_before_publish_keeps_old_index(tmp_path):
    path = tmp_path / "index.sqlite"
    source = tmp_path / "source.txt"
    source.write_text("initial", encoding="utf-8")
    unchanged = source_unchanged_check([source])
    build_index(path, valid_fragments())
    before = path.read_bytes()

    def changed_fragments():
        yield from valid_fragments()
        source.write_text("changed", encoding="utf-8")

    with pytest.raises(CluejoinError, match="changed during"):
        build_index(path, changed_fragments(), replace=True, before_publish=unchanged)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp*"))


def test_source_removed_before_publish_is_recoverable(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("initial", encoding="utf-8")
    unchanged = source_unchanged_check([source])
    source.unlink()
    with pytest.raises(CluejoinError, match="changed during"):
        build_index(tmp_path / "index.sqlite", valid_fragments(), before_publish=unchanged)
    assert not (tmp_path / "index.sqlite").exists()


def test_no_overwrite_without_explicit_replace(tmp_path):
    path = tmp_path / "index.sqlite"
    build_index(path, valid_fragments())
    with pytest.raises(CluejoinError, match="already exists"):
        build_index(path, valid_fragments())


def test_reject_conflicting_sources_and_duplicate_fragment_ids(tmp_path):
    path = tmp_path / "index.sqlite"
    for extra in [Fragment("book", "another.pdf", "p2", "x", page=3), *valid_fragments()]:
        with pytest.raises(CluejoinError):
            build_index(path, [*valid_fragments(), extra])
        assert not path.exists()


def test_coordinates_must_be_complete_and_global(tmp_path):
    for extra in [Fragment("book", "book.pdf", "p2", "x"),
                  Fragment("book", "book.pdf", "p2", "x", page=3, paragraph=1)]:
        first = Fragment("book", "book.pdf", "p1", "a", page=2, paragraph=2)
        with pytest.raises(CluejoinError):
            build_index(tmp_path / "index.sqlite", [first, extra])


@pytest.mark.parametrize("coordinates", [
    [{"page": 1, "paragraph": 20}, {"page": 2, "paragraph": 5}, {"page": 3}],
    [{"page": 1, "line": 20}, {"page": 2, "line": 5}, {"page": 3}],
    [{"paragraph": 1, "line": 20}, {"paragraph": 2, "line": 5}, {"paragraph": 3}],
])
def test_missing_annotations_cannot_hide_coordinate_contradictions(tmp_path, coordinates):
    fragments = [Fragment("book", "book.pdf", str(number), "original words", **location)
                 for number, location in enumerate(coordinates)]
    with pytest.raises(CluejoinError, match="contradict"):
        build_index(tmp_path / "index.sqlite", fragments)


def test_consistent_partial_coordinates_are_accepted(tmp_path):
    fragments = [Fragment("book", "book.pdf", "p1", "alpha", page=1, paragraph=10),
                 Fragment("book", "book.pdf", "p2", "middle", page=1, line=40),
                 Fragment("book", "book.pdf", "p3", "omega", page=2, paragraph=20, line=60)]
    stats = build_index(tmp_path / "index.sqlite", fragments)
    assert stats["fragments"] == 3


def test_arbitrary_sqlite_file_is_not_accepted_as_our_index(tmp_path):
    path = tmp_path / "other.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE private(x)")
    connection.close()
    with pytest.raises(CluejoinError, match="not a supported"):
        open_index(path)


@pytest.mark.parametrize("key, value, message", [
    ("unicode_version", "0.0.0", "index uses Unicode"),
    ("normalizer", "incompatible-new-normalizer", "unsupported text normalization"),
])
def test_normalization_version_mismatch_requires_rebuild(tmp_path, key, value, message):
    path = tmp_path / "index.sqlite"
    build_index(path, valid_fragments())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE metadata SET value=? WHERE key=?", (value, key))
    with pytest.raises(CluejoinError, match=message):
        open_index(path)


def test_corrupt_coordinate_metadata_is_a_clear_cli_error(tmp_path, capsys):
    path = tmp_path / "index.sqlite"
    build_index(path, valid_fragments())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE documents SET axis='unknown'")
    assert main(["search", str(path), "-g", "alpha", "-g", "omega"]) == 2
    assert "invalid coordinate metadata" in capsys.readouterr().err


def test_cli_success_missing_clue_and_errors(tmp_path, capsys):
    index = str(tmp_path / "index.sqlite")
    assert main(["build", index, str(EXAMPLES / "fragments.jsonl"),
                 "--format", "jsonl"]) == 0
    capsys.readouterr()
    assert main(["search", index, "-g", "记忆", "-g", "原文"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["matched_documents"] == 1
    assert main(["search", index, "-g", "missing", "-g", "absent"]) == 1
    assert main(["search", index, "-g", "only one"]) == 2
    assert "two or three" in capsys.readouterr().err
    assert main(["search", index, "-g", "a", "-g", "b", "--limit", "0"]) == 2


def test_search_is_read_only(tmp_path):
    path = tmp_path / "index.sqlite"
    build_index(path, valid_fragments())
    with open_index(path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM documents")


def test_sqlite_uri_metacharacters_cannot_change_read_only_mode(tmp_path):
    from contextlib import closing

    path = tmp_path / "what?mode=rw#fragment%26.sqlite"
    build_index(path, valid_fragments())
    with closing(open_index(path)) as connection:
        assert connection.execute("SELECT count(*) FROM fragments").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM documents")


def test_symlink_loop_is_a_cli_input_error(tmp_path, capsys):
    first, second = tmp_path / "loop-a", tmp_path / "loop-b"
    try:
        first.symlink_to(second.name)
        second.symlink_to(first.name)
    except OSError:
        pytest.skip("symlinks are unavailable")
    assert main(["build", str(tmp_path / "index.sqlite"), str(first)]) == 2
    assert "readwrench clues:" in capsys.readouterr().err


def test_rebuild_never_follows_an_output_symlink(tmp_path):
    original = tmp_path / "original.txt"
    original.write_text("keep this", encoding="utf-8")
    index = tmp_path / "index.sqlite"
    try:
        index.symlink_to(original.name)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(CluejoinError, match="regular file"):
        build_index(index, valid_fragments(), replace=True)
    assert original.read_text() == "keep this"


def test_terminal_escape_in_document_cannot_execute(tmp_path, capsys):
    path = tmp_path / "index.sqlite"
    build_index(path, [Fragment("book\x1b[2J", "book.pdf", "p1", "alpha\x1b[2J omega", page=2)])
    assert main(["search", str(path), "-g", "alpha", "-g", "omega", "--output", "text"]) == 0
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "\\x1b" in output


def test_terminal_controls_and_bidi_are_escaped(tmp_path, capsys):
    path = tmp_path / "index.sqlite"
    build_index(path, [Fragment("book\nspoof", "book\u202e.pdf", "p1",
                               "alpha\u009b2J omega", page=2)])
    assert main(["search", str(path), "-g", "alpha", "-g", "omega", "--output", "text"]) == 0
    output = capsys.readouterr().out
    assert "\u009b" not in output
    assert "\u202e" not in output
    assert "book\\x0aspoof" in output


def test_json_control_escaping_is_lossless(tmp_path, capsys):
    path = tmp_path / "index.sqlite"
    text = "alpha\u009b2J omega\u202e"
    build_index(path, [Fragment("book", "book.pdf", "p1", text, page=1)])
    assert main(["search", str(path), "-g", "alpha", "-g", "omega"]) == 0
    output = capsys.readouterr().out
    assert "\u009b" not in output and "\u202e" not in output
    assert json.loads(output)["documents"][0]["evidence"][0]["context"] == text


def test_query_file_limit_accommodates_all_allowed_nonbmp_clues(tmp_path, capsys):
    index = tmp_path / "index.sqlite"
    term = "𠮷" * 1024
    build_index(index, [Fragment("book", "book.pdf", "p1", term, page=1)])
    query = tmp_path / "query.json"
    encoded = json.dumps([[term] * 32] * 3).encode("ascii")
    assert 100_000 < len(encoded) < MAX_QUERY_BYTES
    query.write_bytes(encoded + b" " * (MAX_QUERY_BYTES - len(encoded)))
    assert main(["search", str(index), "--query", str(query)]) == 0
    assert json.loads(capsys.readouterr().out)["matched_documents"] == 1
    with query.open("ab") as handle:
        handle.write(b" ")
    assert main(["search", str(index), "--query", str(query)]) == 2
    assert "exceeds 1250000 bytes" in capsys.readouterr().err


def test_output_cannot_replace_input_text(tmp_path, capsys):
    path = tmp_path / "input.txt"
    path.write_text("alpha omega", encoding="utf-8")
    assert main(["build", str(path), str(path), "--replace"]) == 2
    assert path.read_text() == "alpha omega"
    assert "must not replace an input" in capsys.readouterr().err


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO required")
@pytest.mark.parametrize("kind", ["input", "query", "index"])
def test_special_files_are_rejected_without_blocking(tmp_path, capsys, kind):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    if kind == "input":
        argv = ["build", str(tmp_path / "out.sqlite"), str(fifo)]
    elif kind == "query":
        argv = ["search", str(tmp_path / "out.sqlite"), "--query", str(fifo)]
    else:
        argv = ["search", str(fifo), "-g", "alpha", "-g", "omega"]
    assert main(argv) == 2
    assert "regular file" in capsys.readouterr().err


def test_closed_output_pipe_has_no_traceback_or_error_noise(tmp_path):
    index = tmp_path / "many.sqlite"
    build_index(index, [Fragment(str(number), "synthetic", "p1", "alpha omega", page=1)
                        for number in range(400)])
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH")]))
    with subprocess.Popen(
        [sys.executable, "-m", "readwrench", "clues", "search", str(index),
         "-g", "alpha", "-g", "omega", "--limit", "400"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    ) as process:
        process.stdout.readline()
        process.stdout.close()
        assert process.wait(timeout=5) == 141
        assert process.stderr.read() == ""
