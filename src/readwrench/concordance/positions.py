"""Sparse character/UTF-8 conversions using Python's Unicode codec.

Only requested boundaries are stored. Each intervening text/byte slice is
encoded or decoded once, avoiding an integer object for every book character.
"""

from .errors import InputError


def _requested(positions, maximum: int) -> list[int]:
    requested = set()
    for position in positions:
        if type(position) is not int or not 0 <= position <= maximum:
            raise InputError("Text offset is outside canonical text or is not an integer")
        requested.add(position)
    return sorted(requested)


def to_utf8(text: str, positions) -> dict[int, int]:
    result = {}
    previous = 0
    offset = 0
    for position in _requested(positions, len(text)):
        offset += len(text[previous:position].encode("utf-8"))
        result[position] = offset
        previous = position
    return result


def to_characters(text: str, positions) -> dict[int, int]:
    encoded = text.encode("utf-8")
    result = {}
    previous = 0
    characters = 0
    for position in _requested(positions, len(encoded)):
        try:
            characters += len(encoded[previous:position].decode("utf-8", errors="strict"))
        except UnicodeDecodeError as exc:
            raise InputError("Highlight offset falls inside a UTF-8 code point") from exc
        result[position] = characters
        previous = position
    return result
