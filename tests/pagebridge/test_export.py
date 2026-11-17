import json

import pytest

for _module in ("pymupdf", "PIL", "numpy", "scipy", "rapidfuzz", "dhash", "reportlab", "pdf_compare"):
    pytest.importorskip(_module)

import pymupdf as fitz

from .helpers import write_pdf
from readwrench.pagebridge.cli import main, new_directory
from readwrench.pagebridge.core import BridgeError, approval_snapshot, make_plan
from readwrench.pagebridge.export import export_comparison, visual_difference


def approve(data):
    data["review"] = {
        "reviewer": "Fixture reviewer",
        "reviewed_at": "2026-11-11T00:00:00Z",
        "approved_map": approval_snapshot(data),
    }
    for row in data["rows"]:
        row["confirmed"] = True
    return data


def test_cli_real_reorder_end_to_end(fixtures, tmp_path):
    review, approved, output = (
        tmp_path / "review",
        tmp_path / "approved.json",
        tmp_path / "comparison",
    )
    assert (
        main(
            [
                "plan",
                str(fixtures / "warn-five.pdf"),
                str(fixtures / "warn-reordered.pdf"),
                "--out",
                str(review),
            ]
        )
        == 0
    )
    assert main(["export", str(review / "mapping.json"), "--out", str(output)]) == 2
    assert not output.exists()
    assert (
        main(
            [
                "confirm",
                str(review / "mapping.json"),
                "--reviewer",
                "Test editor",
                "--out",
                str(approved),
            ]
        )
        == 0
    )
    assert main(["export", str(approved), "--out", str(output), "--aligned"]) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["counts"] == {"matched": 5, "inserted": 0, "deleted": 0, "changed": 0}
    assert summary["structure"] == {"reordered_pairs": 3, "shifted_pairs": 0}
    with fitz.open(output / "diff.pdf") as doc:
        assert len(doc) >= 6
        text = "".join(p.get_text() for p in doc)
        assert "Original - Page 4" in text
        assert "Modified - Page 2" in text
    with (
        fitz.open(output / "aligned-original.pdf") as a,
        fitz.open(output / "aligned-modified.pdf") as b,
    ):
        assert len(a) == len(b) == 5
        assert all(a[i].get_pixmap().samples == b[i].get_pixmap().samples for i in range(5))


def test_insertion_content_diff_export(documents, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    data = approve(make_plan(*documents, assets))
    output = tmp_path / "export"
    output.mkdir()
    summary = export_comparison(data, output)
    assert summary["counts"] == {"matched": 3, "inserted": 1, "deleted": 0, "changed": 1}
    assert summary["structure"] == {"reordered_pairs": 0, "shifted_pairs": 2}
    edited = next(r for r in summary["rows"] if r["original"] == 2)
    assert edited["words_added"] == edited["words_removed"] == 1
    assert edited["visual"]["changed_pixels"] > 0


def test_raster_only_drawing_change(tmp_path):
    paths = [tmp_path / name for name in ("a.pdf", "b.pdf")]
    for path, color in zip(paths, [(1, 0, 0), (0, 0, 1)]):
        with fitz.open() as d:
            page = d.new_page(width=400, height=500)
            page.insert_text((40, 40), "Same text, different diagram color")
            page.draw_rect(fitz.Rect(40, 80, 180, 150), fill=color)
            d.save(path)
    assets = tmp_path / "assets"
    assets.mkdir()
    data = approve(make_plan(*paths, assets))
    output = tmp_path / "export"
    output.mkdir()
    result = export_comparison(data, output)
    assert result["counts"]["changed"] == 1
    assert result["rows"][0]["words_added"] == result["rows"][0]["words_removed"] == 0
    assert result["rows"][0]["visual"]["changed_pixels"] > 1000


def test_completely_blank_pages_export(tmp_path):
    path = tmp_path / "blank.pdf"
    with fitz.open() as d:
        d.new_page()
        d.save(path)
    assets = tmp_path / "assets"
    assets.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    summary = export_comparison(approve(make_plan(path, path, assets)), output, aligned=True)
    assert summary["counts"]["changed"] == 0
    with fitz.open(output / "diff.pdf") as d:
        assert len(d) >= 2


def test_new_directory_cleans_failed_work(tmp_path):
    output = tmp_path / "result"
    with pytest.raises(RuntimeError):
        with new_directory(output) as staged:
            (staged / "half-written.pdf").write_bytes(b"partial")
            raise RuntimeError("Simulated failure")
    assert not output.exists()
    assert not list(tmp_path.glob(".result-*"))


def test_existing_outputs_preserved(tmp_path):
    output = tmp_path / "already-exists"
    output.mkdir()
    (output / "important.pdf").write_bytes(b"preserve me")
    with pytest.raises(BridgeError, match="already exists"):
        with new_directory(output):
            pass
    assert (output / "important.pdf").read_bytes() == b"preserve me"


def test_geometry_change_caught_even_when_white(tmp_path):
    with fitz.open() as a, fitz.open() as b:
        a.new_page(width=300, height=400)
        b.new_page(width=400, height=400)
        _, _, _, visual = visual_difference(a[0], b[0])
        assert visual["geometry_changed"]


def test_unicode_reviewer_and_file_names(documents, tmp_path):
    from readwrench.pagebridge.export import audit_pdf

    assets = tmp_path / "assets"
    assets.mkdir()
    data = approve(make_plan(*documents, assets))
    data["review"]["reviewer"] = "张编辑"
    data["sources"]["original"]["name"] = "原稿.pdf"
    summary = {
        "generated_at": "2026-11-11T00:00:00Z",
        "rows": [],
        "counts": {"matched": 0, "inserted": 0, "deleted": 0, "changed": 0},
    }
    with fitz.open(stream=audit_pdf(data, summary), filetype="pdf") as pdf:
        text = "".join(p.get_text() for p in pdf)
        assert "张编辑" in text
        assert "原稿.pdf" in text


def test_no_corresponding_pages_still_exports_a_complete_map(tmp_path):
    a = write_pdf(tmp_path / "orchard.pdf", ["Orchards produce summer peaches and apples."])
    b = write_pdf(tmp_path / "galaxy.pdf", ["Astronomers survey a distant galaxy."])
    assets = tmp_path / "assets"
    assets.mkdir()
    data = approve(make_plan(a, b, assets, min_score=1.0))
    output = tmp_path / "comparison"
    output.mkdir()
    result = export_comparison(data, output, aligned=True)
    assert result["counts"] == {"matched": 0, "inserted": 1, "deleted": 1, "changed": 0}
    with (
        fitz.open(output / "aligned-original.pdf") as old,
        fitz.open(output / "aligned-modified.pdf") as new,
    ):
        assert len(old) == len(new) == 2
        assert "Orchards" in old[0].get_text() and not new[0].get_text().strip()
        assert not old[1].get_text().strip() and "Astronomers" in new[1].get_text()


def test_changed_only_retains_map_and_bookmarks_actual_changes(documents, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    data = approve(make_plan(*documents, assets))
    output = tmp_path / "changes"
    output.mkdir()
    summary = export_comparison(data, output, changed_only=True, aligned=True)
    assert summary["counts"] == {"matched": 3, "inserted": 1, "deleted": 0, "changed": 1}
    assert len(summary["rows"]) == 4
    shown = [row for row in summary["rows"] if row["report_page"] is not None]
    assert [row["kind"] for row in shown] == ["changed", "inserted"]
    with fitz.open(output / "diff.pdf") as pdf:
        assert len(pdf) == summary["audit_pages"] + 2
        assert [item[2] for item in pdf.get_toc()[1:]] == [row["report_page"] for row in shown]
        text = "".join(page.get_text() for page in pdf)
        assert "Orchards produce" not in text
        assert "railway station" in text
    with fitz.open(output / "aligned-original.pdf") as aligned:
        assert len(aligned) == len(summary["rows"])


def test_changed_only_identical_inputs_still_produce_valid_audit(documents, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    data = approve(make_plan(documents[0], documents[0], assets))
    output = tmp_path / "identical"
    output.mkdir()
    summary = export_comparison(data, output, changed_only=True)
    assert summary["counts"]["changed"] == 0
    assert all(row["report_page"] is None for row in summary["rows"])
    with fitz.open(output / "diff.pdf") as pdf:
        assert len(pdf) == summary["audit_pages"] >= 1
        assert len(pdf.get_toc()) == 1
