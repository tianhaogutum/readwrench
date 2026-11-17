# readwrench check

A reading folder collects problems quietly: an EPUB whose footnotes have no way back, a PDF
that is only scanned images so no quotation can ever be found in it, a saved quote locator
whose book was replaced by another edition, an approved page map whose PDF has since been
revised, a font proof that no longer matches the subset you ship. `readwrench check` walks a
folder, recognises books, papers, fonts, saved web pages and the review files the other
readwrench commands save **by their content**, and runs every analysis that needs no decision
from you. It reports what is broken, stale or missing, and names the command that shows the
details or fixes it.

Use it before you start a reading or writing session on a folder, before you hand a folder to
someone else, or as a gate in a script (`--fail-on warning`).

**Reads / writes:** read-only. It never writes a file, never runs an external program, never
opens a network connection and never prompts. Hidden files and folders, symlinks,
`node_modules`, `__pycache__` and virtual environments are skipped.

**Install:** `pip install readwrench` runs the built-in `inputs` check and the `clues`, `lineage`,
`pdf-pages` and `subset-proof` checks with the standard library only. A check whose extra is not installed is
reported as one `info` finding with the install command (for example
`pip install 'readwrench[footnotes]'`); `pip install 'readwrench[all]'` enables every check.

## Quick start

```sh
pip install 'readwrench[all]'
readwrench check ~/Reading                     # text report of everything under the folder
readwrench check                               # the current folder
readwrench check ~/Reading --fail-on warning   # exit 1 on warnings too
readwrench check books/ --only footnotes,concordance-epub
readwrench check . --format markdown > reading-check.md
readwrench check . --format sarif > readwrench.sarif
readwrench check --list-checks
```

From a clone of the repository, run it on the bundled examples:

```sh
readwrench check examples -v
```

## What it recognises

| Kind | Recognised by |
| --- | --- |
| EPUB book | a zip whose `mimetype` member says `application/epub+zip` |
| PDF document | `%PDF-` in the first kilobyte |
| font file | the sfnt / OpenType / WOFF / WOFF2 / TrueType Collection signature |
| saved web page | HTML (`<!doctype html>`, `.html`), MHTML (`multipart/related`), WARC (`WARC/` record, also gzipped) |
| text or Markdown file | `.txt` / `.md` that decodes as UTF-8 or UTF-16 |
| `readwrench quote` review or locator | JSON with schema `quote-locator.review/v1` or `quote-locator.anchor/v1` |
| `readwrench clues` index | SQLite file with the clues application id |
| `readwrench concordance` workspace | its `manifest.json` |
| `readwrench pdf-pages` page map | JSON with schema `pdf-page-bridge/v1` |
| `readwrench reanchor` review bundle | its `proposal.json` |
| `readwrench subset-proof` report | its `report.json` |
| `readwrench lineage` manifest | `{"version": 1, "url": ..., "captures": [...]}` |

A file named like an EPUB, PDF, font, MHTML or WARC whose content is something else is
reported (`inputs/misnamed`): usually an interrupted download or a mislabelled export.

## Checks

| Check | Area | What it reports | Needs |
| --- | --- | --- | --- |
| `quote` | Find & compare | saved quote reviews and locators whose document is missing, changed (SHA-256) or now extracts different text; edited or inconsistent records | `[quote]` |
| `quote-text-layer` | Find & compare | PDFs, or page ranges, without extractable text (no OCR is run), where a quotation cannot be found | `[quote]` |
| `clues` | Find & compare | clue indexes that are corrupt, unusable by this Python's SQLite/Unicode, or stale against the inputs they were built from | – |
| `concordance-epub` | Find & compare | EPUBs that `concordance init` would refuse: encrypted prose, size limits, malformed archives, undecodable chapters | `[concordance]` |
| `concordance-workspace` | Find & compare | concordance workspaces with tampered snapshots, moved or swapped source editions, an inconsistent Taguette project, conflicting decisions, unreviewed candidates | `[concordance]` |
| `pdf-pages` | PDF | page maps that are invalid, still drafts, edited after approval, or whose PDFs moved or changed | `[pdf-pages]` (hash checks use the standard library) |
| `reanchor` | PDF | annotation review bundles whose proposal, PDFs or evidence images changed, and reviews still pending | `[reanchor]` |
| `footnotes` | EPUB & fonts | note references without a return link, broken or ambiguous note links, broken return links, duplicate ids, unlinked note markers | `[footnotes]` |
| `subset-proof` | EPUB & fonts | proofs that rejected their subset, and subsets, fonts or manuscripts changed since the proof | – |
| `lineage` | Web captures | lineage manifests whose captures are missing, duplicated, unreadable, of another URL, or over the limits | – |
| `inputs` | Inputs | files that cannot be read, are empty, or whose content does not match their name | – |

`readwrench check --list-checks` prints every rule id of every check.

### What it deliberately does not do

Some analyses always need a decision from you, so `check` does not guess them:
which quotation or clue phrases to look for (`quote find`, `clues search`), which concept to
compare across which books (`concordance init`), which two PDFs are versions of each other
(`pdf-pages plan`, `reanchor propose`), which manuscript a font must cover
(`subset-proof build`), and which loose captures belong to one page (`lineage init`). Once you
have run those commands, their saved files are re-verified by `check` on every run.

## Output

- `--format text` (default): findings grouped by area and file; `-v` adds a hint under each
  finding (the command to run) and lists inputs without findings. Colour follows `--color`
  and `NO_COLOR`.
- `--format json`: a `readwrench.check/v1` document with `inputs`, `findings` (`check`, `rule`,
  `ruleId`, `severity`, `message`, `file`, `line`, `pointer`, `subject`, `hint`, `area`),
  `summary` and `failed`.
- `--format markdown`: a summary for a pull request, an issue or a notes page.
- `--format sarif`: SARIF 2.1.0 (driver name `readwrench`); findings without a file become
  tool notifications.

Severities are `error`, `warning` and `info`. Messages never quote long passages of your
documents.

## Exit status

| Code | Meaning |
| --- | --- |
| 0 | no finding at or above `--fail-on` (default `error`; `never` always exits 0 after a successful run) |
| 1 | at least one finding at or above `--fail-on` |
| 2 | usage or input error: unknown option or check name, a path that does not exist |

A check that crashes is isolated: it becomes one `internal` error finding (the exception type
only, never its message) and every other check still runs. Set
`READWRENCH_CHECK_DEBUG=1` to get the traceback instead.

## Options

| Option | Meaning |
| --- | --- |
| `PATH ...` | folders or files to scan (default `.`); a file given explicitly is always classified |
| `--format {text,json,sarif,markdown}` | report format |
| `--fail-on {error,warning,never}` | lowest severity that makes the exit status 1 |
| `--only CHECK[,CHECK]` / `--skip CHECK[,CHECK]` | select checks (repeatable) |
| `--list-checks` | list the checks with their rules and exit |
| `--color {auto,always,never}` | colour of the text report |
| `-v`, `--verbose` | hints and quiet inputs in the text report |

Limits: at most 50,000 files and 12 folder levels per run (reported as
`inputs/scan-truncated`); pass sub-folders directly for larger trees.

## Library use

```python
from readwrench.check import CLASSIFIERS, discover, run_checks, select

discovery = discover(["~/Reading"], CLASSIFIERS)
for finding in run_checks(discovery, select(only=["footnotes"])):
    print(finding.rule_id, finding.file, finding.message)
```

## How a tool adds a check

Each tool contributes checks through `src/readwrench/<tool>/_check.py`: a `CHECKS` tuple of
`readwrench.check.model.Check` records and, optionally, `classify(path, head)`, which
recognises the tool's own saved files from their first 64 KiB. Adapters import only the
standard library at import time; heavy dependencies are imported inside the checker after
`need(...)`, so a missing extra becomes one `missing-dependency` info finding.
