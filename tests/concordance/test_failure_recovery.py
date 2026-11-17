import json
import shutil
import sqlite3
from zipfile import ZipFile

import pytest

from .helpers import require

require()

from readwrench.concordance.books import InputError, read_book
from readwrench.concordance.cli import main
from readwrench.concordance.concepts import load_concepts
from readwrench.concordance.workspace import collect, initialize, label


def test_duplicate_json_keys_rejected_before_search(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"concepts":[{"id":"a","aliases":["law"],"aliases":["power"]}]}')
    with pytest.raises(InputError, match="Duplicate JSON"):
        load_concepts(path)


def test_oversized_query_file_fails_before_loading(tmp_path):
    path = tmp_path / "huge.json"
    with path.open("wb") as stream:
        stream.truncate(8 * 1024 * 1024 + 1)
    with pytest.raises(InputError, match="size limit"):
        load_concepts(path)


def test_nul_alias_rejected(concepts_file):
    with pytest.raises(InputError, match="NUL"):
        load_concepts(concepts_file([{"id": "a", "aliases": ["\x00"]}]))


def test_malformed_encryption_metadata_is_an_input_error(tmp_path, make_book):
    source = make_book()
    with ZipFile(source, "a") as archive:
        archive.writestr("META-INF/encryption.xml", "<encryption>not well-formed")
    with pytest.raises(InputError, match="encryption metadata"):
        read_book(source)


def test_font_obfuscation_declaration_cannot_hide_encrypted_prose(make_book):
    source = make_book()
    with ZipFile(source, "a") as archive:
        archive.writestr("META-INF/encryption.xml", '<encryption><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/><CipherData><CipherReference URI="EPUB/text/chapter.xhtml"/></CipherData></EncryptedData></encryption>')
    with pytest.raises(InputError, match="encrypted.*prose|prose.*encrypted"):
        read_book(source)


def test_font_obfuscation_does_not_block_unencrypted_prose(make_book):
    source = make_book()
    with ZipFile(source, "a") as archive:
        archive.writestr("EPUB/fonts/obfuscated.ttf", b"synthetic obfuscated font bytes; never parsed")
        archive.writestr("META-INF/encryption.xml", '<encryption><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/><CipherData><CipherReference URI="EPUB/fonts/obfuscated.ttf"/></CipherData></EncryptedData></encryption>')
    assert read_book(source).chapters[0].text.endswith("Justice.")


def test_researcher_resized_unicode_quote_is_preserved(tmp_path, make_book, concepts_file):
    path = tmp_path / "resized"
    initialize([make_book(content="<p>自由 and power in context.</p>")], concepts_file(), path)
    text = collect(path)[1].popitem()[1]
    hit = collect(path)[2][0]
    with sqlite3.connect(path / "project.sqlite3") as db:
        db.execute("UPDATE highlights SET start_offset=?,end_offset=? WHERE id=?", (0, len(text.encode()), hit["taguette_highlight_id"]))
    resized = collect(path)[2][0]
    assert resized["exact"] == text
    assert resized["id"] != hit["id"]
    label(path, resized["id"], senses=["researcher selected full context"])
    assert collect(path)[2][0]["status"] == "classified"


def test_bad_database_schema_is_not_migrated(simple_workspace):
    path, _ = simple_workspace
    with sqlite3.connect(path / "project.sqlite3") as db:
        db.execute("UPDATE alembic_version SET version_num='unknown-future-version'")
    original = (path / "project.sqlite3").read_bytes()
    with pytest.raises(InputError, match="schema"):
        collect(path)
    assert (path / "project.sqlite3").read_bytes() == original


def test_badly_typed_sqlite_offsets_are_input_errors(simple_workspace):
    path, _ = simple_workspace
    with sqlite3.connect(path / "project.sqlite3") as db:
        db.execute("UPDATE highlights SET start_offset='invalid offset'")
    with pytest.raises(InputError, match="offset"):
        collect(path)


def test_oversized_external_database_fails_without_opening(simple_workspace, tmp_path):
    path, _ = simple_workspace
    large = tmp_path / "too-large.sqlite3"
    with large.open("wb") as stream:
        stream.truncate(512 * 1024 * 1024 + 1)
    with pytest.raises(InputError, match="size limit"):
        collect(path, database=large)


def test_deleted_marker_fails_with_actionable_error(simple_workspace):
    path, _ = simple_workspace
    with sqlite3.connect(path / "project.sqlite3") as db:
        db.execute("UPDATE documents SET description='User removed metadata'")
    with pytest.raises(InputError, match="marker|matching Taguette project"):
        collect(path)


def test_renamed_document_does_not_change_source_identity(simple_workspace):
    path, _ = simple_workspace
    original = collect(path)[2]
    with sqlite3.connect(path / "project.sqlite3") as db:
        db.execute("UPDATE documents SET name='Researcher display name'")
    assert [h["id"] for h in collect(path)[2]] == [h["id"] for h in original]


def test_readonly_export_sees_committed_wal_data(simple_workspace):
    path, _ = simple_workspace
    with sqlite3.connect(path / "project.sqlite3") as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        hit = collect(path)[2][0]
        label(path, hit["id"], senses=["committed in WAL"])
        assert collect(path)[2][0]["senses"] == ["committed in WAL"]


def test_database_paths_with_uri_reserved_characters(simple_workspace, tmp_path):
    path, _ = simple_workspace
    copy = tmp_path / "taguette # ? %.sqlite3"
    shutil.copyfile(path / "project.sqlite3", copy)
    assert len(collect(path, database=copy)[2]) == 4


def test_cli_can_recover_empty_sense_tag(simple_workspace, capsys):
    path, _ = simple_workspace
    hit = collect(path)[2][0]
    with sqlite3.connect(path / "project.sqlite3") as db:
        db.execute("INSERT INTO tags (project_id,path,description) VALUES (1,'sense/','invalid empty category')")
        tag_id = db.execute("SELECT id FROM tags WHERE path='sense/'").fetchone()[0]
        db.execute("INSERT INTO highlight_tags VALUES (?,?)", (hit["taguette_highlight_id"], tag_id))
    assert main(["list", str(path), "--status", "conflicted"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 1
    assert main(["label", str(path), hit["id"], "--clear"]) == 0
    capsys.readouterr()
    assert collect(path)[2][0]["status"] == "unreviewed"


def test_source_changed_during_read_is_not_assigned_old_digest(make_book, monkeypatch):
    source = make_book()
    from ebooklib import epub
    original_load = epub.EpubReader.load
    def changing_load(self):
        book = original_load(self)
        with ZipFile(source, "a") as archive:
            archive.comment = b"changed concurrently"
        return book
    monkeypatch.setattr(epub.EpubReader, "load", changing_load)
    with pytest.raises(InputError, match="changed during"):
        read_book(source)
