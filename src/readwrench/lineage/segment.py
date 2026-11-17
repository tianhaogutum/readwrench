"""Word segmentation in the spirit of ``Intl.Segmenter(undefined, {granularity: 'word'})``.

The original used ICU's word boundaries (Unicode UAX #29 plus dictionaries for CJK and
South-East Asian scripts). This port implements the UAX #29 rules that matter for prose:
letters and digits join (``don't``, ``3.14``, ``1,000``, ``e.g``), whitespace runs stay
together, combining marks/format characters/ZWJ attach to what precedes them, emoji ZWJ
sequences and regional-indicator pairs stay whole, and Katakana runs join. Each Han or
Hiragana character is its own word (ICU would group dictionary words), and Thai, Lao,
Khmer and Myanmar text is grouped into letter runs. Segments always concatenate back to
the input, so inline differences preserve both texts exactly.
"""
from __future__ import annotations

import unicodedata

NEWLINES = frozenset("\n\r\u000b\u000c\u0085  ")
MID_LETTER = frozenset("··״‧︓﹕")  # ICU drops ':' from MidLetter
MID_NUM_LET = frozenset(".'‘’․﹒＇．")
MID_NUM = frozenset(",;;։،؍٬߸⁄︐︔﹐﹔，；")


def _is_han(code: int) -> bool:
    return (0x2E80 <= code <= 0x2FDF or code in (0x3005, 0x3007) or 0x3021 <= code <= 0x3029
            or 0x3038 <= code <= 0x303B or 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF or 0x20000 <= code <= 0x3FFFF)


def _kind(char: str) -> str:
    code = ord(char)
    if char in NEWLINES:
        return "newline"
    category = unicodedata.category(char)
    if category in ("Mn", "Me", "Mc", "Cf") or 0x1F3FB <= code <= 0x1F3FF:
        return "extend"
    if category == "Zs" or char == "\t":
        return "space"
    if _is_han(code) or 0x3040 <= code <= 0x309F:
        return "ideo"
    if 0x30A0 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF or 0xFF66 <= code <= 0xFF9F or 0x3031 <= code <= 0x3035:
        return "katakana"
    if 0x1F1E6 <= code <= 0x1F1FF:
        return "regional"
    if category == "Nd":
        return "numeric"
    if category.startswith("L") or category == "Nl":
        return "letter"
    if category == "Pc":
        return "extendnumlet"
    if category == "So" and (code >= 0x1F000 or 0x2600 <= code <= 0x27BF or 0x2300 <= code <= 0x23FF
                             or 0x2B00 <= code <= 0x2BFF or code in (0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122)):
        return "pictographic"
    return "other"


def segments(text: str) -> list[tuple[str, bool]]:
    """``[(segment, is_word_like)]`` covering ``text`` exactly."""
    kinds = [_kind(char) for char in text]
    length = len(text)
    result: list[tuple[str, bool]] = []
    i = 0

    def skip_extend(j: int) -> int:
        while j < length and kinds[j] == "extend":
            j += 1
        return j

    while i < length:
        start = i
        kind = kinds[i]
        word = False
        if kind == "newline":
            i += 2 if text[i] == "\r" and i + 1 < length and text[i + 1] == "\n" else 1
        elif kind == "space":
            while i < length and kinds[i] in ("space", "extend"):
                i += 1
        elif kind in ("letter", "numeric", "extendnumlet"):
            word = True
            previous = kind
            i = skip_extend(i + 1)
            while i < length:
                current = kinds[i]
                if current in ("letter", "numeric", "extendnumlet"):
                    previous = current
                    i = skip_extend(i + 1)
                    continue
                char = text[i]
                after = skip_extend(i + 1)
                if after < length:
                    following = kinds[after]
                    if previous == "letter" and following == "letter" and (char in MID_LETTER or char in MID_NUM_LET):
                        i = skip_extend(after + 1)
                        continue
                    if previous == "numeric" and following == "numeric" and (char in MID_NUM or char in MID_NUM_LET):
                        i = skip_extend(after + 1)
                        continue
                break
        elif kind == "katakana":
            word = True
            i = skip_extend(i + 1)
            while i < length and kinds[i] in ("katakana", "extendnumlet"):
                i = skip_extend(i + 1)
        elif kind == "ideo":
            word = True
            i = skip_extend(i + 1)
        elif kind == "regional":
            i += 1
            if i < length and kinds[i] == "regional":
                i += 1
            i = skip_extend(i)
        elif kind == "pictographic":
            i = skip_extend(i + 1)
            while i < length and text[i - 1] == "‍" and kinds[i] == "pictographic":
                i = skip_extend(i + 1)
        else:
            i = skip_extend(i + 1)
        result.append((text[start:i], word))
    return result
