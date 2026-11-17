# readwrench pdf-pages

When a revised PDF gains, loses or moves a page, most comparison tools pair pages
by position or by a short text lookahead, and every later page then looks changed.
`readwrench pdf-pages` matches the pages first: it proposes which page of the
revision corresponds to which page of the original, shows the evidence in a local
review page, and exports a difference report only after a named person has approved
that page map. Use it before comparing two versions of a report, a contract, a
paper or a scanned document whose page order you cannot take for granted.

It is a review adapter for [Py-PDF-Compare](https://github.com/grananda/Py-PDF-Compare),
not a new PDF engine: Py-PDF-Compare draws the word diff and the vector side-by-side
report, PyMuPDF renders, and this command adds the page map, the review and the
approval gate. Formerly the standalone `pdf-page-bridge` tool.

**Reads / writes:** reads the two PDFs and the mapping JSON you name; never modifies
them. `plan` and `export` create one **new** output directory each (an existing
path is refused; work is staged and renamed only after success, so a failed run
leaves nothing behind). `confirm` creates one new JSON file and never overwrites
one. Nothing goes online: the review page makes no network requests and needs no
server.

## Install

```sh
pip install 'readwrench[pdf-pages]'
readwrench pdf-pages --help
```

`plan` and `confirm` run on Python 3.11+. `export` needs **Python 3.12 or newer**,
because Py-PDF-Compare supports only 3.12+; on 3.11 the extra installs without it and
`export` says so. No OCR, ML model, browser automation or account is used.

## Quick start

From a clone of the repository, match a real five-page report against a copy with
page 4 moved to position 2:

```sh
readwrench pdf-pages plan examples/pdf-pages/warn-five.pdf examples/pdf-pages/warn-reordered.pdf --out warn-review
```

It prints the number of proposed rows and how many need attention, then the paths of
`warn-review/review.html` and `warn-review/mapping.json`. Open the HTML file from
disk in a normal browser and check the pairs (or read `mapping.json`). Then record
your approval and export:

```sh
readwrench pdf-pages confirm warn-review/mapping.json --reviewer 'A. Editor' --out warn-approved.json
readwrench pdf-pages export warn-approved.json --out warn-comparison --aligned
```

Expected correspondence: `1→1, 2→3, 3→4, 4→2, 5→5`, and all five pairs keep their
content (`{"changed": 0, "deleted": 0, "inserted": 0, "matched": 5}`). Py-PDF-Compare
on its own reports the moved page 4 as one deleted and one inserted page.

For a richer example with an insertion, a deletion, a move and one real numeric edit,
generate a **synthetic** revision (its correct answer is written to `expected.json`):

```sh
python examples/pdf-pages/make_demo.py --out demo-documents
readwrench pdf-pages plan demo-documents/original.pdf demo-documents/revised.pdf --out demo-review
readwrench pdf-pages confirm demo-review/mapping.json --reviewer 'A. Editor' --out demo-approved.json
readwrench pdf-pages export demo-approved.json --out demo-comparison --changed-only --aligned
```

`expected.json` lists four pairs, one inserted page, one deleted page and one changed
pair: a table cell changes from 474 to 475 while the rest of the matched content stays
the same.

## The three steps

### 1. `plan`: propose a page map

```text
readwrench pdf-pages plan ORIGINAL REVISED --out DIR [--min-score 0.62]
```

PyMuPDF extracts normalized text and renders one small thumbnail per page. RapidFuzz
scores text; `dhash`, Pillow and NumPy compare thumbnail structure and ink. SciPy
solves a one-to-one assignment with a "no counterpart" option for every page, so a
weak match becomes a deletion/insertion instead of a forced pair. Pairs may cross
(reordered pages are allowed).

`DIR` receives `mapping.json` (the editable map), `review.html` and `assets/` (the
thumbnails). Rows are `suggested` or `pending`; a row is pending, with reasons, when
its evidence is weak (`low_similarity`), another candidate is close
(`competing_candidate`), a page looks blank (`blank_looking`), a page has no text
(`visual_only`), the pair crosses another pair (`reordered`), or a page has no
counterpart (`unmatched_original` / `unmatched_modified`). `--min-score` (0-1,
default 0.62) is the floor below which a candidate is not proposed.

**Scores are heuristic evidence, never probabilities of correctness.** Identical
forms and blank pages often need manual pairing. Every row, suggested or pending,
needs human approval.

### 2. Review and approve

**In the browser.** Open `DIR/review.html` from disk; keep it beside its `assets/`
folder. Pending rows are amber and show alternatives with their scores.

1. Compare the thumbnails and excerpts. Change a counterpart in its dropdown if
   needed; choose *Deleted* for an original page without a counterpart. A revised
   page can be used only once.
2. Mark pairs reviewed (pairs are shown 20 at a time; "Mark all displayed pairs
   reviewed" applies only to the visible group).
3. Check the revised pages without a counterpart and the page order, tick the
   confirmation box and enter your name.
4. *Download approved mapping* saves `approved-mapping.json`.

*Save draft* downloads your choices without approving them; reopen the same
`review.html` and use *Resume from JSON* to continue. A file for different source
PDFs, or with invalid or duplicate assignments, is rejected without changing the
current review.

**In a terminal or editor.** Edit the page numbers in `DIR/mapping.json` instead.
Pages are **1-based**; `null` means an insertion or deletion. Every page of both PDFs
must appear exactly once. Then record your approval:

```text
readwrench pdf-pages confirm MAPPING --reviewer NAME --out APPROVED.json
```

`confirm` asserts that you reviewed every current row; it is not an automatic
confidence-based approval. It re-checks both source PDFs against their recorded
SHA-256, drops the suggestion scores (they described the proposal, not your edited
pairs) and writes an approval with your name, a timestamp and a snapshot of the exact
source hashes and ordered page pairs. Editing the pairs afterwards invalidates the
approval. Approval is an audit record, not a cryptographic signature or an access
control.

### 3. `export`: compare the approved pairs

```text
readwrench pdf-pages export APPROVED.json --out DIR [--original PDF] [--modified PDF]
    [--dpi 120] [--pixel-threshold 24] [--aligned] [--changed-only]
```

A draft map is refused. Both PDFs must still match their recorded SHA-256; if they
moved, pass `--original` / `--modified` (the bytes must be identical). The export
re-checks the hashes after rendering and discards the output if a source changed
meanwhile.

| Output | Content |
| --- | --- |
| `diff.pdf` | Audit summary and approved page map, then one panel per pair. Vector page content and red/green changed-word boxes come from Py-PDF-Compare; translucent orange marks rendered pixel differences, including image and layout changes a word diff cannot see. Bookmarks and the *Report page* column lead to changed, inserted and deleted panels. |
| `mapping.json`, `mapping.csv` | The approved correspondence with page relation and content result per row. |
| `summary.json` | Per pair: words added/removed, changed raster pixels, resolution and threshold, page-geometry change; plus counts. |
| `aligned-original.pdf`, `aligned-modified.pdf` | With `--aligned`: same page count and page sizes on both sides (a missing counterpart becomes a blank page), ready for a visual comparator such as `diff-pdf --output-diff=external-diff.pdf DIR/aligned-original.pdf DIR/aligned-modified.pdf`. |

The PDF table, CSV and JSON keep **same number**, **shifted** and **reordered**
correspondence apart from the content result. *Reordered* means the pair crosses
another pair in the original order; it is not an estimate of the fewest pages someone
moved. Inserting five pages can shift every later page number while every matched pair
stays unchanged.

`--changed-only` omits unchanged matched panels from `diff.pdf` (their `report_page`
is null); the full map, inserted and deleted pages, CSV, JSON and aligned outputs are
kept. Identical documents still produce a valid audit PDF.

Raster comparison uses `--dpi` (36-200, default 120; render long edges are capped at
1,400 pixels) and a per-channel `--pixel-threshold` (0-254, default 24), in top-left
page coordinates with no registration or deskew. It catches visible differences at
that resolution, not every microscopic change. Derived outputs flatten visible
annotations and form widgets in memory; the source PDFs are never modified.

## Options and exit status

`--timeout SECONDS` (before the step name, default 180) sets a Unix execution
deadline: `readwrench pdf-pages --timeout 600 export ...`. Windows has no such
deadline. `--version` prints `readwrench pdf-pages <version>`.

Exit status is **0** on success, **2** for an input, review or output problem
(reported as `readwrench pdf-pages: <message>` on stderr) and **130** when
interrupted; in every failure case the partial output is removed.

`python -m readwrench pdf-pages` and `python -m readwrench.pagebridge` are
equivalent.

## Limits

- 1-200 pages per PDF. Encrypted PDFs must be decrypted first. A page's image
  resources may hold at most 40 million source pixels (checked before rendering);
  downsample unusually large scans first.
- PDFs with optional-content layers (OCGs) are refused: flatten the visible layers
  first. Copying such pages can change which layers are visible.
- `/UserUnit` scaling, nonzero media-box origins, crops, rotations and visible
  annotations/widgets are handled.
- No OCR. Scanned pages can be paired by visual evidence, but image-only matches are
  always pending, and different scans of the same page produce pixel differences.
- Text evidence uses up to 12,000 normalized characters per page (digits, footers and
  page numbers are kept); the final word diff uses Py-PDF-Compare's full extraction.
  Text reflowing across a page boundary may need manual interpretation.
- Not a forensic comparison: PDF internals, signatures, attachments, accessibility
  structure and invisible content are out of scope.
- Mapping JSON records page numbers, absolute source paths and hashes; keep it with
  the same access controls as the documents. Maps larger than 4 MiB are refused.

## File format

`mapping.json` and approved maps carry `"schema": "pdf-page-bridge/v1"`;
`summary.json` carries `"schema": "pdf-page-bridge-result/v1"`. These ids are kept
from the standalone tool, so maps made with `pdf-page-bridge` still load. Each row has
`original` and `modified` (1-based page numbers or `null`) and `confirmed`; an
approved map also has `review.reviewer`, `review.reviewed_at` and
`review.approved_map` (both SHA-256 values and the ordered pairs).

## In `readwrench check`

`readwrench check` recognises saved page maps by their schema id and, without
rendering anything, reports maps that `pdf-pages` would refuse (`invalid-map`),
drafts that are not approved yet (`draft-map`), approvals that no longer match their
rows (`approval-mismatch`), and source PDFs that are missing (`source-missing`, with
the path of an identical copy beside the map when there is one) or whose SHA-256 has
changed (`source-changed`).

## Background

Before this tool was written, several existing tools were run or read: Py-PDF-Compare
already aligns pages by text with a three-page lookahead, detects insertions and
deletions, and draws word-level vector reports; `@pdf-diff/core` suggests monotone
page maps with text shingles; diff-pdf, diff-pdf-visually, pdfcmp and the
htmlapps-pdf-compare browser app compare pages visually. None of them keeps an
editable, approved page map bound to the exact source files, with ambiguity and
reordered or image-only pages called out for review; that is the only part added
here. Py-PDF-Compare's comparator is reused through two of its internal methods
(`_add_comparison_page`, `_add_single_page`), which is why its version is pinned; a
public "compare with this page map" API upstream would remove that pin.
