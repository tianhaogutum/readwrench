"""Command-line surface that needs no PDF dependencies."""
import subprocess
import sys

import pytest

from readwrench import __version__
from readwrench.pagebridge.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"readwrench pdf-pages {__version__}"


def test_help_names_the_readwrench_command(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert out.startswith("usage: readwrench pdf-pages")
    for name in ("plan", "confirm", "export"):
        assert name in out


def test_subcommand_help_prog(capsys):
    with pytest.raises(SystemExit):
        main(["confirm", "--help"])
    assert capsys.readouterr().out.startswith("usage: readwrench pdf-pages confirm")


def test_input_errors_use_the_new_name(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    code = main(["confirm", str(missing), "--reviewer", "A. Editor", "--out",
                 str(tmp_path / "approved.json")])
    assert code == 2
    assert capsys.readouterr().err.startswith("readwrench pdf-pages: Mapping does not exist")


def test_dispatcher_routes_pdf_pages():
    result = subprocess.run(
        [sys.executable, "-m", "readwrench", "pdf-pages", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage: readwrench pdf-pages" in result.stdout


def test_subpackage_main_routes_through_dispatcher():
    result = subprocess.run(
        [sys.executable, "-m", "readwrench.pagebridge", "--version"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"readwrench pdf-pages {__version__}"
