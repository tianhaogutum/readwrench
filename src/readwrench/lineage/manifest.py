"""Lineage manifest validation (a small reimplementation of the original zod schema)."""
from __future__ import annotations

import os
import re

from .fileio import LIMITS, InputError, parse_json, read_bounded
from .jsutil import js_len
from .times import TIMESTAMP, iso_time
from .urls import normalize_url

__all__ = ["load_manifest", "validate_manifest", "normalize_url", "iso_time"]

_ENCODING = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")
_FORMATS = ("html", "mhtml", "warc")
# Capture fields in schema order: (name, kind, min length, max length).
_CAPTURE_FIELDS = (
    ("file", "string", 1, None),
    ("capturedAt", "timestamp", None, 100),
    ("captureSource", "string", 1, 300),
    ("label", "string", None, 300),
    ("url", "string", None, None),
    ("allowUrlMismatch", "boolean", None, None),
    ("selector", "string", 1, 1000),
    ("format", "format", None, None),
    ("encoding", "encoding", None, None),
    ("recordId", "string", 1, 500),
)
_REQUIRED = {"file"}


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _check_string(value, path, issues, minimum=None, maximum=None):
    if not isinstance(value, str):
        issues.append((path, f"Invalid input: expected string, received {_type_name(value)}"))
        return False
    length = js_len(value)
    if minimum is not None and length < minimum:
        issues.append((path, f"Too small: expected string to have >={minimum} characters"))
    if maximum is not None and length > maximum:
        issues.append((path, f"Too big: expected string to have <={maximum} characters"))
    return True


def _unknown_keys(data: dict, known, path, issues):
    extra = [key for key in data if key not in known]
    if extra:
        names = ", ".join(f'"{key}"' for key in extra)
        issues.append((path, f"Unrecognized key{'s' if len(extra) > 1 else ''}: {names}"))


def _capture(entry, path, issues) -> dict | None:
    if not isinstance(entry, dict):
        issues.append((path, f"Invalid input: expected object, received {_type_name(entry)}"))
        return None
    result: dict = {}
    for name, kind, minimum, maximum in _CAPTURE_FIELDS:
        here = [*path, name]
        if name not in entry:
            if name in _REQUIRED:
                issues.append((here, "Invalid input: expected string, received undefined"))
            elif name == "allowUrlMismatch":
                result[name] = False
            continue
        value = entry[name]
        if kind == "boolean":
            if not isinstance(value, bool):
                issues.append((here, f"Invalid input: expected boolean, received {_type_name(value)}"))
        elif kind == "format":
            if value not in _FORMATS or not isinstance(value, str):
                issues.append((here, 'Invalid option: expected one of "html"|"mhtml"|"warc"'))
        elif kind == "encoding":
            if _check_string(value, here, issues) and not _ENCODING.match(value):
                issues.append((here, "Invalid string: must match pattern /^[a-zA-Z0-9_-]{1,50}$/"))
        elif kind == "timestamp":
            if isinstance(value, str) and not TIMESTAMP.match(value):
                issues.append((here, "Invalid ISO datetime"))
            _check_string(value, here, issues, maximum=maximum)
        else:
            _check_string(value, here, issues, minimum, maximum)
        result[name] = value
    _unknown_keys(entry, {name for name, *_ in _CAPTURE_FIELDS}, path, issues)
    return result


def validate_manifest(data) -> dict:
    """The parsed manifest (with defaults), or raise :class:`InputError` listing every issue."""
    issues: list[tuple[list, str]] = []
    if not isinstance(data, dict):
        issues.append(([], f"Invalid input: expected object, received {_type_name(data)}"))
    else:
        version = data.get("version")
        if "version" not in data or isinstance(version, bool) or version != 1:
            issues.append((["version"], "Invalid input: expected 1"))
        if "url" not in data:
            issues.append((["url"], "Invalid input: expected string, received undefined"))
        else:
            _check_string(data["url"], ["url"], issues, minimum=1)
        captures = data.get("captures")
        parsed = []
        if not isinstance(captures, list):
            issues.append((["captures"], f"Invalid input: expected array, received "
                                         f"{'undefined' if 'captures' not in data else _type_name(captures)}"))
        else:
            for index, entry in enumerate(captures):
                parsed.append(_capture(entry, ["captures", index], issues))
            if not captures:
                issues.append((["captures"], "Too small: expected array to have >=1 items"))
            if len(captures) > LIMITS["captures"]:
                issues.append((["captures"], f"Too big: expected array to have <={LIMITS['captures']} items"))
        _unknown_keys(data, ("version", "url", "captures"), [], issues)
    if issues:
        detail = "; ".join(f"{'.'.join(str(part) for part in path) or 'root'}: {message}"
                           for path, message in issues)
        raise InputError(f"Invalid manifest: {detail}")
    return {"version": 1, "url": data["url"], "captures": parsed}


def load_manifest(filename) -> dict:
    filename = os.fspath(filename)
    manifest = validate_manifest(parse_json(read_bounded(filename, LIMITS["manifestBytes"]), filename))
    manifest["url"] = normalize_url(manifest["url"])
    root = os.path.dirname(os.path.abspath(filename))
    captures = []
    for index, entry in enumerate(manifest["captures"]):
        if entry.get("url") and normalize_url(entry["url"]) != manifest["url"]:
            raise InputError(f"Capture {index + 1} URL differs from manifest URL")
        captures.append({**entry, "file": os.path.normpath(os.path.join(root, entry["file"])),
                         "inputIndex": index})
    manifest["captures"] = captures
    return manifest
