import json
import os
from pathlib import Path


import pytest

for _module in ("pymupdf", "PIL", "numpy", "scipy", "rapidfuzz", "dhash", "reportlab"):
    pytest.importorskip(_module)

from readwrench.pagebridge.cli import main, write_new_json
from readwrench.pagebridge.core import BridgeError, approval_snapshot, make_plan, read_plan


def test_approval_serialization_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    target = tmp_path / "approved.json"

    def fail(*args, **kwargs):
        raise OSError("Simulated disk/serialization failure")

    monkeypatch.setattr("readwrench.pagebridge.cli.json.dumps", fail)
    with pytest.raises(OSError, match="Simulated"):
        write_new_json(target, {"review": "fixture"})
    assert not target.exists()


def test_approval_disk_flush_failure_is_cleaned(tmp_path, monkeypatch):
    target = tmp_path / "approved.json"

    def fail(descriptor):
        raise OSError("Simulated disk flush failure")

    monkeypatch.setattr("readwrench.pagebridge.cli.os.fsync", fail)
    with pytest.raises(OSError, match="flush"):
        write_new_json(target, {"review": "fixture"})
    assert not target.exists()
    assert not list(tmp_path.glob(".approved.json-*"))


def test_existing_approval_is_never_overwritten(tmp_path):
    target = tmp_path / "approved.json"
    target.write_text("an earlier approved map")
    with pytest.raises(FileExistsError):
        write_new_json(target, {"review": "replacement"})
    assert target.read_text() == "an earlier approved map"
    assert not list(tmp_path.glob(".approved.json-*"))


def test_source_mutation_during_export_rolls_back(documents, tmp_path, monkeypatch):
    from readwrench.pagebridge import export

    assets = tmp_path / "assets"
    assets.mkdir()
    data = make_plan(*documents, assets)
    for row in data["rows"]:
        row["confirmed"] = True
    data["review"] = {
        "reviewer": "Fixture reviewer",
        "reviewed_at": "2026-11-11T00:00:00Z",
        "approved_map": approval_snapshot(data),
    }
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps(data))
    original_prepare = export.prepare_document
    calls = []

    def mutate_after_open(doc):
        original_prepare(doc)
        if not calls:
            with documents[0].open("ab") as stream:
                stream.write(b"\n% source changed during export\n")
        calls.append(True)

    monkeypatch.setattr(export, "prepare_document", mutate_after_open)
    target = tmp_path / "comparison"
    assert main(["export", str(approved), "--out", str(target)]) == 2
    assert not target.exists()
    assert not list(tmp_path.glob(".comparison-*"))


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema":"pdf-page-bridge/v1","schema":"different"}',
        '{"schema":"pdf-page-bridge/v1","unknown":NaN}',
        '{"schema":"pdf-page-bridge/v1","unknown":Infinity}',
        '{"schema":"pdf-page-bridge/v1","unknown":"\\ud800"}',
    ],
)
def test_reject_nonstandard_or_duplicate_json(payload, tmp_path):
    target = tmp_path / "invalid.json"
    target.write_text(payload)
    with pytest.raises(BridgeError, match="Duplicate|Non-finite|surrogates"):
        read_plan(target)


def test_json_size_limit_survives_a_file_growth_race(tmp_path, monkeypatch):
    target = tmp_path / "growing.json"
    target.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    original_stat = Path.stat

    def earlier_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == target:
            values = list(result)
            values[6] = 1  # The file was small when an earlier stat was obtained.
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", earlier_stat)
    with pytest.raises(BridgeError, match="4 MiB"):
        read_plan(target)
