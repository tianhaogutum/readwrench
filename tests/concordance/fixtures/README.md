# `readwrench concordance` test fixtures

The real books live in `examples/concordance/gutenberg/` (unmodified Project Gutenberg
EPUBs; see the README and `provenance.json` there) and are shared by the quick start and
the tests. Every other EPUB is generated in `tmp_path` with EbookLib's real writer
(`conftest.py`) to cover EPUB3, Unicode, malformed input, paths and matching corner cases;
those are synthetic regression inputs. EbookLib's writer always emits EPUB3, so the EPUB2
test uses the real Gutenberg EPUB2 edition `prince-epub2.epub`.

## `schema/`

Retrieved 2026-11-11; the tests never dereference these URLs (the JSON-LD test uses a
local document loader and sends no book content anywhere).

- `w3c-anno.context.jsonld`: <https://www.w3.org/ns/anno.jsonld>, unmodified, SHA-256
  `c10fd886c5c726fbfd51747b8677eb8f7d02c039357269622de7382e5c20d410`. W3C Software and
  Document License (2023), see `licenses/W3C-software-and-document-license-2023.txt`.
- `readium-locator.schema.json`: the Readium Locator reference schema from
  <https://raw.githubusercontent.com/readium/architecture/master/schema/locator.schema.json>
  (upstream SHA-256 `4a35a3e4d66ab4c5d1d0c4f51689c39ef52fc83d5657a9c30a8dfe6c3efbcfef`).
  The published bytes have a trailing comma after the `text` property (line 65) and fail
  strict JSON parsing; this copy removes **only that comma**:

  ```diff
  @@ -62,7 +62,7 @@
             "type": "string"
           }
         }
  -    },
  +    }
     },
     "required": [
       "href",
  ```

  BSD-3-Clause, Copyright (c) 2017, Readium; see
  `licenses/BSD-3-Clause-Readium-architecture.txt`.
