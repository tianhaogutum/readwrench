from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

import zipfile

import pymupdf

from readwrench.reanchor.adapter import Matcher, source_annotations
from readwrench.reanchor.models import Choice, Decisions
from readwrench.reanchor.workflow import ReviewError, apply, propose, sha256, write_json


@pytest.mark.parametrize("stream_content", [False, True])
def test_rich_text_only_comment_is_preserved_not_migrated_empty(tmp_path, stream_content):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    rich = '<body xmlns="http://www.w3.org/1999/xhtml"><p>Please preserve this <b>editorial concern</b>.</p></body>'
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 150), "This passage has an important editorial comment.")
            if annotated:
                a = page.add_highlight_annot(page.search_for("editorial comment", quads=True))
                if stream_content:
                    xref = doc.get_new_xref()
                    doc.update_object(xref, "<<>>")
                    doc.update_stream(xref, rich.encode())
                    doc.xref_set_key(a.xref, "RC", f"{xref} 0 R")
                else:
                    doc.xref_set_key(a.xref, "RC", pymupdf.get_pdf_str(rich))
                doc.xref_set_key(a.xref, "Contents", "null")
            doc.save(path)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = p.annotations[0]
    assert not a.supported
    assert not a.candidates
    assert any("Rich-text-only" in warning for warning in a.warnings)
    if not stream_content:
        assert "editorial concern" in a.info["rich_content"]
    write_json(
        bundle / "decisions.json", Decisions(proposal_id=p.proposal_id, confirmed=True).model_dump()
    )
    audit = apply(bundle, bundle / "decisions.json", tmp_path / "result")
    assert audit["unmatched_count"] == 1
    with zipfile.ZipFile(tmp_path / "result" / "unmatched.zip") as z:
        assert z.read("original.pdf") == old.read_bytes()
    # Re-check actual source features during apply, even for a legacy proposal
    # which incorrectly labelled this comment as eligible for plain writeback.
    with pymupdf.open(old) as source, pymupdf.open(new) as target:
        anchor = source_annotations(source, sha256(old))[0][1]
        candidate = Matcher(target).candidates(anchor, limit=1)[0][0]
    a.supported = True
    a.candidates = [candidate]
    a.candidate_count = 1
    p.proposal_id = p.fingerprint()
    write_json(bundle / "proposal.json", p.model_dump())
    write_json(
        bundle / "decisions.json",
        Decisions(
            proposal_id=p.proposal_id,
            confirmed=True,
            choices=[Choice(annotation_id=a.id, action="accept", candidate_id=candidate.id)],
        ).model_dump(),
    )
    with pytest.raises(ReviewError, match="Rich-text-only"):
        apply(bundle, bundle / "decisions.json", tmp_path / "legacy-result")
    assert not (tmp_path / "legacy-result").exists()


def test_rich_comment_with_plain_contents_migrates_the_reviewed_plain_text(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 150), "The documented claim requires a reference.")
            if annotated:
                a = page.add_highlight_annot(page.search_for("requires a reference", quads=True))
                a.set_info(content="Please cite the original report.", title="Reviewer")
                doc.xref_set_key(
                    a.xref,
                    "RC",
                    pymupdf.get_pdf_str(
                        "<body><p>Please cite the <b>original report</b>.</p></body>"
                    ),
                )
            doc.save(path)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = p.annotations[0]
    assert a.supported and a.candidates
    assert any("formatting is reduced" in warning for warning in a.warnings)
    write_json(
        bundle / "decisions.json",
        Decisions(
            proposal_id=p.proposal_id,
            confirmed=True,
            choices=[Choice(annotation_id=a.id, action="accept", candidate_id=a.candidates[0].id)],
        ).model_dump(),
    )
    apply(bundle, bundle / "decisions.json", tmp_path / "result")
    with pymupdf.open(tmp_path / "result" / "migrated.pdf") as doc:
        page = doc[0]
        assert next(page.annots()).info["content"] == "Please cite the original report."
