"""Top-level `readwrench` dispatcher: overview, version, routing, install hints and error paths."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

import readwrench
from readwrench import cli

SRC = Path(__file__).resolve().parents[1] / "src"

# Every command and its nested subcommands (help must work for all of them, with or without extras).
SUBCOMMANDS = {
    "check": [],
    "quote": ["find", "confirm", "verify", "extract"],
    "clues": ["build", "capture", "search"],
    "concordance": ["init", "list", "check", "export", "label", "resolve"],
    "pdf-pages": ["plan", "confirm", "export"],
    "reanchor": ["propose", "apply"],
    "footnotes": ["scan", "review", "candidate"],
    "subset-proof": ["build", "compare"],
    "lineage": ["init", "build", "archivebox"],
}
# `python -m readwrench.<subpackage>` must route through the dispatcher.
SUBPACKAGES = {
    "check": "readwrench.check",
    "quote": "readwrench.quotelocator",
    "clues": "readwrench.cluejoin",
    "concordance": "readwrench.concordance",
    "pdf-pages": "readwrench.pagebridge",
    "reanchor": "readwrench.reanchor",
    "footnotes": "readwrench.footnotes",
    "subset-proof": "readwrench.subsetproof",
    "lineage": "readwrench.lineage",
}
EXTRAS = {
    "quote": "quote", "concordance": "concordance", "pdf-pages": "pdf-pages", "reanchor": "reanchor",
    "footnotes": "footnotes", "subset-proof": "subset-proof",
}
ALL_HELP = [(name, None) for name in SUBCOMMANDS] + [(name, sub) for name, subs in SUBCOMMANDS.items() for sub in subs]


def run(*args, module=("-m", "readwrench")):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
    # Generous timeout: cold interpreter starts on macOS runners can be slow.
    return subprocess.run([sys.executable, *module, *map(str, args)], capture_output=True, text=True, timeout=120,
                          env=env)


def require(name):
    """Skip commands whose subpackage is not part of this build (yet)."""
    if not cli._installed(cli.BY_NAME[name]):
        pytest.skip(f"readwrench {name} is not shipped in this build")


def _main(argv, capsys):
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("args", [[], ["--help"], ["-h"], ["help"]])
def test_overview_lists_every_group_and_command(args, capsys):
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"readwrench {readwrench.__version__} ")
    for group in cli.GROUPS:
        assert f"\n{group}:\n" in out
    for command in cli.COMMANDS:
        assert f"\n  {command.name} " in out
    assert "(coming soon)" not in out
    for extra in EXTRAS.values():
        assert f"readwrench[{extra}]" in out


def test_command_table_shape():
    groups = {group: [c.name for c in cli.COMMANDS if c.group == group] for group in cli.GROUPS}
    assert groups == {
        "Check": ["check"],
        "Find & compare": ["quote", "clues", "concordance"],
        "PDF": ["pdf-pages", "reanchor"],
        "EPUB & fonts": ["footnotes", "subset-proof"],
        "Web captures": ["lineage"],
    }
    # The read-only check is the entry point: listed first, in its own group.
    assert cli.GROUPS[0] == "Check" and cli.COMMANDS[0].name == "check"
    assert {c.name: c.module for c in cli.COMMANDS} == {name: f"{pkg}.cli" for name, pkg in SUBPACKAGES.items()}
    assert {c.name: c.extra for c in cli.COMMANDS if c.extra} == EXTRAS
    assert all(bool(c.requires) == bool(c.extra) for c in cli.COMMANDS)
    assert [c.name for c in cli.COMMANDS if c.python > (3, 11)] == ["reanchor"]
    assert all(c.group in cli.GROUPS and c.summary for c in cli.COMMANDS)
    assert len(cli.BY_NAME) == len(cli.COMMANDS)
    # subset-proof kept its name and check is new; every other command has a former name.
    assert set(cli.LEGACY.values()) == set(cli.BY_NAME) - {"subset-proof", "check"}
    assert not set(cli.LEGACY) & set(cli.BY_NAME)


def test_extras_match_pyproject():
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((SRC.parent / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    assert set(EXTRAS.values()) | {"all", "test"} == set(extras)
    assert extras["all"] == [f"readwrench[{','.join(EXTRAS.values())}]"]
    assert pyproject["project"]["dependencies"] == []
    assert pyproject["project"]["scripts"] == {"readwrench": "readwrench.cli:main"}


def test_subcommand_table_covers_every_command():
    assert set(SUBCOMMANDS) == set(cli.BY_NAME) == set(SUBPACKAGES)


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version(flag, capsys):
    assert cli.main([flag]) == 0
    assert capsys.readouterr().out == f"readwrench {readwrench.__version__}\n"


@pytest.mark.parametrize("tool", list(SUBCOMMANDS))
def test_tool_help_uses_new_program_name(tool):
    require(tool)
    result = run(tool, "--help")
    assert result.returncode == 0, result.stderr
    assert f"usage: readwrench {tool}" in result.stdout


@pytest.mark.parametrize("tool", list(SUBCOMMANDS))
def test_tool_version_uses_new_program_name(tool):
    require(tool)
    result = run(tool, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"readwrench {tool} "), result.stdout


@pytest.mark.parametrize("tool", list(SUBCOMMANDS))
def test_help_subcommand_routes_to_tool_help(tool, capsys):
    require(tool)
    code, out, err = _main(["help", tool], capsys)
    assert code == 0, err
    assert f"usage: readwrench {tool}" in out


def test_tool_exit_codes_pass_through():
    require("footnotes")
    bad = run("footnotes", "no-such-subcommand")
    assert bad.returncode == 2 and "usage: readwrench footnotes" in bad.stderr


def test_unknown_command_suggests_close_match(capsys):
    assert cli.main(["footnote"]) == 2
    err = capsys.readouterr().err
    assert "unknown command 'footnote'" in err and "did you mean 'footnotes'?" in err


def test_close_match_to_legacy_name_suggests_new_command(capsys):
    assert cli.main(["pdf-reanchr"]) == 2
    assert "did you mean 'reanchor'?" in capsys.readouterr().err


@pytest.mark.parametrize("old,new", sorted(cli.LEGACY.items()))
def test_unknown_legacy_name_points_to_new_command(old, new, capsys):
    assert cli.main([old]) == 2
    assert f"{old} is now 'readwrench {new}'" in capsys.readouterr().err


def test_unknown_command_without_match(capsys):
    assert cli.main(["zzzzzz"]) == 2
    err = capsys.readouterr().err
    assert "unknown command 'zzzzzz'" in err and "did you mean" not in err


def test_unknown_option(capsys):
    assert cli.main(["--frobnicate"]) == 2
    assert "unknown option '--frobnicate'" in capsys.readouterr().err


def test_missing_module_reports_not_available(monkeypatch, capsys):
    ghost = cli.Command("ghost", "readwrench.ghost_tool.cli", "not shipped", "PDF")
    monkeypatch.setitem(cli.BY_NAME, "ghost", ghost)
    monkeypatch.setattr(cli, "COMMANDS", (*cli.COMMANDS, ghost))
    assert "ghost" in cli.overview() and "(coming soon)" in cli.overview()
    assert cli.main(["ghost", "--help"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("readwrench: command 'ghost' is not available") and "Traceback" not in err


def test_subpackage_without_cli_module_reports_not_available(monkeypatch, capsys):
    # The package exists but its cli module does not (e.g. a tool still being ported).
    ghost = cli.Command("ghost", "readwrench.no_such_cli", "half shipped", "PDF")
    monkeypatch.setitem(cli.BY_NAME, "ghost", ghost)
    monkeypatch.setattr(cli, "_installed", lambda command: True)
    assert cli.main(["ghost"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("readwrench: command 'ghost' is not available") and "Traceback" not in err


def _fake_tool(tmp_path, monkeypatch, name, *, python=(3, 11)):
    package = tmp_path / "fake_tool_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(
        "def main(argv=None):\n    import readwrench_missing_dependency  # noqa: F401\n    return 0\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    command = cli.Command(name, "fake_tool_pkg.cli", "needs an extra", "PDF",
                          requires=("readwrench_missing_dependency",), extra="fake", python=python)
    monkeypatch.setitem(cli.BY_NAME, name, command)
    return command


def test_missing_declared_dependency_prints_install_hint(tmp_path, monkeypatch, capsys):
    _fake_tool(tmp_path, monkeypatch, "fake")
    assert cli.main(["fake", "run"]) == 2
    err = capsys.readouterr().err
    assert err == ("readwrench fake needs readwrench_missing_dependency: pipx install 'readwrench[fake]' "
                   "(or pip install 'readwrench[fake]')\n")


def test_missing_dependency_on_too_old_python_names_the_python_version(tmp_path, monkeypatch, capsys):
    _fake_tool(tmp_path, monkeypatch, "fake-new", python=(99, 0))
    assert cli.main(["fake-new"]) == 2
    err = capsys.readouterr().err
    assert "needs Python 99.0 or newer" in err and "pip install" not in err


@pytest.mark.parametrize("tool", list(SUBPACKAGES))
def test_subpackage_main_routes_through_dispatcher(tool):
    require(tool)
    result = run("--help", module=("-m", SUBPACKAGES[tool]))
    assert result.returncode == 0, result.stderr
    assert f"usage: readwrench {tool}" in result.stdout


@pytest.mark.parametrize("flag", ["--help", "-h"])
@pytest.mark.parametrize("command,sub", ALL_HELP, ids=lambda v: v or "-")
def test_every_command_and_subcommand_help_exits_0(command, sub, flag, capsys):
    # Help is built before any optional dependency is imported, so this passes without extras.
    require(command)
    argv = [command, *([sub] if sub else []), flag]
    code, out, err = _main(argv, capsys)
    assert code == 0, (argv, err)
    assert "usage" in out.lower(), (argv, out)
    if sub:
        assert f"readwrench {command} {sub}" in out, (argv, out)
