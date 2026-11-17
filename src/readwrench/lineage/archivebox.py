"""Import per-snapshot ArchiveBox metadata into a lineage manifest."""
from __future__ import annotations

import os
import re

from .fileio import LIMITS, InputError, parse_json, read_bounded
from .times import iso_time
from .urls import normalize_url

PRIORITY = ("singlefile", "dom", "readability", "mercury", "wget")
DEFAULT_FILES = {
    "singlefile": ("singlefile/singlefile.html", "singlefile.html"),
    "dom": ("dom/output.html", "output.html"),
    "readability": ("readability/content.html", "article.html"),
    "mercury": ("mercury/content.html",),
    "wget": (),
}
_HTML_NAME = re.compile(r"\.(?:html?|mhtml?)$", re.IGNORECASE)


def _existing_contained(root: str, relative) -> str | None:
    if not isinstance(relative, str) or os.path.isabs(relative) or not _HTML_NAME.search(relative):
        return None
    filename = os.path.normpath(os.path.join(root, relative))
    if not filename.startswith(root + os.sep):
        return None
    try:
        actual = os.path.realpath(filename, strict=True)
        if not actual.startswith(root + os.sep) or not os.path.isfile(actual):
            return None
        return actual
    except (FileNotFoundError, NotADirectoryError):
        return None


def _plugin(record) -> str | None:
    return record.get("plugin") or record.get("extractor") if isinstance(record, dict) else None


def _parse_index(data: bytes, filename: str) -> tuple[dict, list]:
    if re.search(r"\.jsonl$", filename, re.IGNORECASE):
        text = data.decode("utf-8", "replace").lstrip("﻿")
        lines = [line for line in re.split(r"\r?\n", text) if line.strip()]
        records = [parse_json(line.encode("utf-8"), f"{filename}, nonempty line {i + 1}") for i, line in enumerate(lines)]
        snapshots = [record for record in records if isinstance(record, dict) and record.get("type") == "Snapshot"]
        if len(snapshots) != 1:
            raise InputError(f"Expected exactly one Snapshot record in {filename}")
        snapshot = snapshots[0]
        results = [record for record in records if isinstance(record, dict) and record.get("type") == "ArchiveResult"]
        if any(record.get("snapshot_id") and snapshot.get("id") and record["snapshot_id"] != snapshot["id"]
               for record in results):
            raise InputError(f"ArchiveResult belongs to another snapshot in {filename}")
        return snapshot, results
    snapshot = parse_json(data, filename)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("url"), str):
        raise InputError(f"Expected per-snapshot ArchiveBox index, not a collection export: {filename}")
    if "archive_results" in snapshot and not isinstance(snapshot["archive_results"], list):
        raise InputError(f"archive_results must be an array in {filename}")
    if isinstance(snapshot.get("archive_results"), list):
        results = snapshot["archive_results"]
    else:
        results = []
        history = snapshot.get("history") or {}
        if not isinstance(history, dict):
            history = {}
        for plugin, items in history.items():
            if not isinstance(items, list):
                raise InputError(f"Invalid history for {plugin} in {filename}")
            for item in items:
                results.append({**item, "plugin": plugin} if isinstance(item, dict) else item)
    if any(not isinstance(item, dict) for item in results):
        raise InputError(f"Invalid ArchiveBox extractor record in {filename}")
    return snapshot, results


def _js_value(value) -> str:
    return "(absent)" if value is None else str(value)


def import_archivebox(index_files: list[str], output_filename: str, requested_url: str | None = None) -> dict:
    if not index_files or len(index_files) > LIMITS["captures"]:
        raise InputError(f"Provide 1–{LIMITS['captures']} per-snapshot ArchiveBox index files")
    warnings: list[str] = []
    captures: list[dict] = []
    protected = list(index_files)
    seen: set[str] = set()
    source_url = normalize_url(requested_url) if requested_url else None
    output_dir = os.path.dirname(os.path.abspath(output_filename))
    for filename in index_files:
        snapshot, results = _parse_index(read_bounded(filename, LIMITS["manifestBytes"]), filename)
        url = normalize_url(snapshot["url"])
        source_url = source_url or url
        if source_url != url:
            raise InputError(f"ArchiveBox snapshots have different URLs: {url} and {source_url}")
        root = os.path.realpath(os.path.dirname(os.path.abspath(filename)))
        chosen = None
        for plugin in PRIORITY:
            successful = [r for r in results if _plugin(r) == plugin and r.get("status") == "succeeded"][::-1]
            # Exports are usually ordered, but independent JSON exports need not be: sort
            # valid recorded times instead of dating the file by the last array element.
            starts = {}
            for position, result in enumerate(successful):
                try:
                    starts[position] = iso_time(result.get("start_ts"), "ArchiveBox start_ts")
                except InputError:
                    pass  # Unknown time: keep source order and expose the limitation.
            if len(starts) == len(successful):
                # A stable descending sort, like Array.prototype.sort with b.localeCompare(a).
                order = sorted(range(len(successful)), key=lambda i: _Desc(starts[i]))
                successful = [successful[i] for i in order]
            # Only the latest successful result can describe the extractor's current output;
            # older runs usually overwrote the same file on disk.
            result = successful[0] if successful else None
            output_files = list(result["output_files"].keys()) if result and isinstance(
                result.get("output_files"), dict) else []
            output = None
            if result:
                output = result.get("output_str") if result.get("output_str") is not None else result.get("output")
            explicit = [*output_files, *([output, f"{plugin}/{output}"]
                                         if isinstance(output, str) and _HTML_NAME.search(output) else [])]
            # A declared missing file must not lend its date to a stale default path.
            candidates = explicit if explicit else list(DEFAULT_FILES[plugin])
            for candidate in dict.fromkeys(candidates):
                file = _existing_contained(root, candidate)
                if not file:
                    continue
                # If metadata records only failures for this extractor, never use a stale
                # file left in its directory. Missing history is allowed but undated.
                if not result and any(_plugin(item) == plugin for item in results):
                    continue
                captured_at = None
                if result and result.get("start_ts"):
                    try:
                        captured_at = iso_time(result["start_ts"], f"ArchiveBox {plugin}.start_ts")
                    except InputError:
                        warnings.append(f"{filename}: {plugin} start_ts has no valid explicit timezone; capture time "
                                        "left unknown.")
                if not captured_at:
                    warnings.append(f"{filename}: no reliable extraction start time for {plugin}; bookmark timestamp "
                                    f"{_js_value(snapshot.get('timestamp'))} was not used as capture time.")
                chosen = {"file": os.path.relpath(file, output_dir),
                          "label": f"ArchiveBox {plugin} · {os.path.basename(root)}"}
                if captured_at:
                    chosen.update({"capturedAt": captured_at,
                                   "captureSource": f"ArchiveBox {plugin} extractor start_ts (metadata claim)"})
                if plugin in ("readability", "mercury"):
                    chosen["selector"] = "body"
                protected.append(file)
                if len(successful) > 1:
                    warnings.append(f"{filename}: {len(successful)} successful {plugin} runs share the current output "
                                    "slot; importing only the latest metadata, not fabricating old versions from "
                                    "overwritten files.")
                break
            if result and explicit and not chosen:
                warnings.append(f"{filename}: latest {plugin} result declares unavailable or escaping output paths; "
                                "stale default files were not assigned that result's timestamp.")
            if chosen:
                break
        if not chosen:
            raise InputError(f"No supported existing HTML capture in ArchiveBox snapshot {filename}")
        full = os.path.normpath(os.path.join(os.path.dirname(output_filename) or ".", chosen["file"]))
        full = os.path.abspath(full)
        if full in seen:
            raise InputError(f"ArchiveBox inputs resolve to the same capture file: {full}")
        seen.add(full)
        captures.append(chosen)
    return {"manifest": {"version": 1, "url": source_url, "captures": captures}, "warnings": warnings,
            "protectedFiles": protected}


class _Desc:
    """Sort key that orders strings descending while keeping ``sorted`` stable."""

    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value

    def __lt__(self, other: "_Desc") -> bool:
        return self.value > other.value
