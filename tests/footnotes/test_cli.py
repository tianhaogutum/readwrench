"""``readwrench footnotes`` command-line surface (help, version, errors)."""

from __future__ import annotations

import pytest

from readwrench import __version__
from readwrench.footnotes.cli import main


def test_version_names_the_subcommand(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"readwrench footnotes {__version__}"


def test_help_uses_the_readwrench_prog(capsys):
    with pytest.raises(SystemExit):
        main(["scan", "--help"])
    assert capsys.readouterr().out.startswith("usage: readwrench footnotes scan ")


def test_errors_are_prefixed_with_the_subcommand(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text("[]", encoding="utf-8")
    assert main(["review", str(plan), "--out", str(tmp_path / "decisions.json")]) == 2
    assert capsys.readouterr().err.startswith("readwrench footnotes: ")
