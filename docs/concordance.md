# readwrench concordance

When you want to compare how a term — *liberty*, *power*, *德* — is used across three
to five books, a keyword search finds the words but not their meanings.
`readwrench concordance` turns EPUB books into a [Taguette](https://www.taguette.org/)
project in which every keyword/alias match starts **unreviewed**. You decide, in
Taguette or on the command line, which uses belong to which sense, or reject false
positives. It then exports a concept-by-book matrix in which every cell links back to
the exact passage, chapter and edition.

The tool never treats matching words as the same meaning, and it uses no AI and no
network service. It is a thin adapter: Taguette keeps the coding database and manual
workflow, EbookLib reads EPUB 2/3, and Beautiful Soup/html5lib extract the prose.

Formerly the standalone `concept-concordance` tool. Workspaces, exports and Taguette
projects made by `concept-concordance` keep working unchanged.

**Reads / writes:** reads the EPUB files, the concepts JSON and, when you name one, an
external Taguette database (opened read-only, never migrated). `init` creates one new
workspace directory and `export` one new export directory; both refuse an existing path
and are built in a hidden sibling staging directory, so a failure leaves no partial
output. `label` is the only command that changes anything: it writes your decision into
the workspace's own `project.sqlite3`. `list`, `check` and `resolve` only read.

## Install

```sh
pip install 'readwrench[concordance]'
readwrench concordance --help
```

The `concordance` extra installs Taguette 1.5, EbookLib, Beautiful Soup, html5lib, lxml,
Jinja2, jsonschema and a `setuptools<81` constraint. Taguette 1.5.2 still imports
`pkg_resources` through its pinned OpenTelemetry release, and newer setuptools no
longer ship it. Python 3.11–3.14 are supported (3.13 and 3.14 were exercised with the
real books). `--help` works without the extra; the other
commands print the install command when it is missing.

## Quick start

From a clone of the repository, with the extra installed. The three books are
unmodified Project Gutenberg EPUBs bundled in
[examples/concordance/gutenberg](../examples/concordance/gutenberg/README.md):

```sh
readwrench concordance init \
  examples/concordance/gutenberg/prince.epub \
  examples/concordance/gutenberg/government.epub \
  examples/concordance/gutenberg/republic.epub \
  --concepts examples/concordance/concepts.json --out demo-workspace
readwrench concordance list demo-workspace --concept liberty --limit 3
```

`init` reports 3 books, 75 chapters and 1349 candidates. Read the context of the listed
occurrences, copy a full occurrence `id`, and classify it yourself:

```sh
readwrench concordance label demo-workspace FULL_OCCURRENCE_ID --sense 'political liberty'
readwrench concordance label demo-workspace ANOTHER_OCCURRENCE_ID --reject
readwrench concordance label demo-workspace FULL_OCCURRENCE_ID --clear
readwrench concordance check demo-workspace
readwrench concordance export demo-workspace --out demo-export
```

Open `demo-export/index.html` as a local file. (The first *liberty* hit in *The Prince*
is a proper name, "Ten of Liberty and Peace": an example of why lexical matches need a
human decision.)

Every command prints one JSON document on stdout. Errors go to stderr as
`readwrench concordance: error: <message>` with exit status 2; usage errors also exit 2.
`python -m readwrench concordance` and `python -m readwrench.concordance` are
equivalent.

## Commands

| Command | Arguments and options |
| --- | --- |
| `init BOOK.epub... --concepts FILE --out DIR` | 1–5 EPUBs (3–5 recommended). `--name` (Taguette project name, default `Concept comparison`, at most 50 characters), `--max-hits N` (default 10000, at most 50000), `--include-nonlinear` (also search spine items marked `linear="no"`). |
| `list WORKSPACE` | `--concept ID`, `--book ID`, `--chapter TITLE` (exact EPUB or researcher-supplied chapter title), `--status classified\|unreviewed\|rejected\|conflicted`, `--limit N` (default 20, at most 10000), `--offset N`, plus `--database` / `--project-id`. |
| `check WORKSPACE` | Validates snapshots, Taguette locations and decisions; prints counts per status. `--database` / `--project-id`. |
| `export WORKSPACE --out DIR` | `--context N` characters before/after each quote (default 160, at most 2000), plus `--database` / `--project-id`. |
| `label WORKSPACE OCCURRENCE_ID` | Exactly one of `--sense TEXT` (repeatable for several senses), `--reject`, `--clear`. `--project-id` chooses among copies in the workspace database. |
| `resolve WORKSPACE OCCURRENCE_ID --source BOOK.epub` | Verifies an occurrence against the original edition. `--database` / `--project-id` as used for the export. |

`--database PATH` reads a Taguette database or a Taguette SQLite project export instead
of the workspace's own `project.sqlite3`; `--project-id N` selects one project when the
database holds several imported copies of the workspace.

## Concepts JSON

```json
{"concepts": [
  {"id": "liberty", "label": "Liberty / freedom (query, not equivalence)",
   "aliases": ["liberty", "freedom"], "boundary": "word"}
]}
```

The object contains only `concepts`: 1–100 queries. Each needs a unique ASCII `id`
(letters, digits, `_`, `-`; at most 64) and 1–100 non-empty literal `aliases`; `label`,
`boundary` (`word`, the default, or `substring`) and `case_sensitive` (default false)
are optional. Whitespace in aliases and in the prose collapses to one space. The longest
alias wins at a shared start, and matches within one query do not overlap; different
queries are independent and may select overlapping passages. Use `substring` for
unsegmented Chinese or Japanese text
([examples/concordance/concepts-cjk.json](../examples/concordance/concepts-cjk.json)).
Case-insensitive matching uses Python's Unicode `re.IGNORECASE`; there is no stemming,
translation or semantic expansion. Duplicate JSON keys are rejected and a UTF-8 BOM is
accepted.

## Coding in Taguette

The workspace's `project.sqlite3` is a real Taguette 1.5.2 database. Start Taguette on it
on your own machine:

```sh
taguette --database demo-workspace/project.sqlite3 --no-browser
```

Each EPUB spine resource is one Taguette document with the query matches pre-highlighted
and tagged `query/ID`. Keep those tags and the identity marker in each document's
description. Add your own `sense/YOUR CATEGORY` tags (several per highlight are fine) or
`status/rejected` for an irrelevant candidate; use one or the other, never both. Explain
your categories in Taguette's codebook descriptions: they are exported to
`codebook.json` and shown beside the matrix. `label` writes the same tags, and Taguette
change events, from the command line. Do not edit the same project in Taguette while
running `label`.

A highlight with both a sense and `status/rejected`, or with an empty `sense/` tag, is
*conflicted*: `check` and `export` stop with an error, while `list --status conflicted`
still finds it and `label ... --clear` (or a new `--sense` / `--reject`) repairs it.

You can also import the workspace project into an existing **local** Taguette instance,
code there, export that project as SQLite, and point the tools at it:

```sh
readwrench concordance export demo-workspace \
  --database /path/to/taguette-export.sqlite3 --out reviewed-export
```

Taguette renumbers projects, documents and highlights on import; the adapter finds its
chapters by stable markers and text coordinates, so edition references survive. If you
resize a highlight in Taguette, the export uses the new range.

## Chapters in monolithic ebooks

Some EPUBs — including the bundled Chinese editions — put many logical chapters into
one resource without section markup. The adapter keeps the resource and does not guess
chapter boundaries. In Taguette, select the full text range of a logical chapter and tag
it, for example, `chapter/第三十八章`. An occurrence entirely inside that range then
uses your chapter label; ranges must not overlap within one resource.

```sh
readwrench concordance init examples/concordance/gutenberg/daodejing.epub \
  examples/concordance/gutenberg/analects-zh.epub examples/concordance/gutenberg/prince.epub \
  --concepts examples/concordance/concepts-cjk.json --out cjk-workspace
# after tagging a chapter/第三十八章 range in Taguette:
readwrench concordance list cjk-workspace --chapter '第三十八章' --concept de
```

`chapter_source` in each occurrence records whether the label came from an EPUB heading,
the resource, or a manual range; `manual-chapters.json` keeps your exact boundaries.
[examples/concordance/create_sample.py](../examples/concordance/create_sample.py) builds
the complete five-book sample with one explicit chapter-38 range and one clearly named
demonstration category.

## Verifying a citation

```sh
readwrench concordance resolve demo-workspace FULL_OCCURRENCE_ID \
  --source examples/concordance/gutenberg/prince.epub
```

`resolve` checks the EPUB's SHA-256, re-reads the chapter, compares it with the preserved
canonical text and extracts the quote with Taguette's own UTF-8 snippet extractor. A
different edition fails even when its title and quote are identical. Pass the same
`--database` (and `--project-id`) used for the export to resolve a passage you resized
in a separate Taguette project.

## Export contents

| File | Contents |
| --- | --- |
| `index.html` | The concept/sense-by-book matrix with source contexts, the codebook and the queries. |
| `readers/<chapter>.html` | Each chapter snapshot with highlighted, linkable occurrences. |
| `occurrences.json` / `.csv` | Exact quotes, character positions, EPUB resource paths, chapter source and Readium locators. |
| `matrix.json` / `.csv` | Matrix rows; CSV cells list occurrence IDs instead of bare counts. |
| `annotations.jsonld` | W3C Web Annotations (TextPositionSelector + TextQuoteSelector). |
| `sources.json`, `text/*.txt` | Stable source URNs mapped to the exact canonical text snapshots and edition digests. |
| `codebook.json`, `manual-chapters.json` | Taguette tag definitions and researcher chapter ranges. |

Empty query rows stay in the matrix, so absence is visible. CSV fields are fully quoted,
and text that starts like a formula gets a tab prefix
([OWASP CSV injection](https://owasp.org/www-community/attacks/CSV_Injection)); use the
JSON files for exact values. Annotation identities are scoped to the workspace, so two
independent interpretations of the same passage stay distinct. Exports contain full
normalized chapters, local file paths and excerpts: source rights and citation
conventions remain your responsibility.
[examples/concordance/check_bundle.py](../examples/concordance/check_bundle.py)
independently re-checks an export's quotes, hashes, locators and local links.

## Text model and limits

EPUB spine order is kept, navigation documents are skipped, and non-linear spine items
are left out unless `--include-nonlinear` is given. Front matter, indices and Gutenberg
boilerplate in the spine remain searchable: reject those candidates as appropriate.
Canonical text v1 separates block elements, joins inline text, collapses whitespace,
drops comments, scripts, styles, SVG and embedded active content, and preserves Unicode
code points. Positions are Unicode code-point offsets into that snapshot — **not** XHTML
byte offsets, print pages or EPUB CFIs. The Readium locator (resource href, fragment and
quote context) is the return path to the original; no reader-specific deep link is
promised.

Limits: 1–5 books per workspace; 32 MiB per EPUB (compressed and expanded), 8 MiB per
archive member, 5000 members; 100 queries × 100 aliases; 10,000 seeded candidates by
default (at most 50,000). Encrypted/DRM prose, malformed archives, unsafe paths and the
same EPUB bytes given twice fail before any workspace is written; font obfuscation alone
is fine. JSON inputs are capped at 8 MiB, external Taguette databases at 512 MiB (with a
30-second query deadline), canonical snapshots at 32 MiB. There is no PDF, OCR, MOBI or
DOCX intake: convert first and treat the converted EPUB as its own edition.

## In `readwrench check`

`readwrench check` runs two read-only concordance checks (they need the `concordance`
extra, but not Taguette itself):

- `concordance-epub` reports EPUBs that `init` would refuse: `encrypted-text`,
  `over-limit`, `undecodable-chapter`, `no-searchable-prose`, `unreadable`, and
  `not-epub-name` for an EPUB without the `.epub` suffix.
- `concordance-workspace` recognises a workspace by its `manifest.json` and re-verifies
  it: `invalid-workspace` (manifest or snapshot hashes no longer match),
  `source-missing` / `source-changed` / `source-unreadable` (the recorded source edition
  moved or was replaced, so `resolve` needs `--source`), `database-missing`,
  `taguette-mismatch` (the Taguette project no longer matches the snapshots),
  `conflicting-decisions` (export would refuse) and an `unreviewed` count.

## Background

Taguette, CATMA, Voyant Tools' Contexts and Zotero's EPUB reader already cover manual
coding, concordances and EPUB annotation. Taguette's own highlight export lacks the
original EPUB resource, chapter, edition digest and a portable locator, and has no
concept/sense-by-edition matrix; this adapter adds exactly that around Taguette's
database. Exports follow the
[W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/) and the
[Readium Locator model](https://readium.org/architecture/models/locators/).

Upstream findings, recorded but not submitted:

- The Readium Locator JSON Schema (`schema/locator.schema.json` in readium/architecture,
  retrieved 2026-11-11) has a trailing comma after the `text` property and does not parse
  as strict JSON. The tests validate locators against a copy with only that comma
  removed.
- Taguette 1.5.2 imports `pkg_resources`, which recent setuptools no longer provide;
  hence the `setuptools<81` constraint.
- Taguette 1.5.2's highlight update endpoint changes offsets without refreshing the
  cached snippet, so its native CSV export can show the old quote after a resize. The
  minimal upstream fix recomputes `hl.snippet` with `extract.extract(document.contents,
  hl.start_offset, hl.end_offset)` when an offset changes. readwrench does not patch
  Taguette: it rebuilds every quote from the validated source offsets, so its exports are
  correct either way (`tests/concordance/test_taguette_http.py` demonstrates this; set
  `READWRENCH_CONCORDANCE_EXPECT_TAGUETTE_RESIZE_FIX=1` when testing a patched Taguette).
