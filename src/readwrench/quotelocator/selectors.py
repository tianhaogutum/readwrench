"""Small local JSON adapters for existing W3C and Readium selector vocabulary."""

from __future__ import annotations

from reanchor import TextQuoteSelector


def _text_selector(value: dict) -> TextQuoteSelector:
    fields = {name: value.get(name, "") for name in ("exact", "prefix", "suffix")}
    if any(not isinstance(text, str) for text in fields.values()):
        raise ValueError("Text quote exact/prefix/suffix fields must be strings")
    if not fields["exact"].strip():
        raise ValueError("Text quote selector must contain a non-empty exact quotation")
    return TextQuoteSelector(**fields)


def import_selector(value: dict) -> TextQuoteSelector:
    """Import one selector without fetching its source or trusting stored positions."""
    if not isinstance(value, dict):
        raise ValueError("Selector input must be a JSON object")
    if value.get("type") == "TextQuoteSelector":
        return _text_selector(value)
    if value.get("type") == "Annotation":
        targets = value.get("target", [])
        targets = targets if isinstance(targets, list) else [targets]
        if len(targets) != 1:
            raise ValueError(
                "Annotation must contain exactly one target; split compound selections"
            )
        quotes = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            selectors = target.get("selector", [])
            selectors = selectors if isinstance(selectors, list) else [selectors]
            quotes.extend(
                selector
                for selector in selectors
                if isinstance(selector, dict) and selector.get("type") == "TextQuoteSelector"
            )
        if len(quotes) != 1:
            raise ValueError(
                "Annotation must contain exactly one TextQuoteSelector; split compound selections"
            )
        return _text_selector(quotes[0])
    text = value.get("text")
    if (
        isinstance(text, dict)
        and "highlight" in text
        and isinstance(value.get("href"), str)
        and isinstance(value.get("type"), str)
    ):
        return _text_selector(
            {
                "exact": text["highlight"],
                "prefix": text.get("before", ""),
                "suffix": text.get("after", ""),
            }
        )
    raise ValueError(
        "Expected a TextQuoteSelector, one W3C Annotation target, or a Readium text locator"
    )
