# `readwrench quote` test fixtures

Most test documents are generated in `tmp_path` by the real upstream writers in
`../_writers.py`:

- `write_pdf`: pypdf `PdfWriter`, real page/font/content objects and PDF text
  operators; repeated quotes and a passage split across physical pages. Empty
  and encrypted PDFs use the same writer.
- `write_epub`: EbookLib `write_epub`, a real EPUB3 archive with XHTML chapters,
  NCX and navigation; manifest order differs from spine order. The EPUB2 variant
  changes only the package version of the generated archive (a compatibility
  fixture, not an untouched EPUB2 publication).
- HTML and TXT cases are written inline by the tests; they are synthetic and cover
  inline formatting, hidden text, paragraph boundaries, encodings, ligatures,
  Unicode and quote ambiguity.

`examples/quote/book.txt` is an original synthetic book fragment (MIT, from the
original `quote-locator` tool) that shows why context matters.

## `standards/`

Fetched 2026-11-11 for offline interoperability checks. Neither the application
nor the tests dereference any source URL.

- `anno.jsonld`: <https://www.w3.org/ns/anno.jsonld>, 5,932 bytes, SHA-256
  `c10fd886c5c726fbfd51747b8677eb8f7d02c039357269622de7382e5c20d410`. The unmodified
  W3C Web Annotation JSON-LD context. The W3C Software and Document License is kept
  as `W3C-LICENSE.html`, from <https://www.w3.org/copyright/software-license-2023/>.
- `locator.schema.upstream.json`:
  <https://readium.org/architecture/schema/locator.schema.json>, SHA-256
  `4a35a3e4d66ab4c5d1d0c4f51689c39ef52fc83d5657a9c30a8dfe6c3efbcfef`. The published
  bytes have a trailing comma (line 65) and fail standard JSON parsing.
  `locator.schema.json` removes **only that comma**; the exact change is
  `readium-locator-schema.patch`. Readium's BSD-3-Clause notice is kept in
  `readium-LICENSE.md`, from
  <https://raw.githubusercontent.com/readium/architecture/master/LICENSE.md>.

`test_standards.py` expands exported annotations with PyLD and an allowlisted local
document loader (checking source IRIs, quote text and nonnegative integer
positions), and validates exported Readium locators against the patched schema with
jsonschema and rfc3986-validator (real `uri-reference` checking). These check the
exported structures and vocabulary; they are not a claim of complete reading-system
or W3C test-suite conformance.
