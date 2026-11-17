# readwrench lineage

You saved the same web page several times — a tutorial, a policy, an article you
quoted — with SingleFile, your browser's “Save as MHTML”, a WARC crawler or ArchiveBox.
`readwrench lineage` turns those existing local captures into one offline paragraph
timeline: what was added, removed, edited or moved between captures, the full extracted
text of every capture, and a link back to each original file. It reads archives you
already have; it never fetches web pages.

Formerly the standalone `page-lineage` tool (Node.js); see
[Differences from page-lineage](#differences-from-page-lineage).

**Reads / writes:** reads only the manifest, the capture files it names, and a
SingleFile `*.manifest.json` sidecar next to a capture if one exists. `init` and
`archivebox` write one manifest; `build` writes one self-contained HTML report and,
with `--json`, the timeline as JSON. Existing outputs are refused unless `--force` is
given, and even then an input (capture, sidecar, manifest, ArchiveBox index) is never
overwritten. Each output is staged beside its destination and moved into place.

**Platforms:** Python 3.11+, standard library only (no extra to install). Linux and
macOS are exercised; Windows paths are not specially handled.

## Quick start

```sh
pip install readwrench
readwrench lineage --help
```

From a clone of the repository, build the bundled example — a fictional tutorial
revised across four explicitly dated captures (it is not a genuine archive history):

```sh
readwrench lineage build examples/lineage/manifest.json --out timeline.html --json timeline.json
```

Open `timeline.html` in a browser; no server is needed. The first transition shows two
edited paragraphs (“every six months” → “every month”, “thirty days” → “ninety days”),
the second an added and a removed paragraph, the third one moved paragraph. The
saved outputs of exactly this command are `examples/lineage/timeline.html` and
`timeline.json` (with capture paths made relative).

For your own saved pages:

```sh
readwrench lineage init --url https://example.org/tutorial --out captures.json first.html later.mhtml latest.html
# Review the manifest and add capturedAt timestamps only where you know them.
readwrench lineage build captures.json --out timeline.html --json timeline.json
```

Progress and warnings go to stderr; `--quiet` suppresses them (warnings stay in the
report and JSON). Errors are printed as `readwrench lineage: <message>`. Exit status is
**0** on success and **1** on invalid input or options, extraction failure or a write
failure. `python -m readwrench lineage` and `python -m readwrench.lineage` are
equivalent.

## Commands

| Command | Arguments and options |
| --- | --- |
| `init FILE... --url URL --out MANIFEST` | Write a manifest listing 1–100 local captures (paths relative to the manifest). `--url` is the page's original http(s) URL. |
| `build MANIFEST --out REPORT.html [--json TIMELINE.json]` | Extract every capture and write the report (and JSON). |
| `archivebox INDEX... --out MANIFEST [--url URL]` | Import per-snapshot ArchiveBox `index.json` / `index.jsonl` files into a manifest. |

Every command also takes `--force` (replace existing outputs) and `--quiet`.
`readwrench lineage --version` prints the version.

## Manifest

Paths are relative to the manifest file, not the current directory.

```json
{
  "version": 1,
  "url": "https://example.org/tutorial",
  "captures": [
    {
      "file": "snapshots/first.html",
      "capturedAt": "2026-01-10T18:30:00+01:00",
      "captureSource": "my original capture log",
      "label": "Version I read in January"
    },
    {
      "file": "snapshots/later.mhtml",
      "label": "Later Chromium capture"
    }
  ]
}
```

Optional per-capture fields:

| Field | Meaning |
| --- | --- |
| `capturedAt` | Explicit ISO datetime with `Z` or a `±HH:MM` offset. Takes precedence over embedded capture metadata; conflicts are shown. |
| `captureSource` | Human-readable provenance for an explicit timestamp. |
| `label` | Friendly capture name. |
| `selector` | CSS selector for a known article container, when automatic extraction finds too little. Bypasses Readability and is labeled in the report. |
| `format` | `html`, `mhtml` or `warc`; `.mht`/`.mhtml` and `.warc`/`.warc.gz` are recognized automatically. |
| `encoding` | HTML byte encoding such as `utf-8` when saved transport metadata is missing or wrong. Overrides `<meta>` declarations; a byte-order mark still wins. Not available for MHTML (the MIME charset already decoded it). |
| `recordId` | Select one WARC response by its exact `WARC-Record-ID`. Required when a container holds several successful HTML captures of the URL. |
| `url` | Optional confirmation of the same source URL. |
| `allowUrlMismatch` | Explicitly permit an embedded original URL that differs (an intentional redirect or alias); a warning is recorded. |

Unknown fields are rejected to catch typos. Source identity uses URL normalization
(scheme and host case, default ports, dot segments, percent-encoding) and drops
fragments; query strings, trailing slashes and http versus https stay significant. A
SingleFile sidecar can associate a requested URL with its final redirected URL; both
are kept and the redirect claim is shown.

The manifest format (field names, `"version": 1`) is unchanged from page-lineage, so
existing manifests keep working.

## Capture timestamps are observations

Capture-time precedence:

1. Explicit `capturedAt` in the manifest.
2. A SingleFile CLI v1 sidecar (`page.html.manifest.json`) `time.captureStarted`, after
   verifying its `output.sha256` against the saved bytes.
3. A selected WARC response's `WARC-Date`, or the MHTML MIME `Date` header.
4. An older SingleFile `saved date` comment with an explicit recognized timezone.

Every selected timestamp keeps its source and raw value, and conflicting metadata is
visible. When every capture time is known, captures sort chronologically (ties keep
manifest order and are reported). If any time is unknown, **all captures keep manifest
order** and the report says so. File modification times and ArchiveBox bookmark
timestamps are never used. Article modified dates and WARC HTTP `Last-Modified` are
shown separately as unverified page claims. The report says a difference was
*observed between captures*; it never claims when the page was edited. Dates without
an explicit timezone, and dates whose weekday contradicts the calendar date, stay
unknown.

HTML bytes are decoded as a browser would: byte-order mark, then the `encoding` field
or WARC HTTP charset, then a `<meta>` declaration in the first 1024 bytes, otherwise
Windows-1252. A UTF-8 page saved without a declaration therefore needs
`"encoding": "utf-8"`. The report records the encoding used and where it came from.

## WARC captures

Plain `.warc` and gzip `.warc.gz` files (WARC 1.0/1.1) are read with HTTP gzip/deflate
and chunked transfer decoding. Brotli and chained encodings are not supported. Only a
self-contained **2xx `text/html` response for the manifest URL** is an article capture;
requests, redirects, unrelated resources and revisit records are never turned into
paragraph versions (revisits are counted in a warning). To use several captures from
one container, list the file once per response with distinct `recordId` values. The
report keeps the record ID, the original WARC date and the SHA-256 of the decoded
HTML; original WARC digests are not verified.

Container expansion and decoded payloads are each limited to 50 MiB, selected HTML to
10 MiB and records to 5,000. Truncated records and invalid gzip fail without a partial
report. A content decoder that falls back to raw bytes is reported.

## ArchiveBox

Pass one **per-snapshot** metadata file for each saved capture:

```sh
readwrench lineage archivebox --out captures.json \
  old-export/archive/1700000000/index.json \
  new-export/users/me/snapshots/20261111/example.org/UUID/index.jsonl
readwrench lineage build captures.json --out timeline.html
```

Legacy `history` JSON, 0.9 `archive_results` JSON and current flat
`Snapshot`/`ArchiveResult` JSONL are supported. One representation is chosen per
snapshot, preferring SingleFile, DOM, Readability, Mercury, then wget HTML, and the
selected extractor's successful `start_ts` is used as a metadata claim. Several formats
of the same snapshot are not treated as separate versions. Absolute or escaping output
paths and symlinks outside the snapshot directory are ignored. Archive trees, SQLite
databases and collection exports are not read. The adapter follows ArchiveBox's
serializers as published; read the generated manifest before relying on it.

## Paragraph identity and limits

Exact sequence anchors come from a Myers diff. Unique unchanged paragraphs that move
keep their identity. Edited blocks are matched conservatively inside replacement
regions using word overlap and word order, requiring an unambiguous similarity of at
least 60%; the score is shown as a heuristic, not a probability. Runners-up within
eight percentage points make a match ambiguous, and ambiguous or repeated paragraphs
stay additions/removals. Word differences preserve both texts exactly.

Article extraction is heuristic (a port of Mozilla Readability's content selection)
and may omit captions, tables, code or whole sections, and formulas from MathJax pages
(such captures get a warning). Check **Full extracted captures** in the report and use
`selector` when needed. Text blocks are paragraphs, headings, list items, table cells
and direct container text; `<pre>` keeps indentation and line breaks, other whitespace
is normalized. Formatting-only changes, link targets, images and styles are outside the
text timeline. Paragraph IDs are local to one report.

Inputs are bounded: 100 captures, 10 MiB per capture, 2 MiB per metadata file, 100,000
HTML elements, 256 nesting levels (checked while parsing), 2,000 blocks and 1 million
characters per capture, 50,000 characters per block, and 20,000 blocks / 8 million
characters per collection. Diff and matching budgets degrade to explicit
additions/removals rather than silently dropping text.

The report never embeds capture HTML, escapes all captured text, and its Content
Security Policy blocks every network resource. Opening an original file uses a normal
browser tab, and that file can contain its own active content. Reports and JSON contain
your local file names and extracted text; treat them like the source archive.

## `readwrench check`

`readwrench check FOLDER` recognizes saved lineage manifests and, without writing
anything, extracts every capture they name. It reports invalid manifests
(`lineage/invalid-manifest`), duplicate entries (`duplicate-capture`), missing files
(`capture-missing`), captures that identify another URL (`url-mismatch`), captures that
cannot be read, decoded or yield no article text (`capture-unreadable`), collections
over the limits (`collection-too-large`), and, as information, captures without a known
capture time (`unknown-capture-time`).

## Differences from page-lineage

The original was a Node.js tool built on Mozilla Readability, jsdom, parse5, jsdiff,
mhtml-to-html, Webrecorder warcio, luxon and zod. `readwrench lineage` reimplements
the parts it used in the Python standard library, and keeps the on-disk formats: the
manifest, the timeline JSON (`schemaVersion: 1`, same field names and order) and the
report layout. For the bundled example, the JSON is byte-identical to the original's
and the report differs only in its footer credit.

- **HTML parsing** uses `html.parser` with simplified HTML tree-construction rules
  (implied and optional end tags, tables, templates, raw-text elements). Badly
  mis-nested formatting markup can produce a slightly different tree than a browser.
- **Article extraction** is a port of Readability 0.6.0's content selection
  (`readability.py`); JSON-LD metadata, URL rewriting and lazy-image fixes are left
  out because they do not change extracted text.
- **CSS selectors** (`selector`) support type, id, class and attribute selectors, all
  combinators and common structural pseudo-classes (`:not()`, `:is()`, `:has()`,
  `:nth-child()`…); other pseudo-classes are rejected as invalid.
- **Word segmentation** follows the Unicode word-boundary rules; Chinese and Japanese
  are split per character instead of by dictionary, which can change the similarity
  scores of edited CJK paragraphs slightly.
- **Hosts** with non-ASCII names are converted with IDNA 2003.
- Usage errors exit with status 1 (as before) and are reported by argparse.

## Prior art

ArchiveBox, SingleFile, browser MHTML and WARC crawlers already capture pages;
changedetection.io, webchanges and EDGI web-monitoring-diff already monitor and diff
pages over time. This tool fills a narrower gap: one-shot ingestion of captures you
already saved, with explicit capture-time provenance, conservative paragraph identity
across versions, links to the original files and a portable, escaped-text report.
