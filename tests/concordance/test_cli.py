"""The ``readwrench concordance`` command line: help, version and dispatcher wiring."""
import os
import subprocess
import sys

import pytest

from readwrench import __version__
from readwrench.concordance.cli import main, parser

from .helpers import REPO

NESTED = ("init", "list", "check", "export", "label", "resolve")


def test_help_needs_no_optional_dependency(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: readwrench concordance ")
    for name in NESTED:
        assert name in out


def test_nested_help_uses_new_program_name(capsys):
    with pytest.raises(SystemExit):
        main(["export", "--help"])
    assert capsys.readouterr().out.startswith("usage: readwrench concordance export ")


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"readwrench concordance {__version__}"


def test_parser_matches_documented_options():
    args = parser().parse_args(["init", "a.epub", "--concepts", "c.json", "--out", "ws",
                                "--max-hits", "5", "--include-nonlinear"])
    assert (args.max_hits, args.include_nonlinear) == (5, True)
    args = parser().parse_args(["list", "ws", "--status", "conflicted", "--limit", "3"])
    assert (args.status, args.limit, args.offset) == ("conflicted", 3, 0)


@pytest.mark.parametrize("entry", [["-m", "readwrench", "concordance"], ["-m", "readwrench.concordance"]])
def test_module_entry_points(entry):
    # Works from a clone without installation as well as from an installed package.
    path = os.pathsep.join(filter(None, [str(REPO / "src"), os.environ.get("PYTHONPATH")]))
    result = subprocess.run([sys.executable, *entry, "--help"], cwd=REPO, capture_output=True,
                            text=True, timeout=60, env=dict(os.environ, PYTHONPATH=path))
    assert result.returncode == 0, result.stderr
    assert "readwrench concordance" in result.stdout
