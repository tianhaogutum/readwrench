# readwrench clues

You remember two phrases from a book or paper, maybe from different pages, but not
which file they were in. `readwrench clues` finds the materials that contain **every**
remembered phrase, and returns one original excerpt per clue with its page, paragraph
or line and the distance between them. Use it on text you have already extracted
(`pdftotext` output, your own JSONL fragments, or ripgrep-all JSON); it runs offline,
without model calls, accounts or background processes.

Formerly the standalone `cluejoin` tool; see [Compatibility with cluejoin](#compatibility-with-cluejoin).

**Reads / writes:** `build` and `capture` read only the inputs you name and write one
new SQLite index file (an existing index is replaced only with `--replace`, atomically).
`search` opens the index read-only and writes nothing. `capture` runs the external `rga`
in a temporary directory beside the index and removes it afterwards.

**Platforms:** Python 3.11+, standard library only. Python's `sqlite3` must provide FTS5
with the trigram tokenizer (SQLite 3.34 or newer, as in most current Python builds). `capture` additionally needs POSIX process groups
(macOS/Linux) and the `rga` binary.

## Quick start

```sh
pip install readwrench
readwrench clues --help
```

From a clone of the repository, run it on the bundled synthetic examples (invented
materials; no original PDFs are included for them):

```sh
readwrench clues build demo.sqlite examples/clues/fragments.jsonl --format jsonl
readwrench clues search demo.sqlite --query examples/clues/query.json --output text
readwrench clues search demo.sqlite -g 记忆 -g 原文 --output text
```

The English query returns `catalogue` and `field-notes`, each with a one-page closest
cover; the two decoy files that contain only one clue each are excluded. The Chinese
query returns `中文笔记`, with evidence on physical pages 3 and 9 (difference 6).

Use repeated `-g` for two or three AND groups. Several quoted literals after one `-g`
are OR alternatives. Put the index before the groups:

```sh
readwrench clues search demo.sqlite -g 'amber lantern' 'bronze lantern' -g 'silver compass'
```

`python -m readwrench clues` and `python -m readwrench.cluejoin` are equivalent.

## Commands

```text
readwrench clues build INDEX INPUT... [--format {text,jsonl,rga-json}] [--replace]
readwrench clues capture INDEX INPUT... [--rga RGA] [--timeout SECONDS]
                         [--max-output-mib N] [--replace]
readwrench clues search INDEX (-g LITERAL... [-g LITERAL...] | --query FILE)
                        [--limit N] [--context CHARS] [--output {json,text}]
```

- `build` indexes already-extracted text. `--format` (default `text`) applies to all
  inputs. `build` and `capture` print a one-line JSON receipt
  (`index`, `documents`, `fragments`, `bytes`; `capture` adds `adapter`,
  `upstream_exit_code`, `captured_bytes` and bounded `upstream_stderr`).
- `capture` extracts explicit PDF/plain-text files with ripgrep-all, then builds the
  index (see [Checked capture with rga](#checked-capture-with-rga-optional)).
  `--timeout` defaults to 30 seconds (at most 300), `--max-output-mib` to 128 (1–512).
- `search` takes two or three clue groups, either as repeated `-g` or as `--query`, a
  UTF-8 JSON file holding an array of clue arrays such as
  `[["amber lantern", "bronze lantern"], ["silver compass"]]` (also the way to pass
  clues that begin with `-`). `--limit` (default 20, 1–1000) caps the returned
  materials; `--context` (default 100, 0–10000) sets the characters shown before and
  after each match.

JSON is the default search output. It includes verbatim contexts, exact original
character ranges, page/paragraph/line locations when available, fragment IDs, and
input-record provenance (`origin`: input, record range and coordinate kind). Text output
highlights evidence with `⟦…⟧`. `line` is the line where the match starts,
`fragment_line` the first line of the stored paragraph, and `line_end` the last line of
a wrapped quote. For rga input, lines refer to rga's extracted stream. Terminal control
and bidirectional-override characters from documents are escaped in text output and
`\u`-escaped (losslessly) in JSON.

Exit status: **0** matches, **1** no matching material, **2** invalid input or I/O
failure, **130** interrupted, **141** downstream pipe closed. Errors go to stderr as
`readwrench clues: <message>`.

## Input adapters

### Extracted text (`--format text`)

```sh
pdftotext materials/report.pdf report.txt
readwrench clues build report.sqlite report.txt
readwrench clues search report.sqlite -g 'first phrase' -g 'other phrase'
```

Blank lines separate paragraphs. A form feed (`\f`, Poppler's default page separator)
advances the physical page, including empty pages. Without form feeds only global
paragraph and line coordinates are reported. Keep page breaks: `pdftotext -nopgbrk`
discards them. No OCR or binary parsing is done here. For this adapter the source and
material ID are the absolute path of the extracted-text file; use JSONL to point back
to the original PDF instead. LF, CRLF and CR line separators are recognised.

### JSONL fragments with original IDs (`--format jsonl`)

One UTF-8 JSON object per line:

```json
{"document_id":"book-42","source":"/books/book-42.pdf","fragment_id":"page-7","page":7,"text":"The original extracted words."}
```

Required: `document_id`, `text`, and `page` or `paragraph`. Optional: `source`
(defaults to the ID), `fragment_id` (defaults to input path plus record number) and
`line` (global within the material, not reset per page). A paragraph number is
**global within the material**, not reset on each page. All records of one ID must
agree on `source` and share a complete coordinate kind. Repeated fragment IDs, unknown
fields (misspelled locations), duplicate JSON keys and `NaN`/`Infinity` are rejected. A
paged record cannot contain form feeds: split it into separately located records.
Records may arrive out of order and from several files; the same `document_id` joins
them. Original text is kept unchanged.

### ripgrep-all JSON (`--format rga-json`)

```sh
rga --json -n -i -e 'first phrase' -e 'second phrase' materials/ > hits.jsonl
readwrench clues build hits.sqlite hits.jsonl --format rga-json
readwrench clues search hits.sqlite -g 'first phrase' -g 'second phrase'
```

The adapter accepts ripgrep `match` and `context` events, text or UTF-8 base64 fields,
and rga's `Page N: ` prefixes for PDFs; begin/end/summary events are ignored. Use `-n`
for plain-text inputs. Archives are rejected: rga's textual member prefixes do not give
unambiguous material IDs, so extract members upstream and use JSONL instead.

An imported rga stream contains only the lines and context your upstream search
emitted; `readwrench clues` cannot recover omitted evidence. PDF page spans stay exact;
no paragraph span is invented for sparse PDF lines. For plain text the original line is
the location. Consecutive numbered rga lines on one physical page are joined
(preserving LF/CRLF), so phrases can cross line wraps; gaps, page changes and
unnumbered events prevent joining, and a phrase never crosses separately stored
fragments. rga line numbers count LF records; a bare CR stays inside its record. Check
rga's exit status before importing: the output can be incomplete when one of its
extractors fails.

**rga 0.10.10 note:** `--rga-no-cache` fails there with "No cache?"; use
`--rga-cache-path=./rga-cache` instead. Its config/cache options need `--option=VALUE`,
not a space-separated value. The cache bug is fixed upstream in
[rga PR #338](https://github.com/phiresky/ripgrep-all/pull/338).

### Checked capture with rga (optional)

With `rga`, `rg` and Poppler's `pdftotext` installed, `capture` extracts explicit
PDF/plain-text files (`.pdf`, `.txt`, `.md`, `.log`, `.csv`, `.tsv`, any case) and keeps
the original source paths. From a clone, using the RFC 2616 test fixture:

```sh
readwrench clues capture rfc.sqlite tests/cluejoin/fixtures/rfc2616.pdf --timeout 30
readwrench clues search rfc.sqlite -g 'use affects' -g 'non-idempotent sequences' --output text
```

This finds a phrase wrapped across two lines on physical page 10 and the other clue on
page 30. `capture` asks rga for every line (rather than filtering, which would lose
casefold and line-wrap evidence), uses one worker, an empty config and an isolated
temporary cache, bounds stdout (`--max-output-mib`) and stderr, checks the exit status,
and kills the whole extraction process group on timeout or interruption. Temporary
logs and caches are removed; evidence provenance then names the original source and
its **rga-extracted line** range. `--rga /path/to/rga` selects an executable.
`--replace` keeps the old index if extraction or parsing fails. Windows users can
import an already-checked rga JSON file with `build --format rga-json` instead. Archive
and Office formats are out of scope.

## Matching and distances

- Case-insensitive **literal substring** matching with Unicode casefolding. Whitespace
  runs, including line wraps, match a single space. Regex metacharacters and FTS query
  operators are literal. There is no stemming, typo tolerance, semantic search, accent
  folding or Unicode compatibility normalization. A match never starts or ends halfway
  through a casefold expansion (`ß` → `ss`).
- Every group must match the **same document ID**; groups may share one occurrence.
  One matching alternative per group is returned.
- A sweep selects the closest cover in the document's complete coordinate: physical
  page first, otherwise global paragraph, otherwise original line. Difference 0 means
  the same coordinate, 1 adjacent coordinates; it is a coordinate difference, not a
  count of pages or words in between. A line cover spans each quote from its first to
  its last line.
- Within one unit, ties use fragment order and then character extent. Materials with
  different units are listed in page/paragraph/line order; their distances are **not
  numerically comparable**. `null` means no distance can be established from the
  stored records.
- SQLite FTS5 (trigram tokenizer) supplies candidates; exact verification supplies every
  displayed passage. One- and two-character clues use SQLite `instr`, because trigram
  matching cannot retrieve them; short, common clues can therefore scan the index.

### How the closest cover is chosen

Occurrences are swept by end position. For a fixed right edge, keeping each group's
latest start maximises the left edge of a covering window, and every cover's right
edge is an occurrence end, so this finds a minimum cover without enumerating every
combination of hits; only two or three current hits and the best cover stay in memory.
The ordering key is coordinate span, stored-fragment span, text extent, then earliest
position. Offsets are Python character indices into the stored extraction text, not
UTF-8 bytes. Document IDs and locations come from the input adapter; the tool does not
authenticate an original PDF or repair OCR.

## Index lifecycle and limits

An index is a disposable snapshot: rebuild it when the source material changes
(`readwrench check` reports indexes whose inputs changed; see below). `build` refuses an
existing output unless `--replace` is given, never creates missing directories, and
never writes through a symlink or over an input. It builds a sibling temporary
database, validates every record, checks SQLite integrity and re-checks each input's
identity, size and timestamps before linking or replacing the destination; any failure
leaves the previous index intact.

The index records its schema version, normalizer and the Python Unicode database
version. A Python with a different Unicode version must rebuild the index, because new
casefold mappings would otherwise silently miss old spellings. There are no migrations,
watchers or network calls.

Limits: 1,000,000 characters per paragraph, fragment or contiguous rga block; 8,000,000
characters per JSONL line; 32 alternatives per group; 1,024 characters per clue; query
files up to 1,250,000 bytes. Inputs must be regular files and are streamed. Overlapping
sparse plain-text line captures are rejected; deduplicate them upstream.

## In `readwrench check`

`readwrench check` recognises clue indexes by their SQLite header and application ID.
For each one it reports, without writing anything:

| Rule | Severity | Meaning |
| --- | --- | --- |
| `clues/corrupt-index` | error | the file cannot be read or fails SQLite's integrity check |
| `clues/unsupported-index` | warning | schema version or text normalization this readwrench does not support |
| `clues/unicode-mismatch` | warning | built under another Unicode version; search refuses it |
| `clues/source-missing` | warning | an input it was built from is no longer a file at its recorded path |
| `clues/stale-index` | warning | an extracted-text, JSONL or rga JSON input now yields different fragments |

Each finding carries the `readwrench clues build ... --replace` (or `capture`) command
that rebuilds the index. Inputs captured with `capture` are checked for presence only;
they are not re-extracted.

## Prior art and scope

Finding "a document containing A and B" is already well covered; `readwrench clues` is a
narrow evidence adapter on top of existing extraction, not a new extractor, fuzzy search
engine or desktop indexer. What it adds: clues grouped under an original material ID
across separately extracted fragments, and a small, inspectable evidence bundle with
coordinates and distances.

- [ripgrep-all](https://github.com/phiresky/ripgrep-all) extracts many formats, prefixes
  PDF lines with page numbers and emits ripgrep JSON; it is the recommended extractor
  and the engine behind `capture`.
- [SQLite FTS5](https://www.sqlite.org/fts5.html) provides Boolean AND/OR, NEAR and a
  trigram substring index at row level; it is the retrieval engine here.
- [Docfd](https://github.com/darrenldl/docfd) offers multiline fuzzy search with PDF page
  context and content AND filters in a TUI; its documentation explains why it has no `&`
  operator for independently displayed matches.
- [ugrep](https://ugrep.com/) (`--bool --files`), `git grep --all-match` and
  [Recoll](https://recoll.org/) also select files matching every expression; they do not
  join separately extracted fragments under one material ID.

Use those tools for extraction, OCR, typo-tolerant search and interactive viewing.

## Compatibility with cluejoin

- The command is now `readwrench clues build|capture|search`; options, defaults, output
  JSON (`schema_version` 1) and exit codes are unchanged.
- Index files are unchanged (SQLite application ID `CLUE`, schema version 3, normalizer
  `unicode-casefold-whitespace-v1`): indexes built by `cluejoin` 0.1.0 open as they are.
- Error messages are prefixed `readwrench clues:` instead of `cluejoin:`.
