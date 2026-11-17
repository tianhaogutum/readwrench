"""Confirmation and reproducible verification, without trusting edited review offsets."""

from __future__ import annotations

import hashlib
from urllib.parse import quote as quote_uri_path
from urllib.parse import urlsplit

from reanchor import DescribeOptions, TextQuoteSelector, describe_quote

from .documents import Document
from .matching import locate, occurrences


def _same_json(actual, expected) -> bool:
    """JSON booleans, integers and floats must not alias through Python equality."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same_json(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same_json(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


def _check_document(document: Document, stored: dict) -> None:
    if not isinstance(stored, dict):
        raise ValueError("Record has no document metadata")
    uri, name = stored.get("uri"), stored.get("name")
    if not isinstance(uri, str) or not isinstance(name, str) or not name:
        raise ValueError("Document URI and name must be strings")
    if urlsplit(uri).scheme != "file" or not urlsplit(uri).path:
        raise ValueError("Document URI must be a local file navigation hint")
    if not _same_json(stored.get("sha256"), document.sha256):
        raise ValueError("Source bytes changed: SHA-256 does not match the reviewed document")
    if not _same_json(stored.get("text_sha256"), document.text_sha256):
        raise ValueError("Extracted text changed: text SHA-256 does not match")
    if not _same_json(stored.get("extraction"), document.extraction):
        raise ValueError("Extraction policy or reader versions changed; create a fresh review")
    metadata = document.metadata()
    for key in ("media_type", "characters", "units", "warnings"):
        if not _same_json(stored.get(key), metadata[key]):
            raise ValueError(f"Document {key} metadata was modified or extraction changed")


def _query(record: dict) -> dict:
    query = record.get("query")
    if not isinstance(query, dict) or not isinstance(query.get("text"), str):
        raise ValueError("Record has no valid quotation")
    if not isinstance(query.get("profile"), str):
        raise ValueError("Record has no valid normalization profile")
    if any(not isinstance(query.get(key), str) for key in ("prefix", "suffix")):
        raise ValueError("Record has no valid selector context")
    return query


def _review_again(document: Document, record: dict, window: dict | None = None) -> dict:
    query = _query(record)
    window = record.get("window") if window is None else window
    if not isinstance(window, dict):
        raise ValueError("Record has no candidate window")
    return locate(
        document,
        TextQuoteSelector(exact=query["text"], prefix=query["prefix"], suffix=query["suffix"]),
        profile=query["profile"],
        offset=window.get("offset"),
        limit=window.get("limit"),
    )


def _anchor(document: Document, review: dict, candidate: dict) -> dict:
    start, end = candidate["start"], candidate["end"]
    if end - start < 128:
        selector = describe_quote(document.text, start, end, DescribeOptions(32, 256))
    else:
        # The position is authoritative for the hash-bound rendition. A bounded
        # context window avoids a second expensive global overlap search inside
        # reanchor's descriptor. Global selector ambiguity is measured below.
        window_start = max(0, start - 256)
        window_end = min(len(document.text), end + 256)
        selector = describe_quote(
            document.text[window_start:window_end],
            start - window_start,
            end - window_start,
            DescribeOptions(32, 256),
        )
    context_matches = sum(
        1
        for at in occurrences(document.text, selector.exact)
        if (
            not selector.prefix
            or at >= len(selector.prefix)
            and document.text.startswith(selector.prefix, at - len(selector.prefix), at)
        )
        and document.text.startswith(selector.suffix or "", at + len(selector.exact))
    )
    quote_selector = {
        "type": "TextQuoteSelector",
        "exact": selector.exact,
        "prefix": selector.prefix,
        "suffix": selector.suffix,
    }
    readium = []
    annotation_digest = hashlib.sha256(
        (candidate["id"] + "\0" + review["query"]["text"]).encode("utf-8")
    ).hexdigest()
    for location in candidate["locations"]:
        unit = document.units[location["number"] - 1]
        local_start, local_end = location["start"], location["end"]
        text = document.text[unit.start : unit.end]
        is_epub = document.media_type == "application/epub+zip"
        loc = {
            "href": quote_uri_path(unit.href, safe="/") if is_epub else document.path.as_uri(),
            "type": "application/xhtml+xml" if is_epub else document.media_type,
            "title": unit.label,
            "locations": {"progression": local_start / max(1, len(text))},
            "text": {
                "before": text[max(0, local_start - 32) : local_start],
                "highlight": text[local_start:local_end],
                "after": text[local_end : local_end + 32],
            },
        }
        if document.media_type == "application/pdf":
            # A page-local text fraction is not a progression in the whole PDF.
            loc["locations"] = {"fragments": [f"page={unit.number}"]}
        readium.append(loc)
    return {
        "schema": "quote-locator.anchor/v1",
        "id": f"urn:sha256:{candidate['id']}",
        "confirmed": True,
        "document": document.metadata(),
        "query": review["query"],
        "review_summary": {
            "status": review["status"],
            "candidate_count": review["candidate_count"],
            "window": review["window"],
        },
        "selection": candidate,
        "selector_status": {
            "text_quote_unique": context_matches == 1,
            "matching_contexts": context_matches,
            "position_required": context_matches != 1,
        },
        "annotation": {
            "@context": "http://www.w3.org/ns/anno.jsonld",
            "id": f"urn:sha256:{annotation_digest}",
            "type": "Annotation",
            "motivation": "commenting",
            "body": {
                "type": "TextualBody",
                "value": review["query"]["text"],
                "format": "text/plain",
            },
            "target": {
                "source": f"urn:sha256:{document.text_sha256}",
                "format": "text/plain",
                "selector": [
                    quote_selector,
                    {"type": "TextPositionSelector", "start": start, "end": end},
                ],
            },
        },
        "readium": readium,
        "representation_note": (
            "W3C positions target the extracted text SHA-256 URN, not PDF bytes or HTML DOM. "
            "The document metadata records the source and extraction policy. Readium locators "
            "are hints for the original resource; no EPUB CFI or printed page is invented."
        ),
    }


def confirm(document: Document, review: dict, candidate: str | int) -> dict:
    if not isinstance(review, dict) or review.get("schema") != "quote-locator.review/v1":
        raise ValueError("Expected a quote-locator.review/v1 record")
    if review.get("confirmed") is not False:
        raise ValueError("Expected an unconfirmed review; use an anchor with verify")
    _check_document(document, review.get("document"))
    query = _query(review)
    regenerated = _review_again(document, review)
    if not _same_json(regenerated["query"], query):
        raise ValueError("Review normalization metadata was modified")
    selected = next(
        (
            entry
            for entry in regenerated["candidates"]
            if (type(candidate) is int and entry["index"] == candidate)
            or (isinstance(candidate, str) and entry["id"] == candidate)
        ),
        None,
    )
    if selected is None:
        raise ValueError(
            "Candidate does not exist in this review; use its displayed index or full ID"
        )
    stored = review.get("candidates")
    if not _same_json(stored, regenerated["candidates"]):
        raise ValueError("Review candidate was modified; generate a fresh review")
    for key in ("status", "candidate_count", "truncated", "window", "candidate_limit"):
        if not _same_json(review.get(key), regenerated[key]):
            raise ValueError("Review summary was modified; generate a fresh review")
    return _anchor(document, regenerated, selected)


def check_review(document: Document, review: dict) -> None:
    """Raise ValueError unless a saved, unconfirmed review still matches ``document``.

    The same checks ``confirm`` runs before it exports a locator, without selecting a
    candidate. Used by ``readwrench check``; nothing is written.
    """
    if not isinstance(review, dict) or review.get("schema") != "quote-locator.review/v1":
        raise ValueError("Expected a quote-locator.review/v1 record")
    if review.get("confirmed") is not False:
        raise ValueError("Expected an unconfirmed review; use an anchor with verify")
    _check_document(document, review.get("document"))
    query = _query(review)
    regenerated = _review_again(document, review)
    if not _same_json(regenerated["query"], query):
        raise ValueError("Review normalization metadata was modified")
    if not _same_json(review.get("candidates"), regenerated["candidates"]):
        raise ValueError("Review candidate was modified; generate a fresh review")
    for key in ("status", "candidate_count", "truncated", "window", "candidate_limit"):
        if not _same_json(review.get(key), regenerated[key]):
            raise ValueError("Review summary was modified; generate a fresh review")


def verify(document: Document, anchor: dict) -> dict:
    """Return a negative result for stale/corrupt records; never silently reanchor."""
    try:
        if not isinstance(anchor, dict) or anchor.get("schema") != "quote-locator.anchor/v1":
            raise ValueError("Expected a quote-locator.anchor/v1 record")
        if anchor.get("confirmed") is not True:
            raise ValueError("Anchor is not confirmed")
        _check_document(document, anchor.get("document"))
        summary = anchor.get("review_summary")
        if not isinstance(summary, dict):
            raise ValueError("Anchor has no review summary")
        review = _review_again(document, anchor, summary.get("window"))
        selection = anchor.get("selection")
        if not isinstance(selection, dict) or not isinstance(selection.get("id"), str):
            raise ValueError("Anchor selection is missing or invalid")
        found = next(
            (entry for entry in review["candidates"] if entry["id"] == selection["id"]), None
        )
        if found is None:
            raise ValueError("Confirmed span no longer matches the quote")
        expected = _anchor(document, review, found)
        for key in (
            "id",
            "query",
            "review_summary",
            "selection",
            "selector_status",
            "annotation",
            "representation_note",
        ):
            if not _same_json(anchor.get(key), expected[key]):
                raise ValueError(f"Anchor {key} was modified or is inconsistent with the source")
        # A file may move: compare locators after substituting the stored original URI.
        original_uri = anchor["document"].get("uri")
        for loc in expected["readium"]:
            if loc["href"] == document.path.as_uri():
                loc["href"] = original_uri
        if not _same_json(anchor.get("readium"), expected["readium"]):
            raise ValueError("Anchor Readium locators were modified")
        return {
            "valid": True,
            "id": anchor["id"],
            "reason": "Source bytes, text, policy and selectors match",
        }
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return {"valid": False, "reason": str(exc)}
