"""Mapping JSON: reading, validation and approval snapshots (standard library only).

Kept free of PDF dependencies so ``readwrench check`` can re-verify a saved,
hash-bound mapping without PyMuPDF installed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

MAX_PAGES = 200
# On-disk schema ids are kept from the standalone tool so existing files stay valid.
SCHEMA = "pdf-page-bridge/v1"
RESULT_SCHEMA = "pdf-page-bridge-result/v1"
MAX_MAPPING_BYTES = 4 * 1024 * 1024


class BridgeError(ValueError):
    """An actionable input or review problem."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_plan(raw: bytes):
    """Parse mapping JSON strictly: no duplicate keys, NaN/Infinity or lone surrogates."""
    try:

        def object_with_unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise BridgeError(f"Duplicate JSON field: {key}")
                result[key] = value
            return result

        def reject_nonfinite(value):
            raise BridgeError(f"Non-finite JSON number: {value}")

        data = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=object_with_unique_keys,
            parse_constant=reject_nonfinite,
        )
        # JSON escape syntax permits lone surrogates; UTF-8/PDF labels do not.
        json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise BridgeError(f"Invalid mapping JSON: {exc}") from exc
    return data


def read_plan(path: Path):
    if not path.is_file():
        raise BridgeError(f"Mapping does not exist or is not a regular file: {path}")
    # A stat-then-read check can be bypassed by a growing file. Read at most the
    # limit plus one byte, regardless of metadata observed before opening it.
    with path.open("rb") as stream:
        raw = stream.read(MAX_MAPPING_BYTES + 1)
    if len(raw) > MAX_MAPPING_BYTES:
        raise BridgeError("Mapping JSON exceeds the 4 MiB limit")
    data = parse_plan(raw)
    validate_map(data)
    return data


def approval_snapshot(data):
    """An explicit review snapshot catches accidental edits after confirmation."""
    return {
        "original_sha256": data["sources"]["original"]["sha256"],
        "modified_sha256": data["sources"]["modified"]["sha256"],
        "pairs": [[row["original"], row["modified"]] for row in data["rows"]],
    }


def validate_map(data, *, approved=False):
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise BridgeError(f"Expected mapping schema {SCHEMA}")
    sources = data.get("sources")
    if not isinstance(sources, dict):
        raise BridgeError("Mapping is missing its source identities")
    counts = {}
    for side in ("original", "modified"):
        source = sources.get(side)
        if not isinstance(source, dict):
            raise BridgeError(f"Mapping is missing {side} source identity")
        count = source.get("pages")
        if type(count) is not int or not 1 <= count <= MAX_PAGES:
            raise BridgeError(f"Invalid {side} page count")
        for field in ("path", "name"):
            value = source.get(field)
            if (
                not isinstance(value, str)
                or not value
                or "\x00" in value
                or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
            ):
                raise BridgeError(f"Invalid {side} {field}")
        if not isinstance(source.get("path"), str) or not re.fullmatch(
            r"[a-f0-9]{64}", str(source.get("sha256", ""))
        ):
            raise BridgeError(f"Invalid {side} path or SHA-256 identity")
        counts[side] = count
    rows = data.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= sum(counts.values()):
        raise BridgeError("Mapping must contain one row for every matched or unpaired page")
    seen = {side: set() for side in counts}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not all(side in row for side in counts):
            raise BridgeError(
                f"Row {index} must explicitly contain original and modified; "
                "use null for an unpaired page"
            )
        if row.get("original") is None and row.get("modified") is None:
            raise BridgeError(f"Row {index} needs an original or modified page")
        for side in counts:
            value = row.get(side)
            if value is None:
                continue
            if type(value) is not int or not 1 <= value <= counts[side]:
                raise BridgeError(
                    f"Row {index}: {side} page must be an integer in 1-{counts[side]}"
                )
            if value in seen[side]:
                raise BridgeError(f"Row {index}: {side} page {value} is assigned more than once")
            seen[side].add(value)
        if approved and row.get("confirmed") is not True:
            raise BridgeError(f"Row {index} is not confirmed; review the mapping before export")
    for side, count in counts.items():
        missing = set(range(1, count + 1)) - seen[side]
        if missing:
            raise BridgeError(
                f"Unaccounted {side} pages: {sorted(missing)}; add explicit insert/delete rows"
            )
    if approved:
        review = data.get("review")
        if (
            not isinstance(review, dict)
            or not isinstance(review.get("reviewer"), str)
            or not 1 <= len(review["reviewer"].strip()) <= 120
        ):
            raise BridgeError("Approved mapping needs a named reviewer")
        if review.get("approved_map") != approval_snapshot(data):
            raise BridgeError(
                "Approval does not match the current page map; review and confirm again"
            )
        try:
            timestamp = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("timezone missing")
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise BridgeError("Approval needs a valid reviewed_at timestamp with timezone") from exc
    return data
