# readwrench footnotes

Many EPUBs have note references that jump forward to a footnote or endnote but no
precise way back: no return link at all, one link back to the top of the chapter, or
one shared link for several references to the same note. `readwrench footnotes`
audits the existing note links of a book **offline**, lets you confirm each
reference → note → return path one by one, and builds a **separate** candidate EPUB
that adds only the approved return links. Use it when you want reliable footnote
navigation in a book you already have, without renumbering or rewriting it.

Formerly the standalone `epub-footback` tool.

**Reads / writes:** reads only the EPUB and decision files you name; the input book is
never modified (its hash is checked before and after). `scan` creates one new review
directory, `review` one new decisions file, `candidate` one new bundle directory. An
existing output path is refused. Nothing is extracted to disk and no network access is
made; Java runs only when you pass `--epubcheck-jar`.

## Quick start

```sh
pip install 'readwrench[footnotes]'
readwrench footnotes --help
```

From a clone of the repository, run it on the bundled example (a small EbookLib-written
EPUB 3 with one forward note link and no return link):

```sh
readwrench footnotes scan examples/footnotes/no-return.epub --out review
readwrench footnotes review review/plan.json --out decisions.json
readwrench footnotes candidate examples/footnotes/no-return.epub decisions.json \
  --out candidate --skip-epubcheck
```

`scan` prints a one-line JSON summary (1 reference, status `missing-return`). In
`review`, check the displayed association and enter `a` to approve adding a precise
return. Instead of the terminal review you can open `review/review.html` in a normal
browser, confirm each reference there and use **Download decisions.json**; the page is
self-contained and never loads the book's HTML, scripts, assets or external links. A
text editor works too: in a copy of `plan.json`, set a record's `approved` to `true`
and its `action` to `add_backlink` or `keep`.

For a validated candidate, download the official
[EPUBCheck](https://github.com/w3c/epubcheck/releases) distribution (keep its `lib/`
next to `epubcheck.jar`) and replace `--skip-epubcheck` with
`--epubcheck-jar path/to/epubcheck.jar` (or set `EPUBCHECK_JAR`). EPUBCheck is not
bundled.

Other examples in `examples/footnotes/`: `document-note.epub` (forward link to a whole
notes file), `unicode.epub` (Unicode paths and IDs), `existing-return.epub` (an exact
return already present: kept, not duplicated) and `utf16.epub` (UTF-16 XHTML; a
negative EPUBCheck case, see [Limits](#preservation-and-limits)).

`python -m readwrench footnotes` and `python -m readwrench.footnotes` are equivalent.
`readwrench check` runs the read-only part of `scan` on every EPUB in a folder (see
[Check](#readwrench-check)).

## Commands

### `scan EPUB --out DIR`

Audits note references and writes `DIR/plan.json` (the review plan) and
`DIR/review.html` (offline review page).

| Option | Meaning |
| --- | --- |
| `--out DIR` | New review directory (required; must not exist). |
| `--all-links` | Also review ordinary internal XHTML links. |
| `--ref-xpath XPATH` | Extra reference selector (repeatable, up to 16); must select XHTML `a` elements. |
| `--note-xpath XPATH` | Extra note-container selector (repeatable, up to 16). |
| `--label-template TEXT` | Return link text with `{number}`, `{chapter}`, `{label}`; must include `{number}`. Default: `Return to reference {number} in {chapter}`. |
| `--label-lang TAG` | Language of the new link text, e.g. `zh-Hans`; otherwise the note's language is inherited. |

XPath uses lxml with the `x` (XHTML), `epub` and `opf` namespace prefixes:

```sh
readwrench footnotes scan my-book.epub --all-links --out legacy-review
readwrench footnotes scan my-book.epub \
  --ref-xpath '//x:a[@class="my-noteref"]' \
  --note-xpath '//x:div[@class="my-note"]' --out custom-review
readwrench footnotes scan my-book.epub --out chinese-review \
  --label-template '返回引用 {number}（{chapter}）' --label-lang zh-Hans
```

`--label-lang` applies only to newly inserted navigation links, never to existing
prose. The scan options travel with the decisions and are reapplied to the exact
source book when the candidate is built.

### `review PLAN --out FILE`

Walks the unconfirmed references in a terminal: `a` approve adding a precise return,
`k` keep, `s` skip (default), `q` save and quit. End of input or Ctrl-C also saves the
decisions made so far. Only `missing-return`, `ancestor-return` and `exact-return`
references can be approved for `a`. `review` needs no third-party dependency.

### `candidate EPUB DECISIONS --out DIR`

Re-audits the book, checks that the decisions belong to it (source hash, reference
keys, associations) and writes a new bundle:

* `candidate.epub`: the original book with only the approved navigation insertions.
* `changes.patch`: a human-readable UTF-8 unified XHTML diff. Byte offsets and hashes
  in `manifest.json` are authoritative for UTF-16/CRLF sources.
* `preview.html`: clickable reference → note → return demonstrations using escaped
  excerpts; a path preview, not an EPUB reading system.
* `manifest.json`: source/candidate hashes, every insertion, changed members,
  reviewed/unreviewed counts, an independent ZIP-payload preservation check, and
  separate format validation results.
* `source-epubcheck.json`, `candidate-epubcheck.json` and their logs (unless
  `--skip-epubcheck`), plus a copy of `decisions.json`.

| Option | Meaning |
| --- | --- |
| `--out DIR` | New candidate bundle directory (required; must not exist). |
| `--epubcheck-jar JAR` | EPUBCheck JAR (default: `EPUBCHECK_JAR`). Required unless `--skip-epubcheck`. |
| `--skip-epubcheck` | Build an explicitly unvalidated candidate; the manifest says `not_run`. |
| `--check-timeout 1..60` | Seconds per EPUBCheck run (default 45); Java heap is capped at 256 MiB. |

Exit status: **0** success, **2** invalid input or missing tool (message on stderr as
`readwrench footnotes: <message>`), **3** EPUBCheck reported a failure (the bundle is
kept for inspection), **130** cancelled. Nothing is approved by default and there is
deliberately no approve-all command.

## Review semantics

References are ordered by the package spine, then non-spine XHTML documents.
Navigation documents are excluded unless selected by XPath. Detection uses
`epub:type="noteref"`, `role="doc-noteref"`, known reference classes, superscript links
and links to recognised note containers. These are hints, not proof that a link is a
note, so each decision shows the actual forward destination and a note excerpt.

| Status | Meaning |
| --- | --- |
| `missing-return` | The note has no link back to this reference. |
| `ancestor-return` | A return exists but targets a surrounding element (e.g. the paragraph or chapter start). |
| `exact-return` | A return to this exact reference exists; it is kept, never duplicated. |
| `duplicate-target-id`, `ambiguous-document-note` | The forward link is ambiguous. |
| `missing-target-id`, `missing-target-file`, … | The forward link is broken. |
| `no-safe-note-container`, `self-reference`, `external-link`, … | No return can be added. |

A forward link to a whole XHTML file is supported: a file with one identified note
selects that note; a plain note file selects its body; several identified notes are
reported as ambiguous rather than guessed. Broken or ambiguous forward links must be
fixed in an editor (Sigil, calibre) and rescanned; the tool never guesses a destination.

Actions: `add_backlink` adds an exact return if absent (a new source ID only when
needed); `keep` records that you reviewed the path and chose no change (it does not
claim an exact return exists); `skip`, or an unchecked approval, leaves the reference
out. Several references to one note each receive their own descriptive return link.

## Preservation and limits

The patch engine only inserts source ID attributes and new return-navigation elements
at byte positions supplied by Expat; it never re-serialises existing XHTML. Original
member bytes stay in order; unchanged archive members are stream-copied. Before the
bundle is published, an independent check reads the candidate archive, verifies every
unchanged member and removes/verifies each declared insertion against the original
bytes. ZIP compression bytes and headers can differ. EPUB 3 returns use
`role="doc-backlink"`; EPUB 2 receives ordinary XHTML links. Return labels are new
visible content and may change pagination.

Supported: single-rendition EPUB 2 or EPUB 3, well-formed XHTML in UTF-8, ASCII or
UTF-16, stored/deflated ZIP members; the encoding is preserved. EPUBCheck reports
HTM_058 for UTF-16 EPUB 3 content both before and after the change; the tool keeps that
failure instead of silently re-encoding the book.

Rejected with an actionable error: encryption or font obfuscation, signatures, several
renditions, unsafe or duplicate ZIP paths, `xml:base` and HTML `<base>`, internal DTD
subsets, CFI targets and self-closing empty notes. Standard external XHTML DTDs and
their named entities are handled offline. Unlinked note markers are reported, not
turned into links. Bounds: 5,000 members, 128 MiB expanded ZIP, 32 MiB per member,
8 MiB per XML file, 24 MiB total XHTML, 50,000 XML nodes/attributes per file, 100,000 in
total, 256 nesting levels, 16 MiB per decision file. Large textbooks can exceed these.

An exact backlink is a structural fact, not a guarantee of usable screen-reader focus,
a correct association, a meaningful label or accessibility. EPUBCheck validates format
only. Use the [DAISY notes guidance](https://kb.daisy.org/publishing/docs/html/notes.html),
Ace/SMART as appropriate, and test the candidate in your target reader with the
intended assistive technology.

## readwrench check

`readwrench check` runs the audit of `scan` in memory on each EPUB it finds (needs the
`footnotes` extra) and writes nothing. Findings are grouped per book and rule:

| Rule | Severity | Meaning |
| --- | --- | --- |
| `footnotes/no-return-link` | warning | Note references without a return link. |
| `footnotes/imprecise-return-link` | info | Returns go to a surrounding element. |
| `footnotes/broken-note-link` | error | Forward link to a missing ID or file, or an invalid link. |
| `footnotes/ambiguous-note-link` | warning | Forward link to a duplicated ID or a file with several notes. |
| `footnotes/broken-return-link` | warning | A `doc-backlink` in the note points to a missing target. |
| `footnotes/duplicate-id` | warning | Empty or duplicated `id` attributes. |
| `footnotes/no-note-container`, `unsupported-note-link`, `self-reference` | info | No return can be added automatically. |
| `footnotes/unlinked-note-marker` | info | Superscript markers without a forward link. |
| `footnotes/not-inspected` | info | The book is outside what the tool can safely read (e.g. encrypted). |

Each finding suggests the `readwrench footnotes scan` command that lists every reference.

## Prior art

Survey made before the original tool was written:

* **Sigil FootnoteLinker** generates footnotes and backlinks from author-specified
  classes, renumbering IDs and labels. This tool covers a narrower gap: confirming
  *existing* reading paths individually, including many references to one note, without
  renumbering or rewriting prose. Its published test book (`FNLTest.epub`) is used as a
  test fixture.
* **calibre's editor** reports broken links and allows manual editing; it is a good
  place to fix what this tool reports, but does not walk each note and its returns as
  a confirmation task.
* **Sigil Duokan Footnote Generator** and **KOReader HTML Replacer** generate new notes
  and backlinks rather than auditing existing ones.
* **EbookLib** wrote the small test books; its read/write is not a byte-preserving
  round trip, so it is not used to rewrite your book.
* **EPUBCheck** is reused as the external format validator; **Ace by DAISY** and
  **Thorium Reader** remain the places for accessibility evaluation and real reading
  tests. lxml, Expat, `zipfile` and `difflib` are reused instead of new parsers.
