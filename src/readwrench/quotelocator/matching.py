"""Exhaustive, evidence-only review assembled from reanchor primitives."""

from __future__ import annotations

import bisect
import hashlib
import importlib.metadata
import json
import re
import unicodedata
from array import array
from dataclasses import asdict
from functools import lru_cache

import regex
from rapidfuzz.distance import Levenshtein
from reanchor import (
    NormalizedText,
    NormalizeOptions,
    TextQuoteSelector,
    find_exact,
    normalize,
    to_source_span,
)

from .documents import Document

PROFILES = {
    "typographic": NormalizeOptions(strip_marks=False, case_fold=False),
    "loose": NormalizeOptions(),
}
MAX_QUOTE_CHARS = 10_000
MAX_CANDIDATES = 1000
MAX_REVIEW_PAYLOAD = 8 * 1024 * 1024
CONTEXT_CHARS = 800


def normalized_text(value: str, options: NormalizeOptions) -> NormalizedText:
    first = normalize(value, options)
    # Removing a standalone combining mark can expose whitespace that reanchor
    # already flushed, leaving a trailing/doubled space despite trim/collapse.
    # Reuse its normalizer once more and compose both offset maps, rather than
    # dropping provenance or reimplementing Unicode normalization.
    if not re.search(r"^ | {2}| $", first.text):
        return first
    second = normalize(first.text, options)
    if second.text == first.text:
        return first
    return NormalizedText(
        text=second.text,
        src_start=array("i", (first.src_start[i] for i in second.src_start)),
        src_end=array("i", (first.src_end[i - 1] for i in second.src_end)),
        source_length=len(value),
    )


def candidate_id(document: Document, start: int, end: int) -> str:
    value = f"{document.sha256}:{document.text_sha256}:{start}:{end}"
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def occurrences(text: str, quote: str):
    """Reuse reanchor search without repeating long-pattern setup at every overlap."""
    if len(quote) < 128:
        yield from find_exact(text, quote)
    else:
        # reanchor's str.find loop preprocesses the full pattern on every call.
        # Search its short literal prefix, then validate with the built-in C
        # prefix comparison. This preserves every overlapping exact occurrence.
        for start in find_exact(text, quote[:32]):
            if text.startswith(quote, start):
                yield start


def differences(quote: str, found: str) -> list[dict]:
    if quote == found:
        return []
    if len(found) > 100_000 or len(quote) * len(found) > 100_000_000:
        # Normalization can remove megabytes of whitespace/combining marks.
        # A complete coarse replacement is honest and bounds alignment memory.
        return [
            {
                "operation": "replace",
                "query": quote,
                "source": found,
                "query_start": 0,
                "query_end": len(quote),
                "source_start": 0,
                "source_end": len(found),
                "granularity": "whole-span",
            }
        ]
    changes = []
    pending = None
    for opcode in Levenshtein.opcodes(quote, found):
        if opcode.tag == "equal":
            if pending is not None:
                changes.append(pending)
                pending = None
            continue
        if pending is None:
            pending = {
                "query_start": opcode.src_start,
                "query_end": opcode.src_end,
                "source_start": opcode.dest_start,
                "source_end": opcode.dest_end,
            }
        else:
            pending["query_end"] = opcode.src_end
            pending["source_end"] = opcode.dest_end
    if pending is not None:
        changes.append(pending)
    for change in changes:
        change["query"] = quote[change["query_start"] : change["query_end"]]
        change["source"] = found[change["source_start"] : change["source_end"]]
        change["operation"] = (
            "insert" if not change["query"] else "delete" if not change["source"] else "replace"
        )
    return changes


def _complex_graphemes(text: str) -> tuple[list[int], list[int]]:
    # ASCII and ordinary code points need no boundary table. Record only clusters
    # with several code points (combining accents, emoji joiners, Indic syllables).
    spans = [(m.start(), m.end()) for m in regex.finditer(r"\X", text) if m.end() - m.start() > 1]
    return [start for start, _end in spans], [end for _start, end in spans]


def _whole_graphemes(start: int, end: int, starts: list[int], ends: list[int]) -> tuple[int, int]:
    left = bisect.bisect_right(starts, start) - 1
    if left >= 0 and start < ends[left]:
        start = starts[left]
    right = bisect.bisect_left(starts, end) - 1
    if right >= 0 and end < ends[right]:
        end = ends[right]
    return start, end


def _paragraphs(text: str) -> list[tuple[int, int]]:
    spans = []
    previous = 0
    line_break = r"(?>\r\n|[\r\n\v\u0085\u2028])"
    indentation = r"[^\S\r\n\v\f\u0085\u2028\u2029]*"
    separator = rf"{line_break}(?:{indentation}{line_break})+|[\f\u2029]"
    for match in re.finditer(separator, text):
        if text[previous : match.start()].strip():
            spans.append((previous, match.start()))
        previous = match.end()
    if text[previous:].strip():
        spans.append((previous, len(text)))
    return spans


def _clip(text: str, start: int, end: int, *, around: int | None = None) -> dict:
    left, right = start, end
    if right - left > CONTEXT_CHARS:
        left = max(start, (around or start) - CONTEXT_CHARS // 2)
        right = min(end, left + CONTEXT_CHARS)
        left = max(start, right - CONTEXT_CHARS)
    return {
        "text": text[left:right],
        "start": left,
        "end": right,
        "clipped_before": left > start,
        "clipped_after": right < end,
    }


def _context(
    text: str, spans: list[tuple[int, int]], starts: list[int], start: int, end: int
) -> dict:
    first = max(0, bisect.bisect_right(starts, start) - 1)
    if spans and spans[first][1] <= start and first + 1 < len(spans):
        first += 1
    last = max(first, bisect.bisect_left(starts, end) - 1)
    return {
        "before": _clip(text, *spans[first - 1], around=spans[first - 1][1]) if first else None,
        "containing": [
            _clip(text, *spans[i], around=start if i == first else end)
            for i in range(first, min(last + 1, len(spans)))
        ],
        "after": _clip(text, *spans[last + 1]) if last + 1 < len(spans) else None,
    }


def locations(document: Document, start: int, end: int) -> list[dict]:
    return [
        {
            "label": unit.label,
            "href": unit.href,
            "number": unit.number,
            "start": max(start, unit.start) - unit.start,
            "end": min(end, unit.end) - unit.start,
        }
        for unit in document.units
        if unit.start < end and unit.end > start
    ]


def locate(
    document: Document,
    quote: str | TextQuoteSelector,
    *,
    profile: str = "typographic",
    offset: int = 0,
    limit: int = MAX_CANDIDATES,
) -> dict:
    if isinstance(quote, TextQuoteSelector):
        prefix = "" if quote.prefix is None else quote.prefix
        suffix = "" if quote.suffix is None else quote.suffix
        quote = quote.exact
    else:
        prefix = suffix = ""
    if not isinstance(prefix, str) or not isinstance(suffix, str):
        raise ValueError("Selector prefix and suffix must be strings")
    if max(len(prefix), len(suffix)) > 4096:
        raise ValueError("Selector context exceeds the 4096-character limit per side")
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("Quotation must contain non-whitespace text")
    if len(quote) > MAX_QUOTE_CHARS:
        raise ValueError("Quotation exceeds the 10,000-character limit")
    if profile not in {*PROFILES, "strict"}:
        raise ValueError(f"Unknown normalization profile: {profile}")
    if type(offset) is not int or not 0 <= offset <= 2_000_000:
        raise ValueError("Candidate offset must be an integer between 0 and 2,000,000")
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError("Candidate limit must be an integer between 1 and 1000")
    cluster_starts, cluster_ends = _complex_graphemes(document.text)
    if profile == "strict":
        normalized_prefix, normalized_suffix = prefix, suffix
        spans = (
            (at, at + len(quote))
            for at in occurrences(document.text, quote)
            if _whole_graphemes(at, at + len(quote), cluster_starts, cluster_ends)
            == (at, at + len(quote))
        )
        policy = {"literal": True}
    else:
        options = PROFILES[profile]
        source = normalized_text(document.text, options)
        needle = normalized_text(quote, options).text
        normalized_prefix = normalized_text(prefix, options).text
        normalized_suffix = normalized_text(suffix, options).text
        if not needle:
            raise ValueError("Quotation becomes empty under the selected normalization")

        @lru_cache(maxsize=32)
        def normalized_slice(value: str) -> str:
            return normalized_text(value, options).text

        def supported_spans():
            previous = None
            for at in occurrences(source.text, needle):
                span = to_source_span(source, at, at + len(needle))
                start, end = _whole_graphemes(span.start, span.end, cluster_starts, cluster_ends)
                if previous == (start, end):
                    continue
                literal = end - start == len(quote) and document.text.startswith(quote, start, end)
                if not literal:
                    value = document.text[start:end]
                    normalized = (
                        normalized_slice(value)
                        if len(value) <= 100_000
                        else normalized_text(value, options).text
                    )
                    # Reject partial compatibility expansions such as f inside ﬁ.
                    if normalized != needle:
                        continue
                previous = (start, end)
                yield start, end

        spans = supported_spans()
        policy = asdict(options)
    paragraphs = _paragraphs(document.text)
    paragraph_starts = [a for a, _b in paragraphs]
    candidates = []
    count = 0
    payload = 0
    budget_full = False

    def context_evidence(start: int, end: int) -> dict:
        if profile == "strict":
            before, after, text = start, end, document.text
        else:
            before = bisect.bisect_left(source.src_start, start)
            after = bisect.bisect_right(source.src_end, end)
            text = source.text
            # The selected normalization trims context edges too. Preserve
            # literal whitespace distinctions only in the strict profile.
            while before and text[before - 1].isspace():
                before -= 1
            while after < len(text) and text[after].isspace():
                after += 1
        prefix_match = (
            (
                document.text.endswith(prefix, 0, start)
                or text.endswith(normalized_prefix, 0, before)
            )
            if normalized_prefix
            else None
        )
        suffix_match = (
            (document.text.startswith(suffix, end) or text.startswith(normalized_suffix, after))
            if normalized_suffix
            else None
        )
        evidence = [value for value in (prefix_match, suffix_match) if value is not None]
        return {
            "prefix_matches": prefix_match,
            "suffix_matches": suffix_match,
            "agrees": all(evidence) if evidence else None,
        }

    for index, (start, end) in enumerate(spans, 1):
        count = index
        if index <= offset or len(candidates) >= limit or budget_full:
            continue
        found = document.text[start:end]
        candidate = {
            "index": index,
            "id": candidate_id(document, start, end),
            "start": start,
            "end": end,
            "text": found,
            "method": "exact" if found == quote else "normalized",
            "locations": locations(document, start, end),
            "context_evidence": context_evidence(start, end),
            "context": _context(document.text, paragraphs, paragraph_starts, start, end),
            "differences": differences(quote, found),
        }
        size = len(json.dumps(candidate, ensure_ascii=False).encode("utf-8"))
        if candidates and payload + size > MAX_REVIEW_PAYLOAD:
            budget_full = True
            continue
        candidates.append(candidate)
        payload += size
    next_offset = offset + len(candidates)
    return {
        "schema": "quote-locator.review/v1",
        "document": document.metadata(),
        "query": {
            "text": quote,
            "prefix": prefix,
            "suffix": suffix,
            "profile": profile,
            "normalization": policy,
            "engines": {
                name: importlib.metadata.version(name)
                for name in ("reanchor", "regex", "rapidfuzz")
            },
            "unicode_data_version": unicodedata.unidata_version,
            "normalization_adapter": "literal"
            if profile == "strict"
            else "reanchor-whitespace-map-v1",
        },
        "status": "unmatched" if count == 0 else "unique" if count == 1 else "ambiguous",
        "confirmed": False,
        "candidate_count": count,
        "truncated": len(candidates) < count,
        "candidate_limit": limit,
        "window": {
            "offset": offset,
            "limit": limit,
            "returned": len(candidates),
            "next_offset": next_offset if next_offset < count else None,
            "payload_limited": budget_full,
        },
        "candidates": candidates,
        "note": "Matches are textual evidence only. Select a candidate after checking context.",
    }
