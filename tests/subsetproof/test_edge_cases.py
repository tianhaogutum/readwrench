from __future__ import annotations

import json
from pathlib import Path
import pytest

pytest.importorskip("fontTools")
pytest.importorskip("uharfbuzz")
pytest.importorskip("regex")
pytest.importorskip("brotli")

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.ttLib import TTCollection, TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from readwrench.subsetproof.cli import main

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fixtures" / "fonts"


def run_build(tmp_path, text, font=None, extra=()):
    manuscript = tmp_path / "text.txt"
    manuscript.write_text(text, encoding="utf-8")
    output = tmp_path / "proof"
    code = main(["build", str(font or FONTS / "NotoSans.ttf"), str(manuscript), "--out", str(output), *extra])
    report = json.loads((output / "report.json").read_text()) if output.exists() else None
    return code, report, output


@pytest.mark.parametrize("mapping", [None, "B"])
def test_supported_default_and_nondefault_uvs_survive(mapping, tmp_path):
    font = TTFont(FONTS / "NotoSans.ttf")
    table = CmapSubtable.newSubtable(14)
    table.platformID, table.platEncID, table.language = 0, 5, 0
    table.cmap = {}
    table.uvsDict = {0xFE0F: [(0x41, mapping)]}
    font["cmap"].tables.append(table)
    path = tmp_path / "uvs.ttf"
    font.save(path)
    font.close()
    code, report, output = run_build(tmp_path, "A\ufe0f", path)
    assert code == 0 and report["accepted"]
    assert report["settings"]["subsetter_ignore_missing_unicodes"] is True
    with TTFont(output / "subset.ttf") as candidate:
        assert any(table.format == 14 and (0x41, mapping) in table.uvsDict[0xFE0F]
                   for table in candidate["cmap"].tables)


def test_uvs_in_an_unusable_platform_subtable_does_not_count(tmp_path):
    with TTFont(FONTS / "NotoSans.ttf") as font:
        table = CmapSubtable.newSubtable(14)
        table.platformID, table.platEncID, table.language = 3, 5, 0
        table.cmap, table.uvsDict = {}, {0xFE0F: [(0x41, None)]}
        font["cmap"].tables.append(table)
        path = tmp_path / "uvs-wrong-platform.ttf"
        font.save(path)
    code, report, output = run_build(tmp_path, "A\ufe0f", path)
    assert code == 1
    assert report["coverage"]["original"]["missing"][0]["reason"] == "unsupported_variation_sequence"
    assert not (output / "subset.ttf").exists()


@pytest.mark.parametrize("text", ["A\ufe0f", "\ufe0fA", "A\U000e0100"])
def test_unsupported_variation_sequences_are_rejected(text, tmp_path):
    code, report, output = run_build(tmp_path, text)
    assert code == 1
    assert report["coverage"]["original"]["missing"][0]["reason"] == "unsupported_variation_sequence"
    assert not (output / "subset.ttf").exists()


def test_default_ignorable_exemption_is_visible(tmp_path):
    code, report, _ = run_build(tmp_path, "of\U000e0001fice")
    assert code == 0
    assert report["coverage"]["original"]["exemptions"][0]["codepoint"] == "U+E0001"


def test_mapped_word_joiner_keeps_invisible_space_substitution(tmp_path):
    code, report, _ = run_build(tmp_path, "of\u2060fice")
    assert code == 0
    assert any(glyph["name"] == "space" and glyph["position"]["x_advance"] == 0
               for glyph in report["results"][0]["original"]["glyphs"])


def test_equal_notdef_from_gsub_is_rejected(tmp_path):
    font = TTFont(FONTS / "NotoSans.ttf")
    # Valid cmap coverage, but a deliberately broken layout feature maps A to 0.
    addOpenTypeFeaturesFromString(font, "feature liga { sub A by .notdef; } liga;")
    broken = tmp_path / "broken.ttf"
    font.save(broken)
    font.close()
    code, report, _ = run_build(tmp_path, "A", broken)
    assert code == 1 and not report["coverage"]["original"]["missing"]
    assert {item["side"] for item in report["results"][0]["comparison"]["notdef"]} == {"original", "candidate"}


@pytest.mark.parametrize("text", ["سلام 123", "سلام ١٢٣"])
def test_rtl_numbers_require_external_bidi_itemization(text, tmp_path):
    code, report, output = run_build(tmp_path, text, FONTS / "NotoSansArabic.ttf")
    assert code == 2 and report is None and not output.exists()


@pytest.mark.parametrize("run", [
    {"text": "office", "direction": []},
    {"text": "office", "direction": {}},
    {"text": "office", "script": "fake"},
    {"text": "office", "features": {"liga": True}},
    {"text": "office", "variations": {"wght": float("inf")}},
    {"text": "office", "variations": {"wght": 10 ** 400}},
    {"text": "office", "language": []},
    {"text": "office", "id": "a\u202eb"},
    {"text": "office", "unexpected": "value"},
])
def test_manifest_type_errors_are_clean_input_errors(run, tmp_path, capsys):
    manuscript = tmp_path / "runs.json"
    manuscript.write_text(json.dumps({"runs": [run]}))
    output = tmp_path / "proof"
    assert main(["build", str(FONTS / "NotoSans.ttf"), str(manuscript), "--manifest", "--out", str(output)]) == 2
    assert not output.exists()
    assert 'subset-proof:' in capsys.readouterr().err


def test_duplicate_json_keys_do_not_silently_override(tmp_path):
    manuscript = tmp_path / "runs.json"
    manuscript.write_text('{"runs":[{"text":"office","features":{"liga":1,"liga":0}}]}')
    assert main(["build", str(FONTS / "NotoSans.ttf"), str(manuscript), "--manifest", "--out", str(tmp_path / "proof")]) == 2


def test_utf8_bom_manifest_keeps_text_fields_and_original_hash(tmp_path):
    import hashlib
    manuscript = tmp_path / "runs.json"
    raw = b"\xef\xbb\xbf" + json.dumps({"runs": [{"text": "office"}]}).encode()
    manuscript.write_bytes(raw)
    output = tmp_path / "proof"
    assert main(["build", str(FONTS / "NotoSans.ttf"), str(manuscript), "--manifest", "--out", str(output)]) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["text"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["results"][0]["run"]["text"] == "office"


def test_ttc_face_selection_is_explicit_and_correct(tmp_path):
    collection = TTCollection()
    collection.fonts = [TTFont(FONTS / "NotoSans.ttf"), TTFont(FONTS / "NotoSansArabic.ttf")]
    path = tmp_path / "fonts.ttc"
    collection.save(path)
    collection.close()
    text = tmp_path / "text.txt"
    text.write_text("سلام")
    assert main(["build", str(path), str(text), "--out", str(tmp_path / "no-index")]) == 2
    assert main(["build", str(path), str(text), "--font-index", "1", "--out", str(tmp_path / "correct")]) == 0
    assert main(["build", str(path), str(text), "--font-index", "99", "--out", str(tmp_path / "bad-index")]) == 2


@pytest.mark.parametrize("data", [b"", b"not a font at all", b"\x00\x01\x00\x00" + b"\0" * 12,
                                        b"wOF2" + b"\0" * 12 + b"\xff" * 4])
def test_malformed_fonts_fail_cleanly(data, tmp_path):
    path = tmp_path / "font.ttf"
    path.write_bytes(data)
    code, report, output = run_build(tmp_path, "office", path)
    assert code == 2 and report is None and not output.exists()


def test_combining_cluster_spans_and_source_offsets(tmp_path):
    code, report, _ = run_build(tmp_path, "A\u0301 ffi")
    assert code == 0
    glyphs = report["results"][0]["original"]["glyphs"]
    assert glyphs[0]["span"]["text"] == "A\u0301"
    assert any(glyph["span"]["text"] == "ffi" for glyph in glyphs)


def test_vertical_shaping_uses_both_advance_axes(tmp_path):
    code, report, output = run_build(tmp_path, "office", extra=["--direction", "ttb"])
    assert code == 0
    assert report["results"][0]["original"]["advance_em"]["y"] < 0
    assert 'viewBox=' in (output / "specimen.html").read_text()


@pytest.mark.parametrize("text", ["123", "...", "   ", "\u2060", "\ufeff", "\u0301"])
def test_common_only_and_inherited_only_runs_render(text, tmp_path):
    code, report, output = run_build(tmp_path, text)
    assert code == 0
    assert report["results"][0]["original"]["script"] == "Zyyy"
    assert (output / "specimen.html").exists()
