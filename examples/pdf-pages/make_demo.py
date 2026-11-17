"""Make a synthetic revision from real upstream PDF pages; never approve it.

The revision inserts a USDA page, deletes WARN page 3, moves WARN page 4,
and changes one numeric cell on WARN page 2. This is test data, not a real
government revision. Run the printed plan command, then review it yourself.

Run from a clone (needs the pdf-pages extra):
    python examples/pdf-pages/make_demo.py --out demo-documents
"""

import argparse
import json
from pathlib import Path

import pymupdf as fitz

from readwrench.pagebridge.cli import new_directory


def make_demo(output: Path):
    fixtures = Path(__file__).resolve().parent
    with new_directory(output) as staging:
        with fitz.open(fixtures / "warn-five.pdf") as original:
            original.save(staging / "original.pdf", garbage=3)
        with (
            fitz.open(fixtures / "warn-five.pdf") as warn,
            fitz.open(fixtures / "ag-energy-round-up-2017-02-24.pdf") as energy,
            fitz.open() as revised,
        ):
            revised.insert_pdf(warn, from_page=0, to_page=0)
            revised.insert_pdf(warn, from_page=3, to_page=3)
            revised.insert_pdf(energy)
            revised.insert_pdf(warn, from_page=1, to_page=1)
            revised.insert_pdf(warn, from_page=4, to_page=4)
            page = revised[3]
            word = next(
                word
                for word in page.get_text("words")
                if word[4].isdigit() and len(word[4]) == 3 and int(word[4]) < 998
            )
            rectangle = fitz.Rect(word[:4])
            old, new = word[4], str(int(word[4]) + 1)
            page.add_redact_annot(rectangle, fill=(1, 1, 1))
            page.apply_redactions(images=0, graphics=0)
            page.insert_text((rectangle.x0, rectangle.y1 - 1.6), new, fontsize=7.2)
            page.draw_rect(rectangle + (-1, -1, 1, 1), color=(0.1, 0.3, 0.8), width=0.5)
            revised.set_metadata(
                {"title": "Synthetic readwrench pdf-pages test revision; not government data"}
            )
            revised.save(staging / "revised.pdf", deflate=True, garbage=3)
        (staging / "expected.json").write_text(
            json.dumps(
                {
                    "synthetic_test_fixture": True,
                    "pairs": [[1, 1], [2, 4], [3, None], [4, 2], [5, 5], [None, 3]],
                    "counts": {"matched": 4, "inserted": 1, "deleted": 1, "changed": 1},
                    "numeric_edit": {
                        "original_page": 2,
                        "modified_page": 4,
                        "before": old,
                        "after": new,
                    },
                    "source": "pdfplumber real examples; see examples/pdf-pages/README.md",
                },
                indent=2,
            )
            + "\n"
        )
    return output.resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = make_demo(args.out)
    print(f"Synthetic documents: {output}")
    print("Next: readwrench pdf-pages plan ORIGINAL REVISED --out REVIEW")
    print(f"Original: {output / 'original.pdf'}")
    print(f"Revised: {output / 'revised.pdf'}")


if __name__ == "__main__":
    main()
