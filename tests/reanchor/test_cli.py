from __future__ import annotations

import pytest

for _module in ("pymupdf", "pdfanno", "pydantic", "rapidfuzz", "regex"):
    pytest.importorskip(_module)

import json
import os
import subprocess
import sys
from pathlib import Path

from readwrench import __version__
from readwrench.reanchor.cli import main

SRC = Path(__file__).resolve().parents[2] / "src"


def run(*args):
    # Run from the source tree even when readwrench is not installed.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC), env.get("PYTHONPATH")]))
    return subprocess.run(
        [sys.executable, "-m", "readwrench", "reanchor", *map(str, args)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_cli_json_is_clean_for_malformed_annotation_fixture(upstream, tmp_path):
    source = upstream / "annots-entries-without-subtype.pdf"
    result = run("propose", source, source, "--out", tmp_path / "review")
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["annotations"] == 1


def test_cli_invalid_input_has_actionable_error(tmp_path):
    source = tmp_path / "not-a-pdf.pdf"
    source.write_text("Invalid PDF bytes")
    result = run("propose", source, source, "--out", tmp_path / "review")
    assert result.returncode == 2
    assert "readwrench reanchor:" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "review").exists()


def test_version_and_help_name_the_subcommand(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"readwrench reanchor {__version__}"
    with pytest.raises(SystemExit):
        main(["propose", "--help"])
    assert capsys.readouterr().out.startswith("usage: readwrench reanchor propose")
