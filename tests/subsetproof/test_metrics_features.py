from pathlib import Path
import json
import pytest

pytest.importorskip("fontTools")
pytest.importorskip("uharfbuzz")
pytest.importorskip("regex")
pytest.importorskip("brotli")

from fontTools.ttLib import TTFont
from fontTools.ttLib import newTable
from fontTools.ttLib.tables._k_e_r_n import KernTable_format_0
from fontTools.ttLib.scaleUpem import scale_upem
from readwrench.subsetproof.cli import main

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fixtures/fonts"


def test_external_line_height_change_is_rejected_with_equal_glyphs(tmp_path):
    candidate = tmp_path / "metrics-changed.ttf"
    with TTFont(FONTS / "NotoSans.ttf") as font:
        font["hhea"].ascent += 100
        font["OS/2"].sTypoAscender += 100
        font["OS/2"].usWinAscent += 100
        font.save(candidate)
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office AVATAR")
    output = tmp_path / "proof"
    assert main(["compare", str(FONTS / "NotoSans.ttf"), str(candidate), str(manuscript), "--out", str(output)]) == 1
    report = json.loads((output / "report.json").read_text())
    comparison = report["results"][0]["comparison"]
    assert comparison["changes"] == []
    assert comparison["metric_changes"] == [{"field": "ascender", "original": 1.069, "candidate": 1.169}]
    assert 'metric_changes' in (output / "specimen.html").read_text()


def test_upem_rescaling_is_equivalent(tmp_path):
    candidate = tmp_path / "scaled.ttf"
    with TTFont(FONTS / "NotoSans.ttf") as font:
        scale_upem(font, font["head"].unitsPerEm * 2)
        font.save(candidate)
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("office AVATAR A\u0301")
    assert main(["compare", str(FONTS / "NotoSans.ttf"), str(candidate), str(manuscript), "--out", str(tmp_path / "proof")]) == 0


@pytest.mark.parametrize("features,text", [
    ("smcp=1", "office affine"),
    ("c2sc=1,smcp=1", "AVATAR office"),
    ("onum=1,pnum=1", "0123456789"),
    ("frac=1", "1/2 3/4"),
    ("sups=1", "0123"),
    ("ss01=1,liga=0", "office affine"),
])
def test_requested_serif_publication_features_are_preserved(features, text, tmp_path):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text(text)
    output = tmp_path / "proof"
    assert main(["build", str(FONTS / "SourceSerif4-Regular.otf"), str(manuscript), "--features", features, "--out", str(output)]) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["results"][0]["comparison"]["equal"]


def test_language_specific_turkish_copy(tmp_path):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("fi ffi İstanbul")
    assert main(["build", str(FONTS / "NotoSans.ttf"), str(manuscript), "--language", "tr", "--features", "liga=1", "--out", str(tmp_path / "proof")]) == 0


@pytest.mark.parametrize("keep,disabled,expected", [("*", False, 0), ("kern", False, 0), ("", False, 1), ("", True, 0)])
def test_legacy_kern_retention_obeys_requested_features(keep, disabled, expected, tmp_path):
    font_path = tmp_path / "legacy.ttf"
    with TTFont(FONTS / "NotoSans.ttf") as font:
        del font["GPOS"]
        kern = newTable("kern")
        kern.version = 0
        subtable = KernTable_format_0()
        subtable.version, subtable.coverage = 0, 1
        subtable.kernTable = {("A", "V"): -80}
        kern.kernTables = [subtable]
        font["kern"] = kern
        font.save(font_path)
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text("AV")
    argv = ["build", str(font_path), str(manuscript), "--keep-features", keep, "--out", str(tmp_path / "proof")]
    if disabled:
        argv += ["--features", "kern=0"]
    assert main(argv) == expected
    with TTFont(tmp_path / "proof/subset.ttf") as subset_font:
        assert ("kern" in subset_font) == (keep in {"*", "kern"})
