"""``readwrench check`` adapter for saved reports: standard library only (synthetic files)."""
from __future__ import annotations

import hashlib
import json

from readwrench.check.model import Source
from readwrench.subsetproof import _check


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_report(tmp_path, *, accepted=True, issues=(), mode="build", font=b"font", text=b"office",
                 subset=b"subset", **overrides):
    work = tmp_path / "work"
    (work / "fonts").mkdir(parents=True)
    (work / "fonts" / "orig.ttf").write_bytes(font)
    (work / "copy.txt").write_bytes(text)
    out = work / "out" / "proof"
    out.mkdir(parents=True)
    (out / "subset.ttf").write_bytes(subset)
    report = {"schema_version": 1, "tool": "subset-proof", "version": "0.1.0", "mode": mode,
              "accepted": accepted, "issues": list(issues),
              "original": {"path": "fonts/orig.ttf", "sha256": sha(b"font"), "bytes": 4, "face_index": None},
              "candidate": {"path": "subset.ttf", "sha256": sha(b"subset"), "bytes": 6, "face_index": None},
              "text": {"path": "copy.txt", "sha256": sha(b"office"), "bytes": 6, "format": "utf8_lines"}}
    report.update(overrides)
    path = out / "report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def run(path):
    return list(_check.check_report(Source(_check.KIND, path, "out/proof/report.json", "report")))


def test_classify_recognises_only_subset_proof_reports(tmp_path):
    path = write_report(tmp_path)
    assert _check.classify(path, path.read_bytes()[:65536]) == (_check.KIND, "subset-proof report")
    assert _check.classify(path, b'{"schema_version": 1, "tool": "other"}') is None
    assert _check.classify(path, b"\x00\xff not json") is None


def test_unchanged_accepted_proof_has_no_findings(tmp_path):
    assert run(write_report(tmp_path)) == []


def test_rejected_proof_is_an_error_with_the_reason(tmp_path):
    findings = run(write_report(tmp_path, accepted=False, issues=["shaping_difference"]))
    assert [(f.rule, f.severity) for f in findings] == [("rejected", "error")]
    assert "shapes the manuscript differently" in findings[0].message
    assert "readwrench subset-proof build" in findings[0].hint


def test_changed_subset_and_manuscript_are_reported(tmp_path):
    findings = run(write_report(tmp_path, text=b"office!", subset=b"tampered"))
    rules = sorted((f.rule, f.subject) for f in findings)
    assert rules == [("input-changed", "copy.txt"), ("subset-changed", "subset.ttf")]
    assert all(f.severity == "warning" and f.file == "out/proof/report.json" for f in findings)


def test_missing_files_are_information_only(tmp_path):
    path = write_report(tmp_path, mode="compare",
                        candidate={"path": "elsewhere/missing.woff2", "sha256": sha(b"x"), "face_index": None})
    findings = run(path)
    assert [(f.rule, f.severity) for f in findings] == [("input-not-found", "info")]
    assert "readwrench subset-proof compare" in _check._rerun_hint(json.loads(path.read_text()))


def test_unreadable_malformed_and_future_reports(tmp_path):
    path = tmp_path / "report.json"
    path.write_text("{broken", encoding="utf-8")
    assert [f.rule for f in run(path)] == ["unreadable-report"]
    path.write_text(json.dumps({"schema_version": 1, "tool": "subset-proof"}), encoding="utf-8")
    assert [f.rule for f in run(path)] == ["malformed-report"]
    path.write_text(json.dumps({"schema_version": 2, "tool": "subset-proof"}), encoding="utf-8")
    assert [(f.rule, f.severity) for f in run(path)] == [("unsupported-schema", "info")]


def test_check_never_writes(tmp_path):
    path = write_report(tmp_path, accepted=False, issues=["candidate_missing_unicode"], subset=b"changed")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    run(path)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
