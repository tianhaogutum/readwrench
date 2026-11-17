from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

import json
from pathlib import Path

from readwrench.check.model import HEAD_BYTES, Source
from readwrench.reanchor._check import CHECKS, KIND, classify
from readwrench.reanchor.workflow import propose, write_json


def bundle_source(bundle: Path) -> Source:
    path = bundle / "proposal.json"
    return Source(kind=KIND, path=path, display=str(path), label="proposal")


def run_check(bundle: Path):
    (check,) = CHECKS
    return list(check.run(bundle_source(bundle)))


def rules(findings):
    return {finding.rule for finding in findings}


def test_classify_recognises_only_the_proposal(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    propose(old, new, bundle)
    head = (bundle / "proposal.json").read_bytes()[:HEAD_BYTES]
    assert classify(bundle / "proposal.json", head) == (KIND, "PDF reanchor review proposal")
    for name in ("decisions.template.json", "review.html"):
        path = bundle / name
        assert classify(path, path.read_bytes()[:HEAD_BYTES]) is None
    assert classify(old, old.read_bytes()[:HEAD_BYTES]) is None


def test_fresh_bundle_has_only_a_pending_review_notice(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    propose(old, new, bundle)
    findings = run_check(bundle)
    assert rules(findings) == {"review-pending"}
    assert all(f.severity == "info" for f in findings)
    assert findings[0].hint and "readwrench reanchor apply" in findings[0].hint


def test_changed_input_pdf_is_an_error(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    propose(old, new, bundle)
    with new.open("ab") as stream:
        stream.write(b"\n% edited after review\n")
    findings = run_check(bundle)
    changed = [f for f in findings if f.rule == "pdf-changed"]
    assert len(changed) == 1 and changed[0].severity == "error"
    assert changed[0].pointer == "/new_pdf/sha256"
    assert "review-pending" not in rules(findings)


def test_moved_input_pdf_is_a_warning_with_the_apply_flag(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    propose(old, new, bundle)
    old.rename(tmp_path / "moved.pdf")
    findings = run_check(bundle)
    missing = [f for f in findings if f.rule == "pdf-missing"]
    assert len(missing) == 1 and missing[0].severity == "warning"
    assert "--old-pdf" in missing[0].hint


def test_edited_proposal_and_evidence_are_errors(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    raw = json.loads((bundle / "proposal.json").read_text())
    raw["annotations"][0]["info"]["content"] = "edited"
    write_json(bundle / "proposal.json", raw)
    (bundle / p.annotations[0].image.path).write_bytes(b"changed")
    findings = run_check(bundle)
    assert {"fingerprint-mismatch", "evidence-changed"} <= rules(findings)


def test_invalid_proposal_is_reported_not_raised(tmp_path):
    bundle = tmp_path / "review"
    bundle.mkdir()
    (bundle / "proposal.json").write_text('{"upstream_version": "0.2.2", "proposal_id": 1}')
    findings = run_check(bundle)
    assert rules(findings) == {"invalid-proposal"}
