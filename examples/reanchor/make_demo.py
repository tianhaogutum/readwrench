"""Create a realistic, locally invented editorial revision pair with ReportLab.

Source formatting is produced by ReportLab, embedded annotations by PyMuPDF.
No downloaded document or extracted personal data is used in this demo.

Needs PyMuPDF and ReportLab (``pip install 'readwrench[reanchor]' reportlab``):

    python examples/reanchor/make_demo.py demo-output/editorial-input

The test suite (``tests/reanchor``) also builds its editorial pair with this script.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

LONG_QUOTE = (
    "The review board requires a documented explanation of each proposed change "
    "before the revised document can be approved for circulation."
)
CHANGED_OLD = "The response time improved by twelve percent during the pilot."
CHANGED_NEW = "The response time improved by twenty percent during the pilot."
REMOVED = "Xylophone zebras navigate the obsolete lunar appendix."


def build_pair(output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles["BodyText"].fontSize = 11
    styles["BodyText"].leading = 16

    def pdf(name, title, paragraphs):
        path = output / name
        story = [Paragraph(title, styles["Title"]), Spacer(1, 18)]
        for text in paragraphs:
            story.extend([Paragraph(text, styles["BodyText"]), Spacer(1, 16)])
        SimpleDocTemplate(str(path), pagesize=A4, rightMargin=140, leftMargin=72).build(story)
        return path

    old_clean = pdf(
        "old-clean.pdf",
        "Revision policy · Draft 1",
        [
            "A. Editorial review",
            LONG_QUOTE,
            "Assess the supporting evidence before approval.",
            "Remove the redundant qualification in this sentence.",
            "The implementation details need clarification.",
            CHANGED_OLD,
            REMOVED,
            "A sticky note concerns the final review procedure.",
        ],
    )
    new = pdf(
        "revision.pdf",
        "Revision policy · Draft 2",
        [
            "New introduction: additional background changed pagination and line breaks.",
            "B. Final review procedure",
            "A sticky note concerns the final review procedure.",
            CHANGED_NEW,
            "A. Editorial review",
            "The implementation details need clarification.",
            LONG_QUOTE,
            "Assess the supporting evidence before approval.",
            "Remove the redundant qualification in this sentence.",
        ],
    )
    old = output / "annotated.pdf"
    with pymupdf.open(old_clean) as doc:
        page = doc[0]
        for kind, quote, comment in [
            ("highlight", LONG_QUOTE, "Does every change really require board review?"),
            ("underline", "supporting evidence", "Add a reference."),
            ("strikeout", "redundant qualification", "Please delete this wording."),
            ("squiggly", "implementation details", "Define what this covers."),
            ("highlight", CHANGED_OLD, "Verify the pilot measurement."),
            ("highlight", REMOVED, "This appendix was removed; retain the comment for follow-up."),
        ]:
            method = getattr(page, "add_" + kind + "_annot")
            annot = method(page.search_for(quote, quads=True))
            annot.set_info(
                title="Editorial reviewer",
                subject="Draft review",
                content=comment,
                creationDate="D:20261110120000Z",
                modDate="D:20261110130000Z",
            )
            annot.set_colors(stroke=(1, 0.75, 0))
            annot.set_opacity(0.65)
            annot.update()
        point = page.search_for("A sticky note")[0].tr + (8, 0)
        annot = page.add_text_annot(point, "Who signs off on the final review?")
        annot.set_info(title="Editor", subject="Process")
        annot.update()
        annot = page.add_freetext_annot(
            (72, 720, 390, 750), "A custom text appearance stays with the original."
        )
        annot.set_info(title="Designer")
        annot.update()
        doc.save(old)
    with pymupdf.open(new) as doc:
        page = doc[0]
        page.add_text_annot((500, 60), "Existing comment in the new revision.")
        doc.save(output / "revision-noted.pdf")
    old_clean.unlink()
    return old, output / "revision-noted.pdf"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    for path in build_pair(args.output):
        print(path)


if __name__ == "__main__":
    main()
