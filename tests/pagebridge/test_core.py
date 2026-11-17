import copy
import io
import json

import pytest

for _module in ("pymupdf", "PIL", "numpy", "scipy", "rapidfuzz", "dhash", "reportlab"):
    pytest.importorskip(_module)

import pymupdf as fitz
from PIL import Image

from .helpers import write_pdf
from readwrench.pagebridge.core import (
    BridgeError,
    approval_snapshot,
    extract,
    make_plan,
    open_pdf,
    proposed_map,
    read_plan,
    validate_map,
    verify_sources,
)
from readwrench.pagebridge.review import write_review


def test_real_report_reorder(fixtures):
    _, a = extract(fixtures / "warn-five.pdf")
    _, b = extract(fixtures / "warn-reordered.pdf")
    rows = proposed_map(a, b)
    assert [(r["original"], r["modified"]) for r in rows] == [
        (1, 1),
        (2, 3),
        (3, 4),
        (4, 2),
        (5, 5),
    ]
    assert rows[3]["status"] == "pending"
    assert "reordered" in rows[3]["reasons"]


def test_exact_match_floor_still_allows_reordering(fixtures):
    _, a = extract(fixtures / "warn-five.pdf")
    _, b = extract(fixtures / "warn-reordered.pdf")
    rows = proposed_map(a, b, min_score=1.0)
    assert [(r["original"], r["modified"]) for r in rows] == [
        (1, 1),
        (2, 3),
        (3, 4),
        (4, 2),
        (5, 5),
    ]


def test_real_scans_reorder(fixtures):
    _, a = extract(fixtures / "scan-before.pdf")
    _, b = extract(fixtures / "scan-after.pdf")
    rows = proposed_map(a, b)
    assert [(r["original"], r["modified"]) for r in rows] == [(1, 2), (2, 1)]
    assert all("visual_only" in r["reasons"] and r["status"] == "pending" for r in rows)


def test_insertion_and_actual_edit(documents, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    data = make_plan(*documents, assets)
    assert [(r["original"], r["modified"]) for r in data["rows"]] == [
        (1, 1),
        (2, 3),
        (3, 4),
        (None, 2),
    ]
    assert not any("reordered" in r["reasons"] for r in data["rows"])
    validate_map(data)
    assert len(list(assets.glob("*.png"))) == 7


def test_duplicates_stay_uncertain(tmp_path):
    a = write_pdf(tmp_path / "dup.pdf", ["Repeated form content"] * 2)
    _, features = extract(a)
    rows = proposed_map(features, features)
    assert [(r["original"], r["modified"]) for r in rows] == [(1, 1), (2, 2)]
    assert all(r["status"] == "pending" and "competing_candidate" in r["reasons"] for r in rows)


def test_blank_pages_stay_pending(tmp_path):
    path = tmp_path / "blank.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(path)
    _, features = extract(path)
    rows = proposed_map(features, features)
    assert "blank_looking" in rows[0]["reasons"]


@pytest.fixture
def plan(documents, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    return make_plan(*documents, assets)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: p["rows"][0].update(original=True), "integer"),
        (lambda p: p["rows"][0].update(modified=1.0), "integer"),
        (lambda p: p["rows"][0].update(modified=0), "integer"),
        (lambda p: p["rows"][1].update(modified=1), "more than once"),
        (lambda p: p["rows"].pop(), "Unaccounted"),
        (lambda p: p["rows"][0].update(original=None, modified=None), "needs"),
        (lambda p: p["sources"]["modified"].update(pages=201), "page count"),
        (lambda p: p.update(schema="other/v1"), "schema"),
    ],
)
def test_reject_invalid_maps(plan, mutate, message):
    mutate(plan)
    with pytest.raises(BridgeError, match=message):
        validate_map(plan)


def test_no_export_without_human_approval(plan):
    with pytest.raises(BridgeError, match="not confirmed"):
        validate_map(plan, approved=True)
    for row in plan["rows"]:
        row["confirmed"] = True
    with pytest.raises(BridgeError, match="reviewer"):
        validate_map(plan, approved=True)


def test_changing_pairs_invalidates_approval(plan):
    for row in plan["rows"]:
        row["confirmed"] = True
    plan["review"] = {
        "reviewer": "Editor",
        "reviewed_at": "2026-11-11T00:00:00Z",
        "approved_map": approval_snapshot(plan),
    }
    validate_map(plan, approved=True)
    # An approval cannot silently follow a later accidental page reassignment.
    plan["rows"][0]["modified"], plan["rows"][1]["modified"] = (
        plan["rows"][1]["modified"],
        plan["rows"][0]["modified"],
    )
    with pytest.raises(BridgeError, match="approval|Approval"):
        validate_map(plan, approved=True)


def test_changed_source_rejected(plan, documents):
    documents[0].write_bytes(documents[0].read_bytes() + b"\n%changed\n")
    with pytest.raises(BridgeError, match="SHA-256 changed"):
        verify_sources(plan)


def test_source_can_be_relocated(plan, documents, tmp_path):
    relocated = tmp_path / "relocated.pdf"
    documents[0].rename(relocated)
    paths = verify_sources(plan, original=relocated)
    assert paths["original"] == relocated


def test_invalid_and_encrypted_pdfs(tmp_path):
    path = tmp_path / "invalid.pdf"
    path.write_bytes(b"%PDF-1.7\nnot actually a document")
    with pytest.raises(BridgeError, match="Cannot read"):
        with open_pdf(path):
            pass
    with fitz.open() as doc:
        doc.new_page()
        doc.save(
            tmp_path / "locked.pdf",
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner-password",
            user_pw="user-password",
        )
    with pytest.raises(BridgeError, match="Encrypted"):
        with open_pdf(tmp_path / "locked.pdf"):
            pass


def test_page_bound_before_rendering(tmp_path):
    path = tmp_path / "too-many.pdf"
    with fitz.open() as doc:
        for _ in range(201):
            doc.new_page()
        doc.save(path)
    with pytest.raises(BridgeError, match="1-200"):
        with open_pdf(path):
            pass


def test_huge_image_metadata_rejected_before_render(tmp_path, monkeypatch):
    path = tmp_path / "oversized-image.pdf"
    stream = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(stream, format="PNG")
    with fitz.open() as doc:
        page = doc.new_page()
        xref = page.insert_image(fitz.Rect(20, 20, 40, 40), stream=stream.getvalue())
        # Malformed hostile metadata only: never allocate or generate this image.
        doc.xref_set_key(xref, "Width", "100000")
        doc.xref_set_key(xref, "Height", "100000")
        doc.save(path)

    def no_render(*args, **kwargs):
        raise AssertionError("Oversized image must be rejected before rendering")

    monkeypatch.setattr(fitz.Page, "get_pixmap", no_render)
    with pytest.raises(BridgeError, match="image budget"):
        extract(path)


def test_review_html_does_not_execute_pdf_text(plan, tmp_path):
    data = copy.deepcopy(plan)
    data["pages"]["original"][0]["text_excerpt"] = '</script><script>alert("pdf")</script>'
    data["sources"]["original"]["name"] = '<img src=x onerror="alert(1)">.pdf'
    path = tmp_path / "review.html"
    write_review(data, path)
    html = path.read_text()
    assert "</script><script>alert" not in html
    assert "<img src=x onerror=" not in html
    assert "connect-src 'none'" in html
    assert "\\u003c/script>" in html


def test_invalid_json_error(tmp_path):
    path = tmp_path / "map.json"
    path.write_text('{"rows":')
    with pytest.raises(BridgeError, match="Invalid mapping JSON"):
        read_plan(path)


def test_real_page_deletions(fixtures, tmp_path):
    revised = tmp_path / "deleted.pdf"
    with fitz.open(fixtures / "warn-five.pdf") as doc:
        doc.select([0, 2, 4])
        doc.save(revised)
    _, a = extract(fixtures / "warn-five.pdf")
    _, b = extract(revised)
    rows = proposed_map(a, b)
    assert [(r["original"], r["modified"]) for r in rows] == [
        (1, 1),
        (2, None),
        (3, 2),
        (4, None),
        (5, 3),
    ]
    assert not any("reordered" in r["reasons"] for r in rows)


def test_five_real_insertions_beyond_host_lookahead(fixtures, tmp_path):
    revised = tmp_path / "inserted.pdf"
    with (
        fitz.open(fixtures / "ag-energy-round-up-2017-02-24.pdf") as insert,
        fitz.open(fixtures / "warn-five.pdf") as old,
        fitz.open() as doc,
    ):
        for _ in range(5):
            doc.insert_pdf(insert)
        doc.insert_pdf(old)
        doc.save(revised)
    _, a = extract(fixtures / "warn-five.pdf")
    _, b = extract(revised)
    rows = proposed_map(a, b)
    assert [(r["original"], r["modified"]) for r in rows[:5]] == [
        (1, 6),
        (2, 7),
        (3, 8),
        (4, 9),
        (5, 10),
    ]
    assert [(r["original"], r["modified"]) for r in rows[5:]] == [
        (None, 1),
        (None, 2),
        (None, 3),
        (None, 4),
        (None, 5),
    ]
    assert not any("reordered" in r["reasons"] for r in rows)


def test_missing_nullable_field_is_a_clear_error(plan):
    omitted = plan["rows"][0].pop("modified")
    plan["rows"].append({"original": None, "modified": omitted, "confirmed": False})
    with pytest.raises(BridgeError, match="explicitly"):
        validate_map(plan)


def test_utf8_bom_from_a_text_editor_is_accepted(plan, tmp_path):
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(plan).encode("utf-8"))
    assert read_plan(path)["rows"] == plan["rows"]
