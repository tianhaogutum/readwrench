# Third-party sources and attribution

readwrench is licensed under the GNU Affero General Public License, version 3 or (at your
option) any later version (AGPL-3.0-or-later; see [LICENSE](LICENSE)). Its commands were
previously separate tools by the same authors:

- `concept-concordance`, `pdf-page-bridge` and `pdf-reanchor` were AGPL-3.0-or-later. They
  build on AGPL libraries (EbookLib, PyMuPDF / MuPDF, pdfanno), which is why the combined
  package is AGPL-3.0-or-later rather than MIT.
- `quote-locator`, `cluejoin`, `epub-footback`, `subset-proof` and `page-lineage` were MIT.
  Their code is included here under their original MIT notices, kept in [licenses/](licenses/);
  the combined work is distributed under AGPL-3.0-or-later.

Each section below keeps the original attributions of one command, with paths updated to this
repository's layout. Product names identify interoperable formats and tools only; readwrench
is not affiliated with or endorsed by any project named here.

## At a glance

- **No runtime dependencies in the base install.** `readwrench check`, `clues` and `lineage`
  use only the Python standard library. Every other command's dependencies are installed by
  an extra named after the command (`readwrench[quote]`, `[concordance]`, `[pdf-pages]`,
  `[reanchor]`, `[footnotes]`, `[subset-proof]`, or `[all]`); none of them is vendored.
- **Two ported components are derivative works of JavaScript libraries:** Mozilla
  Readability (Apache-2.0) and jsdiff (BSD-3-Clause), in `readwrench lineage`. Their license
  texts are in [licenses/](licenses/); see [`readwrench lineage`](#readwrench-lineage-formerly-page-lineage).
- **The wheel contains only the `readwrench` package and these notices.** Fonts, EPUBs, PDFs
  and web captures used by the tests and examples ship in the sdist only, each with its
  provenance and license as listed below.
- **`readwrench check`** and the top-level dispatcher are original code for this package
  (AGPL-3.0-or-later). The check adapters (`src/readwrench/<tool>/_check.py`) call the same
  tools' own code.
- Test-only dependencies (`test` extra): pytest (MIT), Hypothesis (MPL-2.0), jsonschema (MIT),
  PyLD (BSD-3-Clause), rfc3986-validator (MIT), EbookLib (AGPL-3.0), ReportLab (BSD-3-Clause),
  pypdf (BSD-3-Clause). Build-only tools: hatchling (MIT) and hatch-fancy-pypi-readme (MIT).

## Contents

- [`readwrench quote` (formerly quote-locator)](#readwrench-quote-formerly-quote-locator)
- [`readwrench clues` (formerly cluejoin)](#readwrench-clues-formerly-cluejoin)
- [`readwrench concordance` (formerly concept-concordance)](#readwrench-concordance-formerly-concept-concordance)
- [`readwrench pdf-pages` (formerly pdf-page-bridge)](#readwrench-pdf-pages-formerly-pdf-page-bridge)
- [`readwrench reanchor` (formerly pdf-reanchor)](#readwrench-reanchor-formerly-pdf-reanchor)
- [`readwrench footnotes` (formerly epub-footback)](#readwrench-footnotes-formerly-epub-footback)
- [`readwrench subset-proof` (formerly the standalone subset-proof)](#readwrench-subset-proof-formerly-the-standalone-subset-proof)
- [`readwrench lineage` (formerly page-lineage)](#readwrench-lineage-formerly-page-lineage)

## `readwrench quote` (formerly quote-locator)

Ported from the standalone `quote-locator` tool, MIT License, Copyright (c) 2026
Quote Locator contributors (full notice: `licenses/quote-locator-MIT.txt`). It is a
small adapter around existing libraries, installed with `readwrench[quote]` and not
vendored:

| Component | Used for | License |
| --- | --- | --- |
| [reanchor](https://github.com/microbluey/reanchor) | offset-preserving normalization, exact occurrence search, span projection, quote selectors | MIT |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) | static HTML/XHTML text extraction | MIT |
| [pypdf](https://github.com/py-pdf/pypdf) | PDF page text extraction | BSD-3-Clause |
| [EbookLib](https://github.com/aerkalov/ebooklib) | EPUB 2/3 spine and resource reading | AGPL-3.0-or-later |
| [lxml](https://lxml.de/) | XML parser for EPUB container/encryption metadata (also used by EbookLib) | BSD-3-Clause |
| [regex](https://pypi.org/project/regex/) | Unicode grapheme cluster boundaries | Apache-2.0 AND CNRI-Python |
| [RapidFuzz](https://rapidfuzz.github.io/RapidFuzz/) | character edit scripts for the displayed differences | MIT |

Exported records follow the W3C [Web Annotation Data Model](https://www.w3.org/TR/annotation-model/)
(TextQuoteSelector, TextPositionSelector) and [Readium locators](https://readium.org/architecture/models/locators/);
no code is copied from either. Prior art surveyed by the original tool: reanchor (reused),
Anchorpoint, dom-anchor-text-quote, the Hypothesis client and VeriQuote (not used).

Test fixtures only (not installed): the W3C Web Annotation JSON-LD context
`tests/quotelocator/fixtures/standards/anno.jsonld` (<https://www.w3.org/ns/anno.jsonld>, unmodified),
under the W3C Software and Document License 2023 (`licenses/W3C-software-and-document-license-2023.txt`,
original page kept next to the fixture); and the Readium locator JSON Schema
(<https://readium.org/architecture/schema/locator.schema.json>), kept as published plus a copy with one
trailing comma removed (`readium-locator-schema.patch`), under Readium's BSD-3-Clause license
(`licenses/BSD-3-Clause-Readium-architecture.txt`, Copyright (c) 2017, Readium).

## `readwrench clues` (formerly cluejoin)

- Original tool: cluejoin 0.1.0, MIT License, Copyright (c) 2026 Cluejoin contributors.
  Full text: `licenses/cluejoin-MIT.txt`.
- Uses only the Python standard library; retrieval is SQLite FTS5 with the trigram tokenizer
  (SQLite is public domain) via Python's `sqlite3`.
- Optional `readwrench clues capture` runs the external ripgrep-all (`rga`) as a separate process and, through
  it, ripgrep and Poppler's `pdftotext`. None of them is bundled, linked or copied. The documented rga 0.10.10 `--rga-no-cache` workaround refers to the upstream fix in
  ripgrep-all PR #338 (https://github.com/phiresky/ripgrep-all/pull/338).
- Prior art credited in `docs/clues.md` (behaviour comparisons only, no code used): ripgrep-all,
  SQLite FTS5, Docfd, ugrep, git grep, Recoll.
- Test fixtures (not shipped in the wheel; under `tests/cluejoin/fixtures/`):
  - `rfc2616.pdf`: RFC 2616, "Hypertext Transfer Protocol -- HTTP/1.1" (June 1999), downloaded unmodified
    from https://www.rfc-editor.org/rfc/rfc2616.pdf. Copyright (C) The Internet Society (1999). All Rights
    Reserved. Copying the unmodified document is permitted by its Full Copyright Statement, reproduced in
    `licenses/RFC2616-ISOC-copyright.txt`.
  - `rfc2616.txt`, `rfc2616-layout-crlf.txt`: Poppler `pdftotext` extractions of that PDF (same notice).
  - `rga-rfc2616.jsonl`, `rga-rfc2616-context.jsonl`: ripgrep-all 0.10.10 JSON output over that PDF
    (same notice for the quoted RFC text).
  - Provenance and checksums: `tests/cluejoin/fixtures/README.md`.

## `readwrench concordance` (formerly concept-concordance)

Formerly the standalone `concept-concordance` tool, AGPL-3.0-or-later, by the same
authors; ported without third-party code. It is a thin adapter around
[Taguette](https://www.taguette.org/) and does not copy or patch Taguette.

- **Runtime dependencies** (`concordance` extra, installed separately, not vendored):
  [Taguette](https://gitlab.com/remram44/taguette) 1.5 (BSD-3-Clause; its database models,
  project copy and UTF-8 snippet extractor are called, and a workspace's
  `project.sqlite3` is a Taguette database);
  [EbookLib](https://github.com/aerkalov/ebooklib) (AGPL-3.0, which informed the
  AGPL-3.0-or-later licensing of the original tool);
  [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) (MIT);
  [html5lib](https://github.com/html5lib/html5lib-python) (MIT);
  [lxml](https://lxml.de/) (BSD-3-Clause); [Jinja2](https://palletsprojects.com/p/jinja/) and
  MarkupSafe (BSD-3-Clause); [jsonschema](https://github.com/python-jsonschema/jsonschema)
  (MIT); [setuptools](https://github.com/pypa/setuptools) (MIT; only a `<81` constraint so
  Taguette's OpenTelemetry dependency can import `pkg_resources`).
- **Standards followed:** exports use the
  [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/) (text position
  and text quote selectors) and the
  [Readium Locator model](https://readium.org/architecture/models/locators/). CSV
  formula-prefixing follows OWASP's
  [CSV Injection](https://owasp.org/www-community/attacks/CSV_Injection) guidance.
- **Prior art credited:** Taguette 1.5.2 (manual coding, the host of this workflow),
  CATMA 7.4.0, Voyant Tools Contexts / VoyantServer 2.6.23 and the Zotero 7 EPUB reader
  already cover concordances, cross-text annotation and EPUB note taking. This tool adds
  only an edition-aware EPUB intake (source SHA-256, resource and quote locators) and a
  concept/sense-by-book matrix to Taguette's workflow.
- **Bundled books:** `examples/concordance/gutenberg/*.epub` are six unmodified
  [Project Gutenberg](https://www.gutenberg.org/) ebooks (Machiavelli, *The Prince*,
  W. K. Marriott translation, #1232, EPUB3 and EPUB2; Locke, *Second Treatise of
  Government*, #7370; Plato, *The Republic*, Benjamin Jowett translation, #1497; Laozi,
  *道德經*, #7337; Confucius, *論語*, #23839), retrieved 2026-11-11 (URLs, sizes and
  SHA-256 in `examples/concordance/gutenberg/provenance.json`). They are public domain in
  the USA. Each file keeps its full Project Gutenberg License and credits; a plain-text
  copy is in [licenses/Project-Gutenberg-License.txt](licenses/Project-Gutenberg-License.txt).
  "Project Gutenberg" is a registered trademark of the Project Gutenberg Literary Archive
  Foundation; the files are redistributed unmodified and free of charge, and readwrench
  is not affiliated with or endorsed by Project Gutenberg. They are used by the quick
  start and the tests.
- **Test fixtures from standards bodies** (`tests/concordance/fixtures/schema/`):
  `w3c-anno.context.jsonld`, the unmodified W3C Web Annotation JSON-LD context
  (<https://www.w3.org/ns/anno.jsonld>), Copyright © World Wide Web Consortium, under the
  W3C Software and Document License 2023
  ([text](licenses/W3C-software-and-document-license-2023.txt)); and
  `readium-locator.schema.json`, the Readium Locator JSON Schema from
  readium/architecture with one trailing comma removed so it parses as strict JSON (the
  change is shown in `tests/concordance/fixtures/README.md`), BSD-3-Clause, Copyright (c)
  2017, Readium ([text](licenses/BSD-3-Clause-Readium-architecture.txt)).
- **Upstream findings, not submitted:** the Readium schema's trailing comma; Taguette
  1.5.2's `pkg_resources` import; Taguette 1.5.2's highlight update leaving a stale
  cached snippet (summarized with the proposed one-line fix in `docs/concordance.md`).
  readwrench applies none of these patches.
- Examples (`concepts*.json`, `create_sample.py`, `check_bundle.py`) and the synthetic
  EPUBs the tests generate are original, under the root license.

## `readwrench pdf-pages` (formerly pdf-page-bridge)

A review/export adapter for [Py-PDF-Compare](https://github.com/grananda/Py-PDF-Compare)
(by grananda), originally the standalone `pdf-page-bridge` tool, AGPL-3.0-or-later like
readwrench. It does not bundle Py-PDF-Compare code. At runtime it subclasses
`pdf_compare.PDFComparator` (version 2026.8.24, pinned) and calls its word-diff and
vector page-composition methods. Py-PDF-Compare's package metadata declares MIT, while
the `LICENSE` file shipped in its 2026.8.24 wheel is the GNU GPL v3. Either is
compatible with AGPL-3.0-or-later (GPLv3 section 13).

Runtime libraries (installed, not bundled): PyMuPDF (AGPL-3.0 or Artifex commercial),
Pillow (MIT-CMU), NumPy (BSD-3-Clause), SciPy (BSD-3-Clause), RapidFuzz (MIT),
dhash (MIT), ReportLab (BSD-3-Clause). The audit page embeds a subset of the CJK font
that ships inside PyMuPDF (`fitz.Font("cjk")`, Droid Sans Fallback, Apache-2.0) so
that Unicode reviewer names and file names render.

Prior art credited in the tool's design (run or read before it was written):
Py-PDF-Compare (text page alignment, insertion/deletion, word diff, vector report),
@pdf-diff/core (text-shingle page mapping), diff-pdf, diff-pdf-visually, pdfcmp,
htmlapps-pdf-compare (visual comparison), pdfplumber and Docling (extraction).
The tool adds only the editable, hash-bound, human-approved page map.

Sample and test PDFs (`examples/pdf-pages/*.pdf`, `tests/pagebridge/fixtures/*.pdf`)
come from the pdfplumber project's examples and test files
(<https://github.com/jsvine/pdfplumber>): a California EDD WARN report (page
selections, re-orderings and 90-DPI image-only renders made with PyMuPDF), a USDA
agricultural energy report, a Federal Register issue and two annotation regression
documents. They are public documents and keep their publishers' rights; they are
not relicensed with readwrench. Provenance and SHA-256 sums are in
`tests/pagebridge/fixtures/README.md` and `manifest.json`.

## `readwrench reanchor` (formerly pdf-reanchor)

An extension of [pdfanno 0.2.2](https://pypi.org/project/pdfanno/0.2.2/)
(AGPL-3.0-or-later, © pdfanno contributors). It imports pdfanno's anchor extraction
(`diff.anchors.extract_anchors`), anchor types, section index, context similarity and
weighted candidate scoring, including private scoring helpers, without modifying or
vendoring pdfanno code. The standalone tool was AGPL-3.0-or-later, matching pdfanno and
PyMuPDF; its license file, including the pdfanno header and the PyMuPDF / MuPDF licensing
note, is kept as `licenses/pdf-reanchor-AGPL-3.0-or-later.txt`.

Runtime libraries (installed by `readwrench[reanchor]`, not bundled):
- [PyMuPDF](https://pymupdf.readthedocs.io/) / MuPDF (Artifex, AGPL-3.0 or commercial
  license): all PDF reading, writing, glyph geometry and rendering. Commercial or
  closed-source distribution of the combined work requires full AGPL-3.0 compliance or a
  MuPDF commercial license from Artifex.
- [pydantic](https://docs.pydantic.dev/) (MIT): proposal/decision contract.
- [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) (MIT): fuzzy alignment.
- [regex](https://github.com/mrabarnett/mrab-regex) (Apache-2.0 / CNRI-Python): Unicode
  grapheme clusters.

Prior art credited in `docs/reanchor.md`: pdfanno, PyMuPDF, pdfplumber, pdfannots, Docling,
Hypothesis client and anchor-quote, rpdf, Coherent PDF, Zotero.

Bundled files:
- `examples/reanchor/reordered_v1.pdf`, `reordered_v2.pdf`: generated by the fixture builder
  in the pdfanno 0.2.2 source distribution (AGPL-3.0-or-later); source archive SHA-256
  `d752a4b908d3eb3fb57da5b3a97d2f58cc45abf8375660981a00530ce4b5aaae`.
- `tests/reanchor/fixtures/upstream/*.pdf` (`hotos17.pdf`, `word2column.pdf`,
  `FreeText-annotation.pdf`, `swapped-corners.pdf`, `indirect-objects.pdf`,
  `annots-entries-without-subtype.pdf`): copied unchanged from the test corpus of
  [pdfannots](https://github.com/0xabu/pdfannots) at commit
  `4a6c71e10ceea6aa64481f0bd19ef1eaa58df89c`, MIT, © Microsoft Corporation (2016-2022) and
  Andrew Baumann (2022-); license in `licenses/pdfannots-MIT.txt` (and next to the fixtures).

## `readwrench footnotes` (formerly epub-footback)

Original code: MIT, "Copyright (c) 2026 epub-footback contributors" — see
[`licenses/epub-footback-MIT.txt`](licenses/epub-footback-MIT.txt).

Runtime dependency (not bundled): [lxml](https://lxml.de/) (BSD-3-Clause), installed by the
`footnotes` extra. Optional external validator (not bundled, run only when the user passes
`--epubcheck-jar`): [EPUBCheck](https://github.com/w3c/epubcheck) (BSD-3-Clause), executed as
`java -jar`. Uses Python's `zipfile`, Expat and `difflib`.

Design informed by (no code copied): Sigil FootnoteLinker (Doitsu), calibre's editor, the Sigil
Duokan Footnote Generator, KOReader HTML Replacer, EbookLib, Ace by DAISY, Thorium Reader, and the
[DAISY notes guidance](https://kb.daisy.org/publishing/docs/html/notes.html).

Test and example data:
- `tests/footnotes/fixtures/upstream/FNLTest.epub`: Doitsu's published Sigil FootnoteLinker test
  book ([MobileRead attachment 157776](https://www.mobileread.com/forums/attachment.php?attachmentid=157776&d=1499438079)),
  unchanged. It carries no explicit rights metadata; kept as test data only and not claimed to be
  covered by this project's licenses.
- `tests/footnotes/fixtures/sigil-linked-epub2.epub`: a test adaptation of that sample (forward
  links and note IDs added with lxml by `tests/footnotes/make_epub2_fixture.py`); same status.
- `tests/footnotes/fixtures/upstream/moby-dick.epub` and `wasteland.epub`: unchanged IDPF
  [EPUB 3 samples](https://github.com/IDPF/epub3-samples/releases/tag/20230704), CC BY-SA 3.0
  Unported (stated in each book's `dc:rights`; attribution embedded). Test data only.
- `examples/footnotes/*.epub`: small original books written by EbookLib 0.20 for the original tool
  (MIT, with its code).

## `readwrench subset-proof` (formerly the standalone subset-proof)

Formerly the standalone `subset-proof` tool, MIT License, Copyright (c) 2026 Subset Proof
contributors ([text](licenses/subset-proof-MIT.txt)). Its code is included here under that
notice.

It is a thin integration layer and contains no third-party code. It wraps, as separately
installed runtime dependencies of the `subset-proof` extra:

- [fontTools](https://github.com/fonttools/fonttools) (MIT) — font parsing, subsetting,
  glyph closure, variable outlines, pens and WOFF/WOFF2; with its `woff` extra,
  [Brotli](https://github.com/google/brotli) (MIT) and
  [zopfli](https://github.com/fonttools/py-zopfli) (Apache-2.0).
- [uharfbuzz](https://github.com/harfbuzz/uharfbuzz) (Apache-2.0), which bundles
  [HarfBuzz](https://github.com/harfbuzz/harfbuzz) (MIT "Old MIT" license) — all text shaping.
- [regex](https://github.com/mrabarnett/mrab-regex) (Apache-2.0 AND CNRI-Python) — Unicode
  `Default_Ignorable_Code_Point` matching.

Prior art credited by the original tool: the subset-then-shape comparison follows HarfBuzz's
regression recorder (`test/shape/record-test.sh`); strict missing-Unicode subsetting is
fontTools' `pyftsubset --no-ignore-missing-unicodes`; whole-family comparison tools
[Diffenator3](https://github.com/googlefonts/diffenator3) and
[Diffenator2](https://github.com/googlefonts/diffenator2) (Google Fonts) and
[fontdiff](https://github.com/davelab6/fontdiff) informed the specimen and scope. No code was
copied from them.

**Test fixture fonts** (in `tests/subsetproof/fixtures/fonts/`, not in the wheel; each license
text is kept next to its font and copied to `licenses/`). All are licensed under the SIL Open
Font License 1.1; provenance and SHA-256 hashes are in `tests/subsetproof/fixtures/sources.json`.

| Font file | Upstream | Copyright / Reserved Font Name | Status | License text |
| --- | --- | --- | --- | --- |
| `NotoSans.ttf` (Noto Sans 2.015, variable) | [google/fonts ofl/notosans](https://github.com/google/fonts/tree/main/ofl/notosans) | Copyright 2022 The Noto Project Authors; no RFN | unmodified | [OFL-1.1-NotoSans.txt](licenses/OFL-1.1-NotoSans.txt) |
| `NotoSansArabic.ttf` (2.012, variable) | [google/fonts ofl/notosansarabic](https://github.com/google/fonts/tree/main/ofl/notosansarabic) | Copyright 2022 The Noto Project Authors; no RFN | unmodified | [OFL-1.1-NotoSansArabic.txt](licenses/OFL-1.1-NotoSansArabic.txt) |
| `NotoSansDevanagari.ttf` (2.007, variable) | [google/fonts ofl/notosansdevanagari](https://github.com/google/fonts/tree/main/ofl/notosansdevanagari) | Copyright 2022 The Noto Project Authors; no RFN | unmodified | [OFL-1.1-NotoSansDevanagari.txt](licenses/OFL-1.1-NotoSansDevanagari.txt) |
| `SourceSerif4-Regular.otf` (4.005) | [adobe-fonts/source-serif](https://github.com/adobe-fonts/source-serif/tree/release/OTF) | Copyright 2014-2023 Adobe, with Reserved Font Name "Source" | unmodified (the RFN is not used by any derivative here) | [OFL-1.1-SourceSerif4.txt](licenses/OFL-1.1-SourceSerif4.txt) |
| `SubsetProofCJKFixture.otf` | derived from Noto Sans CJK SC 2.004, [notofonts/noto-cjk](https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/SimplifiedChinese) | Noto CJK authors (OFL-1.1) | **modified**: fontTools subset (110 glyphs) of the example Chinese copy plus vertical punctuation, renamed "Subset Proof CJK Fixture"; reproducible with `tests/subsetproof/fixtures/make_cid_fixture.py` | [OFL-1.1-NotoSansCJK.txt](licenses/OFL-1.1-NotoSansCJK.txt) |

Fonts generated or mutated at test time are made in temporary directories and are not shipped.
The example copy in `examples/subset-proof/` is short original text written for the tool.

## `readwrench lineage` (formerly page-lineage)

Original tool: page-lineage, MIT License, Copyright (c) 2026 Page Lineage contributors
(`licenses/page-lineage-MIT.txt`). It was a Node.js composition of npm packages; this
Python port reimplements the parts it used with the standard library. No npm code is
bundled; two components are ported closely enough to be derivative works:

- **Mozilla Readability 0.6.0** (Apache-2.0, Copyright (c) 2010 Arc90 Inc and Mozilla
  contributors; `licenses/Readability-Apache-2.0.txt`): `src/readwrench/lineage/readability.py`
  is a Python port of Readability.js's content selection (metadata title/byline,
  prepDocument, grabArticle, prepArticle, simplifyNestedElements), with a notice of
  changes in the file header.
- **jsdiff 9.0.0** (`diff`, BSD-3-Clause, Copyright (c) 2009-2015 Kevin Decker;
  `licenses/jsdiff-BSD-3-Clause.txt`): `src/readwrench/lineage/textdiff.py` ports its
  array diff (Myers algorithm with jsdiff's path selection, timeout and edit budget).

Behaviour reimplemented from scratch (no code copied), following the same public
specifications or documented behaviour:
- jsdom 31.0.0 / parse5 8.0.1 (MIT): HTML tree building on `html.parser` with
  simplified WHATWG tree-construction rules, bounded element count and depth; a small
  CSS selector engine for `querySelector`.
- html-encoding-sniffer 7.0.0 (MIT) and @exodus/bytes 1.16.0 (MIT): WHATWG Encoding
  Standard label table, BOM / transport label / `<meta>` prescan sniffing, legacy decoding.
- luxon 3.7.2 (MIT): ISO, RFC 2822, HTTP-date and JavaScript `Date.toString()` parsing
  with weekday/calendar validation.
- mhtml-to-html 2.2.0 (MIT): MHTML `multipart/related` parsing (headers, main
  resource, base64/quoted-printable, charsets, recovery anomalies).
- Webrecorder warcio.js 2.4.12 (Apache-2.0): WARC 1.0/1.1 record, HTTP header, chunked
  and gzip/deflate decoding.
- zod 4.6.7 (MIT): manifest schema validation and its message wording.
- ICU `Intl.Segmenter` word boundaries (Unicode UAX #29 rules) and the WHATWG URL
  parser (`new URL`), approximated.

Test fixtures (in `tests/lineage/fixtures/`, see its README; not shipped in the wheel):
`nytimes.html` from the Mozilla Readability test corpus (Apache-2.0; article text ©
its publisher); `portfolio.mhtml` (and its conversion `portfolio.html`) from
msindwan/mhtml2html, MIT, Copyright (c) 2016 Mayank Sindwani (`licenses/mhtml2html-MIT.txt`);
`example.warc.gz` and `iana-chunked.warc` from Webrecorder warcio.js test data, with the
license text shipped alongside them, Copyright (c) 2020 Webrecorder Software
(`licenses/warcio-js-test-data-MIT.txt`).

Prior art credited by the original tool: ArchiveBox, SingleFile / single-file-cli,
changedetection.io, webchanges, EDGI web-monitoring-diff (surveyed; no code used).
