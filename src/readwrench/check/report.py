"""Text, Markdown, JSON and SARIF 2.1.0 renderings of one check run."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import PurePath, PurePosixPath
from urllib.parse import quote

from readwrench import __version__

from .model import AREAS, ERROR, INFO, INPUTS, WARNING, Check, Finding, Source, at_or_above, counts, plural
from .registry import BY_NAME

FORMAT = "readwrench.check/v1"
REPO_URL = "https://github.com/tianhaogutum/readwrench"
DOCS_URL = f"{REPO_URL}/blob/main/docs/check.md"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_LEVEL = {ERROR: "error", WARNING: "warning", INFO: "note"}
LABEL = {ERROR: "error", WARNING: "warn", INFO: "info"}
# GitHub rejects comments over 65,536 characters; keep well below it.
MARKDOWN_ROWS_PER_AREA = 40
MARKDOWN_CELL = 300


@dataclass
class Run:
    """Everything a renderer needs about one invocation."""

    paths: list[str]
    sources: list[Source]
    scanned: int
    checks: list[Check]
    findings: list[Finding]
    fail_on: str | None  # "error", "warning" or None (never)

    @property
    def failed(self) -> bool:
        return at_or_above(self.findings, self.fail_on)


def summary_line(run: Run) -> str:
    total = counts(run.findings)
    return " · ".join([plural(len(run.sources), "input"), plural(total[ERROR], "error"),
                       plural(total[WARNING], "warning"), f"{total[INFO]} info"])


def _area(finding: Finding) -> str:
    check = BY_NAME.get(finding.check)
    return check.area if check else INPUTS


def _by_area(findings) -> dict[str, list[Finding]]:
    groups: dict[str, list[Finding]] = {}
    for finding in findings:
        groups.setdefault(_area(finding), []).append(finding)
    return {area: groups[area] for area in AREAS if area in groups}


def _mix(run: Run) -> str:
    labels: dict[str, str] = {}
    kinds = Counter()
    for source in run.sources:
        kinds[source.kind] += 1
        labels.setdefault(source.kind, source.label)

    def label(kind: str) -> str:
        name = labels[kind]
        if kinds[kind] == 1:
            return name
        head, paren, tail = name.partition(" (")
        return f"{head}s{paren}{tail}" if not head.endswith("s") else name

    return ", ".join(f"{kinds[kind]} {label(kind)}" for kind in sorted(kinds, key=lambda k: (-kinds[k], k)))


# --------------------------------------------------------------------------- text

def _safe(value) -> str:
    """Untrusted labels must not move the cursor or recolour the terminal."""
    return "".join(f"\\x{ord(c):02x}" if ord(c) < 32 or ord(c) == 127 else c for c in str(value))


class _Style:
    def __init__(self, color: bool):
        self.color = color

    def __call__(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text


SEVERITY_COLOR = {ERROR: "1;31", WARNING: "33", INFO: "36"}


def render_text(run: Run, *, color: bool = False, verbose: bool = False) -> str:
    style = _Style(color)
    mix = _mix(run)
    lines = [style("readwrench check", "1") + f"  {', '.join(_safe(p) for p in run.paths)}  "
             + style(f"({plural(len(run.sources), 'input')}" + (f": {mix}" if mix else "")
                     + f"; {plural(run.scanned, 'file')} inspected)", "2")]
    if not run.sources and not run.findings:
        lines += ["", "Nothing to check here: no EPUB, PDF, font, saved web page or readwrench review file found.",
                  style("Point it at a folder of books, papers or captures, e.g. `readwrench check ~/Reading`.", "2")]
    labels = {source.display: source.label for source in run.sources}
    findings = run.findings
    width = min(max((len(f.rule_id) for f in findings), default=0), 34)
    for area, group in _by_area(findings).items():
        lines += ["", style(area, "1;4")]
        files: dict[str | None, list[Finding]] = {}
        for finding in group:
            files.setdefault(finding.file, []).append(finding)
        for file in sorted(files, key=lambda name: (name is None, name or "")):
            label = labels.get(file) if file is not None else None
            lines.append("  " + (style(_safe(file), "1") if file is not None else style("(whole run)", "1"))
                         + (style(f"  {label}", "2") if label else ""))
            for finding in files[file]:
                severity = style(LABEL[finding.severity].ljust(5), SEVERITY_COLOR[finding.severity])
                subject = f"{_safe(finding.subject)}: " if finding.subject else ""
                where = (f"line {finding.line}" if finding.line is not None
                         else finding.pointer if verbose and finding.pointer else "")
                lines.append(f"    {severity}  {style(finding.rule_id.ljust(width), '2')}  {subject}"
                             f"{_safe(finding.message)}" + (style(f"  {_safe(where)}", "2") if where else ""))
                if verbose and finding.hint:
                    lines.append(" " * (13 + width) + style(f"-> {_safe(finding.hint)}", "2"))
    if verbose:
        mentioned = {finding.file for finding in run.findings}
        quiet = [source for source in run.sources if source.display not in mentioned]
        if quiet:
            lines += ["", style(f"{plural(len(quiet), 'input')} without findings of their own:", "2")]
            lines += [style(f"    {_safe(source.display)}  {source.label}", "2") for source in quiet]
    lines += ["", style(summary_line(run), "1")]
    if run.findings and not verbose:
        lines.append(style("-v adds a fix hint under each finding; --format markdown writes a shareable summary.",
                           "2"))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- Markdown

def _cell(value) -> str:
    text = " ".join(str(value).split())
    if len(text) > MARKDOWN_CELL:
        text = text[:MARKDOWN_CELL - 1] + "…"
    # Escape what would end the cell, start HTML or turn into a mention or link.
    for char in ("\\", "|", "<", ">", "`", "*", "_", "[", "]"):
        text = text.replace(char, "\\" + char)
    return text.replace("@", "@​")


def _code(value) -> str:
    text = " ".join(str(value).split()).replace("`", "'").replace("|", "\\|")
    return f"`{text}`"


MD_SEVERITY = {ERROR: "**error**", WARNING: "warning", INFO: "info"}


def _md_table(findings: list[Finding]) -> list[str]:
    rows = ["| Severity | Rule | Input | Finding |", "| --- | --- | --- | --- |"]
    for finding in findings[:MARKDOWN_ROWS_PER_AREA]:
        where = finding.file or "(whole run)"
        if finding.line is not None:
            where += f":{finding.line}"
        text = (f"{finding.subject}: " if finding.subject else "") + finding.message
        rows.append(f"| {MD_SEVERITY[finding.severity]} | {_code(finding.rule_id)} | {_code(where)} | {_cell(text)} |")
    if len(findings) > MARKDOWN_ROWS_PER_AREA:
        rows.append(f"| | | | … and {len(findings) - MARKDOWN_ROWS_PER_AREA} more (see `--format json`) |")
    return rows + [""]


def render_markdown(run: Run) -> str:
    total = counts(run.findings)
    verdict = ("**Failed**" if run.failed else "**Passed**") + f" (fail on: {run.fail_on or 'never'})"
    lines = ["## readwrench check", "",
             f"{verdict} · {total[ERROR]} errors · {total[WARNING]} warnings · {total[INFO]} info · "
             f"{plural(len(run.sources), 'input')} checked", ""]
    if run.sources:
        lines += [f"Inputs: {_cell(_mix(run))}", ""]
    else:
        lines += ["No reading material found.", ""]
    for area, group in _by_area(run.findings).items():
        important = [f for f in group if f.severity != INFO]
        notes = [f for f in group if f.severity == INFO]
        area_counts = counts(group)
        lines += [f"### {area}", "",
                  f"{area_counts[ERROR]} errors · {area_counts[WARNING]} warnings · {area_counts[INFO]} info", ""]
        if important:
            lines += _md_table(important)
        if notes:
            lines += ["<details><summary>" + plural(len(notes), "info note") + "</summary>", ""]
            lines += _md_table(notes)
            lines += ["</details>", ""]
    lines += [f"<sub>readwrench {__version__} · read-only, offline · [what each rule means]({DOCS_URL})</sub>", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- JSON

def render_json(run: Run) -> str:
    per_file = Counter(finding.file for finding in run.findings)
    document = {
        "format": FORMAT,
        "tool": {"name": "readwrench", "version": __version__},
        "paths": run.paths,
        "checks": [check.name for check in run.checks],
        "inputs": [{"path": source.display, "kind": source.kind, "label": source.label,
                    "findings": per_file[source.display]} for source in run.sources],
        "findings": [{**finding.to_dict(), "area": _area(finding)} for finding in run.findings],
        "summary": {"inputs": len(run.sources), "inspected": run.scanned, **counts(run.findings)},
        "failOn": run.fail_on or "never",
        "failed": run.failed,
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- SARIF

def _uri(path: str) -> dict:
    pure = PurePath(path)
    if pure.is_absolute():
        return {"uri": "file://" + quote(pure.as_posix(), safe="/")}
    return {"uri": quote(PurePosixPath(*pure.parts).as_posix(), safe="/"), "uriBaseId": "%SRCROOT%"}


def _rule(rule_id: str, finding: Finding) -> dict:
    check = BY_NAME.get(finding.check)
    rule = {"id": rule_id, "name": rule_id.replace("/", "-"),
            "shortDescription": {"text": check.summary if check else rule_id},
            "defaultConfiguration": {"level": SARIF_LEVEL[finding.severity]},
            "helpUri": DOCS_URL,
            "properties": {"check": finding.check, "area": _area(finding)}}
    return rule


def _result(finding: Finding, rule_index: int) -> dict:
    text = (f"{finding.subject}: " if finding.subject else "") + finding.message
    region = {"startLine": finding.line} if finding.line else None
    physical: dict = {"artifactLocation": _uri(finding.file)}
    if region:
        physical["region"] = region
    location: dict = {"physicalLocation": physical}
    if finding.pointer:
        location["logicalLocations"] = [{"fullyQualifiedName": finding.pointer, "kind": "member"}]
    result = {"ruleId": finding.rule_id, "ruleIndex": rule_index, "level": SARIF_LEVEL[finding.severity],
              "message": {"text": text}, "locations": [location],
              "properties": {"check": finding.check, "severity": finding.severity}}
    if finding.hint:
        result["properties"]["hint"] = finding.hint
    return result


def render_sarif(run: Run) -> str:
    rules: list[dict] = []
    index: dict[str, int] = {}
    results, notifications = [], []
    for finding in run.findings:
        if finding.file is None:
            notifications.append({"level": SARIF_LEVEL[finding.severity],
                                  "message": {"text": f"{finding.rule_id}: " + finding.message}})
            continue
        if finding.rule_id not in index:
            index[finding.rule_id] = len(rules)
            rules.append(_rule(finding.rule_id, finding))
        results.append(_result(finding, index[finding.rule_id]))
    crashed = any(finding.rule == "internal" for finding in run.findings)
    document = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "readwrench", "version": __version__, "semanticVersion": __version__,
                                "informationUri": REPO_URL, "rules": rules}},
            "results": results,
            "invocations": [{"executionSuccessful": not crashed, "toolExecutionNotifications": notifications}],
            "columnKind": "unicodeCodePoints",
        }],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


RENDERERS = {"json": render_json, "sarif": render_sarif, "markdown": render_markdown}
