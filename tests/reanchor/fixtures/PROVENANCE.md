# Fixture provenance

`upstream/*.pdf` are copied unchanged from the test corpus of
<https://github.com/0xabu/pdfannots>, commit
`4a6c71e10ceea6aa64481f0bd19ef1eaa58df89c` (retrieved 2026-11-11).
The upstream project's MIT license is included as `PDFANNOTS-LICENSE.txt`
(also in `licenses/pdfannots-MIT.txt`).

- `hotos17.pdf`: 8-page real academic paper, produced by pdfTeX 1.40.17/LaTeX,
  10 embedded markup/comment annotations. Tests real multi-column text and
  multiline markup.
- `word2column.pdf`: Microsoft Word for Microsoft 365, 2 pages, 9 comments.
- `FreeText-annotation.pdf`: PDF Expert 2.5.19 Mac, 1 page, FreeText.
- `swapped-corners.pdf`, `indirect-objects.pdf`,
  `annots-entries-without-subtype.pdf`: upstream pypdf regression fixtures.

Other test PDFs are generated at test time with ReportLab
(`examples/reanchor/make_demo.py`) and PyMuPDF. They contain invented editorial
text and are not represented as real manuscripts.
