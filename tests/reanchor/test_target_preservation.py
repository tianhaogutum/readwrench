from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex", "pypdf"):
    pytest.importorskip(_module)

import pymupdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

from readwrench.reanchor.models import Choice, Decisions
from readwrench.reanchor.workflow import ReviewError, apply, propose, write_json


def test_existing_target_fields_bookmarks_attachments_and_audit_xrefs(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    with pymupdf.open() as doc:
        p = doc.new_page()
        p.insert_text((72, 100), "The target document must retain its existing objects.")
        a = p.add_underline_annot(p.search_for("existing objects", quads=True))
        a.set_info(content="Keep these objects", title="Editor")
        a.update()
        doc.save(old)
    with pymupdf.open() as doc:
        p = doc.new_page()
        p.insert_text((72, 150), "The target document must retain its existing objects.")
        widget = pymupdf.Widget()
        widget.field_name = "reviewer"
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_value = "Alice"
        widget.rect = pymupdf.Rect(72, 220, 300, 250)
        p.add_widget(widget)
        p.add_text_annot((450, 90), "Already in the revision")
        doc.set_toc([[1, "Review section", 1]])
        doc.embfile_add("notes.txt", b"Existing attachment bytes")
        # Retain unreachable objects to force garbage=3 to renumber xrefs later.
        doc.new_page().insert_text((72, 100), "This page will be deleted")
        doc.delete_page(1)
        doc.save(new, garbage=0)
    bundle = tmp_path / "review"
    p = propose(old, new, bundle)
    a = p.annotations[0]
    write_json(
        bundle / "decisions.json",
        Decisions(
            proposal_id=p.proposal_id,
            confirmed=True,
            choices=[Choice(annotation_id=a.id, action="accept", candidate_id=a.candidates[0].id)],
        ).model_dump(),
    )
    audit = apply(bundle, bundle / "decisions.json", tmp_path / "result")
    output = tmp_path / "result" / "migrated.pdf"
    assert PdfReader(output).get_fields()["reviewer"]["/V"] == "Alice"
    with pymupdf.open(output) as doc:
        assert doc.embfile_get("notes.txt") == b"Existing attachment bytes"
        assert doc.get_toc() == [[1, "Review section", 1]]
        page = doc[0]
        assert next(page.widgets()).field_value == "Alice"
        row = audit["migrated"][0]
        copied = page.load_annot(row["new_xref"])
        assert copied.info["id"] == row["new_name"]
        assert copied.info["content"] == "Keep these objects"
        assert any(a.info["content"] == "Already in the revision" for a in page.annots())


def test_signature_field_without_optional_sigflags_is_refused(pair, tmp_path):
    old, new = pair
    writer = PdfWriter(clone_from=new)
    signature = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Sig"),
            NameObject("/T"): TextStringObject("ApprovalSignature"),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in [72, 72, 200, 100]]),
        }
    )
    ref = writer._add_object(signature)
    writer.pages[0].setdefault(NameObject("/Annots"), ArrayObject()).append(ref)
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
        {NameObject("/Fields"): ArrayObject([ref])}
    )
    unsigned_form = tmp_path / "signature-form.pdf"
    writer.write(unsigned_form)
    with pymupdf.open(unsigned_form) as doc:
        assert doc.get_sigflags() <= 0
        assert any(w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE for w in doc[0].widgets())
    bundle = tmp_path / "review"
    p = propose(old, unsigned_form, bundle)
    write_json(
        bundle / "decisions.json", Decisions(proposal_id=p.proposal_id, confirmed=True).model_dump()
    )
    with pytest.raises(ReviewError, match="signature fields"):
        apply(bundle, bundle / "decisions.json", tmp_path / "result")
    assert not (tmp_path / "result").exists()
