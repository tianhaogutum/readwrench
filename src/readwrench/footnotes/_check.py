"""``readwrench check`` adapter: the read-only part of ``readwrench footnotes scan``.

Audits the note references of each EPUB in memory (no plan, HTML or candidate is
written) and reports note references without a return link, imprecise returns,
broken or ambiguous note links, broken return links, duplicate IDs and unlinked
note markers. Findings are grouped per book and rule, so a book with hundreds of
notes produces a handful of lines; ``readwrench footnotes scan`` lists every
reference for review.
"""
from __future__ import annotations

import shlex
import zipfile

from readwrench.check.model import EPUB, ERROR, INFO, WARNING, Check, Finding, need, plural, short

CHECK = "footnotes"

# Reference status (from the audit) -> (rule, severity, message template).
STATUS_RULES = {
    "missing-return": ("no-return-link", WARNING,
                       "{n} without a return link from the note back to the reference"),
    "ancestor-return": ("imprecise-return-link", INFO,
                        "{n} whose return link goes to a surrounding element, not the reference itself"),
    "missing-target-id": ("broken-note-link", ERROR, "{n} pointing to an ID that does not exist"),
    "missing-target-file": ("broken-note-link", ERROR, "{n} pointing to a file that is not in the EPUB"),
    "missing-target-body": ("broken-note-link", ERROR, "{n} pointing to a document without a body"),
    "invalid-percent-escape": ("broken-note-link", ERROR, "{n} with an invalid percent escape in the link"),
    "invalid-link": ("broken-note-link", ERROR, "{n} with a link that cannot be decoded"),
    "unsafe-link-path": ("broken-note-link", ERROR, "{n} with an absolute or unsafe link path"),
    "link-escapes-archive": ("broken-note-link", ERROR, "{n} with a link that leaves the EPUB"),
    "duplicate-target-id": ("ambiguous-note-link", WARNING, "{n} pointing to an ID used more than once"),
    "ambiguous-document-note": ("ambiguous-note-link", WARNING,
                                "{n} pointing to a whole file that holds several notes"),
    "no-safe-note-container": ("no-note-container", INFO,
                               "{n} whose target is not inside a recognisable note container"),
    "target-not-xhtml": ("unsupported-note-link", INFO, "{n} pointing to a non-XHTML resource"),
    "target-not-xhtml-element": ("unsupported-note-link", INFO, "{n} pointing to a non-XHTML element"),
    "external-link": ("unsupported-note-link", INFO, "{n} pointing outside the book"),
    "query-in-link": ("unsupported-note-link", INFO, "{n} with a query string in the link"),
    "epub-cfi-unsupported": ("unsupported-note-link", INFO, "{n} using an EPUB CFI fragment"),
    "self-reference": ("self-reference", INFO, "{n} whose link points back into the reference itself"),
}
# Any other non-exact status the audit may report in future versions.
OTHER = ("unsupported-note-link", INFO, "{n} with status {status}")
EXAMPLES = 3


def _scan_hint(display: str) -> str:
    return f"readwrench footnotes scan {shlex.quote(display)} --out footnotes-review"


def _where(location: dict) -> str:
    path = location.get("path") or "?"
    if location.get("id"):
        return f"{path}#{location['id']}"
    return f"{path} line {location['line']}" if location.get("line") else path


def _examples(locations: list[str]) -> str:
    shown = ", ".join(locations[:EXAMPLES])
    more = len(locations) - EXAMPLES
    return shown + (f" and {more} more" if more > 0 else "")


def check_footnotes(source):
    need("lxml")
    from .audit import audit
    from .base import EpubError
    from .epub import Book

    hint = _scan_hint(source.display)
    try:
        with Book(source.path) as book:
            plan = audit(book).plan
    except (EpubError, OSError, zipfile.BadZipFile, UnicodeError) as exc:
        yield Finding(CHECK, "not-inspected", INFO,
                      f"footnote links not inspected: {short(exc, 200)}", file=source.display)
        return

    grouped: dict[tuple[str, str, str], list[str]] = {}
    broken_returns: list[str] = []
    for record in plan["references"]:
        status = record.get("status")
        where = _where(record.get("location") or {})
        if any(r.get("kind") == "broken-backlink" for r in record.get("returns") or ()):
            broken_returns.append(where)
        if status == "exact-return":
            continue
        rule, severity, template = STATUS_RULES.get(status, OTHER)
        grouped.setdefault((rule, severity, template.replace("{status}", str(status))), []).append(where)

    for (rule, severity, template), locations in grouped.items():
        count = plural(len(locations), "note reference")
        yield Finding(CHECK, rule, severity,
                      template.format(n=count) + f": {_examples(locations)}",
                      file=source.display, pointer=locations[0], hint=hint)
    if broken_returns:
        yield Finding(CHECK, "broken-return-link", WARNING,
                      f"{plural(len(broken_returns), 'note reference')} whose note has a return link "
                      f"(role doc-backlink) to a missing target: {_examples(broken_returns)}",
                      file=source.display, pointer=broken_returns[0], hint=hint)

    duplicates = [w for w in plan["warnings"] if w.get("code") == "invalid-or-duplicate-id"]
    if duplicates:
        places = [f"{w.get('path')}#{w.get('id')}" if w.get("id") else f"{w.get('path')} line {w.get('line')}"
                  for w in duplicates]
        yield Finding(CHECK, "duplicate-id", WARNING,
                      f"{plural(len(duplicates), 'element')} with an empty or duplicated id; note links "
                      f"to them are ambiguous: {_examples(places)}",
                      file=source.display, pointer=places[0], hint=hint)
    markers = [w for w in plan["warnings"] if w.get("code") == "unlinked-note-marker"]
    if markers:
        places = [f"{w.get('path')} line {w.get('line')}" for w in markers]
        yield Finding(CHECK, "unlinked-note-marker", INFO,
                      f"{plural(len(markers), 'superscript note marker')} without a link to the note: "
                      f"{_examples(places)}",
                      file=source.display, pointer=places[0],
                      hint="Create the forward links in an EPUB editor (for example Sigil FootnoteLinker), "
                           "then run " + hint)


CHECKS = (
    Check(
        name=CHECK,
        area=EPUB,
        summary="Footnote links: note references without a return link, broken or ambiguous note links",
        kinds=frozenset({"epub"}),
        run=check_footnotes,
        rules=("no-return-link", "imprecise-return-link", "broken-note-link", "ambiguous-note-link",
               "broken-return-link", "no-note-container", "unsupported-note-link", "self-reference",
               "duplicate-id", "unlinked-note-marker", "not-inspected"),
        requires=("lxml",),
        extra="footnotes",
        tool="footnotes",
        note="Only links with note semantics (epub:type noteref, doc-noteref, known classes, superscript "
             "links, links into note containers) are audited; plain internal links are not. A return link "
             "is a structural fact, not an accessibility verdict.",
    ),
)
