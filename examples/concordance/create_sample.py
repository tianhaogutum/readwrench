"""Build a reviewable five-book sample with one explicitly illustrative code.

Run from a clone with readwrench[concordance] installed:

    python examples/concordance/create_sample.py --workspace sample-workspace --out sample-export

The bundled Gutenberg books are immutable local editions. The chapter-38
boundaries below are explicit, book-specific choices, not automatic chapter
inference.
"""

import argparse
import json
from pathlib import Path

from taguette import database as d

from readwrench.concordance.exporting import export_workspace
from readwrench.concordance.positions import to_utf8
from readwrench.concordance.workspace import collect, initialize, label, plain_html, taguette_session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("sample-workspace"))
    parser.add_argument("--out", type=Path, default=Path("sample-export"))
    args = parser.parse_args()
    if args.workspace.exists() or args.out.exists():
        parser.error("Choose fresh workspace and export directories")
    root = Path(__file__).resolve().parent
    names = ["prince.epub", "government.epub", "republic.epub", "daodejing.epub", "analects-zh.epub"]
    initialize([root / "gutenberg" / name for name in names], root / "concepts-cjk.json",
               args.workspace, name="跨书概念对照台 · 演示")
    manifest, texts, _ = collect(args.workspace)
    chapter = next(chapter for chapter in manifest["chapters"] if "第三十八章" in texts[chapter["id"]])
    text = texts[chapter["id"]]
    start, end = text.index("第三十八章"), text.index("第三十九章")
    offsets = to_utf8(text, [start, end])
    with taguette_session(args.workspace / "project.sqlite3") as db:
        document = db.query(d.Document).filter_by(filename=chapter["id"] + ".html").one()
        tag = d.Tag(project_id=document.project_id, path="chapter/第三十八章",
                    description="Explicit chapter range in this fixture; chosen for the demonstration")
        db.add(tag)
        db.flush()
        db.add(d.Command.tag_add("admin", tag))
        highlight = d.Highlight(document=document, start_offset=offsets[start], end_offset=offsets[end],
                                snippet=plain_html(text[start:end]), tags=[tag])
        db.add(highlight)
        db.flush()
        db.add(d.Command.highlight_add("admin", document, highlight, [tag.id]))
        db.commit()
    hit = next(hit for hit in collect(args.workspace)[2]
               if hit["chapter"] == "第三十八章" and hit["exact"] == "德" and hit["before"].endswith("上"))
    label(args.workspace, hit["id"], senses=["演示归类（不代表概念同义）"])
    summary = export_workspace(args.workspace, args.out)
    print(json.dumps(dict(summary, sample_note="One illustrative manual category; all other candidates need researcher review"),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
