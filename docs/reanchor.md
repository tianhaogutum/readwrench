# readwrench reanchor

You annotated a PDF, then a revised version arrived: pages moved, lines reflowed,
some sentences changed. `readwrench reanchor` carries your embedded comments
(highlights, underlines, strike-outs, squiggles and sticky notes) from the old PDF to
the revision **after you review each move**. It proposes target text spans, shows the
old annotation and every candidate as screenshots with matching evidence in an offline
HTML page, and writes **only explicitly accepted** choices into a new copy of the
revision. Rejected, unsupported and unresolved comments are kept in an unmatched
archive together with the original PDF.

It is an extension of [pdfanno 0.2.2](https://pypi.org/project/pdfanno/0.2.2/), not a
new PDF engine: pdfanno supplies annotation anchors, models and five-signal scoring;
PyMuPDF supplies all PDF reading, writing, glyph geometry and rendering; RapidFuzz
supplies fast fuzzy alignment; regex supplies Unicode grapheme boundaries. No models,
OCR, network service, browser automation or server is used.

Formerly the standalone `pdf-reanchor` tool.

**Reads / writes:** reads the two PDFs (and, for `apply`, the bundle and a decisions
file) and never modifies them. `propose` creates one new bundle directory
(`proposal.json`, `decisions.template.json`, `review.html`, `assets/*.png`); `apply`
creates one new result directory (`migrated.pdf`, `audit.json`, `unmatched.json`,
`unmatched.zip`). An existing output path is refused; nothing is committed when a
step fails.

## Quick start

```sh
pip install 'readwrench[reanchor]'
readwrench reanchor --help
```

`readwrench[reanchor]` pulls in pdfanno, PyMuPDF, pydantic, RapidFuzz and regex.
pdfanno 0.2.2 declares Python 3.12+, so this command needs Python 3.12 or newer.

From a clone of the repository, run it on the bundled pdfanno example pair (three
highlights; the revision inserts text so one quote moves to another page):

```sh
readwrench reanchor propose \
  examples/reanchor/reordered_v1.pdf examples/reanchor/reordered_v2.pdf \
  --out demo-output/review
```

Open `demo-output/review/review.html` directly in your browser. Inspect the old
quote and comment, the old page image, the candidate images and the signal scores.
Choose candidates individually, tick the confirmation box, then **Export confirmed
decisions**. The browser downloads `decisions.json`; use its actual download path:

```sh
readwrench reanchor apply demo-output/review ~/Downloads/decisions.json \
  --out demo-output/result
```

For a richer invented editorial example (multi-line markup, edited and deleted text,
sticky notes and an unsupported FreeText appearance), build a pair first; this needs
ReportLab as well (`pip install reportlab`):

```sh
python examples/reanchor/make_demo.py demo-output/editorial-input
readwrench reanchor propose demo-output/editorial-input/annotated.pdf \
  demo-output/editorial-input/revision-noted.pdf --out demo-output/editorial-review
```

`python -m readwrench reanchor` and `python -m readwrench.reanchor` are equivalent.
`readwrench check` re-verifies saved bundles read-only (see below).

## Commands

```
readwrench reanchor propose OLD_PDF NEW_PDF --out DIR [--candidates 1..20] [--minimum-text F]
readwrench reanchor apply BUNDLE DECISIONS --out DIR [--old-pdf PATH] [--new-pdf PATH]
readwrench reanchor --version
```

`propose` options:

- `--out DIR` (required): new bundle directory; never overwritten.
- `--candidates N`: candidates shown per annotation, 1 to 20 (default 5). The
  default may include false positives; `--candidates 20` exposes more.
- `--minimum-text F`: fuzzy text similarity floor, 0.50 to 1.00 (default 0.78). It
  controls which fuzzy candidates are offered, not approval.

`apply` options:

- `BUNDLE`: the directory written by `propose`. `DECISIONS`: the confirmed
  `decisions.json` exported from `review.html`.
- `--out DIR` (required): new result directory.
- `--old-pdf PATH`, `--new-pdf PATH`: if the input files moved, point at them here;
  they must be byte-identical (same SHA-256) to the reviewed files.

The CLI writes one JSON document to stdout and diagnostics to stderr as
`readwrench reanchor: <message>`. Exit status **0** means a proposal or result was
created, including normal unmatched outcomes; **2** means an input, confirmation,
validation or write error.

## Result directory

- `migrated.pdf`: a copy of the revision, retaining its existing annotations and
  adding accepted comments with their original type, author, content, subject, dates,
  colors, opacity and flags where supported. Each migrated annotation gets a
  `/NM` name of the form `pdf-reanchor:<hash>`, recorded in the audit.
- `audit.json`: input, decision and output hashes, migrated ids and counts.
- `unmatched.json`: every unaccepted source comment and the reason
  (`unsupported`, `no_candidates`, `rejected`, `unresolved`).
- `unmatched.zip`: unresolved metadata, source screenshots, and the **entire original
  PDF** whenever any comments remain. This keeps the original unsupported
  appearance, attachment and discussion data instead of pretending to reconstruct it.

Edited PDF bytes, an edited proposal, changed evidence images, wrong decisions,
invalid geometry or a missing confirmation fail before a result directory is
committed.

## Review contract

Every annotation starts unresolved, including exact matches with high scores.
**Save progress** exports unconfirmed JSON (`decisions.progress.json`); import it
later to resume. Importing decisions always resets the confirmation checkbox. Missing
decisions remain unmatched. There is no bulk automatic-accept command.

JSON uses zero-based page indexes; the HTML shows page numbers starting at 1.
Candidate scores are ranking heuristics (pdfanno 0.2.2 weights: text .40, context .30,
layout .15, page .10, length .05), not calibrated probabilities.

The bundle formats are unchanged from the standalone tool: `proposal.json`
(`schema_version` 1, `upstream_version` `"0.2.2"`, a `proposal_id` that is the
SHA-256 fingerprint of the rest of the proposal), and `decisions.json`:

```json
{
  "schema_version": 1,
  "proposal_id": "<from proposal.json>",
  "confirmed": true,
  "choices": [
    {"annotation_id": "ann-…", "action": "accept", "candidate_id": "c-…", "note": ""},
    {"annotation_id": "ann-…", "action": "reject", "candidate_id": null, "note": "moved by hand"}
  ]
}
```

`action` is `accept`, `reject` or `unresolved`; only `accept` names a `candidate_id`.
Unknown fields, duplicate annotation ids and unconfirmed files are refused.

## Scope and limits

- Migrates Highlight, Underline, StrikeOut, Squiggly and sticky Text notes. Sticky
  notes use nearby source text, clearly labelled; this is weaker evidence than a
  selected quote. FreeText, ink, stamps, redactions, attachments and replies/groups
  remain unmatched with the original PDF intact.
- Rich-text-only comments without plain `/Contents` remain unmatched. When both
  representations exist, the migration uses the plain comment text and flags the
  formatting difference in the review.
- Only embedded PDF review annotations count. Zotero users must first export a PDF
  with embedded annotations. Links, popups and form widgets are structural objects.
- No OCR. Scans without a text layer remain unmatched. Complex reading order,
  overlapping glyphs, disjoint selections, heavily rewritten text and text spanning
  page boundaries may not yield usable candidates.
- Normalizes case, Unicode compatibility characters, whitespace, smart punctuation
  and line-end hyphenation. Inspect the images when these distinctions matter.
- Encrypted PDFs need a separately decrypted copy. Revisions with signature fields,
  signature flags or certification entries are refused for writeback; use a separate
  unsigned revision, including for an empty signature form field.
- Existing target content, interactive fields and annotations are passed through
  PyMuPDF's save. Byte-identical serialization, exact original annotation appearance
  streams and preservation of digital signatures are not promised.
- Bundles and unmatched archives contain document text and images. Treat the
  complete bundle as part of your document, especially when sharing it.
- pdfanno's scoring helpers are private APIs, so the dependency is held to the 0.2.x
  line and the integration is isolated in `readwrench/reanchor/adapter.py`.

## `readwrench check`

`readwrench check` recognises a bundle's `proposal.json` by content and re-runs the
verification `apply` would do, read-only: the proposal fingerprint, the SHA-256 of
both PDFs at their recorded paths, and every evidence image. Rules:

| Rule | Severity | Meaning |
|---|---|---|
| `reanchor/invalid-proposal` | error | not a valid proposal (schema, size, duplicate ids) |
| `reanchor/fingerprint-mismatch` | error | `proposal.json` was edited after `propose` |
| `reanchor/pdf-changed` | error | an input PDF's bytes changed since review |
| `reanchor/evidence-changed` | error | an evidence image is missing, changed or outside `assets/` |
| `reanchor/pdf-missing` | warning | an input PDF moved; `apply` needs `--old-pdf`/`--new-pdf` |
| `reanchor/review-pending` | info | a valid bundle, with annotation and candidate counts |
| `reanchor/no-annotations` | info | the original PDF had no review annotations |

It does not re-run matching, judge candidates or read decisions files.

## Background

pdfanno 0.2.2 already extracts old embedded annotation anchors and scores new
locations using text, surrounding context, layout, page proximity and length; its
`diff` reports one assigned winner per annotation. Its review flow and
accepted-diff-to-plan helper are future work upstream. `readwrench reanchor` adds the
missing stage: an offline gallery of alternatives with screenshots, explicit
confirmation, native writeback, and complete unmatched preservation with byte-bound
inputs. It reuses pdfanno's anchor extraction, anchor types and weighted scoring, and
differs where visual review needs it:

1. One selected quote that spans several lines is one candidate with all its line
   quads (pdfanno's exact search yields one candidate per `search_for` quad).
2. Context comes from the annotated occurrence, not the first matching text on the
   page.
3. Fuzzy alternatives are generated with RapidFuzz even when an exact match exists
   elsewhere, so a changed original passage is not hidden by an unchanged decoy.
4. Normalized text keeps an offset map to MuPDF glyph quads, so line-end
   hyphenation, ligatures and combining characters still map to writeable geometry.

Other tools surveyed (PyMuPDF, pdfplumber, pdfannots, Docling, Hypothesis /
anchor-quote, rpdf, Coherent PDF, Zotero) extract, merge or copy annotations, but none
offers a revision-aware, explicitly confirmed migration of embedded PDF comments with
local evidence. Zotero keeps its own annotations in a database: export a PDF with
embedded annotations first; database-only comments cannot be recovered from PDF bytes.

The example pair `examples/reanchor/reordered_v1.pdf` / `reordered_v2.pdf` was
generated by the fixture builder in the pdfanno 0.2.2 source distribution
(AGPL-3.0-or-later). Test fixtures and their licenses are described in
`tests/reanchor/fixtures/PROVENANCE.md`.

## Development

The tests live in `tests/reanchor/` and skip when the `reanchor` extra is missing.
Some also need ReportLab and pypdf. `test_review_ui.py` runs DOM tests of
`review.html` with Node.js and jsdom (no browser or network) and skips unless `node`
can resolve `jsdom`, for example:

```sh
npm install --prefix /path/to/scratch --no-save jsdom@27.2.0
NODE_PATH=/path/to/scratch/node_modules pytest tests/reanchor/test_review_ui.py
```
