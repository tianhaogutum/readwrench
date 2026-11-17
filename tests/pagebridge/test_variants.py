"""Actual PDF geometry, annotations, widgets and upstream generator variants."""

import pytest

for _module in ("pymupdf", "PIL", "numpy", "scipy", "rapidfuzz", "dhash", "reportlab", "pdf_compare"):
    pytest.importorskip(_module)

import pymupdf as fitz

from readwrench.pagebridge.core import (
    BridgeError,
    approval_snapshot,
    extract,
    make_plan,
    open_pdf,
    proposed_map,
)
from readwrench.pagebridge.export import (
    aligned_page,
    export_comparison,
    prepare_document,
    visual_difference,
)


def exported(paths, tmp_path, *, aligned=True):
    assets = tmp_path / "assets"
    assets.mkdir()
    plan = make_plan(*paths, assets)
    for row in plan["rows"]:
        row["confirmed"] = True
    plan["review"] = {
        "reviewer": "Generated fixture review",
        "reviewed_at": "2026-11-11T00:00:00Z",
        "approved_map": approval_snapshot(plan),
    }
    output = tmp_path / "comparison"
    output.mkdir()
    return plan, export_comparison(plan, output, aligned=aligned), output


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_cropped_number_edit(rotation, tmp_path):
    paths = [tmp_path / name for name in ("old.pdf", "new.pdf")]
    for path, number in zip(paths, ("17", "19")):
        with fitz.open() as doc:
            page = doc.new_page(width=420, height=600)
            page.insert_text((70, 100), f"The approved quantity is {number} units.", fontsize=16)
            page.draw_rect(fitz.Rect(70, 150, 240, 230), color=(0.1, 0.4, 0.7))
            page.set_cropbox(fitz.Rect(40, 50, 380, 550))
            page.set_rotation(rotation)
            doc.save(path)
    _, result, output = exported(paths, tmp_path)
    assert result["counts"]["changed"] == 1
    assert result["rows"][0]["words_added"] == result["rows"][0]["words_removed"] == 1
    with fitz.open(output / "diff.pdf") as doc:
        report = doc[-1]
        assert report.rect.width > report.rect.height if rotation in (90, 270) else True
        # Host-highlighted word rectangles must remain inside the source panels.
        rects = [d["rect"] for d in report.get_drawings() if d["fill"] is not None]
        assert rects
        assert all(report.rect.contains(rect) for rect in rects)
        pix = report.get_pixmap(matrix=fitz.Matrix(0.6, 0.6))
        assert pix.width > 0 and pix.height > 0
        with fitz.open(paths[0]) as old, fitz.open(paths[1]) as new:
            mask, _, _, visual = visual_difference(old[0], new[0])
            scale = visual["effective_dpi"] / 72
            delta = fitz.Rect(mask.getbbox()) / scale
            expected = delta + (20, 60, 20, 60)
            removed_boxes = [
                drawing["rect"]
                for drawing in report.get_drawings()
                if drawing["fill"] is not None
                and drawing["fill"][0] > 0.99
                and abs(drawing["fill"][1] - 0.7) < 0.01
            ]
            assert len(removed_boxes) == 1
            assert removed_boxes[0].intersects(expected), (
                "Word highlight misses the actual rendered edit"
            )
    for source_path, aligned_name in zip(paths, ["aligned-original.pdf", "aligned-modified.pdf"]):
        with fitz.open(source_path) as source, fitz.open(output / aligned_name) as aligned_pdf:
            assert source[0].get_pixmap().samples == aligned_pdf[0].get_pixmap().samples


def test_form_value_change_and_source_widgets_preserved(tmp_path):
    paths = [tmp_path / name for name in ("form-old.pdf", "form-new.pdf")]
    for path, value in zip(paths, ("100", "200")):
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=550)
            page.insert_text((40, 50), "Invoice total:")
            widget = fitz.Widget()
            widget.field_name = "invoice_total"
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            widget.rect = fitz.Rect(40, 80, 200, 115)
            widget.field_value = value
            widget.text_fontsize = 16
            page.add_widget(widget)
            doc.save(path)
    _, result, output = exported(paths, tmp_path)
    assert result["counts"]["changed"] == 1
    assert result["rows"][0]["visual"]["changed_pixels"] > 0
    for path, value in zip(paths, ("100", "200")):
        with fitz.open(path) as source:
            widgets = list(source[0].widgets())
            assert len(widgets) == 1 and widgets[0].field_value == value
    with fitz.open(output / "aligned-modified.pdf") as derivative:
        assert not list(derivative[0].widgets())
        assert "200" in derivative[0].get_text()


@pytest.mark.parametrize(
    "name",
    [
        "annotations-rotated-90.pdf",
        "annotations-unicode-issues.pdf",
        "ag-energy-round-up-2017-02-24.pdf",
    ],
)
def test_real_upstream_annotation_and_generator_variants(name, fixtures, tmp_path):
    path = fixtures / name
    _, result, output = exported((path, path), tmp_path)
    assert result["counts"]["changed"] == 0
    with (
        fitz.open(output / "aligned-original.pdf") as a,
        fitz.open(output / "aligned-modified.pdf") as b,
    ):
        assert a[0].get_pixmap().samples == b[0].get_pixmap().samples


def test_real_multicolumn_federal_register_reorder_and_stamp(fixtures, tmp_path):
    source = fixtures / "federal-register-2020-17221.pdf"
    revised = tmp_path / "revised-register.pdf"
    order = [0, 1, 7, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14]
    with fitz.open(source) as doc:
        doc.select(order)
        annotation = doc[4].add_rect_annot(fitz.Rect(30, 40, 100, 70))
        annotation.set_colors(stroke=(0.9, 0.1, 0.1))
        annotation.set_border(width=3)
        annotation.update()
        doc.save(revised)
    plan, result, _ = exported((source, revised), tmp_path)
    assert {r["original"]: r["modified"] for r in plan["rows"]} == {
        old + 1: new + 1 for new, old in enumerate(order)
    }
    assert result["counts"] == {"matched": 15, "inserted": 0, "deleted": 0, "changed": 1}
    changed = next(row for row in result["rows"] if row["kind"] == "changed")
    assert changed["original"] == 4 and changed["modified"] == 5


def test_mixed_text_and_image_pages(fixtures, tmp_path):
    source, revised = tmp_path / "mixed.pdf", tmp_path / "mixed-reordered.pdf"
    with (
        fitz.open(fixtures / "warn-five.pdf") as words,
        fitz.open(fixtures / "scan-before.pdf") as scans,
        fitz.open() as doc,
    ):
        doc.insert_pdf(words, from_page=1, to_page=1)
        doc.insert_pdf(scans, from_page=0, to_page=0)
        doc.insert_pdf(words, from_page=4, to_page=4)
        doc.save(source)
        doc.select([2, 1, 0])
        doc.save(revised)
    _, a = extract(source)
    _, b = extract(revised)
    rows = proposed_map(a, b)
    assert [(r["original"], r["modified"]) for r in rows] == [(1, 3), (2, 2), (3, 1)]
    assert "visual_only" in rows[1]["reasons"]


@pytest.mark.parametrize("variant", ["user_unit", "nonzero_media_origin"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_unusual_physical_page_coordinates_preserve_visible_render(variant, rotation, tmp_path):
    path = tmp_path / f"{variant}.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=550)
        page.insert_text((50, 50), "Visible coordinate test", fontsize=18)
        page.draw_rect(fitz.Rect(40, 80, 180, 130), fill=(0.2, 0.5, 0.8))
        if variant == "user_unit":
            doc.xref_set_key(page.xref, "UserUnit", "2")
        else:
            page.set_mediabox(fitz.Rect(-30, -40, 370, 510))
        page.set_rotation(rotation)
        doc.save(path)
    with open_pdf(path) as source, fitz.open() as output:
        before = source[0].get_pixmap().samples
        prepare_document(source)
        rect = source[0].rect
        aligned_page(output, source, 0, rect.width, rect.height)
        assert output[0].get_pixmap().samples == before


def test_optional_content_layers_fail_before_unreliable_vector_copy(tmp_path):
    path = tmp_path / "layers.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 50), "Visible public content")
        layer = doc.add_ocg("Hidden notes", on=False)
        page.insert_text((50, 200), "Hidden content must not become visible", oc=layer)
        doc.save(path)
    with pytest.raises(BridgeError, match="flatten visible layers"):
        extract(path)
