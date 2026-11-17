from __future__ import annotations

import pytest

pytest.importorskip("lxml")
pytest.importorskip("ebooklib")

import copy
import json
from pathlib import Path
from zipfile import ZipFile


from readwrench.footnotes.audit import audit
from readwrench.footnotes.cli import main, read_json
from readwrench.footnotes.epub import Book, EpubError
from readwrench.footnotes.patch import make_patch
from tests.footnotes.books import FIXTURES, approved, make_book, rewrite


def add_member(path, tmp_path, name, data):
    output = tmp_path / "added.epub"
    with ZipFile(path) as source, ZipFile(output, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr(name, data)
    return output


@pytest.fixture
def source(tmp_path):
    return make_book(tmp_path / "book.epub")


@pytest.mark.parametrize("name,data,message", [
    ("../outside.txt", b"escape", "unsafe"),
    ("META-INF/signatures.xml", b"<signatures/>", "Signed"),
    ("META-INF/encryption.xml", b'<encryption><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"/></encryption>', "Encrypted"),
])
def test_unsupported_archives_never_extract(source, tmp_path, name, data, message):
    variant = add_member(source, tmp_path, name, data)
    with pytest.raises(EpubError, match=message):
        Book(variant)
    assert not (tmp_path / "outside.txt").exists()


def test_duplicate_zip_entries_are_rejected(source, tmp_path):
    with pytest.warns(UserWarning, match="Duplicate"):
        variant = add_member(source, tmp_path, "mimetype", b"application/epub+zip")
    with pytest.raises(EpubError, match="Duplicate archive"):
        Book(variant)


def test_expansion_limits_checked_before_reading_members(source, monkeypatch):
    monkeypatch.setattr("readwrench.footnotes.epub.MAX_EXPANDED", 100)
    with pytest.raises(EpubError, match="expanded-size"):
        Book(source)


@pytest.mark.parametrize("extra,message", [
    ('<p xml:base="../">text</p>', "xml:base"),
    ('<base href="https://example.org/"/>', "base URL"),
])
def test_base_urls_are_not_silently_misresolved(source, extra, message):
    with ZipFile(source) as z:
        data = z.read("EPUB/Text/chapter.xhtml").replace(b"<body>", b"<body>" + extra.encode())
    variant = rewrite(source, {"EPUB/Text/chapter.xhtml": data})
    with pytest.raises(EpubError, match=message):
        Book(variant)


def test_internal_entities_are_rejected_without_reading_local_files(source):
    with ZipFile(source) as z:
        data = z.read("EPUB/Text/chapter.xhtml")
    text = data.decode().replace('<!DOCTYPE html>', '<!DOCTYPE html [<!ENTITY secret SYSTEM "file:///etc/passwd">]>')
    if text == data.decode():
        text = text.replace('<html', '<!DOCTYPE html [<!ENTITY secret SYSTEM "file:///etc/passwd">]><html', 1)
    variant = rewrite(source, {"EPUB/Text/chapter.xhtml": text.encode()})
    with pytest.raises(EpubError, match="Internal DTD subsets"):
        Book(variant)


@pytest.mark.parametrize("value", [None, [], {}, 1, True, "approve"])
def test_invalid_decision_actions_produce_a_controlled_error(source, value):
    with Book(source) as book:
        fresh = audit(book)
        plan = approved(fresh.plan)
        plan["references"][0]["action"] = value
        with pytest.raises(EpubError, match="Invalid review action"):
            make_patch(book, fresh, plan)


def test_duplicate_decisions_are_rejected(source):
    with Book(source) as book:
        fresh = audit(book)
        plan = approved(fresh.plan)
        plan["references"].append(copy.deepcopy(plan["references"][0]))
        with pytest.raises(EpubError, match="Duplicate or unknown"):
            make_patch(book, fresh, plan)


@pytest.mark.parametrize("label", ["", " ", "bad\nlabel", "bad\x00label", "x" * 257, [], 1, "bad\ud800", "bad\ufffe"])
def test_invalid_labels_cannot_reach_xml(source, label):
    with Book(source) as book:
        fresh = audit(book)
        plan = approved(fresh.plan)
        plan["references"][0]["backlink_label"] = label
        with pytest.raises(EpubError, match="printable backlink label"):
            make_patch(book, fresh, plan)


def test_keep_explicitly_does_not_claim_an_exact_return(source):
    with Book(source) as book:
        fresh = audit(book)
        plan = approved(fresh.plan)
        plan["references"][0]["action"] = "keep"
        patch = make_patch(book, fresh, plan)
        assert patch.changes == {}
        assert patch.manifest["paths"][0]["result"] == "kept-without-exact-return"


@pytest.mark.parametrize("contents", ['{"schema_version":1,"schema_version":2}', '{"value":NaN}', '[]'])
def test_ambiguous_json_is_rejected(tmp_path, contents):
    path = tmp_path / "bad.json"
    path.write_text(contents)
    with pytest.raises(EpubError):
        read_json(path)


def test_cli_refuses_overwriting_review(source, tmp_path, capsys):
    output = tmp_path / "scan"
    assert main(["scan", str(source), "--out", str(output)]) == 0
    sentinel = output / "sentinel.txt"
    sentinel.write_text("keep")
    assert main(["scan", str(source), "--out", str(output)]) == 2
    assert sentinel.read_text() == "keep"
    assert "Output already exists" in capsys.readouterr().err


def test_cli_skips_validation_only_explicitly(source, tmp_path, monkeypatch):
    monkeypatch.delenv("EPUBCHECK_JAR", raising=False)
    with Book(source) as book:
        plan = approved(audit(book).plan)
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps(plan))
    output = tmp_path / "candidate"
    assert main(["candidate", str(source), str(decisions), "--out", str(output)]) == 2
    assert not output.exists()
    assert main(["candidate", str(source), str(decisions), "--out", str(output), "--skip-epubcheck"]) == 0
    result = json.loads((output / "manifest.json").read_text())
    assert result["format_validation"]["status"] == "not_run"
    assert result["accessibility_status"] == "not_assessed"


def test_cli_partial_terminal_review_survives_eof(source, tmp_path, monkeypatch):
    with Book(source) as book:
        plan = audit(book).plan
    plan_path, output = tmp_path / "plan.json", tmp_path / "decisions.json"
    plan_path.write_text(json.dumps(plan))
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError()))
    assert main(["review", str(plan_path), "--out", str(output)]) == 0
    assert not json.loads(output.read_text())["references"][0]["approved"]


def test_grouped_note_class_does_not_merge_individual_notes(tmp_path):
    chapter = '<p>One <a role="doc-noteref" href="notes.xhtml#n1">1</a>; two <a role="doc-noteref" href="notes.xhtml#n2">2</a>.</p>'
    notes = '<div class="footnotes"><ol><li id="n1"><p>First note.</p></li><li id="n2"><p>Second note.</p></li></ol></div>'
    path = make_book(tmp_path / "grouped.epub", {"Text/chapter.xhtml": chapter}, notes)
    with Book(path) as book:
        fresh = audit(book)
        refs = fresh.plan["references"]
        assert refs[0]["note_key"] != refs[1]["note_key"]
        assert {fresh.notes[r["note_key"]][1].tag.rsplit("}", 1)[-1] for r in refs} == {"li"}


def test_anchor_inside_paragraph_is_not_mistaken_for_whole_note(tmp_path):
    notes = '<p><span id="note-1">1.</span> Actual explanatory note.</p>'
    path = make_book(tmp_path / "inline-anchor.epub", notes=notes)
    with Book(path) as book:
        fresh = audit(book)
        key = fresh.plan["references"][0]["note_key"]
        assert fresh.notes[key][1].tag.endswith("}p")
        patch = make_patch(book, fresh, approved(fresh.plan))
        assert b'Actual explanatory note. <a' in patch.changes["EPUB/Text/notes.xhtml"]


def test_library_api_cannot_overwrite_source_or_an_existing_candidate(source, tmp_path):
    original = source.read_bytes()
    with Book(source) as book:
        with pytest.raises(EpubError, match="differ from the source"):
            book.write_candidate(source, {})
        output = tmp_path / "existing.epub"
        output.write_bytes(b"keep")
        with pytest.raises(EpubError, match="already exists"):
            book.write_candidate(output, {})
    assert source.read_bytes() == original
    assert output.read_bytes() == b"keep"


def test_source_change_during_processing_discards_cli_bundle(source, tmp_path, monkeypatch):
    with Book(source) as book:
        decisions = approved(audit(book).plan)
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(decisions))
    monkeypatch.setattr(Book, "verify_unchanged", lambda self: (_ for _ in ()).throw(EpubError("changed during processing")))
    output = tmp_path / "candidate"
    assert main(["candidate", str(source), str(path), "--out", str(output), "--skip-epubcheck"]) == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".footnotes-*"))


def test_terminal_interrupt_saves_partial_decisions(source, tmp_path, monkeypatch):
    with Book(source) as book:
        plan = audit(book).plan
    path, output = tmp_path / "plan.json", tmp_path / "decisions.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert main(["review", str(path), "--out", str(output)]) == 0
    assert output.exists()


def test_generated_epub2_backlinks_are_excluded_in_all_links_mode(tmp_path):
    source_path = FIXTURES / "sigil-linked-epub2.epub"
    with ZipFile(source_path) as z:
        notes = z.read("OEBPS/Text/endnotes.xhtml").replace(b'class="note"', b'class="untyped"')
    source = rewrite(source_path, {"OEBPS/Text/endnotes.xhtml": notes}, tmp_path / "untyped.epub")
    with Book(source) as book:
        fresh = audit(book, {"all_links": True})
        assert len(fresh.plan["references"]) == 4
        patch = make_patch(book, fresh, approved(fresh.plan))
        candidate = tmp_path / "candidate.epub"
        book.write_candidate(candidate, patch.changes)
    with Book(candidate) as book:
        assert len(audit(book, {"all_links": True}).plan["references"]) == 4


def test_dtd_default_id_cannot_masquerade_as_an_explicit_return_anchor(source):
    with ZipFile(source) as z:
        data = z.read("EPUB/Text/chapter.xhtml")
    text = data.decode().replace('<!DOCTYPE html>', '<!DOCTYPE html [<!ATTLIST a id CDATA "defaulted">]>')
    variant = rewrite(source, {"EPUB/Text/chapter.xhtml": text.encode()})
    with pytest.raises(EpubError, match="Internal DTD subsets"):
        Book(variant)


def test_partial_candidate_write_failure_cleans_staging(source, tmp_path, monkeypatch):
    original = source.read_bytes()
    with Book(source) as book:
        plan = approved(audit(book).plan)
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps(plan))

    def failed_write(self, path, changes):
        path.write_bytes(b"partial ZIP")
        raise OSError("No space left on device (injected)")

    monkeypatch.setattr(Book, "write_candidate", failed_write)
    output = tmp_path / "candidate"
    assert main(["candidate", str(source), str(decisions), "--out", str(output), "--skip-epubcheck"]) == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".footnotes-*"))
    assert source.read_bytes() == original


def test_partial_review_save_is_removed_on_disk_failure(source, tmp_path, monkeypatch):
    with Book(source) as book:
        plan = audit(book).plan
    path, output = tmp_path / "plan.json", tmp_path / "decisions.json"
    path.write_text(json.dumps(plan))
    original_open = Path.open

    class FailedStream:
        def __enter__(self):
            self.stream = original_open(output, "xb")
            return self

        def write(self, data):
            self.stream.write(b"partial JSON")
            raise OSError("No space left on device (injected)")

        def __exit__(self, *args):
            self.stream.close()

    def open_path(self, *args, **kwargs):
        if self == output and args and args[0] == "xb":
            return FailedStream()
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "a")
    assert main(["review", str(path), "--out", str(output)]) == 2
    assert not output.exists()
