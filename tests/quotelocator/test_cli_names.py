"""``readwrench quote`` naming, version and entry points (no optional dependency needed)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from readwrench import __version__
from readwrench.quotelocator.cli import main

ROOT = Path(__file__).resolve().parents[2]


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"readwrench quote {__version__}"


@pytest.mark.parametrize("command", ["find", "confirm", "verify", "extract"])
def test_help_names_the_subcommand(capsys, command):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith(f"usage: readwrench quote {command}")


def test_dispatcher_and_python_m_route_to_the_tool(capsys):
    from readwrench.cli import main as readwrench_main

    with pytest.raises(SystemExit):
        readwrench_main(["quote", "--version"])
    assert capsys.readouterr().out.strip() == f"readwrench quote {__version__}"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH")]))
    for module in (["-m", "readwrench", "quote"], ["-m", "readwrench.quotelocator"]):
        result = subprocess.run(
            [sys.executable, *module, "--help"], capture_output=True, text=True, env=env, check=False
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("usage: readwrench quote")
