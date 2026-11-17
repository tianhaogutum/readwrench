"""Build the timeline (captures, transitions, paragraph lineages) from a manifest."""
from __future__ import annotations

import os

from .align import create_lineage
from .extract import extract_capture
from .fileio import InputError
from .jsutil import js_len
from .manifest import load_manifest

SEMANTICS = ("Capture timestamps record observations, never the webpage modification date. Paragraph identities "
             "are heuristic for edited text; ambiguous matches remain additions/removals.")


def build_timeline(filename, on_progress=lambda message: None) -> dict:
    """``{"timeline": ..., "protectedFiles": [...]}`` (inputs that outputs must never replace)."""
    manifest = load_manifest(filename)
    captures = []
    warnings = []
    seen = set()
    total_chars = total_blocks = 0
    # Sequential extraction keeps only one input tree alive at a time.
    for entry in manifest["captures"]:
        try:
            canonical = os.path.realpath(entry["file"], strict=True)
        except OSError:
            canonical = entry["file"]
        key = canonical + "\0" + (entry.get("recordId") or "")
        if key in seen:
            raise InputError(f"Duplicate capture file/record: {entry['file']}")
        seen.add(key)
        on_progress(f"Extracting {entry['inputIndex'] + 1}/{len(manifest['captures'])}: "
                    f"{entry.get('label') or entry['file']}")
        result = extract_capture(entry, manifest["url"])
        total_chars += sum(js_len(block["text"]) for block in result["blocks"])
        total_blocks += len(result["blocks"])
        if total_chars > 8_000_000:
            raise InputError("Collection exceeds 8,000,000 extracted text characters; split the timeline into smaller "
                             "batches")
        if total_blocks > 20_000:
            raise InputError("Collection exceeds 20,000 extracted blocks; split the timeline into smaller batches")
        captures.append(result)
    all_timed = all(capture["captureTime"]["at"] for capture in captures)
    if all_timed:
        captures.sort(key=lambda capture: (capture["captureTime"]["at"], capture["inputIndex"]))
        if any(index and capture["captureTime"]["at"] == captures[index - 1]["captureTime"]["at"]
               for index, capture in enumerate(captures)):
            warnings.append("Some capture times coincide at millisecond precision; manifest order breaks ties and "
                            "does not establish their real sequence. Raw timestamp claims are retained.")
    else:
        warnings.append("At least one capture has unknown capture time. All captures retain manifest order; this "
                        "order is supplied by the user, not inferred chronology.")
    protected = [os.fspath(filename)]
    for capture in captures:
        protected.extend(capture.pop("protectedFiles"))
    lineage = create_lineage(captures)
    timeline = {
        "schemaVersion": 1, "sourceURL": manifest["url"], "title": captures[-1]["title"],
        "ordering": "capture-time" if all_timed else "manifest", "warnings": warnings, "captures": captures,
        "transitions": lineage["transitions"], "lineages": lineage["lineages"], "semantics": SEMANTICS,
    }
    return {"timeline": timeline, "protectedFiles": protected}
