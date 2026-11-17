"""Imported first by every test module that needs the ``quote`` extra: skips the module
cleanly when one of its runtime dependencies is not installed."""

import pytest

for _name in ("reanchor", "bs4", "pypdf", "ebooklib", "lxml", "regex", "rapidfuzz"):
    pytest.importorskip(_name)
