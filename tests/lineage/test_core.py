"""Extraction, alignment, timeline, report, output guards, ArchiveBox and CLI (from the original core suite)."""
import hashlib
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser

import pytest

from readwrench.lineage.align import align_blocks, create_lineage, inline_diff
from readwrench.lineage.archivebox import import_archivebox
from readwrench.lineage.cli import main
from readwrench.lineage.extract import extract_capture
from readwrench.lineage.fileio import InputError, read_bounded, write_outputs
from readwrench.lineage.manifest import iso_time, normalize_url
from readwrench.lineage.render import render_html
from readwrench.lineage.timeline import build_timeline

from .helpers import ARTICLE_TEXTS, FIXTURES, ROOT, SOURCE, block, page


def test_readability_parses_real_new_york_times_fixture():
    result = extract_capture({"file": str(FIXTURES / "nytimes.html"), "inputIndex": 0},
                             "https://www.nytimes.com/2017/01/13/world/africa/sudan-sanctions.html")
    assert re.search("Sudan", result["title"], re.IGNORECASE)
    assert len(result["blocks"]) > 10
    assert any("Obama" in b["text"] for b in result["blocks"])
    assert result["captureTime"]["at"] is None, "article dates are not capture timestamps"
    assert result["extraction"]["method"] == "Mozilla Readability"


def test_real_chrome_mhtml_inconsistent_date_is_reported_not_invented():
    result = extract_capture({"file": str(FIXTURES / "portfolio.mhtml"), "inputIndex": 0}, "http://msindwan.bitbucket.org/")
    assert "Max Sindwani" in result["title"]
    assert result["captureTime"]["at"] is None
    assert any("MHTML Date is invalid, inconsistent" in w for w in result["warnings"])
    assert any("software engineering student" in b["text"] for b in result["blocks"])


def test_tutorial_code_preserves_indentation_line_breaks_and_blank_lines(capture):
    file, entry = capture([], selector="article")
    code = 'def restore():\n    if ready:\n        return "ok"\n\nrestore()\n'
    file.write_text(f"<article><p>Before code</p><pre><code>{code}</code></pre><p>After code</p></article>")
    result = extract_capture(entry, SOURCE)
    assert result["blocks"][1] == {"kind": "pre", "text": code}
    assert [b["text"] for b in result["blocks"] if b["kind"] != "pre"] == ["Before code", "After code"]


def test_legacy_encoding_declaration_is_honoured(capture):
    file, entry = capture([], selector="article")
    file.write_bytes('<meta charset="windows-1252"><article><p>Le caf\xe9 co\xfbte 5 \x80.</p></article>'.encode("latin-1"))
    assert extract_capture(entry, SOURCE)["blocks"][0]["text"] == "Le café coûte 5 €."


def test_preflight_rejects_excessive_elements(capture):
    file, entry = capture()
    file.write_text("<span>x</span>" * 100_001)
    with pytest.raises(InputError, match="exceeds 100000 elements"):
        extract_capture(entry, SOURCE)


def test_captured_scripts_and_resources_are_never_used(capture):
    file, entry = capture()
    file.write_text(page().replace("</head>", '<script>globalThis.__capturedScriptExecuted=true</script>'
                                              '<link rel="stylesheet" href="https://invalid.example/style.css"></head>')
                    .replace("</article>", '<img src="https://invalid.example/beacon"></article>'))
    result = extract_capture(entry, SOURCE)
    assert len(result["blocks"]) >= 3
    assert not any("capturedScriptExecuted" in b["text"] for b in result["blocks"])


def test_singlefile_comment_and_explicit_timestamp_conflict_are_visible(capture):
    file, entry = capture(capturedAt="2026-02-01T10:00:00+02:00")
    file.write_text(f"<!-- Page saved with SingleFile\n url: {SOURCE}#intro\n saved date: Wed Jan 01 2025 12:00:00 "
                    f"GMT+0000 (Coordinated Universal Time)\n-->{page()}")
    result = extract_capture(entry, SOURCE)
    assert result["captureTime"]["at"] == "2026-02-01T08:00:00.000Z"
    assert result["observedTimes"][0]["at"] == "2025-01-01T12:00:00.000Z"
    assert any("conflict" in w for w in result["warnings"])


def test_singlefile_sidecar_hash_is_verified_before_metadata_is_trusted(capture):
    file, entry = capture()
    sha256 = hashlib.sha256(file.read_bytes()).hexdigest()
    sidecar = {"format": "singlefile-manifest", "version": 1, "output": {"sha256": sha256}, "subject": {"url": SOURCE},
               "time": {"captureStarted": "2026-03-01T00:00:00Z"}}
    (file.parent / (file.name + ".manifest.json")).write_text(json.dumps(sidecar))
    result = extract_capture(entry, SOURCE)
    assert result["captureTime"]["at"] == "2026-03-01T00:00:00.000Z"
    assert "hash verified" in result["captureTime"]["source"]
    file.write_text(page([*ARTICLE_TEXTS, "This local change invalidates the sidecar."]))
    with pytest.raises(InputError, match="SHA-256 mismatch"):
        extract_capture(entry, SOURCE)


def test_singlefile_recorded_redirect_is_accepted(capture):
    file, entry = capture()
    final_url = "https://example.org/new-tutorial"
    file.write_text(f"<!-- Page saved with SingleFile\nurl: {final_url}\n-->{page()}")
    sha256 = hashlib.sha256(file.read_bytes()).hexdigest()
    sidecar = {"format": "singlefile-manifest", "version": 1, "output": {"sha256": sha256},
               "subject": {"url": SOURCE, "finalUrl": final_url}, "time": {"captureStarted": "2026-03-01T00:00:00Z"}}
    sidecar_file = file.parent / (file.name + ".manifest.json")
    sidecar_file.write_text(json.dumps(sidecar))
    result = extract_capture(entry, SOURCE)
    assert any("redirect claim" in w for w in result["warnings"])
    assert any(item["url"] == SOURCE for item in result["observedURLs"])
    assert any(item["url"] == final_url for item in result["observedURLs"])
    sidecar["time"]["captureEnded"] = "2026-02-01T00:00:00Z"
    sidecar_file.write_text(json.dumps(sidecar))
    with pytest.raises(InputError, match="precedes captureStarted"):
        extract_capture(entry, SOURCE)


def test_url_mismatch_rejected_unless_alias_is_declared(capture):
    file, entry = capture()
    file.write_text(f"<!-- Page saved with SingleFile\nurl: https://elsewhere.example/article\n-->{page()}")
    with pytest.raises(InputError, match="identifies https://elsewhere"):
        extract_capture(entry, SOURCE)
    result = extract_capture({**entry, "allowUrlMismatch": True}, SOURCE)
    assert any("alias override" in w for w in result["warnings"])


def test_selector_keeps_nested_list_and_blockquote_text_exactly_once(capture):
    file, entry = capture([], selector="article")
    file.write_text("<article>Direct text<ul><li>Parent item<ul><li>Nested item</li></ul>Tail text</li></ul><blockquote>"
                    "<p>Quoted words</p></blockquote><p>Last text<br>Second line</p><p hidden>Hidden material</p></article>")
    assert [b["text"] for b in extract_capture(entry, SOURCE)["blocks"]] == [
        "Direct text", "Parent item", "Nested item", "Tail text", "Quoted words", "Last text", "Second line"]


def test_invalid_selectors_empty_articles_and_invalid_input_fail_clearly(capture):
    file, entry = capture()
    with pytest.raises(InputError, match="Invalid CSS selector"):
        extract_capture({**entry, "selector": "["}, SOURCE)
    with pytest.raises(InputError, match="matched nothing"):
        extract_capture({**entry, "selector": "#missing"}, SOURCE)
    file.write_text("<html><head><script>alert(1)</script></head><body></body></html>")
    with pytest.raises(InputError, match=re.compile("no article|No readable text", re.IGNORECASE)):
        extract_capture(entry, SOURCE)
    with pytest.raises(InputError, match="exceeds"):
        read_bounded(file, 3)


def test_iso_timestamps_require_timezone_and_real_date():
    assert iso_time("2026-03-01T05:00:00+05:00", "test") == "2026-03-01T00:00:00.000Z"
    for value in ("2026-02-30T00:00:00Z", "2026-03-01", "2026-03-01T00:00:00", "tomorrow"):
        with pytest.raises(InputError, match="Invalid"):
            iso_time(value, "test")
    assert normalize_url("HTTPS://EXAMPLE.ORG:443/tutorial#first") == SOURCE
    with pytest.raises(InputError, match="HTTP"):
        normalize_url("javascript:alert(1)")
    with pytest.raises(InputError, match="credentials"):
        normalize_url("https://user:pass@example.org")


def test_anchors_preserve_identity_through_insertion_and_modification():
    before = [block(t) for t in ("First unchanged paragraph.", "Store every backup on the same device.",
                                 "Final unchanged paragraph.")]
    after = [block(t) for t in ("New introduction.", "First unchanged paragraph.",
                                "Store every backup on a separate device.", "Final unchanged paragraph.")]
    result = align_blocks(before, after)
    assert result["pairs"][1]["old"] == 0 and result["pairs"][1]["type"] == "unchanged"
    assert result["pairs"][2]["old"] == 1 and result["pairs"][2]["type"] == "modified"
    assert result["removed"] == []


def test_unique_relocated_paragraphs_are_moves():
    result = align_blocks([block(t) for t in ("A first", "B second", "C third", "D fourth")],
                          [block(t) for t in ("C third", "A first", "B second", "D fourth")])
    assert result["pairs"][0] == {"old": 2, "type": "moved", "confidence": 1}
    assert len(result["pairs"]) == 4


def test_ambiguous_edited_paragraphs_remain_additions_and_removals():
    result = align_blocks([block("We retain customer records for 30 days."), block("We retain customer records for 60 days.")],
                          [block("We retain customer records for 90 days.")])
    assert result["pairs"] == {} and result["removed"] == [0, 1]


def test_same_vocabulary_in_different_order_is_not_an_edit():
    assert align_blocks([block("alpha beta gamma delta epsilon zeta")],
                        [block("zeta epsilon delta gamma beta alpha")])["pairs"] == {}


def test_near_threshold_runners_up_make_identity_ambiguous():
    assert align_blocks([block("a b c d e f g h i j"), block("a b c d e q r s")],
                        [block("a b c d e f x y z")])["pairs"] == {}


def test_short_heading_revisions_share_one_tokenization():
    assert align_blocks([block("Keep a daily backup")], [block("Keep a daily backup safely")])["pairs"][0]["type"] == "modified"


@pytest.mark.parametrize("before, after", [
    ("Keep  two\nspaces.", "Keep two\nspaces!"),
    ("备份保留三十天。👩🏽‍💻", "备份保留六十天。👩🏽‍💻"),
    ("مرحبا بالعالم", "مرحبا بالجميع"),
    ("a < b & c", "</script><script>x</script>"),
])
def test_inline_diff_preserves_both_texts(before, after):
    parts = inline_diff(before, after)
    assert "".join(p["text"] for p in parts if not p["added"]) == before
    assert "".join(p["text"] for p in parts if not p["removed"]) == after


def test_reverse_tied_or_unknown_capture_points_have_no_interval_bounds():
    def interval(left, right):
        captures = [{"id": "a", "captureTime": {"at": left}, "blocks": [block("First text.")]},
                    {"id": "b", "captureTime": {"at": right}, "blocks": [block("Second text.")]}]
        return create_lineage(captures)["transitions"][0]["observedInterval"]
    for left, right in (("2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z"),
                        ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"), (None, "2026-02-01T00:00:00Z")):
        found = interval(left, right)
        assert [found["chronological"], found["after"], found["by"]] == [False, None, None]
    valid = interval("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
    assert valid["chronological"] is True and valid["after"] == "2026-01-01T00:00:00Z"


def test_capture_order_is_chronological_only_when_every_time_is_known(tmp_path):
    (tmp_path / "a.html").write_text(page())
    (tmp_path / "b.html").write_text(page([*ARTICLE_TEXTS, "A new final paragraph."]))
    manifest = {"version": 1, "url": SOURCE, "captures": [{"file": "b.html", "capturedAt": "2026-02-01T00:00:00Z"},
                                                           {"file": "a.html", "capturedAt": "2026-01-01T00:00:00Z"}]}
    filename = tmp_path / "manifest.json"
    filename.write_text(json.dumps(manifest))
    timeline = build_timeline(filename)["timeline"]
    assert [c["id"] for c in timeline["captures"]] == ["c2", "c1"]
    assert timeline["transitions"][0]["counts"]["added"] == 1
    assert timeline["ordering"] == "capture-time"
    del manifest["captures"][1]["capturedAt"]
    filename.write_text(json.dumps(manifest))
    timeline = build_timeline(filename)["timeline"]
    assert [c["id"] for c in timeline["captures"]] == ["c1", "c2"]
    assert timeline["ordering"] == "manifest"
    assert "unknown" in timeline["warnings"][0]


class _Report(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: dict[str, int] = {}
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.csp = None
        self.searches: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags[tag] = self.tags.get(tag, 0) + 1
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a" and (attrs.get("href") or "").startswith("#"):
            self.links.append(attrs["href"][1:])
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy":
            self.csp = attrs["content"]
        if "data-search" in attrs:
            self.searches.append(json.loads(attrs["data-search"]))


def _report(text: str) -> _Report:
    parser = _Report()
    parser.feed(text)
    parser.close()
    return parser


def test_report_escapes_captured_markup_and_links_resolve(tmp_path):
    (tmp_path / "a.html").write_text(page())
    (tmp_path / "b.html").write_text(page([*ARTICLE_TEXTS, "&lt;/script&gt;&lt;script&gt;globalThis.pwned=1&lt;/script&gt;"]))
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "url": SOURCE, "captures": [
        {"file": "a.html"}, {"file": "b.html", "label": "<img src=x onerror=alert(1)>"}]}))
    report = render_html(build_timeline(tmp_path / "manifest.json")["timeline"])
    parsed = _report(report)
    assert parsed.tags.get("script") == 1
    assert "img" not in parsed.tags
    assert "connect-src 'none'" in parsed.csp
    assert all(link in parsed.ids for link in parsed.links)
    # The quote lookup searches unchanged baseline text in both captures.
    assert len(re.findall(r'class="captured-block"><a[^>]*>[^<]*</a><div class="block-text" dir="auto">[^<]*primary storage',
                          report)) == 2


def test_title_change_is_visible_when_paragraphs_are_unchanged(tmp_path):
    (tmp_path / "a.html").write_text(page().replace("A changing tutorial", "Old tutorial title"))
    (tmp_path / "b.html").write_text(page().replace("A changing tutorial", "New tutorial title"))
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "url": SOURCE,
                                                        "captures": [{"file": "a.html"}, {"file": "b.html"}]}))
    timeline = build_timeline(tmp_path / "manifest.json")["timeline"]
    transition = timeline["transitions"][0]
    assert transition["changes"] == []
    assert "Old tutorial title" in transition["titleChange"]["before"]
    assert "New tutorial title" in transition["titleChange"]["after"]
    report = render_html(timeline)
    assert "Extracted title changed" in report
    # Old and new titles are searched separately, so a quote cannot span both versions.
    assert ["Old tutorial title", "New tutorial title"] in _report(report).searches


def test_output_guards_protect_inputs_existing_files_and_aliases(tmp_path):
    source = tmp_path / "source.html"
    source.write_text("original")
    with pytest.raises(InputError, match="overwrite an input"):
        write_outputs([{"filename": source, "data": "changed"}], force=True, protected_files=[source])
    alias = tmp_path / "alias.html"
    alias.symlink_to(source)
    with pytest.raises(InputError, match="overwrite an input"):
        write_outputs([{"filename": alias, "data": "changed"}], force=True, protected_files=[source])
    with pytest.raises(InputError, match="Output exists"):
        write_outputs([{"filename": source, "data": "changed"}])
    out = tmp_path / "out.html"
    with pytest.raises(InputError, match="different"):
        write_outputs([{"filename": out, "data": "html"}, {"filename": out, "data": "json"}])
    assert source.read_text() == "original"


def test_staging_failure_leaves_no_partial_outputs(tmp_path):
    obstruction = tmp_path / "ordinary-file"
    obstruction.write_text("keep this original content")
    output = tmp_path / "report.html"
    with pytest.raises(InputError, match="Cannot write report"):
        write_outputs([{"filename": output, "data": "a report"}, {"filename": obstruction / "report.json", "data": "{}"}])
    assert not output.exists()
    assert obstruction.read_text() == "keep this original content"
    assert os.listdir(tmp_path) == ["ordinary-file"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
def test_fifo_inputs_and_outputs_fail_promptly(tmp_path):
    fifo = tmp_path / "capture.html"
    os.mkfifo(fifo)
    code = ("import sys\nfrom readwrench.lineage.fileio import read_bounded, write_outputs\n"
            "for call in (lambda: read_bounded(sys.argv[1]), lambda: write_outputs([{'filename': sys.argv[1], 'data': 'r'}])):\n"
            "    try:\n        call()\n        sys.exit(2)\n    except Exception as error:\n        print(error)\n")
    result = subprocess.run([sys.executable, "-c", code, str(fifo)], capture_output=True, text=True, timeout=10,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert "Not a regular file" in result.stdout
    assert "Output exists" in result.stdout


def test_archivebox_adapters_use_extractor_start_never_bookmark_date(tmp_path):
    (tmp_path / "singlefile.html").write_text(page())
    old = tmp_path / "index.json"
    old.write_text(json.dumps({"url": SOURCE, "timestamp": "1000000000", "history": {"singlefile": [
        {"output": "singlefile.html", "status": "succeeded", "start_ts": "2026-02-01T01:02:03+00:00"}]}}))
    output = tmp_path / "captures.json"
    assert import_archivebox([str(old)], str(output))["manifest"]["captures"][0]["capturedAt"] == "2026-02-01T01:02:03.000Z"
    modern = tmp_path / "index.jsonl"
    modern.write_text("\n".join(json.dumps(record) for record in [
        {"type": "Snapshot", "id": "one", "url": SOURCE, "timestamp": "1000000000"},
        {"type": "ArchiveResult", "snapshot_id": "one", "plugin": "singlefile", "status": "succeeded",
         "output_files": {"singlefile.html": {"size": 123}}, "start_ts": "2026-03-01T01:02:03Z"}]))
    assert import_archivebox([str(modern)], str(output))["manifest"]["captures"][0]["capturedAt"] == "2026-03-01T01:02:03.000Z"
    old.write_text(json.dumps({"url": SOURCE, "timestamp": "1000000000"}))
    result = import_archivebox([str(old)], str(output))
    assert "capturedAt" not in result["manifest"]["captures"][0]
    assert any("not used as capture time" in w for w in result["warnings"])


def test_cli_handles_errors_and_builds_both_outputs(tmp_path, capture, capsys):
    file, _ = capture()
    manifest = tmp_path / "captures.json"
    assert main(["init", "--url", SOURCE, "--out", str(manifest), str(file)]) == 0
    output, json_output = tmp_path / "timeline.html", tmp_path / "timeline.json"
    assert main(["build", str(manifest), "--out", str(output), "--json", str(json_output), "--quiet"]) == 0
    assert "Page Lineage" in output.read_text()
    assert len(json.loads(json_output.read_text())["captures"]) == 1
    assert main(["build", str(manifest), "--out", str(output)]) == 1
    assert "Output exists" in capsys.readouterr().err
    assert main(["build", str(manifest), "--out", str(output), "--typo"]) == 1
    assert "readwrench lineage:" in capsys.readouterr().err


def test_module_entry_point_reports_version():
    result = subprocess.run([sys.executable, "-m", "readwrench", "lineage", "--version"], capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=60)
    assert result.returncode == 0
    assert result.stdout.startswith("readwrench lineage ")
