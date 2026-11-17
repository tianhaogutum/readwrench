"""Parser, help and version: standard library only, so these run without the extra."""
from __future__ import annotations

import pytest

from readwrench import __version__
from readwrench.cli import main as readwrench_main
from readwrench.subsetproof.cli import main


def test_help_uses_the_readwrench_prog(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--help"])
    assert stopped.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: readwrench subset-proof")
    assert "build" in out and "compare" in out
    assert "Strict manuscript proof" in out


@pytest.mark.parametrize("command", ["build", "compare"])
def test_nested_help_names_the_subcommand(command, capsys):
    with pytest.raises(SystemExit) as stopped:
        main([command, "--help"])
    assert stopped.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"usage: readwrench subset-proof {command}")
    assert "--out" in out and ("--keep-features" in out) == (command == "build")


def test_version(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == f"readwrench subset-proof {__version__}"


def test_dispatcher_routes_help(capsys):
    with pytest.raises(SystemExit):
        readwrench_main(["subset-proof", "--help"])
    assert capsys.readouterr().out.startswith("usage: readwrench subset-proof")


def test_missing_out_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["build", "font.ttf", "copy.txt"])
    assert stopped.value.code == 2
    assert "--out" in capsys.readouterr().err
