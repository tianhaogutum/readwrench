"""Fixtures for the ``readwrench lineage`` suite (the tool needs only the standard library)."""
import pytest

from .helpers import ARTICLE_TEXTS, page


@pytest.fixture
def capture(tmp_path):
    """A written article page and its manifest entry; extra keys go into the entry."""
    def make(texts=ARTICLE_TEXTS, **extra):
        file = tmp_path / "page.html"
        file.write_text(page(texts), encoding="utf-8")
        return file, {"file": str(file), "inputIndex": 0, **extra}
    return make
