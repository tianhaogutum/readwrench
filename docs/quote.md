# readwrench quote

You remember a sentence from a book or paper but not where it is, or a note quotes
a passage that appears more than once. `readwrench quote` searches a local TXT,
Markdown, HTML, PDF or DRM-free EPUB file for **every** exact or normalized
occurrence, shows each one with its surrounding paragraphs and a character diff,
and lets you pick one explicitly. Only then does it export a locator (W3C Web
Annotation selectors plus Readium-style locators) bound to the file's SHA-256,
which you can re-verify later.

It never confirms a quotation for you, never returns a fuzzy look-alike as a
match, and never invents a page number. Everything runs offline: no network,
models, OCR or background servers.

Formerly the standalone `quote-locator` tool. Saved records keep their schema ids
(`quote-locator.review/v1`, `quote-locator.anchor/v1`) and text policy, so reviews
and locators written by `quote-locator` still confirm and verify with the same
reader and engine versions installed (a version change is reported, never ignored).

**Reads / writes:** reads only the source document and the quote, selector, review
or locator files you name. It writes a file only with `find --output` or
`confirm --output`; output files are created atomically with private permissions,
an existing file is never replaced without `--force`, and a source or input
record can never be overwritten. `verify` and `extract` write nothing.

## Quick start

```sh
pip install 'readwrench[quote]'
readwrench quote --help
```

From a clone of the repository, on the bundled synthetic example (a short book in
which one sentence appears twice):

```sh
readwrench quote find examples/quote/book.txt \
  --quote 'Careful readers check the original passage before citing it.' \
  --output review.json

# The example has TWO candidates. Read their context, then select one explicitly.
readwrench quote confirm examples/quote/book.txt review.json \
  --candidate 2 --output anchor.json

readwrench quote verify examples/quote/book.txt anchor.json --json
```

`find` reports `AMBIGUOUS: 2 candidate(s); unconfirmed`, each candidate with its
previous, containing and next paragraph. `confirm` re-extracts the source,
recomputes the candidate and writes `anchor.json`; `verify` prints
`{"valid": true, ...}`.

```sh
# Normalized match: the source has a ligature, curly quotes, a line-end hyphen and extra spaces.
readwrench quote find examples/quote/book.txt \
  --quote 'A fine "quotation" survives a hyphenated line and multiple spaces.'

# No fuzzy substitute is returned for a fabricated quotation (exit status 1).
readwrench quote find examples/quote/book.txt --quote 'The evidence says 99 percent.' --json

# Start from an existing note: a W3C TextQuoteSelector with saved context.
readwrench quote find examples/quote/book.txt --selector examples/quote/selector.json
```

`python -m readwrench quote` and `python -m readwrench.quotelocator` are
equivalent. The library API lives in `readwrench.quotelocator`: `load_document`,
`locate`, `confirm`, `verify`, `import_selector`, `Document` and `DocumentError`.

## Commands

```text
readwrench quote find SOURCE (--quote TEXT | --quote-file FILE | --selector FILE)
                      [--profile {strict,typographic,loose}] [--encoding NAME]
                      [--offset N] [--limit N] [--json] [--output FILE] [--force]
readwrench quote confirm SOURCE REVIEW --candidate INDEX_OR_ID --output FILE [--force]
readwrench quote verify SOURCE ANCHOR [--json]
readwrench quote extract SOURCE [--encoding NAME]
```

- **find** lists all candidates and confirms nothing, even when there is only
  one. The status is `unique`, `ambiguous` or `unmatched`; an unmatched query
  exports no locator. `--json` prints the machine-readable review on stdout;
  `--output FILE` saves it for `confirm`.
- **confirm** is your explicit selection: `--candidate` takes the displayed
  1-based index or the full candidate ID (IDs stay stable for the same bytes,
  extraction and span). It re-extracts the source and recomputes the review, so a
  stale or edited review is refused.
- **verify** checks the source hash, the extracted text, the reader versions, the
  selection, the diff and both exported selector representations. A moved,
  byte-identical file in the same format still verifies; an edited source needs a
  fresh review.
- **extract** prints the exact text projection that offsets refer to (its
  SHA-256 is stored in every record).

`--quote-file` is for multi-line or shell-sensitive quotations: UTF-8 (an
optional BOM is accepted), one final CRLF/LF is removed and internal line breaks
are kept.

`--selector` imports a local W3C `TextQuoteSelector`, a W3C Annotation with
exactly one text quote selector, or a Readium locator with `text.highlight`.
Every occurrence of the quote stays in the result; `context_evidence` says
whether the saved prefix/suffix agree under the chosen profile, and a mismatch
never hides a real occurrence. Compound selections are refused rather than
picking a fragment. Stored source URLs and positions are neither fetched nor
trusted: the local source is always searched anew.

Exit status: **0** success (candidates found, confirmed, valid locator); **1**
unmatched quotation or invalid locator; **2** invalid input or I/O failure,
reported on stderr as `readwrench quote: <message>`.

## Matching profiles

| Profile | Behavior |
| --- | --- |
| `strict` | Literal text, including case, spaces, line breaks and punctuation. |
| `typographic` (default) | reanchor's Unicode compatibility decomposition, typographic punctuation, collapsed whitespace, zero-width removal and line-end dehyphenation; keeps case and diacritics. |
| `loose` | reanchor's default normalization: additionally lowercases and folds diacritics. Explicit opt-in. |

Every candidate carries the original source slice and the character-level
differences from your quotation. `normalized` means equal under the recorded
policy, not literally equal or semantically equivalent. Numbers, omissions,
ellipses, translations and paraphrases are never filled in. Punctuation folding
and hyphen joining can change meaning, and compatibility folding also merges
superscripts and width variants: check the displayed source and diff, and use
`strict` when those distinctions matter.

Substring and overlapping matches are found; token boundaries are not assumed,
so Chinese and other unsegmented text work. A match never splits a Unicode
grapheme cluster (a decomposed accent, a joined emoji). Terminal output escapes
control and bidirectional characters and clips previews at 2,000 code points and
50 change groups, with explicit notices; JSON keeps the full source slice and
every edit.

## Source formats and offsets

- **TXT / Markdown**: UTF-8 by default, `--encoding NAME` for other explicit
  encodings. Markdown is searched as literal source, markup included.
- **HTML / XHTML**: Beautiful Soup's static parser keeps inline text contiguous
  and puts blank lines at block boundaries. Scripts, styles, comments, templates,
  `hidden` and `aria-hidden="true"` elements and ruby annotations (`rt`, `rp`,
  `rtc`) are excluded. External CSS/JS are not evaluated; nothing is fetched.
- **PDF**: pypdf extracts each physical page in plain mode. Page numbers are
  1-based physical pages, not printed page labels. Image-only pages are flagged;
  OCR is not run. Reading order and mathematical notation depend on the PDF.
- **EPUB 2/3**: EbookLib follows the spine (non-linear items included) and
  Beautiful Soup reads the XHTML. Resource paths resolve from the ZIP root, and
  Readium hrefs are URI-escaped. DRM-encrypted content, spines with non-XHTML
  items and multiple package renditions are refused with an explicit error.

Pages or spine items are joined with exactly two LF characters, so a search can
cross a page boundary. Offsets count Unicode code points from zero, end
exclusive. The exported W3C `TextQuoteSelector` and `TextPositionSelector` target
the **content-addressed extracted-text URN** (`urn:sha256:...`), not offsets into
PDF bytes or an HTML DOM; the record links it to the source hash, the extraction
policy and the exact reader versions. Readium locators add hrefs, text context and
PDF page fragments as navigation hints. No EPUB CFI, printed page or browser range
is fabricated. `selector_status` says whether quote plus context is still
ambiguous, in which case the hash-bound position is required. File paths are
navigation hints, not authenticated provenance or a signature.

## Limits

Inputs: 20 MiB per file, 2,000,000 extracted characters, 2,000 pages or spine
items, 10,000 quotation characters, 4,096 characters of imported context per
side; EPUBs also 20 MiB expanded and 3,000 entries, and 2 MiB per HTML/XHTML
resource. PDF parsing uses pypdf's configuration API with an 8 MiB
decoded-stream limit and a bounded parsing budget (recorded in the metadata).

A review counts every matching span but shows at most 1,000 candidates per
window (and at most 8 MiB of candidate data; the first candidate is always kept),
marking truncation explicitly. `--offset N` and `--limit N` reach every
candidate; indices stay global and 1-based, and `window.next_offset` continues
after what was actually returned. Saved records are capped at 24 MiB. Context is
clipped to 800 characters per paragraph, with explicit flags. These are practical
bounds, not a sandbox for hostile files.

An empty or image-only document can only show that no extractable text matched.
A hash-bound locator guarantees reproducibility for the same rendition, not
survival across edits: after an edit, run a fresh review and confirm again.

## In `readwrench check`

`readwrench check` recognises saved reviews and confirmed locators by their
schema id and re-verifies each against its document (looked for at the recorded
`file:` URI, then next to the record under its recorded name). It reports
`source-changed` and `extraction-changed` (warnings: the record is stale),
`record-inconsistent` and `invalid-record` (errors: the record was edited or is
corrupt), `source-unreadable`, and `source-not-found` (a notice). The
`quote-text-layer` check reports PDFs with no extractable text at all
(`no-text-layer`) or some text-less pages (`pages-without-text`), where a
quotation can never be found without OCR, and PDFs beyond the limits above
(`not-searchable`). Nothing is written.

## Background

This is a small adapter around [reanchor](https://github.com/microbluey/reanchor),
not a new matching engine. reanchor already provides offset-preserving
normalization, exact occurrence search, span projection and quote selectors, but
its resolver returns one best match with a few rivals and works on strings, not
files. `readwrench quote` adds local format reading, an exhaustive review of exact
**and** normalized candidates, explicit confirmation and a hash-bound locator.
Similar tools surveyed (Anchorpoint, dom-anchor-text-quote, the Hypothesis client,
VeriQuote) anchor one best match or work in a browser DOM; none keeps every
candidate for review before a durable selector is exported. The W3C
[Web Annotation Data Model](https://www.w3.org/TR/annotation-model/) and
[Readium locators](https://readium.org/architecture/models/locators/) define the
exported vocabulary.

While adapting reanchor 0.3.0, three behaviors were handled locally:

- A normalized hit can project onto a source glyph that supports more characters
  (`f` inside `ﬁ`, `.` inside `…`). Each projected slice is normalized again and
  kept only if it equals the whole normalized quotation.
- Dropping a standalone combining mark can expose leading, trailing or doubled
  spaces. Normalization is re-run once in that case, composing both offset maps
  (recorded as `reanchor-whitespace-map-v1`).
- Repeating a long pattern search at every overlap redoes its setup; long
  quotations are searched by a 32-character prefix and then checked in full,
  keeping every overlapping occurrence.

RapidFuzz supplies the character diff and `regex` the Unicode grapheme
boundaries.
