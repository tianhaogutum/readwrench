"""``readwrench check`` adapter for ``readwrench pdf-pages``: re-verify saved page maps.

A page map (``mapping.json`` from ``plan``, a draft saved from the review page, or an
approved map from ``confirm`` or the review page) is bound to the SHA-256 of the two
PDFs it describes. This check reads each map read-only, validates it exactly as
``readwrench pdf-pages`` does, reports whether it is still a draft or carries an
approval that no longer matches its rows, and re-hashes both source PDFs to report
ones that are missing or have changed since the map was made. It needs no PDF library,
never renders a page and never writes.
"""
from __future__ import annotations

from pathlib import Path
import re
import shlex

from readwrench.check.model import ERROR, INFO, PDF, WARNING, Check, Finding, Source, plural, short

NAME = "pdf-pages"
KIND = "pdf-pages-map"
# Same value as ``readwrench.pagebridge.mapping.SCHEMA``; repeated so discovery stays a
# pure header test.
SCHEMA_PATTERN = re.compile(rb'"schema"\s*:\s*"pdf-page-bridge/v1"')

RULES = ("invalid-map", "draft-map", "approval-mismatch", "source-missing", "source-changed")

# The schema id is the first key the tool writes; a map that puts it further in is
# still recognised as long as it falls inside this window of the head.
SNIFF_BYTES = 4096


def classify(path, head: bytes):
    text = head[:SNIFF_BYTES]
    if text.startswith(b"\xef\xbb\xbf"):
        text = text[3:]
    if not text.lstrip().startswith(b"{") or not SCHEMA_PATTERN.search(text):
        return None
    return KIND, "readwrench pdf-pages page map"


_HASHES: dict[tuple[str, int, int], str] = {}


def _hash(path: Path) -> str:
    """SHA-256 of ``path``; cached per (path, size, mtime) because several maps share PDFs."""
    from .mapping import sha256

    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _HASHES:
        _HASHES[key] = sha256(path)
    return _HASHES[key]


def _relocated(map_path: Path, name: str, digest: str) -> Path | None:
    """A file with the recorded name and hash beside the map or one folder up, if any."""
    for folder in (map_path.parent, map_path.parent.parent):
        candidate = folder / name
        try:
            if candidate.is_file() and _hash(candidate) == digest:
                return candidate
        except OSError:
            continue
    return None


def check_map(source: Source):
    from .mapping import BridgeError, read_plan, validate_map

    file = source.display
    quoted = shlex.quote(file)
    replan = "readwrench pdf-pages plan ORIGINAL.pdf REVISED.pdf --out NEW_REVIEW"
    confirm = f"readwrench pdf-pages confirm {quoted} --reviewer NAME --out approved.json"

    def finding(rule, severity, message, *, pointer=None, subject=None, hint=None):
        return Finding(NAME, rule, severity, message, file=file, pointer=pointer,
                       subject=subject, hint=hint)

    try:
        data = read_plan(source.path)
    except BridgeError as exc:
        yield finding("invalid-map", ERROR,
                      f"readwrench pdf-pages would refuse this page map: {short(exc, 200)}",
                      hint=f"fix the JSON or make a new map: {replan}")
        return

    review = data.get("review")
    approved = False
    if isinstance(review, dict) and "approved_map" in review:
        try:
            validate_map(data, approved=True)
            approved = True
        except BridgeError as exc:
            yield finding("approval-mismatch", ERROR,
                          f"the recorded approval no longer holds: {short(exc, 200)}; "
                          "export refuses this map",
                          pointer="/review", hint=f"review the map again, then {confirm}")
    else:
        rows = data["rows"]
        pending = sum(1 for row in rows if row.get("confirmed") is not True)
        yield finding("draft-map", INFO,
                      f"draft page map, not approved yet ({pending} of "
                      f"{plural(len(rows), 'row')} not marked reviewed); export needs a "
                      "named approval",
                      hint=f"open the review.html beside it, or after checking every pair: "
                           f"{confirm}")

    for side in ("original", "modified"):
        identity = data["sources"][side]
        recorded = Path(identity["path"])
        pointer = f"/sources/{side}"
        try:
            present = recorded.is_file()
            digest = _hash(recorded) if present else None
        except OSError as exc:
            present, digest = False, None
            reason = short(exc, 120)
        else:
            reason = "no longer a file there"
        if not present:
            found = _relocated(source.path, identity["name"], identity["sha256"])
            if found is not None and approved:
                hint = (f"readwrench pdf-pages export {quoted} --{side} "
                        f"{shlex.quote(str(found))} --out comparison")
            elif approved:
                hint = (f"readwrench pdf-pages export {quoted} --{side} PATH --out comparison "
                        "(the file must still have the recorded SHA-256)")
            else:
                hint = replan
            where = f"; an identical copy is at {found}" if found is not None else ""
            yield finding("source-missing", WARNING,
                          f"{side} PDF {identity['name']!r} cannot be re-checked: "
                          f"{reason} ({recorded}){where}",
                          pointer=pointer, subject=identity["name"], hint=hint)
            continue
        if digest != identity["sha256"]:
            if approved:
                message = (f"{side} PDF {identity['name']!r} changed since it was approved "
                           "(SHA-256 differs); the approval is stale and export refuses it")
            else:
                message = (f"{side} PDF {identity['name']!r} changed since the map was "
                           "proposed (SHA-256 differs); confirm and export refuse it")
            yield finding("source-changed", ERROR if approved else WARNING, message,
                          pointer=pointer, subject=identity["name"],
                          hint=f"make and review a new map: {replan}")


CHECKS = (
    Check(
        name=NAME,
        area=PDF,
        summary="Saved PDF page maps: valid, approved, and still bound to the two PDFs "
                "they describe",
        kinds=frozenset({KIND}),
        run=check_map,
        rules=RULES,
        extra="pdf-pages",
        tool="pdf-pages",
        note="Source PDFs are compared by SHA-256 only; pages are not rendered and the "
             "proposed correspondence is not re-scored.",
    ),
)
