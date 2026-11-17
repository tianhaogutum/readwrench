"""Use FTS5 for candidates, then select a literal evidence cover."""

import bisect
import heapq
import itertools
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .model import CluejoinError, line_break_ends, normalize, normalize_mapped, string

UNIT_ORDER = {"page": 0, "paragraph": 1, "line": 2}


@dataclass(frozen=True, slots=True)
class Hit:
    group: int
    term: str
    row: sqlite3.Row
    order: int
    start: int
    end: int
    line: int | None
    line_end: int | None

    @property
    def position(self) -> tuple[int, int]:
        return self.order, self.start


def validate_groups(groups: object) -> list[list[str]]:
    if not isinstance(groups, list) or not 2 <= len(groups) <= 3:
        raise CluejoinError("provide exactly two or three clue groups")
    output = []
    for group in groups:
        if not isinstance(group, list) or not 1 <= len(group) <= 32:
            raise CluejoinError("each clue group must be an array of 1 to 32 literal alternatives")
        distinct = {}
        for term in group:
            term = string(term, "clue", maximum=1024)
            distinct.setdefault(normalize(term), term)
        output.append(list(distinct.values()))
    return output


def candidate_predicate(terms: Sequence[str]) -> tuple[str, list[str]]:
    """FTS5 cannot match fewer than three characters; exact instr() fills that gap."""
    long_terms = []
    short_terms = []
    for term in terms:
        normalized = normalize(term)
        (long_terms if len(normalized) >= 3 else short_terms).append(normalized)
    clauses = []
    parameters = []
    if long_terms:
        clauses.append("id IN (SELECT rowid FROM fragment_fts WHERE fragment_fts MATCH ?)")
        # Quoting is FTS5 quoting, not SQL interpolation. SQL parameters carry the expression.
        parameters.append(" OR ".join('"' + term.replace('"', '""') + '"' for term in long_terms))
    for term in short_terms:
        clauses.append("instr(norm, ?) > 0")
        parameters.append(term)
    return "(" + " OR ".join(clauses) + ")", parameters


def _term_hits(
    row: sqlite3.Row, order: int, group: int, term: str,
    norm: str, starts: Sequence[int], ends: Sequence[int],
    line_ends: Sequence[int],
) -> Iterator[tuple[int, int, int, str, Hit]]:
    needle = normalize(term)
    offset = 0
    while (found := norm.find(needle, offset)) != -1:
        finish = found + len(needle)
        # Match full original characters, never just half of a casefold expansion.
        if (found == 0 or starts[found] != starts[found - 1]) and (
            finish == len(norm) or ends[finish - 1] != ends[finish]
        ):
            start = starts[found]
            line = row["line"]
            line_end = line
            if line is not None:
                line += bisect.bisect_right(line_ends, start)
                line_end += bisect.bisect_right(line_ends, ends[finish - 1] - 1)
            hit = Hit(group, term, row, order, start, ends[finish - 1], line, line_end)
            yield hit.end, hit.start, group, term, hit
        offset = found + 1  # overlapping literal occurrences are significant


def fragment_hits(row: sqlite3.Row, order: int, groups: list[list[str]]) -> Iterator[Hit]:
    norm, starts, ends = normalize_mapped(row["text"])
    line_ends = line_break_ends(row["text"], row["origin_kind"]) if row["line"] else []
    streams = [
        _term_hits(row, order, group, term, norm, starts, ends, line_ends)
        for group, terms in enumerate(groups) for term in terms
    ]
    for *_, hit in heapq.merge(*streams, key=lambda item: item[:4]):
        yield hit


def _span(hits: Sequence[Hit], field: str) -> int | None:
    if field == "line":
        if any(hit.line is None for hit in hits):
            return None
        return max(hit.line_end for hit in hits) - min(hit.line for hit in hits)
    values = [hit.row[field] for hit in hits]
    return max(values) - min(values) if all(value is not None for value in values) else None


def _cover_score(hits: Sequence[Hit], axis: str) -> tuple[int, int, int, int, int]:
    first = min(hits, key=lambda hit: hit.position)
    last = max(hits, key=lambda hit: (hit.order, hit.end))
    # Same-coordinate ties use source fragment order, then actual character extent.
    return (
        _span(hits, axis),
        last.order - first.order,
        last.row["text_offset"] + last.end - first.row["text_offset"] - first.start,
        first.order,
        first.start,
    )


def select_cover(
    rows: Iterator[sqlite3.Row], groups: list[list[str]], axis: str
) -> tuple[list[Hit], tuple[int, int, int, int, int]] | None:
    """Sweep intervals by end position, keeping the latest start for each group.

    For a fixed right edge, choosing each group's latest available start maximizes
    the left edge of a covering window. This avoids a Cartesian product of hits.
    Only the current and best two/three-item covers remain in memory.
    """
    latest: dict[int, Hit] = {}
    best: list[Hit] | None = None
    best_score = None
    for row in rows:
        for hit in fragment_hits(row, row["position"], groups):
            previous = latest.get(hit.group)
            if previous is not None and previous.position >= hit.position:
                continue
            latest[hit.group] = hit
            if len(latest) != len(groups):
                continue
            cover = [latest[group] for group in range(len(groups))]
            score = _cover_score(cover, axis)
            if best_score is None or score < best_score:
                best, best_score = cover, score
    return (best, best_score) if best is not None else None


def evidence(hit: Hit, context: int) -> dict:
    start = max(0, hit.start - context)
    end = min(len(hit.row["text"]), hit.end + context)
    return {
        "group": hit.group + 1,
        "matched_term": hit.term,
        "fragment_id": hit.row["fragment_id"],
        "page": hit.row["page"],
        "paragraph": hit.row["paragraph"],
        "line": hit.line,
        "line_end": hit.line_end,
        "fragment_line": hit.row["line"],
        "match": hit.row["text"][hit.start:hit.end],
        "match_start": hit.start,
        "match_end": hit.end,
        "context": hit.row["text"][start:end],
        "context_start": start,
        "highlight_start": hit.start - start,
        "highlight_end": hit.end - start,
        "origin": {
            "input": hit.row["origin"], "record": hit.row["record"],
            "record_end": hit.row["record_end"],
            "kind": hit.row["origin_kind"],
        },
    }


def search(
    connection: sqlite3.Connection, groups: list[list[str]], *, limit: int = 20, context: int = 100
) -> dict:
    groups = validate_groups(groups)
    if not 1 <= limit <= 1000 or not 0 <= context <= 10_000:
        raise CluejoinError("limit must be 1..1000 and context must be 0..10000")
    predicates = [candidate_predicate(group) for group in groups]
    parameters = [param for _, params in predicates for param in params]
    intersection = " INTERSECT ".join(
        "SELECT document_id FROM fragments WHERE " + predicate for predicate, _ in predicates
    )
    selected: list[tuple[tuple, dict]] = []
    count = 0
    any_predicate = " OR ".join(predicate for predicate, _ in predicates)
    # One statement lets SQLite materialize each FTS candidate list once. A query
    # per document would rebuild those global lists for every matching material.
    rows = connection.execute(
        "WITH eligible AS (" + intersection + ") "
        "SELECT fragments.*, documents.source, documents.axis FROM fragments "
        "JOIN documents USING (document_id) "
        "WHERE document_id IN (SELECT document_id FROM eligible) AND (" + any_predicate + ") "
        "ORDER BY document_id, position",
        parameters * 2,
    )
    for document_id, document_rows in itertools.groupby(rows, key=lambda row: row["document_id"]):
        first = next(document_rows)
        axis = first["axis"]
        if axis not in UNIT_ORDER:
            raise CluejoinError("index has invalid coordinate metadata; rebuild the index")
        # Resume the partially consumed group after peeking at its metadata.
        found = select_cover(
            itertools.chain([first], document_rows), groups, axis  # noqa: B031
        )
        if found is None:
            continue
        hits, score = found
        count += 1
        result = {
            "document_id": document_id, "source": first["source"],
            "distance_unit": axis, "distance": score[0],
            "page_span": _span(hits, "page"),
            "paragraph_span": _span(hits, "paragraph"),
            "line_span": _span(hits, "line"),
            "evidence": [evidence(hit, context) for hit in hits],
        }
        key = (UNIT_ORDER[axis], *score, document_id)
        bisect.insort(selected, (key, result), key=lambda item: item[0])
        if len(selected) > limit:
            selected.pop()
    return {
        "schema_version": 1,
        "groups": groups,
        "matched_documents": count,
        "returned_documents": len(selected),
        "ranking": "unit (page, paragraph, line), coordinate span, fragment span, character extent",
        "documents": [result for _, result in selected],
    }
