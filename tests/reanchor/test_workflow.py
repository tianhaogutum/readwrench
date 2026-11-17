from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

import json
import zipfile

import pymupdf

from readwrench.reanchor.models import Choice, Decisions
from readwrench.reanchor.workflow import ReviewError, apply, propose, sha256, write_json


def decide(bundle, proposal, *, accept=None, confirmed=True):
    choices = []
    for a in proposal.annotations:
        if a.candidates and (accept is None or accept(a)):
            choices.append(
                Choice(annotation_id=a.id, action="accept", candidate_id=a.candidates[0].id)
            )
    path = bundle / "decisions.json"
    write_json(
        path,
        Decisions(
            proposal_id=proposal.proposal_id, confirmed=confirmed, choices=choices
        ).model_dump(),
    )
    return path


def test_real_editorial_roundtrip(pair, tmp_path):
    old, new = pair
    before = (sha256(old), sha256(new))
    bundle = tmp_path / "review"
    proposal = propose(old, new, bundle)
    assert len(proposal.annotations) == 8
    assert sum(a.supported for a in proposal.annotations) == 7
    multiline = next(a for a in proposal.annotations if "board requires" in a.selected_text)
    assert len(multiline.candidates[0].quads) >= 2
    assert "circulation" in multiline.candidates[0].matched_text
    changed = next(a for a in proposal.annotations if "twelve percent" in a.selected_text)
    assert any("twenty percent" in c.matched_text for c in changed.candidates)
    sticky = next(a for a in proposal.annotations if a.kind == "text")
    assert sticky.anchor_basis == "nearby text"
    assert sticky.candidates
    removed = next(a for a in proposal.annotations if "xylophone" in a.selected_text)
    assert not removed.candidates
    decisions = decide(bundle, proposal)
    audit = apply(bundle, decisions, tmp_path / "result")
    assert audit["migrated_count"] == 6
    assert audit["unmatched_count"] == 2
    assert before == (sha256(old), sha256(new))
    with pymupdf.open(tmp_path / "result" / "migrated.pdf") as doc:
        records = [
            (a.type[1], a.info, a.opacity, a.vertices) for p in doc for a in p.annots() or []
        ]
    assert len(records) == 7  # includes an existing target comment
    assert any(
        info["content"] == "Existing comment in the new revision." for _, info, _, _ in records
    )
    copied = next(row for row in records if row[1].get("content", "").startswith("Does every"))
    assert copied[1]["title"] == "Editorial reviewer"
    assert copied[1]["creationDate"] == "D:20261110120000Z"
    assert copied[2] == pytest.approx(0.65)
    assert len(copied[3]) >= 8
    assert {kind for kind, *_ in records} >= {
        "Highlight",
        "Underline",
        "StrikeOut",
        "Squiggly",
        "Text",
    }
    # A second PDF parser must see native, editable annotation dictionaries.
    PdfReader = pytest.importorskip("pypdf").PdfReader
    parsed = PdfReader(tmp_path / "result" / "migrated.pdf")
    assert sum(len(p.get("/Annots", [])) for p in parsed.pages) >= 7
    with zipfile.ZipFile(tmp_path / "result" / "unmatched.zip") as z:
        assert z.read("original.pdf") == old.read_bytes()
        unmatched = json.loads(z.read("unmatched.json"))["annotations"]
        assert len(unmatched) == 2
        assert any(r["annotation"]["kind"] == "freetext" for r in unmatched)


@pytest.mark.parametrize(
    "failure",
    [
        "unconfirmed",
        "wrong-proposal",
        "wrong-candidate",
        "modified-image",
        "modified-input",
        "modified-proposal",
    ],
)
def test_refuses_invalid_review(pair, tmp_path, failure):
    old, new = pair
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    dpath = decide(bundle, p)
    data = json.loads(dpath.read_text())
    if failure == "unconfirmed":
        data["confirmed"] = False
    elif failure == "wrong-proposal":
        data["proposal_id"] = "wrong"
    elif failure == "wrong-candidate":
        data["choices"][0]["candidate_id"] = "wrong"
    elif failure == "modified-image":
        (bundle / p.annotations[0].image.path).write_bytes(b"changed")
    elif failure == "modified-input":
        with new.open("ab") as f:
            f.write(b"\n% edited after review\n")
    elif failure == "modified-proposal":
        raw = json.loads((bundle / "proposal.json").read_text())
        raw["annotations"][0]["info"]["content"] = "edited"
        write_json(bundle / "proposal.json", raw)
    write_json(dpath, data)
    with pytest.raises((ReviewError, ValueError)):
        apply(bundle, dpath, tmp_path / "result")
    assert not (tmp_path / "result").exists()
    assert not list(tmp_path.glob(".result-*"))


def test_empty_choices_preserve_everything(pair, tmp_path):
    old, new = pair
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    dpath = decide(bundle, p, accept=lambda a: False)
    result = apply(bundle, dpath, tmp_path / "result")
    assert result["migrated_count"] == 0
    assert result["unmatched_count"] == len(p.annotations)


@pytest.mark.parametrize(
    "name",
    [
        "hotos17.pdf",
        "word2column.pdf",
        "FreeText-annotation.pdf",
        "swapped-corners.pdf",
        "indirect-objects.pdf",
        "annots-entries-without-subtype.pdf",
    ],
)
def test_real_upstream_formats(upstream, tmp_path, name):
    source = upstream / name
    bundle = tmp_path / "review"
    p = propose(source, source, bundle)
    assert p.annotations
    assert (bundle / "review.html").is_file()
    for item in p.annotations:
        assert item.image
        if item.supported:
            assert item.candidates, (name, item.selected_text, item.warnings)
    dpath = decide(bundle, p)
    audit = apply(bundle, dpath, tmp_path / "result")
    assert audit["migrated_count"] + audit["unmatched_count"] == len(p.annotations)


def test_no_overwrite(pair, tmp_path):
    old, new = pair
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ReviewError, match="already exists"):
        propose(old, new, output)
    assert marker.read_text() == "keep"


def test_invalid_and_encrypted_pdfs(tmp_path):
    invalid = tmp_path / "bad.pdf"
    invalid.write_text("not a PDF")
    with pytest.raises((RuntimeError, ValueError, pymupdf.FileDataError)):
        propose(invalid, invalid, tmp_path / "bad")
    protected = tmp_path / "protected.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Secret")
        doc.save(
            protected, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="test", owner_pw="owner"
        )
    with pytest.raises(ReviewError, match="Encrypted"):
        propose(protected, protected, tmp_path / "encrypted")


def test_decisions_schema_is_strict():
    with pytest.raises(ValueError):
        Decisions(proposal_id="x", confirmed="true")
    with pytest.raises(ValueError):
        Choice(annotation_id="a", action="accept")
    with pytest.raises(ValueError):
        Decisions(proposal_id="x", choices=[Choice(annotation_id="a", action="reject")] * 2)


def test_distinct_comments_on_identical_quads_are_not_collapsed(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    quote = "Two reviewers can comment on the same passage."
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 150), quote)
            if annotated:
                for author, content in [
                    ("Alice", "Clarify the claim."),
                    ("Bob", "Keep the supporting example. 🙂"),
                ]:
                    a = page.add_highlight_annot(page.search_for(quote, quads=True))
                    a.set_info(title=author, content=content)
                    a.update()
            doc.save(path)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    assert len(p.annotations) == 2
    assert len({a.id for a in p.annotations}) == 2
    dpath = decide(bundle, p)
    result = apply(bundle, dpath, tmp_path / "result")
    assert result["migrated_count"] == 2
    with pymupdf.open(tmp_path / "result" / "migrated.pdf") as doc:
        page = doc[0]
        records = [a.info for a in page.annots()]
        assert {a["title"] for a in records} == {"Alice", "Bob"}
        assert len({a["id"] for a in records}) == 2
        assert any(a["content"].endswith("🙂") for a in records)


def test_no_source_comments_preserves_existing_target_comments(pair, tmp_path):
    old, new = pair
    unannotated = tmp_path / "unannotated.pdf"
    with pymupdf.open(old) as doc:
        for page in doc:
            for xref in [a.xref for a in page.annots() or []]:
                page.delete_annot(page.load_annot(xref))
        doc.save(unannotated)
    bundle = tmp_path / "review"
    p = propose(unannotated, new, bundle)
    assert not p.annotations
    assert any("No embedded review annotations" in warning for warning in p.warnings)
    path = decide(bundle, p)
    audit = apply(bundle, path, tmp_path / "result")
    assert audit["total"] == audit["migrated_count"] == audit["unmatched_count"] == 0
    with pymupdf.open(tmp_path / "result" / "migrated.pdf") as doc:
        assert sum(len(list(page.annots() or [])) for page in doc) == 1
