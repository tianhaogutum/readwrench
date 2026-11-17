# readwrench documentation

One page per command. Each page starts with the problem the command solves and a quick
start, followed by the full reference. Run `readwrench <command> --help` for the flags.
Quick starts use the sample inputs under [`examples/`](../examples/), so run them from a
clone of the repository (`git clone https://github.com/tianhaogutum/readwrench`).

Every command works offline and needs Python 3.11+ on Linux or macOS. `check`, `clues` and
`lineage` use only the standard library; the others name the extra to install
(`pip install 'readwrench[<extra>]'`, or `readwrench[all]`). `reanchor` and the `export` step
of `pdf-pages` need Python 3.12+, because pdfanno and Py-PDF-Compare do.

## Check

| Command | Use it to |
| --- | --- |
| [`readwrench check`](check.md) | Scan a reading folder in one read-only pass: broken EPUB footnotes, PDFs without a text layer, stale quote locators, page maps, annotation bundles, font proofs and capture manifests, with the command that shows the details |

## Find and compare

| Command | Use it to | Extra |
| --- | --- | --- |
| [`readwrench quote`](quote.md) | Recover a lost quotation in a local TXT, Markdown, HTML, PDF or EPUB file, review every exact or normalized occurrence in context, and save a locator bound to the file | `quote` |
| [`readwrench clues`](clues.md) | Find the material that contains two or three remembered phrases, with one excerpt per clue and the distance between them | – |
| [`readwrench concordance`](concordance.md) | Compare how a term is used across three to five EPUB books: reviewed occurrences in Taguette, exported as a concept-by-book matrix | `concordance` |

## PDF

| Command | Use it to | Extra |
| --- | --- | --- |
| [`readwrench pdf-pages`](pdf-pages.md) | Match corresponding pages of two PDF versions, approve the page map, then export a Py-PDF-Compare report that is not shifted by an inserted page | `pdf-pages` |
| [`readwrench reanchor`](reanchor.md) | Carry highlights and comments from an old PDF to a revised one, after reviewing each proposed position | `reanchor` |

## EPUB and fonts

| Command | Use it to | Extra |
| --- | --- | --- |
| [`readwrench footnotes`](footnotes.md) | Audit EPUB footnote links, confirm each reference → note → return path, and build a copy with precise return links | `footnotes` |
| [`readwrench subset-proof`](subset-proof.md) | Prove that a subset font still sets the actual manuscript text, with every glyph, cluster and OpenType feature, before you ship it | `subset-proof` |

## Web captures

| Command | Use it to | Extra |
| --- | --- | --- |
| [`readwrench lineage`](lineage.md) | Turn several saved captures (HTML, MHTML, WARC, ArchiveBox) of one web page into an offline paragraph timeline | – |

## Former tool names

| Former tool | Now |
| --- | --- |
| `quote-locator` | `readwrench quote` |
| `cluejoin` | `readwrench clues` |
| `concept-concordance` | `readwrench concordance` |
| `pdf-page-bridge` | `readwrench pdf-pages` |
| `pdf-reanchor` | `readwrench reanchor` |
| `epub-footback` | `readwrench footnotes` |
| `subset-proof` | `readwrench subset-proof` |
| `page-lineage` (Node.js) | `readwrench lineage` (Python port) |

Saved files keep their formats and schema ids, so reviews, locators, indexes, workspaces,
page maps, bundles, reports and manifests made by the former tools still work. Typing a former
tool name after `readwrench` prints the new command.
