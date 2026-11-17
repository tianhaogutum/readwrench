"""Fixtures for the ``readwrench pdf-pages`` suite; PDF helpers live in :mod:`.helpers`.

Test modules that need the ``pdf-pages`` extra skip themselves with
``pytest.importorskip``; ``test_check.py`` and ``test_cli.py`` need only the standard
library.
"""
from pathlib import Path

import pytest

from .helpers import write_pdf

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures():
    return FIXTURES


@pytest.fixture
def documents(tmp_path):
    a = write_pdf(
        tmp_path / "original.pdf",
        [
            "Orchards produce apples pears and summer peaches.",
            "The railway station opens at half past seven.",
            "A telescope surveys distant stars in the galaxy.",
        ],
    )
    b = write_pdf(
        tmp_path / "revised.pdf",
        [
            "Orchards produce apples pears and summer peaches.",
            "An inserted notice about new council regulations.",
            "The railway station opens at half past eight.",
            "A telescope surveys distant stars in the galaxy.",
        ],
    )
    return a, b
