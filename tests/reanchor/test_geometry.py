from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

import json

import pymupdf

from readwrench.reanchor.models import Choice, Decisions
from readwrench.reanchor.text import TextIndex, normalize
from readwrench.reanchor.workflow import apply, propose, write_json

QUOTE = "The anchor belongs to this carefully reviewed paragraph."


def make_pdf(path, *, rotation=0, text_rotation=0, crop=False, annotated=False, extra=False):
    with pymupdf.open() as doc:
        p = doc.new_page(width=620, height=820)
        point = (180, 650) if text_rotation == 90 else (140, 210)
        p.insert_text(point, QUOTE, fontsize=11, rotate=text_rotation)
        if extra:
            p.insert_text(
                (150, 400), "The additional introduction changes the layout.", fontsize=11
            )
        if annotated:
            a = p.add_highlight_annot(p.search_for(QUOTE, quads=True))
            a.set_info(content="A rotated source comment", title="Reviewer")
            a.update()
        if crop:
            p.set_cropbox(pymupdf.Rect(100, 100, 570, 760))
        p.set_rotation(rotation)
        doc.save(path)


@pytest.mark.parametrize(
    "source_rotation,target_rotation", [(0, 90), (90, 0), (90, 270), (180, 90), (270, 180)]
)
@pytest.mark.parametrize("crop", [False, True])
def test_page_rotation_and_crop(tmp_path, source_rotation, target_rotation, crop):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    make_pdf(old, rotation=source_rotation, crop=crop, annotated=True)
    make_pdf(new, rotation=target_rotation, crop=crop, extra=True)
    bundle = tmp_path / "review"
    proposal = propose(old, new, bundle)
    assert len(proposal.annotations) == 1
    a = proposal.annotations[0]
    assert a.candidates
    assert a.selected_text == normalize(QUOTE)
    assert a.candidates[0].evidence.layout == pytest.approx(1.0)
    decision = Decisions(
        proposal_id=proposal.proposal_id,
        confirmed=True,
        choices=[Choice(annotation_id=a.id, action="accept", candidate_id=a.candidates[0].id)],
    )
    write_json(bundle / "decisions.json", decision.model_dump())
    result = apply(bundle, bundle / "decisions.json", tmp_path / "result")
    assert result["migrated_count"] == 1
    with pymupdf.open(tmp_path / "result" / "migrated.pdf") as doc:
        p = doc[0]
        annot = next(p.annots())
        index = TextIndex(p)
        span = index.selected_range([[v for point in annot.vertices for v in point]])
        assert index.text[slice(*span)] == normalize(QUOTE)


@pytest.mark.parametrize("source_text_rotation,target_text_rotation", [(90, 0), (0, 90), (90, 90)])
def test_rotated_text_quads(tmp_path, source_text_rotation, target_text_rotation):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    make_pdf(old, text_rotation=source_text_rotation, annotated=True)
    make_pdf(new, text_rotation=target_text_rotation)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = p.annotations[0]
    assert a.candidates
    decision = Decisions(
        proposal_id=p.proposal_id,
        confirmed=True,
        choices=[Choice(annotation_id=a.id, action="accept", candidate_id=a.candidates[0].id)],
    )
    write_json(bundle / "decisions.json", decision.model_dump())
    assert apply(bundle, bundle / "decisions.json", tmp_path / "result")["migrated_count"] == 1


def test_repeated_quote_context_comes_from_annotated_occurrence(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            p = doc.new_page()
            p.insert_text((72, 100), "Team Alpha reports the original finding.", fontsize=11)
            p.insert_text((72, 120), "The review board approved this proposal.", fontsize=11)
            p.insert_text((72, 400), "Team Beta reports a separate finding.", fontsize=11)
            p.insert_text((72, 420), "The review board approved this proposal.", fontsize=11)
            if annotated:
                hits = p.search_for("The review board approved this proposal.", quads=True)
                p.add_highlight_annot(hits[1])
            doc.save(path)
    p = propose(old, new, tmp_path / "review")
    a = p.annotations[0]
    assert a.context_before.endswith("team beta reports a separate finding. ")
    assert len(a.candidates) >= 2
    assert a.candidates[0].quads[0][1] > 350


def test_changed_original_is_not_hidden_by_exact_decoy(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    source_quote = "The new deployment reduced latency by twelve percent across all regions."
    target_quote = source_quote.replace("twelve", "twenty")
    for path, revised in [(old, False), (new, True)]:
        with pymupdf.open() as doc:
            p = doc.new_page(width=750)
            p.insert_text((50, 100), "Team Alpha:")
            p.insert_text((50, 120), target_quote if revised else source_quote)
            p.insert_text((50, 400), "Team Beta:")
            p.insert_text((50, 420), source_quote)
            if not revised:
                p.add_highlight_annot(p.search_for(source_quote, quads=True)[0])
            doc.save(path)
    p = propose(old, new, tmp_path / "review")
    a = p.annotations[0]
    assert any(
        "twenty percent" in c.matched_text and c.evidence.method == "fuzzy" for c in a.candidates
    )
    assert any("twelve percent" in c.matched_text for c in a.candidates)


def test_html_escapes_pdf_text(pair, tmp_path):
    old, new = pair
    with pymupdf.open(old) as doc:
        page = doc[0]
        next(page.annots()).set_info(content="</script><script>globalThis.evil=true</script>")
        doc.save(tmp_path / "injected.pdf")
    p = propose(tmp_path / "injected.pdf", new, tmp_path / "review")
    html = (tmp_path / "review" / "review.html").read_text()
    assert "</script><script>globalThis.evil" not in html
    raw = html.split('id="proposal">')[1].split("</script>")[0]
    assert json.loads(raw)["annotations"][0]["info"]["content"] == p.annotations[0].info["content"]


def test_normalization_offsets_cover_ligatures_and_line_hyphens():
    assert normalize("The ﬁnal co-\noperation was clear.") == "the final cooperation was clear."
    assert normalize("memory-mapped") == "memory-mapped"
