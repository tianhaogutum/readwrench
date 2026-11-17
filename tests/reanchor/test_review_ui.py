"""Optional DOM tests for the offline review page, run with Node.js and jsdom.

``review.html`` carries the confirmation contract in its JavaScript (every choice
starts unresolved, export of confirmed decisions is gated, imports reset the
confirmation). These tests run ``review_ui.test.cjs`` with ``node --test``; they skip
unless ``node`` is on PATH and can resolve ``jsdom`` (for example
``npm install --no-save jsdom@27.2.0`` in a scratch directory, then point
``NODE_PATH`` at its ``node_modules``).
"""

from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

import os
import shutil
import subprocess
from pathlib import Path

from readwrench.reanchor.workflow import propose

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "review_ui.test.cjs"


def node_with_jsdom() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    probe = subprocess.run(
        [node, "-e", "require.resolve('jsdom')"],
        cwd=HERE,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode != 0:
        pytest.skip("jsdom is not resolvable from Node.js (set NODE_PATH)")
    return node


def test_review_page_dom(pair, tmp_path):
    node = node_with_jsdom()
    old, new = pair
    propose(old, new, tmp_path / "review")
    plain = tmp_path / "input" / "revision.pdf"
    propose(plain, plain, tmp_path / "review-empty")
    env = dict(os.environ)
    env["REANCHOR_REVIEW_BUNDLE"] = str(tmp_path / "review")
    env["REANCHOR_REVIEW_EMPTY"] = str(tmp_path / "review-empty")
    env.setdefault("NODE_OPTIONS", "--max-old-space-size=1024")
    result = subprocess.run(
        [node, "--test", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
