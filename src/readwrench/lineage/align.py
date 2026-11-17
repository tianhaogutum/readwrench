"""Paragraph alignment between consecutive captures and paragraph lineages.

Exact sequence anchors come from the jsdiff port; unique exact paragraphs that moved keep
their identity; edited paragraphs are matched conservatively inside replacement regions.
Uncertain identity is never asserted.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
import time

from .jsutil import js_len, number
from .segment import segments
from .textdiff import diff_arrays


def tokens(text: str) -> list[str]:
    return [segment for segment, _ in segments(text)]


def inline_diff(before: str, after: str) -> list[dict]:
    result = diff_arrays(tokens(before), tokens(after), timeout_ms=100, max_edit_length=10_000)
    if result is None:
        # A bounded differ must still preserve both full texts on fallback.
        return [{"text": before, "added": False, "removed": True},
                {"text": after, "added": True, "removed": False}]
    return [{"text": "".join(part["value"]), "added": part["added"], "removed": part["removed"]}
            for part in result]


class _Print:
    __slots__ = ("counts", "length", "parts", "lowered", "characters")

    def __init__(self, parts, lowered=None):
        self.parts = parts
        self.length = len(parts)
        self.lowered = lowered
        self.counts: dict[str, int] = {}
        for part in parts:
            self.counts[part] = self.counts.get(part, 0) + 1
        self.characters = None


def _fingerprint(text: str) -> _Print:
    lowered = text.lower()
    return _Print([segment for segment, word in segments(lowered) if word and segment], lowered)


def _character_print(fingerprint: _Print) -> _Print:
    if fingerprint.characters is None:
        fingerprint.characters = _Print(list(fingerprint.lowered))
    return fingerprint.characters


def _similarity(a: _Print, b: _Print) -> float:
    # One shared tokenization for the pair: independent per-text fallback made a
    # four-word heading incomparable with its five-word revision.
    if min(a.length, b.length) < 5:
        a, b = _character_print(a), _character_print(b)
    if not a.length or not b.length:
        return 0
    common = sum(min(count, b.counts.get(token, 0)) for token, count in a.counts.items())
    if 2 * common / (a.length + b.length) < 0.52:
        return 0
    # Bag overlap is a cheap upper bound, not proof of an edit: verify the order.
    changes = diff_arrays(a.parts, b.parts, timeout_ms=10, max_edit_length=4000)
    if changes is None:
        return 0
    in_order = sum(change["count"] for change in changes if not change["added"] and not change["removed"])
    return 2 * in_order / (a.length + b.length)


def _occurrences(blocks) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for index, block in enumerate(blocks):
        result.setdefault(block["text"], []).append(index)
    return result


def align_blocks(before: list[dict], after: list[dict]) -> dict:
    warnings: list[str] = []
    pairs: dict[int, dict] = {}  # current index -> previous index, match type
    old_used: set[int] = set()
    regions: list[dict] = []
    old_index = new_index = 0
    pending = None
    changes = diff_arrays([b["text"] for b in before], [b["text"] for b in after],
                          timeout_ms=200, max_edit_length=4000)
    if changes is None:
        warnings.append("Paragraph sequence diff exceeded its work budget; unique exact matches and "
                        "conservative replacements were used.")
        regions.append({"old": list(range(len(before))), "next": list(range(len(after)))})
    else:
        for change in changes:
            if not change["added"] and not change["removed"]:
                if pending:
                    regions.append(pending)
                    pending = None
                for _ in range(change["count"]):
                    pairs[new_index] = {"old": old_index, "type": "unchanged", "confidence": 1}
                    old_used.add(old_index)
                    old_index += 1
                    new_index += 1
            else:
                pending = pending or {"old": [], "next": []}
                if change["removed"]:
                    for _ in range(change["count"]):
                        pending["old"].append(old_index)
                        old_index += 1
                if change["added"]:
                    for _ in range(change["count"]):
                        pending["next"].append(new_index)
                        new_index += 1
        if pending:
            regions.append(pending)

    # Only unique exact paragraphs can be moved with certainty. Repeated boilerplate is
    # left to sequence alignment instead of arbitrarily stealing an identity.
    old_occurrences, new_occurrences = _occurrences(before), _occurrences(after)
    for text, indices in new_occurrences.items():
        old = old_occurrences.get(text)
        if len(indices) == 1 and old is not None and len(old) == 1 and indices[0] not in pairs \
                and old[0] not in old_used:
            pairs[indices[0]] = {"old": old[0], "type": "moved", "confidence": 1}
            old_used.add(old[0])

    old_prints: dict[int, _Print] = {}
    new_prints: dict[int, _Print] = {}

    def get_print(blocks, cache, index):
        if index not in cache:
            cache[index] = _fingerprint(blocks[index]["text"])
        return cache[index]

    budget = 10_000
    deadline = time.monotonic() + 0.2
    for region in regions:
        old = [i for i in region["old"] if i not in old_used]
        new = [i for i in region["next"] if i not in pairs]
        if not old or not new:
            continue
        if len(old) * len(new) > budget:
            warnings.append("A large replacement exceeded the 10,000 candidate budget; unmatched blocks are "
                            "shown as additions/removals.")
            continue
        budget -= len(old) * len(new)
        candidates = []
        timed_out = False
        for a in old:
            for b in new:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if len(old_occurrences[before[a]["text"]]) > 1 or len(new_occurrences[after[b]["text"]]) > 1:
                    continue
                len_a, len_b = js_len(before[a]["text"]), js_len(after[b]["text"])
                if min(len_a, len_b) / max(len_a, len_b) < 0.4:
                    continue
                score = _similarity(get_print(before, old_prints, a), get_print(after, new_prints, b))
                # Near-threshold runners-up take part in the ambiguity check below, even
                # when they cannot independently qualify as a modification.
                if score >= 0.52:
                    candidates.append({"old": a, "next": b, "score": score})
            if timed_out:
                break
        if timed_out:
            warnings.append("Order-aware replacement matching exceeded its 200 ms work budget; the incomplete "
                            "region remains additions/removals.")
            continue
        candidates.sort(key=lambda c: (-c["score"], c["old"], c["next"]))
        for candidate in candidates:
            if candidate["score"] < 0.6:
                continue
            if candidate["old"] in old_used or candidate["next"] in pairs:
                continue
            ambiguous = any(other is not candidate
                            and (other["old"] == candidate["old"] or other["next"] == candidate["next"])
                            and other["score"] >= candidate["score"] - 0.08 for other in candidates)
            if ambiguous:
                continue
            pairs[candidate["next"]] = {"old": candidate["old"], "type": "modified",
                                        "confidence": number(_round3(candidate["score"]))}
            old_used.add(candidate["old"])
    return {"pairs": pairs, "removed": [i for i in range(len(before)) if i not in old_used], "warnings": warnings}


def _round3(value: float) -> float:
    """``Number(value.toFixed(3))`` (rounds the exact binary value half up)."""
    return float(Decimal(value).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def create_lineage(captures: list[dict]) -> dict:
    serial = 0

    def next_id() -> str:
        nonlocal serial
        serial += 1
        return f"p{serial:05d}"

    lineages: dict[str, dict] = {}
    transitions: list[dict] = []
    first = captures[0]
    for index, block in enumerate(first["blocks"]):
        block["lineageId"] = next_id()
        lineages[block["lineageId"]] = {"id": block["lineageId"], "observations": [
            {"captureId": first["id"], "index": index, "state": "first-observed"}], "removals": []}
    for position in range(1, len(captures)):
        previous, current = captures[position - 1], captures[position]
        alignment = align_blocks(previous["blocks"], current["blocks"])
        changes = []
        counts = {"added": 0, "removed": 0, "modified": 0, "moved": 0, "unchanged": 0}
        for new_index, block in enumerate(current["blocks"]):
            pair = alignment["pairs"].get(new_index)
            old_block = previous["blocks"][pair["old"]] if pair else None
            block["lineageId"] = (old_block or {}).get("lineageId") or next_id()
            kind = pair["type"] if pair else "added"
            counts[kind] += 1
            if block["lineageId"] not in lineages:
                lineages[block["lineageId"]] = {"id": block["lineageId"], "observations": [], "removals": []}
            lineages[block["lineageId"]]["observations"].append(
                {"captureId": current["id"], "index": new_index, "state": kind})
            if kind != "unchanged":
                changes.append({
                    "type": kind, "lineageId": block["lineageId"],
                    "oldIndex": pair["old"] if pair else None, "newIndex": new_index,
                    "before": old_block["text"] if old_block else None, "after": block["text"],
                    "confidence": pair["confidence"] if pair else None,
                    "inline": inline_diff(old_block["text"], block["text"])
                    if old_block and kind == "modified" else None,
                })
        for index in alignment["removed"]:
            block = previous["blocks"][index]
            counts["removed"] += 1
            changes.append({"type": "removed", "lineageId": block["lineageId"], "oldIndex": index, "newIndex": None,
                            "before": block["text"], "after": None, "confidence": None, "inline": None})
            lineages[block["lineageId"]]["removals"].append(
                {"captureId": current["id"], "previousCaptureId": previous["id"], "index": index})
        title_change = None
        if previous.get("title") != current.get("title"):
            title_change = {"before": previous.get("title"), "after": current.get("title"),
                            "inline": inline_diff(previous.get("title") or "", current.get("title") or "")}
        before_at = previous["captureTime"].get("at")
        after_at = current["captureTime"].get("at")
        chronological = bool(before_at and after_at and before_at < after_at)
        extraction_warnings = []
        old_extraction, new_extraction = previous.get("extraction"), current.get("extraction")
        if old_extraction and new_extraction and (
                old_extraction.get("method") != new_extraction.get("method")
                or old_extraction.get("selector") != new_extraction.get("selector")):
            extraction_warnings.append("Extraction method or selector differs between these captures; review the "
                                       "extracted scopes before interpreting text differences.")
        transitions.append({
            "from": previous["id"], "to": current["id"], "counts": counts, "changes": changes,
            "warnings": alignment["warnings"] + extraction_warnings,
            "titleChange": title_change,
            "observedInterval": {
                "chronological": chronological,
                "after": before_at if chronological else None,
                "by": after_at if chronological else None,
                "meaning": "Difference observed between these captures; webpage edit time is unknown."
                if chronological else "No strictly increasing chronological interval is established; this "
                "comparison follows supplied capture order. Webpage edit time is unknown.",
            },
        })
    return {"transitions": transitions, "lineages": list(lineages.values())}
