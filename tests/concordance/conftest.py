"""Fixtures for the ``readwrench concordance`` suite; paths live in :mod:`.helpers`."""
from pathlib import Path
import json

import pytest

from .helpers import GUTENBERG

HERE = Path(__file__).resolve().parent

# Taguette 1.5.2 imports pkg_resources through its pinned OpenTelemetry release; the
# deprecation warnings it triggers are upstream noise, not failures of this tool.
_UPSTREAM_WARNINGS = (
    "ignore:pkg_resources is deprecated as an API:UserWarning",
    "ignore:Deprecated call to.*:DeprecationWarning",
)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if HERE in Path(str(item.path)).resolve().parents:
            for spec in _UPSTREAM_WARNINGS:
                item.add_marker(pytest.mark.filterwarnings(spec))


@pytest.fixture
def make_book(tmp_path):
    from ebooklib import epub

    def make(name="test", content=None, *, version="3.0", title=None, filename="text/chapter.xhtml", direction=None):
        book = epub.EpubBook()
        book.version = version
        book.set_identifier("shared-upstream-identifier")
        book.set_title(title or name)
        book.set_language("en")
        book.add_author("Fixture Author")
        if direction:
            book.direction = direction
        chapter = epub.EpubHtml(title="First chapter", file_name=filename)
        chapter.content = content or '<h1 id="chapter-one">Chapter One</h1><p>Power and authority. Liberty and freedom. Justice.</p>'
        book.add_item(chapter)
        book.toc = [chapter]
        book.spine = [chapter]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        path = tmp_path / f"{name}.epub"
        epub.write_epub(path, book)
        return path
    return make


@pytest.fixture
def concepts_file(tmp_path):
    def make(concepts=None):
        path = tmp_path / "concepts.json"
        path.write_text(json.dumps({"concepts": concepts or [
            {"id": "power", "label": "Power", "aliases": ["power", "authority"]},
            {"id": "liberty", "label": "Liberty", "aliases": ["liberty", "freedom"]},
        ]}), encoding="utf-8")
        return path
    return make


@pytest.fixture
def simple_workspace(tmp_path, make_book, concepts_file):
    from readwrench.concordance.workspace import initialize
    book = make_book()
    path = tmp_path / "workspace"
    initialize([book], concepts_file(), path)
    return path, book


@pytest.fixture
def real_books():
    return [GUTENBERG / name for name in ["prince.epub", "government.epub", "republic.epub"]]
