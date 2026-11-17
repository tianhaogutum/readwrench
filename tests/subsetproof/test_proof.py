from __future__ import annotations

import json
from pathlib import Path
import pytest

pytest.importorskip("fontTools")
pytest.importorskip("uharfbuzz")
pytest.importorskip("regex")
pytest.importorskip("brotli")

from fontTools import subset
from fontTools.ttLib import TTFont
from readwrench.subsetproof.cli import main

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fixtures" / "fonts"
# The copy SubsetProofCJKFixture.otf was derived from (examples/subset-proof/chinese.txt).
CHINESE_COPY = "字体子集验收\n海报与电子刊：排版、连字、缺字检查。\n交付前，请核对真实文稿。\n"


def invoke(tmp_path, text="office affine AVATAR", *, font="NotoSans.ttf", options=(), command="build", candidate=None):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_text(text, encoding="utf-8")
    output = tmp_path / "proof"
    argv = [command, str(FONTS / font)]
    if candidate is not None:
        argv.append(str(candidate))
    argv += [str(manuscript), "--out", str(output), *options]
    code = main(argv)
    report = json.loads((output / "report.json").read_text()) if output.exists() else None
    return code, report, output


def upstream_subset(destination, text, *, names=True, features=None):
    options = subset.Options()
    options.glyph_names = names
    if features is not None:
        options.layout_features = features
    font = TTFont(FONTS / "NotoSans.ttf", recalcTimestamp=False)
    worker = subset.Subsetter(options)
    worker.populate(text=text)
    worker.subset(font)
    font.save(destination)
    font.close()


def test_real_ligature_and_gid_renumbering_are_accepted(tmp_path):
    code, report, output = invoke(tmp_path, options=["--features", "liga=1,kern=1"])
    assert code == 0 and report["accepted"]
    result = report["results"][0]
    assert len(result["original"]["glyphs"]) < len(result["run"]["text"])
    assert any(a["gid"] != b["gid"] for a, b in zip(result["original"]["glyphs"], result["candidate"]["glyphs"]))
    assert result["comparison"]["equal"]
    assert (output / "subset.ttf").stat().st_size < (FONTS / "NotoSans.ttf").stat().st_size
    html = (output / "specimen.html").read_text()
    assert '<svg ' in html and '<path ' in html and 'ACCEPTED' in html


def test_missing_unicode_is_located_before_subsetting(tmp_path):
    manuscript = tmp_path / "copy.txt"
    manuscript.write_bytes("office🚀\r\n🚀".encode())
    output = tmp_path / "proof"
    code = main(["build", str(FONTS / "NotoSans.ttf"), str(manuscript), "--out", str(output)])
    report = json.loads((output / "report.json").read_text())
    assert code == 1 and not report["accepted"]
    assert [(m["line"], m["column"], m["offset"]) for m in report["coverage"]["original"]["missing"]] == [(1, 7, 6), (2, 1, 9)]
    assert report["issues"] == ["original_missing_unicode"]
    assert not list(output.glob("subset.*"))


def test_dropped_ligatures_and_positioning_are_rejected(tmp_path):
    code, report, _ = invoke(tmp_path, options=["--keep-features", ""])
    assert code == 1
    assert report["issues"] == ["shaping_difference"]
    changes = report["results"][0]["comparison"]["changes"]
    assert any("glyph_identity" in change["fields"] for change in changes)
    assert not report["coverage"]["candidate"]["missing"]


def test_kerning_loss_is_found_with_unchanged_glyphs(tmp_path):
    code, report, _ = invoke(tmp_path, text="AVATAR", options=["--keep-features", "liga"])
    assert code == 1
    changes = report["results"][0]["comparison"]["changes"]
    assert any(change["fields"] == ["position"] for change in changes)


def test_external_fonttools_subset_without_names_passes(tmp_path):
    candidate = tmp_path / "external.ttf"
    upstream_subset(candidate, "office affine AVATAR", names=False)
    code, report, _ = invoke(tmp_path, command="compare", candidate=candidate)
    assert code == 0
    assert report["results"][0]["comparison"]["identity_method"] == "outline_equivalence"


def test_external_missing_character_does_not_pass_equal_notdef(tmp_path):
    candidate = tmp_path / "missing.ttf"
    upstream_subset(candidate, "office")
    code, report, _ = invoke(tmp_path, text="office!", command="compare", candidate=candidate)
    assert code == 1
    assert "candidate_missing_unicode" in report["issues"]
    missing = report["coverage"]["candidate"]["missing"]
    assert missing[0]["codepoint"] == "U+0021" and missing[0]["column"] == 7
    assert report["results"][0]["comparison"]["notdef"][0]["side"] == "candidate"


def test_arabic_joining_and_marks_survive(tmp_path):
    code, report, _ = invoke(tmp_path, text="السَّلَامُ عَلَيْكُمْ", font="NotoSansArabic.ttf",
                             options=["--language", "ar", "--direction", "rtl"])
    assert code == 0
    assert report["results"][0]["original"]["direction"] == "rtl"
    assert report["results"][0]["original"]["script"] == "Arab"
    assert any(glyph["position"]["x_offset"] != 0 for glyph in report["results"][0]["original"]["glyphs"])


def test_devanagari_reordering_survives(tmp_path):
    code, report, _ = invoke(tmp_path, text="नमस्ते विश्व कर्म क्षेत्र", font="NotoSansDevanagari.ttf")
    assert code == 0
    assert report["results"][0]["original"]["script"] == "Deva"


def test_multiple_variable_locations_are_checked(tmp_path):
    code, report, _ = invoke(tmp_path, options=["--at", "wght=100,wdth=75", "--at", "wght=900,wdth=100"])
    assert code == 0 and len(report["results"]) == 2
    assert report["results"][0]["original"]["glyphs"][0]["outline_sha256"] != report["results"][1]["original"]["glyphs"][0]["outline_sha256"]


@pytest.mark.parametrize("flavor", ["woff", "woff2"])
def test_real_webfont_roundtrip_is_shaped(flavor, tmp_path):
    code, report, output = invoke(tmp_path, options=["--flavor", flavor])
    assert code == 0 and (output / f"subset.{flavor}").exists()
    # Also exercise a compressed original, not just a generated candidate.
    original = tmp_path / f"compressed.{flavor}"
    font = TTFont(FONTS / "NotoSans.ttf")
    font.flavor = flavor
    font.save(original)
    font.close()
    second = tmp_path / "second-proof"
    assert main(["build", str(original), str(tmp_path / "copy.txt"), "--out", str(second)]) == 0


def test_cff_outlines_and_ligature_closure(tmp_path):
    code, report, output = invoke(tmp_path, font="SourceSerif4-Regular.otf")
    assert code == 0 and (output / "subset.otf").exists()


def test_chinese_poster_copy_with_cid_keyed_cff_font(tmp_path):
    # The small derived CID fixture covers the Chinese example copy (horizontal shaping).
    code, report, output = invoke(tmp_path, text="海报与电子刊：排版、连字、缺字检查。", font="SubsetProofCJKFixture.otf")
    assert code == 0
    assert report["results"][0]["original"]["script"] == "Hani"
    assert (output / "subset.otf").stat().st_size < 100_000


def test_cid_keyed_cjk_and_vertical_metrics_roundtrip(tmp_path):
    text = CHINESE_COPY
    code, report, output = invoke(tmp_path, text=text, font="SubsetProofCJKFixture.otf",
        options=["--direction", "ttb", "--features", "vert=1,vrt2=1", "--flavor", "woff2"])
    assert code == 0 and len(report["results"]) == 3
    assert all(result["original"]["advance_em"]["y"] < 0 for result in report["results"])
    assert (output / "subset.woff2").exists()


def test_disabled_ligature_matches_without_ligature_retention(tmp_path):
    code, report, _ = invoke(tmp_path, text="office", options=["--features", "liga=0,kern=0", "--keep-features", ""])
    assert code == 0
    assert len(report["results"][0]["original"]["glyphs"]) == 6


def test_manifest_records_run_settings(tmp_path):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps({"runs": [{"id": "heading", "text": "office", "features": {"liga": 0},
                           "variations": {"wght": 800}, "language": "en"}]}))
    out = tmp_path / "proof"
    assert main(["build", str(FONTS / "NotoSans.ttf"), str(path), "--manifest", "--at", "wght=100", "--out", str(out)]) == 0
    report = json.loads((out / "report.json").read_text())
    assert report["results"][0]["location"] == {"wght": 800.0}
    assert report["results"][0]["run"]["id"] == "heading"


@pytest.mark.parametrize("text", ["office سلام", "A\tB", "a\u202eb", "", "a\x00b"])
def test_unsupported_manuscript_layout_fails_without_partial_output(text, tmp_path):
    code, report, output = invoke(tmp_path, text=text)
    assert code == 2 and report is None and not output.exists()


@pytest.mark.parametrize("option,value", [("--at", "wght=10000"), ("--at", "wght=nan"),
    ("--at", "fake=1"), ("--features", "fake=1"), ("--features", "liga=-1"), ("--keep-features", "fake")])
def test_invalid_settings_are_actionable(option, value, tmp_path):
    code, report, output = invoke(tmp_path, options=[option, value])
    assert code == 2 and not output.exists()


def test_html_escapes_manuscript_and_run_identifiers(tmp_path):
    code, _, output = invoke(tmp_path, text='<script>alert("x")</script>')
    assert code == 0
    html = (output / "specimen.html").read_text()
    assert '<script>' not in html and '&lt;script&gt;' in html


def test_existing_output_is_never_overwritten(tmp_path):
    manuscript = tmp_path / "text.txt"
    manuscript.write_text("office")
    output = tmp_path / "proof"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("valuable")
    assert main(["build", str(FONTS / "NotoSans.ttf"), str(manuscript), "--out", str(output)]) == 2
    assert marker.read_text() == "valuable" and list(output.iterdir()) == [marker]
