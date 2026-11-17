"""Bounded JSON reads using the standard parser, with ambiguity rejected."""

import json
from pathlib import Path

from .errors import InputError


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise InputError(f"Non-finite value is not valid JSON: {value}")


def read_text(path: Path, *, maximum: int, name: str, encoding: str = "utf-8") -> str:
    try:
        if path.stat().st_size > maximum:
            raise InputError(f"{name} exceeds the {maximum} byte size limit")
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise InputError(f"{name} exceeds the {maximum} byte size limit")
        return data.decode(encoding)
    except (OSError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(f"Cannot read {name}: {exc}") from exc


def read_json(path: Path, *, maximum: int = 8 * 1024 * 1024, name: str = "JSON"):
    try:
        return json.loads(read_text(path, maximum=maximum, name=name, encoding="utf-8-sig"),
                          object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except RecursionError as exc:
        raise InputError(f"Cannot read {name}: JSON nesting is too deep") from exc
    except ValueError as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(f"Cannot read {name}: {exc}") from exc
