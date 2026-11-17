<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo.svg" alt="readwrench" width="320" height="64">
  </picture>
</p>

<p align="center">
  <strong>Find the quotation you lost, carry your notes to the revised edition, and keep the books, papers, fonts and saved web pages you read trustworthy, offline.</strong><br>
  One CLI for readers and annotators. <code>readwrench check</code> scans a reading folder in one read-only pass and tells you which command fixes what.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue" alt="License: AGPL-3.0-or-later"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
</p>

<p align="center">
  English · <a href="README.zh-CN.md">简体中文</a>
</p>

```sh
uvx readwrench check ~/Reading        # or: pipx install readwrench  /  pip install readwrench
```

## Quick start

`check` takes any folder or file. It recognises EPUBs, PDFs, fonts, saved web pages (HTML,
MHTML, WARC) and the review files the other commands save by their content, runs every
analysis that needs no decision from you, and never writes anything:

```sh
pipx install 'readwrench[all]'                 # every command and every check
readwrench check ~/Reading                     # EPUB footnotes, PDF text layers, stale locators...
readwrench check thesis/ --fail-on warning     # as a gate before you submit or share a folder
readwrench check . --format markdown           # a summary to paste into notes or an issue
```

To try it on the bundled samples from a clone:

```sh
git clone https://github.com/tianhaogutum/readwrench && cd readwrench
pip install -e '.[all]'
readwrench check examples -v
```

Exit status: `0` nothing at or above `--fail-on` (default `error`), `1` findings at or above
it, `2` usage error or missing path. `--format json|sarif|markdown` for machines and pages;
`--only`/`--skip` select checks, `--list-checks` lists them. A crashing check is isolated and
reported; a check whose extra is missing becomes one info finding with the install command.
Details: [docs/check.md](docs/check.md).

## What `check` finds

| You have | `check` reports | To look closer or fix it |
| --- | --- | --- |
| **EPUB books** | note references without a way back, broken or ambiguous note links; books a concordance cannot ingest (encrypted, malformed, undecodable) | `footnotes`, `concordance` |
| **PDFs** | documents or page ranges without a text layer, where no quotation can be found | `quote` |
| **Saved quotes** | locators and reviews whose book or paper changed, moved or now extracts different text | `quote verify` |
| **Clue indexes** | indexes that are corrupt or stale against the texts they were built from | `clues build --replace` |
| **PDF page maps and annotation bundles** | maps still in draft, edited after approval, or whose PDFs changed; review bundles whose PDFs or evidence changed | `pdf-pages`, `reanchor` |
| **Font proofs** | rejected subsets, and subsets, fonts or manuscripts changed since the proof | `subset-proof` |
| **Web captures** | timeline manifests with missing, unreadable or foreign captures | `lineage` |
| **Anything** | unreadable, empty or misnamed files (a `.pdf` that is not a PDF) | – |

What always needs your decision (which quote, which two PDFs, which manuscript) is left to the
commands below; once you have run them, `check` re-verifies their saved files on every run.

## All commands

| | Command | What it does | Docs |
| --- | --- | --- | --- |
| **Check** | `check` | One read-only scan of a reading folder, with the command that shows or fixes each problem | [check](docs/check.md) |
| **Find & compare** | `quote` | Recover a lost quotation in a local TXT, Markdown, HTML, PDF or EPUB file; review every occurrence in context, then save a locator bound to the file | [quote](docs/quote.md) |
| | `clues` | Find the material that contains two or three remembered phrases, with one excerpt per clue and the distance between them | [clues](docs/clues.md) |
| | `concordance` | Compare how a term is used across three to five EPUB books: reviewed occurrences in Taguette, exported as a concept matrix | [concordance](docs/concordance.md) |
| **PDF** | `pdf-pages` | Match corresponding pages of two PDF versions before comparing them, so an inserted page does not shift everything | [pdf-pages](docs/pdf-pages.md) |
| | `reanchor` | Carry highlights and comments from an old PDF to the revised one, after you review each move | [reanchor](docs/reanchor.md) |
| **EPUB & fonts** | `footnotes` | Review EPUB footnote paths, confirm each one, and build a copy with precise return links | [footnotes](docs/footnotes.md) |
| | `subset-proof` | Prove that a subset font still sets the actual manuscript text and OpenType features | [subset-proof](docs/subset-proof.md) |
| **Web captures** | `lineage` | Turn several saved captures of one web page into an offline paragraph timeline | [lineage](docs/lineage.md) |

Run `readwrench <command> --help` for the flags. Former tool names (`quote-locator`,
`cluejoin`, `concept-concordance`, `pdf-page-bridge`, `pdf-reanchor`, `epub-footback`,
`page-lineage`) print the new command; saved files from those tools keep working.

## Install

readwrench needs Python 3.11+ on Linux or macOS. The base install has **no third-party
dependencies**: `check`, `clues` and `lineage` use only the standard library. The other
commands each have an extra; without it, the command prints the line to install it.

```sh
pipx install readwrench                        # check, clues, lineage
pipx install 'readwrench[footnotes]'           # + one command (pip install works the same way)
pipx install 'readwrench[all]'                 # everything
```

| Extra | Command | Brings in |
| --- | --- | --- |
| `quote` | `quote` | reanchor, pypdf, EbookLib, Beautiful Soup, lxml, regex, RapidFuzz |
| `concordance` | `concordance` | Taguette 1.5, EbookLib, Beautiful Soup, html5lib, lxml, Jinja2, jsonschema |
| `pdf-pages` | `pdf-pages` | PyMuPDF, Py-PDF-Compare (Python 3.12+, for `export`), Pillow, NumPy, SciPy, RapidFuzz, dhash, ReportLab |
| `reanchor` | `reanchor` | pdfanno 0.2, PyMuPDF, pydantic, RapidFuzz, regex (Python 3.12+) |
| `footnotes` | `footnotes` | lxml |
| `subset-proof` | `subset-proof` | fontTools (with WOFF/WOFF2), uharfbuzz, regex |
| `all` | all of the above | |

## Principles

- **Offline.** No command opens a network connection, uploads a document or calls a model.
- **You decide.** Where a command proposes something (which occurrence is your quotation,
  which page matches which, where an annotation moves, which link returns from a note), you
  review it before anything is written; nothing is confirmed on your behalf.
- **Originals stay untouched.** Commands write new files or new folders and do not overwrite
  unless you ask (`--force`, `--replace`); `check` writes nothing at all.
- **Bound to the exact file.** Saved reviews and locators record the SHA-256 of the book, PDF
  or font they describe, so a changed edition is noticed instead of silently mismatched.

## Docs

- [Documentation index](docs/README.md), one page per command
- [Third-party sources and attribution](THIRD_PARTY.md)

## License

[AGPL-3.0-or-later](LICENSE). Several commands were MIT-licensed tools; their notices are kept
in [licenses/](licenses/) and listed, with every bundled sample and test file, in
[THIRD_PARTY.md](THIRD_PARTY.md).
