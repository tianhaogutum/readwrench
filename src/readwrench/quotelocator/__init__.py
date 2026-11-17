"""``readwrench quote``: recover a lost quotation in a local book or paper.

Review every exact or normalized occurrence with its surrounding paragraphs, confirm
one explicitly, then export and re-verify a locator bound to the source's SHA-256.
Formerly the standalone ``quote-locator`` tool; record schema ids
(``quote-locator.review/v1``, ``quote-locator.anchor/v1``) are unchanged.

The library API is loaded lazily, so importing this package does not import its
optional dependencies (reanchor, Beautiful Soup, pypdf, EbookLib, regex, RapidFuzz).
"""

from importlib import import_module

_EXPORTS = {
    "Document": "documents",
    "DocumentError": "documents",
    "load_document": "documents",
    "locate": "matching",
    "confirm": "records",
    "verify": "records",
    "import_selector": "selectors",
}

__all__ = [
    "Document",
    "DocumentError",
    "load_document",
    "locate",
    "confirm",
    "verify",
    "import_selector",
]


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value
