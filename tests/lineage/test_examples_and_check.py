"""The bundled example stays byte-compatible with the original tool, and the check adapter."""
import json
import os
import shutil

from readwrench.check.model import Source
from readwrench.lineage._check import CHECKS, KIND, classify
from readwrench.lineage.jsutil import stringify, to_utf8
from readwrench.lineage.render import render_html
from readwrench.lineage.timeline import build_timeline

from .helpers import ROOT, SOURCE, page

EXAMPLE = ROOT / "examples" / "lineage"


def _example_timeline(base):
    timeline = build_timeline(base / "manifest.json")["timeline"]
    for capture in timeline["captures"]:
        capture["file"] = os.path.relpath(capture["file"], os.path.realpath(base))
    return timeline


def test_example_json_and_report_match_the_saved_outputs(tmp_path):
    # Build from a copy so the test never writes into the repository.
    base = tmp_path / "example"
    shutil.copytree(EXAMPLE, base)
    timeline = _example_timeline(base)
    assert to_utf8(stringify(timeline) + "\n") == (EXAMPLE / "timeline.json").read_bytes()
    assert to_utf8(render_html(timeline)) == (EXAMPLE / "timeline.html").read_bytes()
    counts = [transition["counts"] for transition in timeline["transitions"]]
    assert counts[0]["modified"] == 2 and timeline["ordering"] == "capture-time"


def _run(path):
    source = Source(KIND, path, str(path), "readwrench lineage manifest")
    return list(CHECKS[0].run(source))


def test_check_classifies_manifests_and_passes_the_example():
    manifest = EXAMPLE / "manifest.json"
    assert classify(manifest, manifest.read_bytes()) == (KIND, "readwrench lineage manifest")
    assert classify(manifest, b'{"url": "https://example.org/", "timestamp": "1"}') is None
    assert _run(manifest) == []


def test_check_reports_missing_duplicate_and_mismatched_captures(tmp_path):
    (tmp_path / "a.html").write_text(page())
    (tmp_path / "other.html").write_text(f"<!-- Page saved with SingleFile\nurl: https://elsewhere.example/\n-->{page()}")
    manifest = tmp_path / "captures.json"
    manifest.write_text(json.dumps({"version": 1, "url": SOURCE, "captures": [
        {"file": "a.html"}, {"file": "gone.html"}, {"file": "a.html"}, {"file": "other.html"}]}))
    rules = sorted(finding.rule for finding in _run(manifest))
    assert rules == ["capture-missing", "duplicate-capture", "url-mismatch"]


def test_check_reports_invalid_manifest_and_unknown_times(tmp_path):
    manifest = tmp_path / "captures.json"
    manifest.write_text(json.dumps({"version": 1, "url": SOURCE, "captures": [{"file": "a.html", "typo": 1}]}))
    assert [finding.rule for finding in _run(manifest)] == ["invalid-manifest"]
    (tmp_path / "a.html").write_text(page())
    manifest.write_text(json.dumps({"version": 1, "url": SOURCE, "captures": [{"file": "a.html"}]}))
    findings = _run(manifest)
    assert [(finding.rule, finding.severity) for finding in findings] == [("unknown-capture-time", "info")]
    assert findings[0].hint.startswith("readwrench lineage build ")
