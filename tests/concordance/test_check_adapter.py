"""``readwrench check`` adapter: EPUB intake problems and saved-workspace verification."""
import sqlite3
from zipfile import ZipFile

from readwrench.check.model import Source

from .helpers import require

require()

from readwrench.concordance import _check  # noqa: E402


def epub_source(path):
    return Source("epub", path, path.name, "EPUB")


def workspace_source(path):
    manifest = path / "manifest.json"
    return Source(_check.WORKSPACE_KIND, manifest, f"{path.name}/manifest.json", "workspace")


def rules(findings):
    return sorted(finding.rule for finding in findings)


def test_classify_recognises_only_workspace_manifests(simple_workspace, tmp_path):
    path, _ = simple_workspace
    manifest = path / "manifest.json"
    head = manifest.read_bytes()[:65536]
    assert _check.classify(manifest, head) == (_check.WORKSPACE_KIND, "readwrench concordance workspace")
    other = tmp_path / "manifest.json"
    other.write_text('{"name": "something else"}')
    assert _check.classify(other, other.read_bytes()) is None
    assert _check.classify(tmp_path / "notes.json", head) is None


def test_readable_epub_has_no_findings(make_book):
    assert list(_check.check_epub(epub_source(make_book()))) == []


def test_encrypted_prose_is_reported(make_book):
    source = make_book()
    with ZipFile(source, "a") as archive:
        archive.writestr("META-INF/encryption.xml", '<encryption><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/><CipherData><CipherReference URI="EPUB/text/chapter.xhtml"/></CipherData></EncryptedData></encryption>')
    finding, = _check.check_epub(epub_source(source))
    assert (finding.check, finding.rule, finding.severity) == ("concordance-epub", "encrypted-text", "warning")
    assert finding.hint.startswith("readwrench concordance init ")


def test_broken_archive_is_reported(tmp_path):
    path = tmp_path / "broken.epub"
    path.write_text("not a ZIP")
    assert rules(_check.check_epub(epub_source(path))) == ["unreadable"]


def test_fresh_workspace_only_reports_unreviewed_candidates(simple_workspace):
    path, _ = simple_workspace
    finding, = _check.check_workspace(workspace_source(path))
    assert (finding.rule, finding.severity) == ("unreviewed", "info")
    assert finding.message.startswith("4 of 4 candidate occurrences")


def test_moved_or_replaced_source_edition(simple_workspace):
    path, source = simple_workspace
    with ZipFile(source, "a") as archive:
        archive.comment = b"different edition bytes"
    assert "source-changed" in rules(_check.check_workspace(workspace_source(path)))
    source.unlink()
    assert "source-missing" in rules(_check.check_workspace(workspace_source(path)))


def test_tampered_snapshot_is_an_error(simple_workspace):
    path, _ = simple_workspace
    next((path / "text").iterdir()).write_text("tampered")
    finding, = _check.check_workspace(workspace_source(path))
    assert (finding.rule, finding.severity) == ("invalid-workspace", "error")


def test_conflicting_decision_blocks_export(simple_workspace):
    from readwrench.concordance.workspace import collect, label
    path, _ = simple_workspace
    hit = collect(path)[2][0]
    label(path, hit["id"], senses=["capacity"])
    with sqlite3.connect(path / "project.sqlite3") as db:
        tag_id = db.execute("SELECT id FROM tags WHERE path='status/rejected'").fetchone()[0]
        db.execute("INSERT INTO highlight_tags VALUES (?, ?)", (hit["taguette_highlight_id"], tag_id))
    before = (path / "project.sqlite3").read_bytes()
    findings = list(_check.check_workspace(workspace_source(path)))
    assert "conflicting-decisions" in rules(findings)
    assert (path / "project.sqlite3").read_bytes() == before


def test_missing_database(simple_workspace):
    path, _ = simple_workspace
    (path / "project.sqlite3").unlink()
    assert rules(_check.check_workspace(workspace_source(path))) == ["database-missing"]
