"""``readwrench check`` adapter: saved page maps re-verified against their PDFs.

Standard library only: the adapter compares SHA-256 identities and never opens a PDF,
so the "PDFs" here are plain byte files.
"""
import copy
import hashlib
import json

import pytest

from readwrench.check.model import ERROR, INFO, WARNING, Source
from readwrench.pagebridge import _check
from readwrench.pagebridge.mapping import approval_snapshot


def identity(path):
    return {
        "path": str(path.resolve()),
        "name": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "pages": 2,
    }


@pytest.fixture
def sources(tmp_path):
    original = tmp_path / "original.pdf"
    revised = tmp_path / "revised.pdf"
    original.write_bytes(b"%PDF-1.7\n% original bytes\n")
    revised.write_bytes(b"%PDF-1.7\n% revised bytes\n")
    return original, revised


@pytest.fixture
def draft(sources):
    original, revised = sources
    return {
        "schema": "pdf-page-bridge/v1",
        "version": "0.1.0",
        "sources": {"original": identity(original), "modified": identity(revised)},
        "rows": [
            {"original": 1, "modified": 1, "confirmed": False, "status": "suggested"},
            {"original": 2, "modified": None, "confirmed": False, "status": "pending"},
            {"original": None, "modified": 2, "confirmed": False, "status": "pending"},
        ],
        "review": None,
    }


def approve(data):
    data = copy.deepcopy(data)
    for row in data["rows"]:
        row["confirmed"] = True
    data["review"] = {
        "reviewer": "A. Editor",
        "reviewed_at": "2026-11-11T00:00:00+00:00",
        "approved_map": approval_snapshot(data),
    }
    return data


def run(path):
    head = path.read_bytes()[:64 * 1024]
    assert _check.classify(path, head) == (_check.KIND, "readwrench pdf-pages page map")
    source = Source(_check.KIND, path, path.name, "page map")
    return list(_check.check_map(source))


def save(path, data, *, bom=False):
    raw = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw)
    return path


def rules(findings):
    return sorted((f.rule, f.severity) for f in findings)


def test_classify_ignores_other_json(tmp_path):
    other = tmp_path / "other.json"
    other.write_text('{"schema": "something-else/v1"}')
    assert _check.classify(other, other.read_bytes()) is None
    result = tmp_path / "summary.json"
    result.write_text('{"schema": "pdf-page-bridge-result/v1"}')
    assert _check.classify(result, result.read_bytes()) is None


def test_draft_map_is_reported_as_info(draft, tmp_path):
    findings = run(save(tmp_path / "mapping.json", draft, bom=True))
    assert rules(findings) == [("draft-map", INFO)]
    assert "3 of 3 rows" in findings[0].message
    assert "readwrench pdf-pages confirm" in findings[0].hint


def test_intact_approval_has_no_findings(draft, tmp_path):
    assert run(save(tmp_path / "approved.json", approve(draft))) == []


def test_rows_edited_after_approval(draft, tmp_path):
    data = approve(draft)
    # Pair original page 2 with revised page 2 instead of a deletion plus an insertion.
    data["rows"][1]["modified"] = 2
    del data["rows"][2]
    findings = run(save(tmp_path / "approved.json", data))
    assert rules(findings) == [("approval-mismatch", ERROR)]


def test_changed_source_makes_approval_stale(draft, sources, tmp_path):
    path = save(tmp_path / "approved.json", approve(draft))
    sources[1].write_bytes(sources[1].read_bytes() + b"% edited\n")
    findings = run(path)
    assert rules(findings) == [("source-changed", ERROR)]
    assert findings[0].pointer == "/sources/modified"


def test_changed_source_of_a_draft_is_a_warning(draft, sources, tmp_path):
    path = save(tmp_path / "mapping.json", draft)
    sources[0].write_bytes(b"%PDF-1.7\n% replaced\n")
    assert rules(run(path)) == [("draft-map", INFO), ("source-changed", WARNING)]


def test_missing_source_points_at_an_identical_copy(draft, sources, tmp_path):
    folder = tmp_path / "review"
    folder.mkdir()
    path = save(folder / "approved.json", approve(draft))
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    sources[0].rename(moved / sources[0].name)
    (folder / sources[0].name).write_bytes((moved / sources[0].name).read_bytes())
    findings = run(path)
    assert rules(findings) == [("source-missing", WARNING)]
    assert "--original" in findings[0].hint
    assert str(folder / sources[0].name) in findings[0].hint


def test_invalid_map(draft, tmp_path):
    draft["rows"].pop()
    findings = run(save(tmp_path / "mapping.json", draft))
    assert rules(findings) == [("invalid-map", ERROR)]
    assert "Unaccounted modified pages" in findings[0].message


def test_duplicate_keys_are_invalid(tmp_path):
    path = tmp_path / "mapping.json"
    path.write_text('{"schema": "pdf-page-bridge/v1", "schema": "pdf-page-bridge/v1"}')
    assert rules(run(path)) == [("invalid-map", ERROR)]


def test_registered_check_metadata():
    (check,) = _check.CHECKS
    assert check.name == check.tool == "pdf-pages"
    assert check.kinds == {_check.KIND}
    assert set(check.rules) == set(_check.RULES)
