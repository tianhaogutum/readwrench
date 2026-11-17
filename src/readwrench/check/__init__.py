"""``readwrench check``: one read-only pass over a folder of reading material.

``check`` finds EPUB books, PDF documents, font files, saved web pages (HTML,
MHTML, WARC) and the review files the other readwrench tools save, by their
content, then runs every analysis of those tools that needs no decision from the
reader. It never writes a file, never runs an external program and never opens a
network connection.

Library use::

    from readwrench.check import CLASSIFIERS, discover, run_checks
    findings = run_checks(discover(["."], CLASSIFIERS))
"""
from __future__ import annotations

from .discover import KINDS, Discovery, DiscoveryError, discover
from .model import SEVERITIES, Check, Finding, Source
from .registry import CHECKS, CLASSIFIERS, run_checks, select

__all__ = [
    "CHECKS", "CLASSIFIERS", "KINDS", "SEVERITIES", "Check", "Discovery", "DiscoveryError", "Finding", "Source",
    "discover", "run_checks", "select",
]
