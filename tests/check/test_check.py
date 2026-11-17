"""``readwrench check``: discovery by content, crash isolation, missing extras, formats and exit codes."""
import json
import subprocess
import sys
import zipfile

import pytest

from readwrench.check import cli, discover, registry
from readwrench.check.discover import classify_generic
from readwrench.check.model import ERROR, INFO, WARNING, Check, Finding, MissingDependency, PDF


def make_epub(path, *, mimetype_first=True):
    with zipfile.ZipFile(path, "w") as archive:
        members = [("META-INF/container.xml", "<container/>")]
        mimetype = ("mimetype", "application/epub+zip")
        for name, data in ([mimetype, *members] if mimetype_first else [*members, mimetype]):
            archive.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_STORED)
    return path


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "reading"
    (root / "books").mkdir(parents=True)
    make_epub(root / "books" / "novel.epub")
    (root / "paper.pdf").write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n")
    (root / "fonts").mkdir()
    (root / "fonts" / "Body.otf").write_bytes(b"OTTO" + b"\x00" * 60)
    (root / "page.html").write_text("<!doctype html><html><body><p>Hello</p></body></html>", encoding="utf-8")
    (root / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (root / "broken.pdf").write_text("this is not a pdf", encoding="utf-8")
    (root / ".hidden").mkdir()
    make_epub(root / ".hidden" / "skipped.epub")
    return root


def kinds(found):
    return sorted((source.display.rsplit("/", 1)[-1], source.kind) for source in found.sources)


def test_discovery_classifies_by_content_and_skips_hidden(library):
    found = discover.discover([str(library)])
    assert kinds(found) == [("Body.otf", "font"), ("notes.md", "text"), ("novel.epub", "epub"),
                            ("page.html", "html"), ("paper.pdf", "pdf")]
    misnamed = [f for f in found.findings if f.rule == "misnamed"]
    assert [f.file.rsplit("/", 1)[-1] for f in misnamed] == ["broken.pdf"]
    assert misnamed[0].severity == WARNING


def test_epub_with_mimetype_not_first_is_still_an_epub(tmp_path):
    path = make_epub(tmp_path / "late.epub", mimetype_first=False)
    head = path.read_bytes()[:65536]
    assert classify_generic(path, head) == ("epub", "EPUB book")


def test_plain_zip_is_not_an_epub(tmp_path):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.txt", "x")
    assert classify_generic(path, path.read_bytes()) is None


def test_warc_and_mhtml_are_recognised(tmp_path):
    import gzip

    warc = tmp_path / "capture.warc.gz"
    warc.write_bytes(gzip.compress(b"WARC/1.1\r\nWARC-Type: warcinfo\r\n\r\n"))
    mhtml = tmp_path / "capture.mht"
    mhtml.write_text("MIME-Version: 1.0\r\nContent-Type: multipart/related; boundary=x\r\n\r\n", encoding="ascii")
    assert classify_generic(warc, warc.read_bytes())[0] == "warc"
    assert classify_generic(mhtml, mhtml.read_bytes())[0] == "mhtml"


def test_missing_path_is_a_usage_error(tmp_path, capsys):
    assert cli.main([str(tmp_path / "nope")]) == 2
    assert "no such file or directory" in capsys.readouterr().err


def test_explicit_unrecognised_file_is_an_info_finding(tmp_path):
    other = tmp_path / "data.bin"
    other.write_bytes(b"\x00\x01\x02")
    found = discover.discover([str(other)])
    assert [(f.rule, f.severity) for f in found.findings] == [("unrecognised", INFO)]


def test_list_checks_lists_every_check(capsys):
    assert cli.main(["--list-checks"]) == 0
    out = capsys.readouterr().out
    for check in registry.CHECKS:
        assert f"  {check.name} " in out


def test_unknown_check_name_exits_2(tmp_path, capsys):
    assert cli.main([str(tmp_path), "--only", "no-such-check"]) == 2
    assert "unknown check(s): no-such-check" in capsys.readouterr().err


def test_only_and_skip_leaving_nothing_exits_2(tmp_path, capsys):
    assert cli.main([str(tmp_path), "--only", "inputs", "--skip", "inputs"]) == 2
    assert "leave no check to run" in capsys.readouterr().err


@pytest.mark.parametrize("fail_on,code", [("never", 0), ("error", 0), ("warning", 1)])
def test_fail_on_threshold(library, fail_on, code, capsys):
    # The misnamed broken.pdf is a warning; with only the built-in inputs check nothing is an error.
    assert cli.main([str(library), "--only", "inputs", "--fail-on", fail_on]) == code


def test_empty_folder_says_nothing_to_check(tmp_path, capsys):
    assert cli.main([str(tmp_path), "--color", "never"]) == 0
    assert "Nothing to check here" in capsys.readouterr().out


def test_json_report_shape(library, capsys):
    assert cli.main([str(library), "--only", "inputs", "--format", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["format"] == "readwrench.check/v1"
    assert document["tool"]["name"] == "readwrench"
    assert document["checks"] == ["inputs"]
    assert {item["kind"] for item in document["inputs"]} == {"epub", "pdf", "font", "html", "text"}
    assert document["summary"]["warning"] == 1 and document["failed"] is False


def test_sarif_report_is_2_1_0(library, capsys):
    assert cli.main([str(library), "--only", "inputs", "--format", "sarif"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["version"] == "2.1.0"
    run = document["runs"][0]
    assert run["tool"]["driver"]["name"] == "readwrench"
    assert [result["ruleId"] for result in run["results"]] == ["inputs/misnamed"]


def test_markdown_report(library, capsys):
    assert cli.main([str(library), "--only", "inputs", "--format", "markdown"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("## readwrench check") and "`inputs/misnamed`" in out


def _fake(name, run, *, requires=(), extra=None):
    return Check(name, PDF, f"fake {name}", frozenset({"pdf"}), run, requires=requires, extra=extra, tool="-")


def test_crashing_check_is_isolated(library):
    def boom(source):
        yield Finding("boom", "first", WARNING, "before the crash", source.display)
        raise RuntimeError("secret document text")

    def fine(source):
        yield Finding("fine", "ok", INFO, "still ran", source.display)

    found = discover.discover([str(library)])
    findings = registry.run_checks(found, [_fake("boom", boom), _fake("fine", fine)])
    rules = sorted(f.rule_id for f in findings)
    assert rules == ["boom/first", "boom/internal", "fine/ok"]
    internal = next(f for f in findings if f.rule == "internal")
    assert internal.severity == ERROR and "RuntimeError" in internal.message
    assert "secret" not in internal.message


def test_missing_optional_dependency_is_one_info_finding(library):
    def needs_extra(source):
        raise MissingDependency("fitz")
        yield  # pragma: no cover

    found = discover.discover([str(library)])
    (finding,) = registry.run_checks(found, [_fake("pdfy", needs_extra, requires=("fitz",), extra="reanchor")])
    assert (finding.rule, finding.severity) == ("missing-dependency", INFO)
    assert "readwrench[reanchor]" in finding.hint


def test_module_not_found_of_declared_requirement_counts_as_missing(library):
    def imports(source):
        import readwrench_no_such_module  # noqa: F401
        yield  # pragma: no cover

    found = discover.discover([str(library)])
    (finding,) = registry.run_checks(found, [_fake("imp", imports, requires=("readwrench_no_such_module",))])
    assert finding.rule == "missing-dependency"


def test_check_names_are_unique_and_described():
    names = [check.name for check in registry.CHECKS]
    assert len(names) == len(set(names))
    assert all(check.summary and check.area for check in registry.CHECKS)
    assert registry.CHECKS[-1].name == "inputs"
    for check in registry.CHECKS:
        if check.tool != "-":
            from readwrench.cli import BY_NAME
            assert check.tool in BY_NAME, check
            assert check.extra is None or check.extra == BY_NAME[check.tool].extra, check


def test_adapters_load_without_problems():
    assert registry.LOAD_PROBLEMS == []


def test_runs_through_the_dispatcher(library):
    result = subprocess.run([sys.executable, "-m", "readwrench", "check", str(library), "--only", "inputs"],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("readwrench check")
