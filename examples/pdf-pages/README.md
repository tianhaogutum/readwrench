# `readwrench pdf-pages` examples

Run from a clone of the repository with the `pdf-pages` extra installed. See
[docs/pdf-pages.md](../../docs/pdf-pages.md) for the full walkthrough.

| File | What it is |
| --- | --- |
| `warn-five.pdf` | First five pages of the real California WARN report (from the pdfplumber examples). |
| `warn-reordered.pdf` | The same pages in order `[1,4,2,3,5]`: a moved page. |
| `ag-energy-round-up-2017-02-24.pdf` | A real one-page USDA report, used by `make_demo.py` as an inserted page. |
| `make_demo.py` | Builds a **synthetic** revision with an insertion, a deletion, a move and one numeric edit, plus `expected.json` describing the correct answer. |
| `check_output.py` | Re-reads an export directory with pypdf (a second PDF parser) and checks the bookmarks, page counts and map agree. Needs `pip install pypdf`. |

Source PDFs: <https://github.com/jsvine/pdfplumber> (`examples/pdfs/ca-warn-report.pdf` and
`examples/pdfs/ag-energy-round-up-2017-02-24.pdf`), downloaded on 2026-11-11. The WARN files here
are page selections made with PyMuPDF; the publishers' originals are unchanged and keep their
rights. They are sample data, not relicensed with readwrench.
