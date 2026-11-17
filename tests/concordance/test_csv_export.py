import csv
import json

from .helpers import require

require()

from readwrench.concordance.exporting import export_workspace
from readwrench.concordance.workspace import collect, initialize, label


def test_formula_like_research_text_is_literal_in_csv_but_exact_in_json(tmp_path, make_book, concepts_file):
    source = make_book(title="=1+1", content='<p>=SUM(1,2) power and authority</p>')
    path = tmp_path / "workspace"
    initialize([source], concepts_file([{"id": "a", "label": "+concept", "aliases": ["power"]}]), path)
    hit = collect(path)[2][0]
    label(path, hit["id"], senses=["@category"])
    out = tmp_path / "csv"
    export_workspace(path, out)
    with (out / "occurrences.csv").open(newline="") as stream:
        row, = list(csv.DictReader(stream))
    assert row["book"] == "\t=1+1"
    assert row["concept"] == "\t+concept"
    assert row["before"].startswith("\t=SUM")
    with (out / "matrix.csv").open(newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0][4].startswith("\t=1+1")
    assert next(row for row in rows[1:] if row[2] == "classified")[3] == "\t@category"
    exact = json.loads((out / "occurrences.json").read_text())["occurrences"][0]
    assert exact["book"] == "=1+1" and exact["concept"] == "+concept"
    assert exact["before"].startswith("=SUM")
