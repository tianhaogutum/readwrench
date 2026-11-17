from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

import zipfile

import pymupdf

from readwrench.reanchor.models import Choice, Decisions
from readwrench.reanchor.workflow import ReviewError, apply, propose, sha256, write_json


def test_broken_output_symlink_is_not_followed(pair, tmp_path):
    old, new = pair
    output = tmp_path / "output-link"
    destination = tmp_path / "unintended-destination"
    output.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ReviewError, match="already exists"):
        propose(old, new, output)
    assert output.is_symlink()
    assert not destination.exists()


def test_render_failure_leaves_no_partial_bundle(pair, tmp_path, monkeypatch):
    import readwrench.reanchor.workflow as workflow

    old, new = pair

    def fail(*args, **kwargs):
        raise RuntimeError("Simulated rasterizer failure")

    monkeypatch.setattr(workflow, "render_asset", fail)
    with pytest.raises(RuntimeError, match="Simulated"):
        propose(old, new, tmp_path / "review")
    assert not (tmp_path / "review").exists()
    assert not list(tmp_path.glob(".review-*"))


def test_save_failure_leaves_no_partial_migration(pair, tmp_path, monkeypatch):
    old, new = pair
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = next(a for a in p.annotations if a.candidates)
    d = Decisions(
        proposal_id=p.proposal_id,
        confirmed=True,
        choices=[Choice(annotation_id=a.id, action="accept", candidate_id=a.candidates[0].id)],
    )
    write_json(bundle / "decisions.json", d.model_dump())
    before = sha256(new)

    def fail(*args, **kwargs):
        raise OSError("Simulated disk full")

    monkeypatch.setattr(pymupdf.Document, "save", fail)
    with pytest.raises(OSError, match="disk full"):
        apply(bundle, bundle / "decisions.json", tmp_path / "result")
    assert sha256(new) == before
    assert not (tmp_path / "result").exists()
    assert not list(tmp_path.glob(".result-*"))


def test_move_inputs_requires_byte_identity(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    d = Decisions(proposal_id=p.proposal_id, confirmed=True)
    write_json(bundle / "decisions.json", d.model_dump())
    moved_old = old.rename(tmp_path / "moved-old.pdf")
    moved_new = new.rename(tmp_path / "moved-new.pdf")
    result = apply(
        bundle, bundle / "decisions.json", tmp_path / "result", old=moved_old, new=moved_new
    )
    assert result["unmatched_count"] == len(p.annotations)


def test_discussion_reply_is_preserved_with_original(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 100), "The discussion concerns this paragraph.")
        root = page.add_highlight_annot(page.search_for("this paragraph", quads=True))
        root.set_info(content="Please explain", title="Editor")
        reply = page.add_text_annot((350, 90), "The author replies here.")
        reply.set_info(title="Author")
        reply.set_irt_xref(root.xref)
        doc.save(old)
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 140), "The discussion concerns this paragraph.")
        doc.save(new)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    assert len(p.annotations) == 2
    reply = next(a for a in p.annotations if "replies" in a.info["content"])
    assert not reply.supported
    assert any("Reply/group" in warning for warning in reply.warnings)
    d = Decisions(proposal_id=p.proposal_id, confirmed=True)
    write_json(bundle / "decisions.json", d.model_dump())
    result = apply(bundle, bundle / "decisions.json", tmp_path / "result")
    assert result["unmatched_count"] == 2
    with zipfile.ZipFile(tmp_path / "result" / "unmatched.zip") as z:
        assert z.read("original.pdf") == old.read_bytes()


def test_accepted_choice_with_recomputed_bad_geometry_is_still_rejected(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = next(a for a in p.annotations if a.candidates)
    c = a.candidates[0]
    c.quads[0] = [1.0, 1.0, 40.0, 1.0, 1.0, 20.0, 40.0, 20.0]  # no underlying text
    p.proposal_id = p.fingerprint()
    write_json(bundle / "proposal.json", p.model_dump())
    write_json(
        bundle / "decisions.json",
        Decisions(
            proposal_id=p.proposal_id,
            confirmed=True,
            choices=[Choice(annotation_id=a.id, action="accept", candidate_id=c.id)],
        ).model_dump(),
    )
    with pytest.raises(ReviewError, match="does not select"):
        apply(bundle, bundle / "decisions.json", tmp_path / "result")
    assert not (tmp_path / "result").exists()


def test_decision_edit_during_apply_is_not_committed(pair, tmp_path, monkeypatch):
    import readwrench.reanchor.workflow as workflow

    old, new = pair
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = next(a for a in p.annotations if a.candidates)
    d = Decisions(
        proposal_id=p.proposal_id,
        confirmed=True,
        choices=[Choice(annotation_id=a.id, action="accept", candidate_id=a.candidates[0].id)],
    )
    dpath = bundle / "decisions.json"
    write_json(dpath, d.model_dump())
    migrate = workflow._migrate

    def edit_during_migration(*args, **kwargs):
        result = migrate(*args, **kwargs)
        d.confirmed = False
        write_json(dpath, d.model_dump())
        return result

    monkeypatch.setattr(workflow, "_migrate", edit_during_migration)
    with pytest.raises(ReviewError, match="Decisions changed"):
        apply(bundle, dpath, tmp_path / "result")
    assert not (tmp_path / "result").exists()


def test_archive_contains_rejected_candidate_evidence(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    write_json(
        bundle / "decisions.json", Decisions(proposal_id=p.proposal_id, confirmed=True).model_dump()
    )
    apply(bundle, bundle / "decisions.json", tmp_path / "result")
    with zipfile.ZipFile(tmp_path / "result" / "unmatched.zip") as z:
        for a in p.annotations:
            for c in a.candidates:
                assert z.read(c.image.path) == (bundle / c.image.path).read_bytes()


def test_evidence_directory_symlink_is_rejected(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    write_json(
        bundle / "decisions.json", Decisions(proposal_id=p.proposal_id, confirmed=True).model_dump()
    )
    (bundle / "assets").rename(tmp_path / "outside-assets")
    (bundle / "assets").symlink_to(tmp_path / "outside-assets", target_is_directory=True)
    with pytest.raises(ReviewError, match="symbolic link"):
        apply(bundle, bundle / "decisions.json", tmp_path / "result")
    assert not (tmp_path / "result").exists()
