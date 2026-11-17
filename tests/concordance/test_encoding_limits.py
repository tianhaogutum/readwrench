import json
from zipfile import ZipFile

from .helpers import require

require()

from lxml import etree
import pytest

from readwrench.concordance.books import InputError, read_book
from readwrench.concordance.concepts import load_concepts
from readwrench.concordance.workspace import collect, initialize


def test_valid_utf16_chapter_does_not_create_an_unreadable_utf8_snapshot(tmp_path, make_book, concepts_file):
    source = make_book(content="<h1>UTF16 chapter</h1><p>power " + "自" * 2_900_000 + " power</p>")
    with ZipFile(source) as archive:
        members = [(item, archive.read(item)) for item in archive.infolist()]
    with ZipFile(source, "w") as archive:
        for item, data in members:
            if item.filename.endswith("text/chapter.xhtml"):
                data = etree.tostring(etree.fromstring(data), encoding="utf-16", xml_declaration=True)
                assert len(data) < 8 * 1024 * 1024
            archive.writestr(item, data)
    path = tmp_path / "utf16"
    initialize([source], concepts_file(), path)
    manifest, texts, hits = collect(path)
    assert len(hits) == 2
    snapshot = path / manifest["chapters"][0]["text_file"]
    assert snapshot.stat().st_size > 8 * 1024 * 1024
    assert all(texts[h["chapter_id"]][h["start"]:h["end"]] == "power" for h in hits)


def test_encryption_entries_with_wrong_namespace_are_not_silently_ignored(make_book):
    source = make_book()
    with ZipFile(source, "a") as archive:
        archive.writestr("META-INF/encryption.xml", '<encryption><EncryptedData><EncryptionMethod Algorithm="unknown"/></EncryptedData></encryption>')
    with pytest.raises(InputError, match="encryption metadata"):
        read_book(source)


def test_utf8_bom_concept_json_is_supported(tmp_path):
    path = tmp_path / "bom.json"
    path.write_text(json.dumps({"concepts": [{"id": "a", "aliases": ["power"]}]}), encoding="utf-8-sig")
    assert load_concepts(path)[0]["id"] == "a"


def test_deep_json_nesting_is_a_friendly_error(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text("[" * 30000 + "0" + "]" * 30000)
    with pytest.raises(InputError):
        load_concepts(path)
