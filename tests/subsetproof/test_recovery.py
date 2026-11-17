from pathlib import Path
import json
import os
import pytest

pytest.importorskip("fontTools")
pytest.importorskip("uharfbuzz")
pytest.importorskip("regex")
pytest.importorskip("brotli")

from readwrench.subsetproof.cli import main

HERE = Path(__file__).resolve().parent
FONT = HERE / "fixtures/fonts/NotoSans.ttf"


def test_inserted_glyphs_do_not_mark_the_unchanged_suffix(tmp_path):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office")
    output = tmp_path / "proof"
    assert main(["build", str(FONT), str(manuscript), "--keep-features", "", "--out", str(output)]) == 1
    report = json.loads((output / "report.json").read_text())
    changes = report["results"][0]["comparison"]["changes"]
    assert len(changes) == 3
    assert not any(event[side] and event[side]["name"] in {"o", "c", "e"}
                   for event in changes for side in ["original", "candidate"])


def test_interrupted_specimen_write_leaves_no_partial_result(tmp_path, monkeypatch):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office")
    output = tmp_path / "proof"

    def broken_renderer(*args):
        yield "<!doctype html>partial"
        raise OSError("simulated full disk")

    monkeypatch.setattr("readwrench.subsetproof.proof.render", broken_renderer)
    assert main(["build", str(FONT), str(manuscript), "--out", str(output)]) == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".subset-proof-*"))
    assert manuscript.read_text() == "office"


def test_large_matrix_fails_before_font_loading(tmp_path):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office " * 4000)
    assert main(["build", str(tmp_path / "nonexistent.ttf"), str(manuscript), "--at", "wght=100", "--at", "wght=900", "--out", str(tmp_path / "proof")]) == 2
    assert not (tmp_path / "proof").exists()


def test_many_run_specific_coordinates_are_bounded(tmp_path):
    manuscript = tmp_path / "copy.json"
    manuscript.write_text(json.dumps({"runs": [{"text": "A", "variations": {"wght": 100 + n}}
                                               for n in range(33)]}))
    assert main(["build", str(FONT), str(manuscript), "--manifest", "--out", str(tmp_path / "proof")]) == 2


def test_broken_symlink_output_is_not_replaced(tmp_path):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office")
    output = tmp_path / "proof"
    output.symlink_to(tmp_path / "nonexistent")
    assert main(["build", str(FONT), str(manuscript), "--out", str(output)]) == 2
    assert output.is_symlink()


def test_manuscript_changed_during_read_is_not_misidentified(tmp_path, monkeypatch, capsys):
    import readwrench.subsetproof.proof as proof
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office")
    actual_read = proof.read_runs

    def changing_read(path, **kwargs):
        runs = actual_read(path, **kwargs)
        path.write_text("updated office copy")
        return runs

    monkeypatch.setattr(proof, "read_runs", changing_read)
    assert main(["build", str(FONT), str(manuscript), "--out", str(tmp_path / "proof")]) == 2
    assert "changed while it was being read" in capsys.readouterr().err
    assert not (tmp_path / "proof").exists()
    assert manuscript.read_text() == "updated office copy"


def test_font_changed_during_read_is_detected(tmp_path, monkeypatch, capsys):
    import readwrench.subsetproof.core as core
    font = tmp_path / "font.ttf"
    font.write_bytes(FONT.read_bytes())
    actual_state = core.file_state
    first_call = True

    def changing_state(path):
        nonlocal first_call
        state = actual_state(path)
        if first_call:
            first_call = False
            with path.open("ab") as stream:
                stream.write(b"changed")
        return state

    monkeypatch.setattr(core, "file_state", changing_state)
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office")
    assert main(["build", str(font), str(manuscript), "--out", str(tmp_path / "proof")]) == 2
    assert "Font changed while it was being read" in capsys.readouterr().err
    assert not (tmp_path / "proof").exists()


def test_missing_locations_are_printed_without_opening_json(tmp_path, capsys):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office🚀")
    assert main(["build", str(FONT), str(manuscript), "--out", str(tmp_path / "proof")]) == 1
    stderr = capsys.readouterr().err
    assert "line 1, column 7" in stderr and "U+1F680 ROCKET" in stderr


def test_large_manifest_is_rejected_before_constructing_all_runs(tmp_path):
    manuscript = tmp_path / "copy.json"
    manuscript.write_text(json.dumps({"runs": [{"text": "A"}] * 20_001}))
    assert main(["build", str(FONT), str(manuscript), "--manifest", "--out", str(tmp_path / "proof")]) == 2


def test_aggregate_glyph_expansion_budget_cleans_staging(tmp_path, monkeypatch):
    monkeypatch.setattr("readwrench.subsetproof.proof.MAX_TOTAL_GLYPHS", 1)
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office")
    assert main(["build", str(FONT), str(manuscript), "--out", str(tmp_path / "proof")]) == 2
    assert not (tmp_path / "proof").exists() and not list(tmp_path.glob(".subset-proof-*"))


def test_candidate_glyph_expansion_budget_cleans_staging(tmp_path, monkeypatch):
    monkeypatch.setattr("readwrench.subsetproof.proof.MAX_TOTAL_GLYPHS", 5)
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office")
    assert main(["build", str(FONT), str(manuscript), "--keep-features", "", "--out", str(tmp_path / "proof")]) == 2
    assert not (tmp_path / "proof").exists() and not list(tmp_path.glob(".subset-proof-*"))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipe")
def test_named_pipe_input_is_rejected_without_waiting_for_a_writer(tmp_path, capsys):
    pipe = tmp_path / "copy.fifo"
    os.mkfifo(pipe)
    assert main(["build", str(FONT), str(pipe), "--out", str(tmp_path / "proof")]) == 2
    assert "regular input file" in capsys.readouterr().err
    assert not (tmp_path / "proof").exists()
