"""Records shared by ``readwrench check``, its discovery step and every tool's check adapter.

A tool contributes checks through ``readwrench/<tool>/_check.py``, which exposes

* ``CHECKS``: a tuple of :class:`Check` records, and optionally
* ``classify(path, head) -> tuple[str, str] | None``: recognise one of the tool's own
  saved files (a review plan, a manifest, a locator file...) by content and return
  ``(kind, label)``; ``head`` is the first :data:`HEAD_BYTES` bytes of the file. It must
  never raise and never read more than it needs.

Adapters import only the standard library and this module at import time. Heavy optional
dependencies are imported inside the checker, after :func:`need` has confirmed they are
installed; a missing one becomes a single info finding with the install command.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
from pathlib import Path
from typing import Callable, Iterable

ERROR = "error"
WARNING = "warning"
INFO = "info"
# Most severe first; the index doubles as the sort key.
SEVERITIES = (ERROR, WARNING, INFO)
RANK = {severity: index for index, severity in enumerate(SEVERITIES)}

# How much of each file discovery reads to recognise it.
HEAD_BYTES = 64 * 1024

# Report sections (the same groups as the top-level command table).
FIND = "Find & compare"
PDF = "PDF"
EPUB = "EPUB & fonts"
WEB = "Web captures"
INPUTS = "Inputs"
AREAS = (FIND, PDF, EPUB, WEB, INPUTS)


@dataclass(frozen=True)
class Finding:
    """One problem (or notice) about one input.

    ``file`` is the display path of the input; it is ``None`` only for run-level
    notices. ``line`` is a 1-based line number; ``pointer`` a location inside the
    input (a JSON Pointer, an EPUB member path and fragment, a PDF page such as
    ``page 3``); either may be absent. ``subject`` names what the finding is about
    (a footnote id, a capture, a font). Messages never quote long passages of the
    reader's documents.
    """

    check: str
    rule: str
    severity: str
    message: str
    file: str | None = None
    line: int | None = None
    pointer: str | None = None
    subject: str | None = None
    hint: str | None = None

    def __post_init__(self):
        if self.severity not in RANK:
            raise ValueError(f"unknown severity {self.severity!r}")

    @property
    def rule_id(self) -> str:
        """Stable, globally unique rule identifier, e.g. ``footnotes/no-return-link``."""
        return f"{self.check}/{self.rule}"

    def sort_key(self):
        return (self.file is None, self.file or "", RANK[self.severity], self.check, self.rule,
                self.line or 0, self.pointer or "", self.subject or "", self.message)

    def to_dict(self) -> dict:
        return {
            "check": self.check, "rule": self.rule, "ruleId": self.rule_id,
            "severity": self.severity, "message": self.message, "file": self.file,
            "line": self.line, "pointer": self.pointer, "subject": self.subject, "hint": self.hint,
        }


@dataclass
class Source:
    """One recognised input. ``display`` is the path as shown in reports and suggested commands."""

    kind: str
    path: Path
    display: str
    label: str
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Check:
    name: str
    area: str
    summary: str
    # Source kinds this check consumes (see readwrench.check.discover.KINDS).
    kinds: frozenset[str]
    # Per-input checks take (source); cross-input checks take (sources).
    run: Callable[..., Iterable[Finding]] | None = None
    cross_file: bool = False
    rules: tuple[str, ...] = ()
    # Top-level import names of optional dependencies, and the extra that installs them.
    requires: tuple[str, ...] = ()
    extra: str | None = None
    # The readwrench command whose analysis this check runs ("-" for built-in checks).
    tool: str = "-"
    # Anything the reader should know about what the check does not cover.
    note: str | None = None


class MissingDependency(Exception):
    """An optional dependency of a check is not installed (one info finding per check)."""

    def __init__(self, module: str):
        super().__init__(module)
        self.module = module


def available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def need(*modules: str) -> None:
    """Raise :class:`MissingDependency` for the first module that is not installed."""
    for module in modules:
        if not available(module):
            raise MissingDependency(module)


def counts(findings) -> dict[str, int]:
    result = dict.fromkeys(SEVERITIES, 0)
    for finding in findings:
        result[finding.severity] += 1
    return result


def at_or_above(findings, threshold: str | None) -> bool:
    """True if any finding is at least as severe as ``threshold`` (None: never)."""
    if threshold is None:
        return False
    limit = RANK[threshold]
    return any(RANK[finding.severity] <= limit for finding in findings)


def short(value, limit: int = 240) -> str:
    """One line, at most ``limit`` characters."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def plural(count: int, word: str, plural_form: str | None = None) -> str:
    return f"{count} {word if count == 1 else plural_form or word + 's'}"
