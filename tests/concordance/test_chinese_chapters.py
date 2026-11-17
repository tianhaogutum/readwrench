import json
from pathlib import Path

import pytest

from .helpers import EXAMPLES, GUTENBERG, require

require()

from taguette import database as d

from readwrench.concordance.books import InputError
from readwrench.concordance.cli import main
from readwrench.concordance.exporting import export_workspace
from readwrench.concordance.positions import to_utf8
from readwrench.concordance.workspace import collect, initialize, plain_html, taguette_session


def add_chapter(path, chapter_id, text, start, end, title):
    positions = to_utf8(text, [start, end])
    with taguette_session(path / "project.sqlite3") as db:
        document = db.query(d.Document).filter_by(filename=chapter_id + ".html").one()
        tag = d.Tag(project_id=document.project_id, path="chapter/" + title, description="Researcher selected chapter boundaries")
        db.add(tag)
        db.flush()
        db.add(d.Highlight(document=document, start_offset=positions[start], end_offset=positions[end],
                           snippet=plain_html(text[start:end]), tags=[tag]))
        db.commit()


def test_five_real_books_and_manual_chinese_chapter_ranges(tmp_path, real_books, capsys):
    sources = real_books + [GUTENBERG / "daodejing.epub", GUTENBERG / "analects-zh.epub"]
    path = tmp_path / "five-books"
    initialize(sources, EXAMPLES / "concepts-cjk.json", path)
    manifest, texts, hits = collect(path)
    assert len(manifest["books"]) == 5
    chinese = [h for h in hits if h["concept_id"] == "de"]
    assert {h["book"] for h in chinese} == {"道德經", "論語"}
    assert all(h["status"] == "unreviewed" for h in hits)
    chapter = next(c for c in manifest["chapters"] if "第三十八章" in texts[c["id"]])
    text = texts[chapter["id"]]
    start, end = text.index("第三十八章"), text.index("第三十九章")
    add_chapter(path, chapter["id"], text, start, end, "第三十八章")
    _, _, reviewed = collect(path)
    scoped = [h for h in reviewed if h["chapter"] == "第三十八章"]
    assert scoped and any(h["exact"] == "德" and h["before"].endswith("上") for h in scoped)
    assert all(start <= h["start"] < h["end"] <= end for h in scoped)
    assert all(h["chapter_source"] == "manual_range" for h in scoped)
    assert main(["list", str(path), "--chapter", "第三十八章", "--concept", "de", "--limit", "100"]) == 0
    filtered = json.loads(capsys.readouterr().out)
    assert filtered["total"] > 0
    assert all(h["chapter"] == "第三十八章" for h in filtered["occurrences"])
    out = tmp_path / "five-book-matrix"
    summary = export_workspace(path, out)
    assert summary["manual_chapter_ranges"] == 1
    assert "第三十八章" in (out / "index.html").read_text()
    manual, = json.loads((out / "manual-chapters.json").read_text())
    assert manual["exact"] == text[start:end]
    # A range never leaks its chapter label into later, unmarked chapters.
    later = [h for h in reviewed if h["chapter_id"] == chapter["id"] and h["start"] >= end]
    assert later and all(h["chapter_source"] != "manual_range" for h in later)


def test_overlapping_manual_chapters_require_user_resolution(simple_workspace):
    path, _ = simple_workspace
    manifest, texts, _ = collect(path)
    chapter = manifest["chapters"][0]
    text = texts[chapter["id"]]
    add_chapter(path, chapter["id"], text, 0, 20, "First")
    add_chapter(path, chapter["id"], text, 10, 30, "Second")
    with pytest.raises(InputError, match="overlap"):
        collect(path)
