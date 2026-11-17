# Fixture provenance

Real PDFs from the pdfplumber project (<https://github.com/jsvine/pdfplumber>), downloaded on
2026-11-11. SHA-256 sums are in `manifest.json`.

From `https://raw.githubusercontent.com/jsvine/pdfplumber/stable/examples/pdfs/`:

- `ag-energy-round-up-2017-02-24.pdf` (50,918 bytes, one page): USDA agricultural energy report,
  a second real generator/layout variant.

From `https://raw.githubusercontent.com/jsvine/pdfplumber/stable/tests/pdfs/`:

- `annotations-rotated-90.pdf` (170,174 bytes, one page): rotated PDF with annotation appearances.
- `annotations-unicode-issues.pdf` (57,927 bytes, one page): Unicode text/annotation regression
  document.
- `federal-register-2020-17221.pdf` (713,992 bytes, 15 pages): a real Federal Register
  multi-column publication with embedded fonts and dense text.

Derived locally with PyMuPDF 1.26.7 from `ca-warn-report.pdf`
(<https://raw.githubusercontent.com/jsvine/pdfplumber/stable/examples/pdfs/ca-warn-report.pdf>,
the California Employment Development Department WARN layoff report 2015-2016; the full
16-page original is not shipped), without modifying the upstream originals:

- `warn-five.pdf`: first five pages of the WARN report.
- `warn-reordered.pdf`: source pages `[1,4,2,3,5]`, testing non-monotone correspondence.
- `scan-before.pdf`: WARN source pages `[1,4]` rendered at 90 DPI, embedded as images with no
  text layer.
- `scan-after.pdf`: the same scans in order `[4,1]`.

These PDFs keep their publishers' rights; they are test data and are not relicensed with
readwrench. Small documents generated during the tests use ReportLab and PyMuPDF; see
`helpers.py`, `conftest.py` and the tests themselves.
