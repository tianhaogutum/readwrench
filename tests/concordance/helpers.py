"""Paths and dependency guards shared by the ``readwrench concordance`` tests."""
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EXAMPLES = REPO / "examples" / "concordance"
GUTENBERG = EXAMPLES / "gutenberg"
SCHEMA = HERE / "fixtures" / "schema"

# Import names of the readwrench[concordance] extra (plus Tornado, a Taguette dependency).
EPUB_DEPENDENCIES = ("ebooklib", "bs4", "html5lib", "lxml")
# pkg_resources first: with setuptools>=81 Taguette 1.5.2 cannot be imported at all, and the
# suites skip instead of failing at collection.
ALL_DEPENDENCIES = (*EPUB_DEPENDENCIES, "jsonschema", "jinja2", "pkg_resources", "taguette")


def require(*modules: str) -> None:
    """Skip the calling test module when an optional dependency is missing."""
    for module in modules or ALL_DEPENDENCIES:
        pytest.importorskip(module)
