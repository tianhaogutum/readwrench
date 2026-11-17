"""``readwrench check`` adapter: re-verify saved ``readwrench subset-proof`` reports.

A proof run writes ``report.json`` with the SHA-256 of the original font, the manuscript
and the candidate subset. This check reads such a report (standard library only, nothing
is shaped again) and reports

* a proof whose verdict was a rejection (``accepted`` is false), and
* a subset, original font or manuscript whose bytes no longer match the proven ones, so
  the saved proof does not apply to them any more.

Build mode stores the subset next to the report (``subset.ttf`` and friends). Other paths
are stored as given on the command line, often relative to a working directory that is
not recorded; they are looked up relative to the report's folder and then each parent
folder, and a file that cannot be found is reported as information only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex

from readwrench.check.model import EPUB, ERROR, INFO, WARNING, Check, Finding, short

CHECK = "subset-proof"
KIND = "subset-proof-report"
SCHEMA_VERSION = 1
# Bound the work for the files a report names (fonts are at most 50 MiB, manuscripts 4 MiB).
MAX_HASH_BYTES = 64 * 1024 * 1024
# The report is a JSON object written by json.dump; "tool" is its second key.
_TOOL = re.compile(rb'"tool"\s*:\s*"subset-proof"')

ISSUES = {
    "original_missing_unicode": "the original font lacks characters the manuscript uses",
    "candidate_missing_unicode": "the subset lacks characters the manuscript uses",
    "shaping_difference": "the subset shapes the manuscript differently from the original",
}
RULES = ("unreadable-report", "malformed-report", "unsupported-schema", "rejected",
         "subset-changed", "input-changed", "input-not-found")


def classify(path: Path, head: bytes):
    """Recognise a ``report.json`` written by ``readwrench subset-proof`` (or the former tool)."""
    try:
        start = head[:2048].lstrip()
        if not start.startswith(b"{") or b'"schema_version"' not in start or not _TOOL.search(start):
            return None
        return KIND, "subset-proof report"
    except Exception:  # classify must never raise
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _locate(recorded: str, report_dir: Path, *, beside: bool) -> Path | None:
    if beside:
        candidates = [report_dir / Path(recorded).name]
    elif Path(recorded).is_absolute():
        candidates = [Path(recorded)]
    else:
        candidates = [folder / recorded for folder in (report_dir, *report_dir.parents)]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _entry(report: dict, key: str) -> dict | None:
    value = report.get(key)
    if value is None:
        return None
    if (not isinstance(value, dict) or not isinstance(value.get("path"), str)
            or not isinstance(value.get("sha256"), str)):
        raise ValueError(f"{key} must record a path and a sha256")
    return value


def _rerun_hint(report: dict) -> str:
    original, text, candidate = report["original"], report["text"], report.get("candidate")
    compare = report.get("mode") == "compare" and candidate is not None
    words = ["readwrench", "subset-proof", "compare" if compare else "build", original["path"]]
    if compare:
        words.append(candidate["path"])
    words += [text["path"], "--out", "NEW_DIR"]
    if text.get("format") == "run_manifest":
        words.append("--manifest")
    if isinstance(original.get("face_index"), int):
        words += ["--font-index", str(original["face_index"])]
    if compare and isinstance(candidate.get("face_index"), int):
        words += ["--candidate-index", str(candidate["face_index"])]
    return ("re-run the proof with the same options (--features, --at, --keep-features...): "
            + " ".join(shlex.quote(word) for word in words))


def check_report(source):
    display = source.display

    def finding(rule, severity, message, **extra):
        return Finding(CHECK, rule, severity, short(message), file=display, **extra)

    try:
        report = json.loads(source.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        yield finding("unreadable-report", ERROR, f"cannot read the subset-proof report: {exc}")
        return
    if not isinstance(report, dict):
        yield finding("malformed-report", ERROR, "subset-proof report is not a JSON object")
        return
    if report.get("schema_version") != SCHEMA_VERSION:
        yield finding("unsupported-schema", INFO,
                      f"subset-proof report schema_version {report.get('schema_version')!r} is not "
                      f"{SCHEMA_VERSION}; not re-verified", pointer="/schema_version")
        return
    try:
        original = _entry(report, "original")
        text = _entry(report, "text")
        candidate = _entry(report, "candidate")
        if original is None or text is None:
            raise ValueError("original and text are required")
        accepted = report.get("accepted")
        issues = report.get("issues")
        if not isinstance(accepted, bool) or not isinstance(issues, list):
            raise ValueError("accepted must be a boolean and issues a list")
    except ValueError as exc:
        yield finding("malformed-report", ERROR, f"subset-proof report is incomplete: {exc}")
        return

    hint = _rerun_hint(report)
    if not accepted:
        reasons = "; ".join(ISSUES.get(str(issue), str(issue)) for issue in issues) or "no issue recorded"
        yield finding("rejected", ERROR, f"the saved proof rejected the subset: {reasons}",
                      pointer="/accepted", subject=Path(original["path"]).name,
                      hint=f"open {Path(display).parent / 'specimen.html'} for the located differences; "
                           "then " + hint)

    report_dir = source.path.parent
    build = report.get("mode") == "build"
    sides = [("original", original, "original font", False), ("text", text, "manuscript", False)]
    if candidate is not None:
        sides.append(("candidate", candidate, "subset", build))
    for key, entry, what, beside in sides:
        recorded = entry["path"]
        located = _locate(recorded, report_dir, beside=beside)
        if located is None:
            where = "next to the report" if beside else "relative to the report's folders"
            yield finding("input-not-found", INFO,
                          f"{what} {recorded} named by the proof was not found {where}; not re-verified",
                          pointer=f"/{key}/path", subject=Path(recorded).name)
            continue
        try:
            size = located.stat().st_size
            recorded_size = entry.get("bytes")
            if isinstance(recorded_size, int) and size != recorded_size:
                now = f"{size:,} bytes, proven {recorded_size:,}"
            elif size > MAX_HASH_BYTES:
                now = f"{size:,} bytes, larger than any font the proof accepts"
            else:
                digest = _sha256(located)
                if digest == entry["sha256"].lower():
                    continue
                now = f"sha256 {digest[:12]}…, proven {entry['sha256'][:12]}…"
        except OSError as exc:
            yield finding("input-not-found", INFO, f"cannot read {what} {recorded}: {exc}",
                          pointer=f"/{key}/path", subject=Path(recorded).name)
            continue
        shown = located.name if beside else recorded
        rule = "subset-changed" if key == "candidate" else "input-changed"
        yield finding(rule, WARNING,
                      f"{what} {shown} changed since the proof ({now}); the saved proof no longer applies",
                      pointer=f"/{key}/sha256", subject=located.name, hint=hint)


CHECKS = (
    Check(
        name=CHECK,
        area=EPUB,
        summary="Saved subset-proof reports: rejected subsets, and subsets, fonts or manuscripts "
                "changed since the proof",
        kinds=frozenset({KIND}),
        run=check_report,
        rules=RULES,
        requires=(),
        extra=None,
        tool="subset-proof",
        note="Compares file hashes with the saved report; it does not shape the text again "
             "(run 'readwrench subset-proof compare' for a new proof).",
    ),
)

__all__ = ["CHECKS", "KIND", "check_report", "classify"]
