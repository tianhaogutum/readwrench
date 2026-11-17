"""Small shared types and a reversible case/whitespace normalization."""

import re
from array import array
from dataclasses import dataclass

MAX_TEXT = 1_000_000
MAX_RECORD = 8_000_000


class CluejoinError(Exception):
    """Invalid input or an unsupported index, with a user-facing explanation."""


@dataclass(frozen=True, slots=True)
class Fragment:
    document_id: str
    source: str
    fragment_id: str
    text: str
    page: int | None = None
    paragraph: int | None = None
    line: int | None = None
    origin: str = ""
    record: int = 0
    record_end: int | None = None
    origin_kind: str = "record"


def normalize(text: str) -> str:
    """Unicode casefold; every nonempty run of whitespace becomes one space."""
    return " ".join(text.casefold().split())


def line_break_ends(text: str, origin_kind: str) -> array:
    """rga JSON numbers LF records; ordinary extracted text uses universal newlines."""
    pattern = r"\n" if origin_kind in {"rga-json-record", "rga-extracted-line"} else r"\r\n|\r|\n"
    return array("I", (match.end() for match in re.finditer(pattern, text)))


def normalize_mapped(text: str) -> tuple[str, array, array]:
    """Return normalized text and original character ranges for each character.

    Arrays keep the offset map bounded even for long extracted paragraphs.
    A casefold expansion (ß -> ss) maps both letters to the same original range.
    """
    chars: list[str] = []
    starts = array("I")
    ends = array("I")
    space_start: int | None = None
    for i, char in enumerate(text):
        if char.isspace():
            if chars and space_start is None:
                space_start = i
            continue
        if space_start is not None:
            chars.append(" ")
            starts.append(space_start)
            ends.append(i)
            space_start = None
        folded = char.casefold()
        chars.append(folded)
        starts.extend([i] * len(folded))
        ends.extend([i + 1] * len(folded))
    return "".join(chars), starts, ends


def positive_int(value: object, field: str, *, optional: bool = True) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**31 - 1:
        raise CluejoinError(f"{field} must be a positive integer no greater than 2147483647")
    return value


def string(value: object, field: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CluejoinError(f"{field} must be a nonempty string")
    if len(value) > maximum or "\x00" in value:
        raise CluejoinError(f"{field} is too long or contains a NUL character")
    # Lone JSON surrogates cannot be stored in SQLite or emitted as valid UTF-8.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CluejoinError(f"{field} contains an unpaired Unicode surrogate") from exc
    return value
