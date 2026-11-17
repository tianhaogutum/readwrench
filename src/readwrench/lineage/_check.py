"""``readwrench check`` adapter for ``readwrench lineage``: re-read saved lineage manifests.

A lineage manifest lists local captures of one web page. This check validates the
manifest and extracts every capture it names in memory, exactly as ``build`` would,
and reports captures that are missing, unreadable or undecodable, that identify a
different URL than the manifest, that yield no article text, or that would exceed the
collection limits. It writes no report, never opens the network and never runs
captured scripts.
"""
from __future__ import annotations

import json
import os
import re
import shlex

from readwrench.check.model import ERROR, INFO, WEB, Check, Finding, Source, plural, short

NAME = "lineage"
KIND = "lineage-manifest"
RULES = ("invalid-manifest", "duplicate-capture", "capture-missing", "url-mismatch", "capture-unreadable",
         "collection-too-large", "unknown-capture-time")
_MANIFEST_KEYS = {"version", "url", "captures"}


def classify(path, head: bytes):
    try:
        text = head.decode("utf-8", "strict").lstrip("﻿ \t\r\n")
    except UnicodeDecodeError:
        return None
    if not text.startswith("{") or '"captures"' not in text or '"version"' not in text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        # Larger than the sniffed head: recognise the shape of the first capture only.
        if re.match(r'\{\s*"version"\s*:\s*1\s*,\s*"url"\s*:\s*"[^"]*"\s*,\s*"captures"\s*:\s*\[\s*\{\s*"file"', text):
            return KIND, "readwrench lineage manifest"
        return None
    if not isinstance(data, dict) or not set(data) <= _MANIFEST_KEYS or not _MANIFEST_KEYS - {"version"} <= set(data):
        return None
    captures = data.get("captures")
    if data.get("version") != 1 or not isinstance(captures, list) or not captures:
        return None
    if not all(isinstance(entry, dict) and isinstance(entry.get("file"), str) for entry in captures):
        return None
    return KIND, "readwrench lineage manifest"


def _rule(message: str) -> str:
    if message.startswith("Cannot read ") and ("No such file" in message or "not a directory" in message.lower()):
        return "capture-missing"
    if " identifies " in message and "not the requested" in message:
        return "url-mismatch"
    return "capture-unreadable"


def check_manifest(source: Source):
    from .extract import extract_capture
    from .fileio import InputError
    from .jsutil import js_len
    from .manifest import load_manifest

    file = source.display
    hint = f"readwrench lineage build {shlex.quote(file)} --out timeline.html"

    def finding(rule, severity, message, *, pointer=None, subject=None):
        return Finding(NAME, rule, severity, message, file=file, pointer=pointer, subject=subject, hint=hint)

    try:
        manifest = load_manifest(source.path)
    except InputError as error:
        yield finding("invalid-manifest", ERROR, short(error, 400))
        return
    seen: set[str] = set()
    usable = []
    for position, entry in enumerate(manifest["captures"]):
        pointer = f"/captures/{position}"
        subject = entry.get("label") or os.path.basename(entry["file"])
        try:
            canonical = os.path.realpath(entry["file"], strict=True)
        except OSError:
            canonical = entry["file"]
        key = canonical + "\0" + (entry.get("recordId") or "")
        if key in seen:
            yield finding("duplicate-capture", ERROR, "the same capture file/record is listed more than once",
                          pointer=pointer, subject=subject)
            continue
        seen.add(key)
        try:
            capture = extract_capture(entry, manifest["url"])
        except InputError as error:
            message = str(error)
            yield finding(_rule(message), ERROR, short(message, 400), pointer=pointer, subject=subject)
            continue
        usable.append(capture)
    if len(usable) != len(manifest["captures"]):
        return
    characters = sum(js_len(block["text"]) for capture in usable for block in capture["blocks"])
    blocks = sum(len(capture["blocks"]) for capture in usable)
    if characters > 8_000_000 or blocks > 20_000:
        yield finding("collection-too-large", ERROR,
                      f"the captures extract to {blocks} blocks and {characters} characters, over the 20,000-block / "
                      "8,000,000-character collection limit; split the timeline into smaller manifests")
        return
    unknown = [capture for capture in usable if not capture["captureTime"]["at"]]
    if unknown:
        yield finding("unknown-capture-time", INFO,
                      f"{plural(len(unknown), 'capture')} of {len(usable)} {'has' if len(unknown) == 1 else 'have'} no "
                      "known capture time, so the timeline follows manifest order; add capturedAt where known")


CHECKS = (
    Check(NAME, WEB, "Saved lineage manifests: every capture still exists, decodes, matches the page URL and yields "
                     "article text", frozenset({KIND}), run=check_manifest, rules=RULES, tool=NAME,
          note="Extraction is a port of Mozilla Readability's heuristics; inspect the built report for missing text."),
)
