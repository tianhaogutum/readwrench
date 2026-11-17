from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

from pathlib import Path

import pymupdf

from readwrench.reanchor.models import Choice, Decisions
from readwrench.reanchor.text import normalize
from readwrench.reanchor.workflow import apply, propose, write_json


def test_unicode_cluster_normalization():
    assert normalize("café") == normalize("cafe\u0301")
    assert normalize("\u1100\u1161") == normalize("\uac00")


@pytest.mark.parametrize("composed_source", [True, False])
def test_canonical_combining_glyphs_roundtrip(tmp_path, composed_source):
    font = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    if not font.exists():
        pytest.skip(
            "This real-font fixture requires macOS Arial; no proprietary font is redistributed"
        )
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    for path, annotated in [(old, True), (new, False)]:
        composed = composed_source if annotated else not composed_source
        text = "café" if composed else "cafe\u0301"
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname="ReviewFont", fontfile=str(font))
            page.insert_text((72, 150), text, fontname="ReviewFont", fontsize=18)
            if annotated:
                word = page.get_text("words")[0]
                page.add_highlight_annot(pymupdf.Rect(word[:4]))
            doc.save(path)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = p.annotations[0]
    assert a.selected_text == "café"
    assert a.candidates[0].matched_text == "café"
    write_json(
        bundle / "decisions.json",
        Decisions(
            proposal_id=p.proposal_id,
            confirmed=True,
            choices=[Choice(annotation_id=a.id, action="accept", candidate_id=a.candidates[0].id)],
        ).model_dump(),
    )
    assert apply(bundle, bundle / "decisions.json", tmp_path / "result")["migrated_count"] == 1


def test_partial_ligature_is_not_offered_as_writeable_geometry(tmp_path):
    reportlab = pytest.importorskip("reportlab")
    font = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname="ReviewFont", fontfile=str(font))
            page.insert_text(
                (72, 150), "f" if annotated else "ﬁ", fontname="ReviewFont", fontsize=18
            )
            if annotated:
                page.add_highlight_annot(pymupdf.Rect(page.get_text("words")[0][:4]))
            doc.save(path)
    p = propose(old, new, tmp_path / "review")
    assert p.annotations[0].selected_text == "f"
    assert not p.annotations[0].candidates


def test_chinese_text_and_comment_roundtrip(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    quote = "修订后的文档应当保留每一条审阅意见。"
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text(
                (72, 100 if annotated else 240), quote, fontname="china-s", fontsize=14
            )
            if annotated:
                a = page.add_highlight_annot(page.search_for(quote, quads=True))
                a.set_info(content="请核对批注是否跟随正确的段落。", title="审阅人")
                a.update()
            doc.save(path)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = p.annotations[0]
    assert a.selected_text == quote
    assert a.candidates[0].matched_text == quote
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
        copied = next(page.annots())
        assert copied.info["content"] == "请核对批注是否跟随正确的段落。"
        assert copied.info["title"] == "审阅人"


def test_image_only_annotations_are_unmatched_without_coordinate_copying(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    with pymupdf.open() as rendered:
        p = rendered.new_page()
        p.insert_text((72, 100), "This becomes a raster scan, without a text layer.")
        image = p.get_pixmap().tobytes("png")
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_image(page.rect, stream=image)
            if annotated:
                a = page.add_highlight_annot(pymupdf.Rect(72, 85, 300, 105))
                a.set_info(content="A comment on a scan must not move by coordinates.")
                a.update()
            doc.save(path)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    assert len(p.annotations) == 1
    assert not p.annotations[0].supported
    assert not p.annotations[0].candidates
    assert any("no extractable text" in warning for warning in p.warnings)
    write_json(
        bundle / "decisions.json", Decisions(proposal_id=p.proposal_id, confirmed=True).model_dump()
    )
    result = apply(bundle, bundle / "decisions.json", tmp_path / "result")
    assert result["migrated_count"] == 0
    assert result["unmatched_count"] == 1
