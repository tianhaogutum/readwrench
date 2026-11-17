"""Independently parse an export directory with pypdf, a second PDF parser.

Needs ``pip install pypdf`` in addition to the pdf-pages extra:
    python examples/pdf-pages/check_output.py comparison
"""

import argparse
import csv
import json
from pathlib import Path

from pypdf import PdfReader

from readwrench.pagebridge.mapping import read_plan, validate_map


def check_output(directory: Path):
    mapping = read_plan(directory / "mapping.json")
    validate_map(mapping, approved=True)
    summary = json.loads((directory / "summary.json").read_text())
    expected_pairs = [(r["original"], r["modified"]) for r in mapping["rows"]]
    actual_pairs = [(r["original"], r["modified"]) for r in summary["rows"]]
    assert actual_pairs == expected_pairs, "Summary and approved map disagree"
    with (directory / "mapping.csv").open(newline="") as stream:
        assert len(list(csv.DictReader(stream))) == len(expected_pairs)
    parsed = {}
    for name in ("diff.pdf", "aligned-original.pdf", "aligned-modified.pdf"):
        path = directory / name
        if not path.exists():
            continue
        reader = PdfReader(path, strict=True)
        assert not reader.is_encrypted
        assert not reader.get_fields(), (
            f"Review derivative unexpectedly contains live fields: {name}"
        )
        if name == "diff.pdf" and "audit_pages" in summary:
            destinations = [
                reader.get_destination_page_number(item)
                for item in reader.outline
                if not isinstance(item, list)
            ]
            expected_destinations = [0] + [
                row["report_page"] - 1
                for row in summary["rows"]
                if row["kind"] != "unchanged" and row.get("report_page")
            ]
            assert destinations == expected_destinations, "PDF bookmarks point to incorrect pages"
        if "audit_pages" in summary and name == "diff.pdf":
            panel_count = sum(row.get("report_page") is not None for row in summary["rows"])
            assert len(reader.pages) == summary["audit_pages"] + panel_count
            for row in summary["rows"]:
                if row.get("report_page") is not None:
                    assert summary["audit_pages"] < row["report_page"] <= len(reader.pages)
        else:
            assert len(reader.pages) >= len(expected_pairs)
        for page in reader.pages:
            assert page.mediabox.width > 0 and page.mediabox.height > 0
        parsed[name] = reader
    assert "diff.pdf" in parsed
    if "aligned-original.pdf" in parsed:
        a, b = parsed["aligned-original.pdf"], parsed["aligned-modified.pdf"]
        assert len(a.pages) == len(b.pages) == len(expected_pairs)
        assert all(x.mediabox == y.mediabox for x, y in zip(a.pages, b.pages))
    return {
        "approved_rows": len(expected_pairs),
        "parsed_pdf_pages": {name: len(reader.pages) for name, reader in parsed.items()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(check_output(parser.parse_args().directory), indent=2))
