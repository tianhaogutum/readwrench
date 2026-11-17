import json
import shutil
import sqlite3

from .helpers import require

require()

from readwrench.concordance.cli import main
from readwrench.concordance.workspace import collect, taguette_session


def test_resolve_cli_can_verify_resized_passage_from_external_review(simple_workspace, tmp_path, capsys):
    path, source = simple_workspace
    external = tmp_path / "reviewed.sqlite3"
    shutil.copyfile(path / "project.sqlite3", external)
    original = collect(path)[2][0]
    with sqlite3.connect(external) as db:
        db.execute("UPDATE highlights SET start_offset=0 WHERE id=?", (original["taguette_highlight_id"],))
    reviewed = collect(path, database=external)[2][0]
    assert reviewed["id"] != original["id"]
    assert main(["resolve", str(path), reviewed["id"], "--source", str(source), "--database", str(external)]) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["exact"] == reviewed["exact"]
    assert resolved["edition_sha256"] == reviewed["edition_sha256"]


def test_cli_label_selects_correct_project_when_workspace_was_imported_twice(simple_workspace, tmp_path, capsys):
    from taguette import database as d
    path, _ = simple_workspace
    original = collect(path)[2][0]
    imported = tmp_path / "two-copies.sqlite3"
    with taguette_session(imported) as target:
        target.add(d.User(login="admin"))
        target.commit()
        with taguette_session(path / "project.sqlite3") as source:
            assert d.copy_project(source, target, 1, "admin") == 1
            assert d.copy_project(source, target, 1, "admin") == 2
    shutil.copyfile(imported, path / "project.sqlite3")
    assert main(["label", str(path), original["id"], "--project-id", "2", "--sense", "second copy decision"]) == 0
    capsys.readouterr()
    assert collect(path, project_id=1)[2][0]["senses"] == []
    assert collect(path, project_id=2)[2][0]["senses"] == ["second copy decision"]
