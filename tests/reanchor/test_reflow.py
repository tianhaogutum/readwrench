from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

import pymupdf

from readwrench.reanchor.models import Choice, Decisions
from readwrench.reanchor.workflow import apply, propose, write_json


def accepted(bundle, p):
    a = p.annotations[0]
    write_json(
        bundle / "decisions.json",
        Decisions(
            proposal_id=p.proposal_id,
            confirmed=True,
            choices=[Choice(annotation_id=a.id, action="accept", candidate_id=a.candidates[0].id)],
        ).model_dump(),
    )


@pytest.mark.parametrize("source_angle,target_angle", [(23, -15), (-15, 32)])
def test_diagonal_text_geometry(tmp_path, source_angle, target_angle):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    quote = "A diagonal anchor can move and change orientation safely."
    for path, angle, annotated in [(old, source_angle, True), (new, target_angle, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page()
            point = pymupdf.Point(130, 400)
            page.insert_text(point, quote, fontsize=12, morph=(point, pymupdf.Matrix(angle)))
            if annotated:
                page.add_highlight_annot(page.search_for(quote, quads=True))
            doc.save(path)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    assert p.annotations[0].candidates
    accepted(bundle, p)
    assert apply(bundle, bundle / "decisions.json", tmp_path / "result")["migrated_count"] == 1


def test_actual_pdf_line_end_hyphenation(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text(
                (72, 150), "co-\noperation" if annotated else "cooperation", fontsize=14
            )
            if annotated:
                # MuPDF's dehyphenating search changed between 1.26 and 1.27.
                # Create native markup from the real word boxes in either version.
                words = page.get_text(
                    "words", flags=pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_DEHYPHENATE
                )
                assert [word[4] for word in words] == ["co-", "operation"]
                page.add_highlight_annot([pymupdf.Rect(word[:4]) for word in words])
            doc.save(path)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = p.annotations[0]
    assert a.selected_text == "cooperation"
    assert a.candidates[0].matched_text == "cooperation"
    assert len(a.candidates[0].quads) == 1
    accepted(bundle, p)
    assert apply(bundle, bundle / "decisions.json", tmp_path / "result")["migrated_count"] == 1


def test_disjoint_highlight_is_preserved_instead_of_joined_into_wrong_quote(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text(
                (72, 150),
                "Alpha and many unrelated intervening words then Omega."
                if annotated
                else "Alpha Omega.",
            )
            if annotated:
                page.add_highlight_annot(
                    [
                        page.search_for("Alpha", quads=True)[0],
                        page.search_for("Omega", quads=True)[0],
                    ]
                )
            doc.save(path)
    p = propose(old, new, tmp_path / "review")
    assert not p.annotations[0].supported
    assert not p.annotations[0].candidates
    assert any("contiguous" in warning for warning in p.annotations[0].warnings)


def test_cross_page_move_is_not_filtered_by_proximity_window(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    quote = "The revised section moved far beyond its former page."
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 150), quote)
        page.add_highlight_annot(page.search_for(quote, quads=True))
        doc.save(old)
    with pymupdf.open() as doc:
        for i in range(9):
            doc.new_page().insert_text((72, 100), f"New preliminary section {i + 1}")
        doc.new_page().insert_text((72, 400), quote)
        doc.save(new)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    c = p.annotations[0].candidates[0]
    assert c.page_index == 9
    assert c.evidence.page_proximity == 0
    accepted(bundle, p)
    audit = apply(bundle, bundle / "decisions.json", tmp_path / "result")
    assert audit["migrated"][0]["page_index"] == 9
