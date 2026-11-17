"""The list of checks (gathered from each tool's adapter), and the crash-isolating loop that runs them."""
from __future__ import annotations

import importlib
import importlib.util
import os
from typing import Iterable

from .discover import Classifier, Discovery
from .model import AREAS, ERROR, INFO, INPUTS, Check, Finding, MissingDependency, plural

ISSUES_URL = "https://github.com/tianhaogutum/readwrench/issues"
# Set to 1 to let a crashing check raise its traceback instead of reporting it.
DEBUG_ENV = "READWRENCH_CHECK_DEBUG"

# Tool subpackages that may ship a ``_check`` adapter, in report order.
ADAPTERS = ("quotelocator", "cluejoin", "concordance", "pagebridge", "reanchor", "footnotes",
            "subsetproof", "lineage")

INPUTS_CHECK = Check(
    "inputs", INPUTS, "Files that cannot be read, are empty, or whose content does not match their name",
    frozenset(), rules=("unreadable", "empty", "misnamed", "unrecognised", "scan-truncated"))


def _load() -> tuple[tuple[Check, ...], tuple[Classifier, ...], list[Finding]]:
    checks: list[Check] = []
    classifiers: list[Classifier] = []
    problems: list[Finding] = []
    for name in ADAPTERS:
        module_name = f"readwrench.{name}._check"
        try:
            if importlib.util.find_spec(f"readwrench.{name}") is None or importlib.util.find_spec(module_name) is None:
                continue
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - one broken adapter must not stop the others
            if os.environ.get(DEBUG_ENV) == "1":
                raise
            problems.append(Finding("check", "internal", ERROR,
                                    f"the check adapter of readwrench.{name} failed to load ({type(exc).__name__}); "
                                    "its checks did not run", hint=f"please report it at {ISSUES_URL}"))
            continue
        checks.extend(getattr(module, "CHECKS", ()))
        classify = getattr(module, "classify", None)
        if callable(classify):
            classifiers.append(classify)
    order = {area: index for index, area in enumerate(AREAS)}
    checks.sort(key=lambda check: order.get(check.area, len(AREAS)))
    return (*checks, INPUTS_CHECK), tuple(classifiers), problems


CHECKS, CLASSIFIERS, LOAD_PROBLEMS = _load()
BY_NAME = {check.name: check for check in CHECKS}


def select(only: Iterable[str] = (), skip: Iterable[str] = ()) -> list[Check]:
    """Checks to run, in registry order. Raises ValueError for unknown names."""
    only, skip = list(only), list(skip)
    unknown = sorted({name for name in [*only, *skip] if name not in BY_NAME})
    if unknown:
        raise ValueError(f"unknown check(s): {', '.join(unknown)} (see --list-checks)")
    return [check for check in CHECKS if (not only or check.name in only) and check.name not in skip]


def _internal(check: Check, exc: BaseException, display: str | None) -> Finding:
    # Only the exception type: messages of unexpected errors may quote document content.
    return Finding(check.name, "internal", ERROR,
                   f"the {check.name} check crashed ({type(exc).__name__}); its results are missing, "
                   "other checks still ran", display,
                   hint=f"please report it at {ISSUES_URL} (set {DEBUG_ENV}=1 for the traceback)")


def missing_dependency(check: Check, module: str, count: int) -> Finding:
    install = (f"pipx install 'readwrench[{check.extra}]' (or pip install 'readwrench[{check.extra}]')"
               if check.extra else None)
    return Finding(check.name, "missing-dependency", INFO,
                   f"not run on {plural(count, 'input')}: the optional dependency {module} is not installed",
                   hint=install)


class _Missing(Exception):
    """An optional dependency is absent; ``produced`` holds the findings yielded before that."""

    def __init__(self, module: str, produced: list[Finding]):
        super().__init__(module)
        self.module = module
        self.produced = produced


def _guarded(check: Check, argument, display: str | None) -> list[Finding]:
    """Findings of one check call; raises _Missing when an optional dependency is absent."""
    produced: list[Finding] = []
    try:
        for finding in check.run(argument):
            produced.append(finding)
        return produced
    except MissingDependency as exc:
        raise _Missing(exc.module, produced) from None
    except ModuleNotFoundError as exc:
        root = (exc.name or "").partition(".")[0]
        if root and root in check.requires:
            raise _Missing(root, produced) from None
        if os.environ.get(DEBUG_ENV) == "1":
            raise
        return produced + [_internal(check, exc, display)]
    except Exception as exc:  # noqa: BLE001 - one broken checker must not abort the run
        if os.environ.get(DEBUG_ENV) == "1":
            raise
        return produced + [_internal(check, exc, display)]


def run_checks(discovery: Discovery, selected: Iterable[Check] | None = None) -> list[Finding]:
    """Run ``selected`` checks (default: all) over discovered inputs; never raises for a checker bug."""
    selected = list(CHECKS if selected is None else selected)
    names = {check.name for check in selected}
    findings = [finding for finding in discovery.findings if finding.check in names]
    findings += LOAD_PROBLEMS
    for check in selected:
        if check.run is None:
            continue
        relevant = [source for source in discovery.sources if source.kind in check.kinds]
        if not relevant:
            continue
        if check.cross_file:
            try:
                findings.extend(_guarded(check, relevant, None))
            except _Missing as missing:
                findings.extend(missing.produced)
                findings.append(missing_dependency(check, missing.module, len(relevant)))
            continue
        for index, source in enumerate(relevant):
            try:
                findings.extend(_guarded(check, source, source.display))
            except _Missing as missing:
                # Findings already produced are kept; the remaining inputs become one notice.
                findings.extend(missing.produced)
                findings.append(missing_dependency(check, missing.module, len(relevant) - index))
                break
    return sorted(findings, key=Finding.sort_key)
