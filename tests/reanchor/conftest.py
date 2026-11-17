from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Each test module skips itself when a dependency of the ``reanchor`` extra is missing.


def _load_make_demo():
    # The demo builder ships as a user-facing example, not as package code.
    path = ROOT / "examples" / "reanchor" / "make_demo.py"
    spec = importlib.util.spec_from_file_location("reanchor_make_demo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pair(tmp_path):
    pytest.importorskip("reportlab")
    return _load_make_demo().build_pair(tmp_path / "input")


@pytest.fixture
def upstream():
    return Path(__file__).resolve().parent / "fixtures" / "upstream"
