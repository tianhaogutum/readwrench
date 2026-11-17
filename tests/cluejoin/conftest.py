import sqlite3
from pathlib import Path

import pytest

from readwrench.cluejoin.importers import read_fragments
from readwrench.cluejoin.store import build_index, open_index


def _has_fts5_trigram() -> bool:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(x, tokenize='trigram')")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()


# `readwrench clues` needs SQLite FTS5 with the trigram tokenizer (SQLite >= 3.34).
HAS_TRIGRAM = _has_fts5_trigram()


def pytest_collection_modifyitems(config, items):
    if HAS_TRIGRAM:
        return
    skip = pytest.mark.skip(reason="SQLite FTS5 trigram tokenizer (SQLite >= 3.34) is unavailable")
    here = Path(__file__).resolve().parent
    for item in items:
        if here in Path(item.path).resolve().parents:
            item.add_marker(skip)


@pytest.fixture
def indexed(tmp_path):
    connections = []

    def create(fragments=None, *, inputs=None, format="jsonl"):
        path = tmp_path / f"index-{len(connections)}.sqlite"
        if fragments is None:
            fragments = read_fragments([Path(item) for item in inputs], format)
        build_index(path, fragments)
        connection = open_index(path)
        assert isinstance(connection, sqlite3.Connection)
        connections.append(connection)
        return connection

    yield create
    for connection in connections:
        connection.close()
