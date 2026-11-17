from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

import pymupdf

from readwrench.reanchor.text import TextIndex
from readwrench.reanchor.workflow import propose


def test_note_near_bottom_of_long_block_uses_that_line(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    lines = [
        f"Background line {i}: editorial context supplies a different procedure." for i in range(14)
    ]
    lines.append("Final approval requires the designated editor to sign the release.")
    for path, annotated in [(old, True), (new, False)]:
        with pymupdf.open() as doc:
            page = doc.new_page(width=700)
            page.insert_text((72, 100 if annotated else 200), "\n".join(lines), fontsize=10)
            if annotated:
                rect = page.search_for(lines[-1])[0]
                page.add_text_annot((rect.x1 + 5, rect.y0), "Who is the designated editor?")
            doc.save(path)
    p = propose(old, new, tmp_path / "review")
    a = p.annotations[0]
    assert "final approval" in a.selected_text
    assert a.candidates
    assert "final approval" in a.candidates[0].matched_text


def test_exact_decoy_inside_same_text_block_keeps_changed_alternative(tmp_path):
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    quote = "The deployment reduced latency by twelve percent across all regions."
    for path, revised in [(old, False), (new, True)]:
        with pymupdf.open() as doc:
            page = doc.new_page(width=750)
            lines = [
                "Team Alpha measured its own deployment.",
                quote.replace("twelve", "twenty") if revised else quote,
                "Team Beta measured a separate deployment.",
                quote,
            ]
            page.insert_text((50, 100), "\n".join(lines), fontsize=10)
            assert len(TextIndex(page).blocks) == 1
            if not revised:
                page.add_highlight_annot(page.search_for(quote, quads=True)[0])
            doc.save(path)
    p = propose(old, new, tmp_path / "review")
    a = p.annotations[0]
    assert any(
        "twenty percent" in c.matched_text and c.evidence.method == "fuzzy" for c in a.candidates
    )
    assert any("twelve percent" in c.matched_text for c in a.candidates)
