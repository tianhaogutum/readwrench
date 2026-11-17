from html.parser import HTMLParser
import json
from pathlib import Path
import pytest

pytest.importorskip("fontTools")
pytest.importorskip("uharfbuzz")
pytest.importorskip("regex")
pytest.importorskip("brotli")

from readwrench.subsetproof.cli import main
from readwrench.subsetproof.core import FontAsset
from readwrench.subsetproof.inputs import Run

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fixtures/fonts"


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.fragments = []
        self.uses = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if attrs.get("href", "").startswith("#"):
            self.fragments.append(attrs["href"][1:])
        self.uses += tag == "use"


def test_multirun_specimen_has_unique_valid_outline_and_jump_references(tmp_path):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office office\nAVAV AVAV\noffice")
    output = tmp_path / "proof"
    assert main(["build", str(FONTS / "NotoSans.ttf"), str(manuscript), "--keep-features", "", "--out", str(output)]) == 1
    report = json.loads((output / "report.json").read_text())
    parsed = References()
    parsed.feed((output / "specimen.html").read_text())
    assert len(parsed.ids) == len(set(parsed.ids))
    assert all(target in parsed.ids for target in parsed.fragments)
    assert any(target.startswith("result-") for target in parsed.fragments)
    assert parsed.uses == sum(len(result[side]["glyphs"]) for result in report["results"] for side in ("original", "candidate"))


def test_many_missing_occurrences_stay_complete_in_json_with_a_bounded_html_table(tmp_path):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("🚀" * 250)
    output = tmp_path / "proof"
    assert main(["build", str(FONTS / "NotoSans.ttf"), str(manuscript), "--out", str(output)]) == 1
    report = json.loads((output / "report.json").read_text())
    html = (output / "specimen.html").read_text()
    assert len(report["coverage"]["original"]["missing"]) == 250
    assert html.count("<tr>") < 250 and "further occurrences are recorded" in html


def test_loaded_font_uses_one_frozen_snapshot_for_shaping_and_outlines(tmp_path):
    input_font = tmp_path / "editable.ttf"
    input_font.write_bytes((FONTS / "NotoSans.ttf").read_bytes())
    run = Run("heading", "office A\u0301", 1)
    control = FontAsset(FONTS / "NotoSans.ttf")
    expected = control.shape(run, {})
    control.close()
    frozen = FontAsset(input_font)
    try:
        # Change to a different real outline format before the first draw.
        # A live path-backed fontTools reader would now read inconsistent data.
        input_font.write_bytes((FONTS / "SourceSerif4-Regular.otf").read_bytes())
        assert frozen.shape(run, {}) == expected
    finally:
        frozen.close()
