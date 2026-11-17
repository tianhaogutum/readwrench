# readwrench subset-proof

A font subset made for a poster, an EPUB or a web edition can quietly lose a character,
a ligature or a kerning pair, and nobody notices until the page is printed or published.
`readwrench subset-proof` proves a subset against the **actual manuscript text**: it checks
Unicode coverage before subsetting, builds the subset with fontTools (or takes one made by
another tool), shapes the original and the subset with HarfBuzz, and reports every glyph,
outline, cluster and spacing difference at its line and column. An offline HTML specimen
draws the exact shaped outlines, with differences in red.

Use it before you embed a subset font in an EPUB, a PDF workflow or a website, or when you
receive a subset from `pyftsubset` or `hb-subset` and want to know whether it still sets
your copy exactly like the original.

Formerly the standalone `subset-proof` tool; the report format is unchanged.

**Reads / writes:** reads the original font, the manuscript (plain UTF-8 text or a JSON
run manifest) and, for `compare`, the candidate subset; never modifies them. Writes one
new `--out` directory (an existing path, including a dangling symlink, is refused) with
`report.json`, `specimen.html` and, for `build`, the generated `subset.ttf` / `subset.otf`
/ `subset.woff` / `subset.woff2`. Everything is staged in a temporary sibling directory and
renamed into place at the end, so a failed run leaves no partial output. No network access.

## Quick start

```sh
pip install 'readwrench[subset-proof]'
readwrench subset-proof --help
```

From a clone of the repository (the fonts are the test fixtures, under the SIL Open Font
License; the copy in `examples/subset-proof/` is short original text):

```sh
readwrench subset-proof build tests/subsetproof/fixtures/fonts/NotoSans.ttf \
  examples/subset-proof/poster.txt --features liga=1,kern=1 --out out/poster
```

Open `out/poster/specimen.html` in a browser or read `out/poster/report.json`; the run also
writes `out/poster/subset.ttf`. Keep the report beside the font: **a generated font is
proven only when `accepted` is true**.

```sh
# Prove the actual delivery format, after compression, at two variable-font locations.
readwrench subset-proof build tests/subsetproof/fixtures/fonts/NotoSans.ttf \
  examples/subset-proof/poster.txt --flavor woff2 \
  --at wght=400,wdth=100 --at wght=800,wdth=75 --out out/web

# Deliberately drop ligatures and kerning to see a rejection and red glyphs.
readwrench subset-proof build tests/subsetproof/fixtures/fonts/NotoSans.ttf \
  examples/subset-proof/poster.txt --keep-features '' --out out/broken

# Prove an existing subset, e.g. one made by hb-subset or pyftsubset.
readwrench subset-proof compare tests/subsetproof/fixtures/fonts/NotoSans.ttf \
  out/poster/subset.ttf examples/subset-proof/poster.txt --out out/external-check

# Explicit Arabic shaping runs with per-run settings.
readwrench subset-proof build tests/subsetproof/fixtures/fonts/NotoSansArabic.ttf \
  examples/subset-proof/arabic.json --manifest --out out/arabic

# Chinese copy (CID-keyed CFF font) and Devanagari copy.
readwrench subset-proof build tests/subsetproof/fixtures/fonts/SubsetProofCJKFixture.otf \
  examples/subset-proof/chinese.txt --out out/chinese
readwrench subset-proof build tests/subsetproof/fixtures/fonts/NotoSansDevanagari.ttf \
  examples/subset-proof/devanagari.txt --out out/devanagari
```

stdout is one line of JSON: `{"accepted": ..., "issues": [...], "report": ..., "specimen": ...}`.
For a rejection, stderr lists the first missing characters with run, line and column, and
the first differing runs. Invalid input is reported on stderr as
`readwrench subset-proof: <message>`.

Exit status: **0** proven, **1** rejected (missing coverage or a shaping difference),
**2** invalid input, unsupported scope or an I/O failure (no output directory is created).

`python -m readwrench subset-proof ...` and `python -m readwrench.subsetproof ...` are
equivalent.

## Commands and options

```text
readwrench subset-proof build   FONT TEXT --out DIR [options] [--keep-features TAGS] [--flavor sfnt|woff|woff2]
readwrench subset-proof compare FONT CANDIDATE TEXT --out DIR [options] [--candidate-index N]
```

- `build` generates a fontTools subset of `FONT` for the manuscript and proves it.
- `compare` proves a `CANDIDATE` subset produced by any other tool.

| Option | Meaning |
| --- | --- |
| `--out DIR` | New output directory; required; must not exist. |
| `--manifest` | `TEXT` is a JSON run manifest instead of plain text. |
| `--font-index N` | Zero-based face index; required for a font collection (`.ttc`/`.otc`). |
| `--candidate-index N` | (`compare`) Face index of a candidate collection. |
| `--features TAGS` | Shaping features for both fonts, e.g. `liga=1,kern=1,ss01=1` or `liga=0`. |
| `--at AXIS=VALUE,...` | Variable-font location, e.g. `wght=700,wdth=75`; repeat for several locations. |
| `--direction` | `auto` (default), `ltr`, `rtl`, `ttb` or `btt`. |
| `--script CODE` | Four-letter Unicode script code, e.g. `Latn`, `Arab`. |
| `--language TAG` | BCP 47 language tag (default `und`), e.g. `tr` for Turkish `fi` handling. |
| `--keep-features TAGS` | (`build`) OpenType features to retain; default `*` (all); an empty value drops all. |
| `--flavor` | (`build`) Output format: `sfnt` (TTF/OTF, default), `woff` or `woff2`. |
| `--version` | Print `readwrench subset-proof <version>`. |

`--keep-features '*'` is the safe default: fontTools keeps every layout feature and
performs glyph closure. `--keep-features liga,kern` keeps only those tags; the comparison
still uses HarfBuzz's normal defaults, so dropping required layout behavior is rejected.
`--features liga=0,ss01=1` controls both shaping calls; it cannot request a feature the
original font does not have. Explicitly dropping `kern` also drops a legacy `kern` table.

The original is shaped **before** subsetting, and the glyph IDs it produces are passed to
fontTools as well as the manuscript's codepoints (the method of HarfBuzz's own regression
recorder). This keeps canonical-composition glyphs and invisible space substitutions that
a plain codepoint list can miss, without changing the manuscript text.

## Inputs

**Plain text** (UTF-8): each nonempty line is one shaping run. Text is never NFC/NFD
normalized, trimmed or deduplicated; a BOM stays a literal codepoint. CRLF and
supplementary-plane offsets are preserved; line and column count Unicode codepoints (not
UTF-8 bytes or visual graphemes). Tabs and bidi controls are rejected because this command
does not do paragraph layout; mixed scripts and mixed LTR/RTL text (including RTL text with
digits) must be split into explicit homogeneous runs first, e.g. with a manifest.

**Run manifest** (`--manifest`; a UTF-8 BOM is allowed; duplicate keys are rejected):

```json
{"runs":[
  {"id":"heading","text":"office affine","language":"en","script":"Latn",
   "features":{"liga":1},"variations":{"wght":650}},
  {"id":"caption","text":"AVATAR","direction":"ltr"}
]}
```

Each run may set `direction`, `script`, `language`, `features` and `variations`;
unspecified settings come from the command line, and run-level coordinates override the
`--at` coordinates. In manifest reports `line` is the run number and `offset` is relative
to the run.

**Fonts:** TTF, OTF (CFF and CFF2), WOFF, WOFF2 and collections, read through fontTools;
HarfBuzz always shapes the decoded sfnt, so compressed delivery formats are proven after
decompression. Each font is read once into a bounded snapshot used for hashing, outlines
and shaping; a font or manuscript that changes during the run is refused.

## What is compared

- **Coverage first.** Every visible character needs a nonzero cmap mapping in the original;
  missing ones are listed with run, line, column and context, and no subset is built.
  Default-ignorable characters (ZWJ, ZWNJ, tag characters...) may lack a mapping and are
  recorded as exemptions. A variation selector counts only if the exact base/selector pair
  is in the font's format-14 cmap.
- **Shaping.** For each run and location: glyph identity, decomposed outline fingerprints,
  clusters, advances and offsets (rounded to 1e-9 em), and the directional font extents
  (ascender, descender, line gap), so unchanged glyphs with a changed line height are
  rejected too. `build` maps subset glyph IDs back to the original; `compare` uses outline
  equivalence, independent of glyph names, which may be missing.
- **`.notdef`** output is a rejection even if both fonts produce it identically.

`report.json` (schema `"schema_version": 1`, `"tool": "subset-proof"`) records font and
manuscript SHA-256 hashes, dependency versions, effective settings, every missing
occurrence, the shaped glyphs with manuscript spans, and field-level differences.
`specimen.html` is self-contained (no web fonts, scripts or network requests; a strict
Content-Security-Policy) and draws the shaped outlines at 32 px/em.

`readwrench check` recognises saved `report.json` files and, without shaping again, reports
a proof that rejected its subset and a subset, original font or manuscript whose bytes no
longer match the proven ones.

## Scope and limits

- It proves the supplied **runs and variable locations**, not every possible text,
  feature combination or location of a variable font.
- HarfBuzz shapes runs; there is no font fallback, bidi itemization, line wrapping or page
  layout. Extract and itemize publication copy first.
- The specimen draws vector outlines; it does not test rasterization, hinting or a
  particular application's layout engine. Use Diffenator3 or fontdiff and an application
  proof for those.
- Color and bitmap fonts (COLR/CPAL, CBDT/CBLC, sbix, SVG, EBDT/EBLC), AAT (morx/mort),
  Graphite (Silf) and DMAP tables are refused. Outline equivalence can treat two identical
  looking glyphs as equal, and can reject visually equal outlines that are encoded
  differently.
- Bounded inputs: 50 MiB per font, 4 MiB of text, 100,000 codepoints per run, 20,000 runs,
  32 `--at` locations and 32 distinct effective locations, **50,000 characters × locations**
  per invocation, and 100,000 shaped glyphs per side. Split longer copy into batches.
- Fonts should come from trusted sources; this is not a sandbox for hostile font files.

## fontTools and variation selectors

fontTools 4.68's strict missing-Unicode mode (`ignore_missing_unicodes = False`) reports a
supported variation selector as missing even when the requested base/selector pair is in
the font's format-14 cmap: its cmap closure computes UVS coverage separately but never adds
the selectors to the matched codepoints. `readwrench subset-proof` therefore checks every
ordinary codepoint and every exact base/selector pair itself before subsetting, enables
fontTools' ignore switch **only** when the admitted manuscript contains a verified pair,
records that decision in `report.json` (`settings.subsetter_ignore_missing_unicodes`), and
checks the subset's pair coverage and shaping again. No fontTools code is patched.

## Prior art

This is an integration of existing tools, not a new subsetter or shaper:

- **fontTools / pyftsubset** does the font parsing, subsetting, glyph closure, feature
  retention, variable outlines and WOFF2. `pyftsubset --no-ignore-missing-unicodes` already
  refuses missing characters; this command adds manuscript locations for every occurrence
  and an independent shaping proof of the saved subset.
- **HarfBuzz** (through uharfbuzz) does all shaping. Its regression recorder
  (`test/shape/record-test.sh`) already subsets and compares shaping; this command applies
  that method to full manuscripts with a structured report.
- **Diffenator3** and **Diffenator2** compare whole font families (glyphs, kerning, tables,
  word images, variable locations); **fontdiff** renders HarfBuzz-shaped text into raster
  difference PDFs. Use them for broad font QA and raster checks; this command adds strict
  manuscript coverage before subsetting and per-occurrence diagnostics.
