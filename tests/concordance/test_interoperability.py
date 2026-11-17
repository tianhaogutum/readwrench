import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from .helpers import SCHEMA, require

require()

from jsonschema import Draft7Validator, FormatChecker

from readwrench.concordance.books import read_book
from readwrench.concordance.exporting import export_workspace
from readwrench.concordance.workspace import collect, initialize, label, taguette_session


def test_readium_reference_schema_after_documented_local_syntax_patch(simple_workspace, tmp_path):
    # jsonschema checks "uri-reference" only when rfc3986-validator is installed.
    pytest.importorskip("rfc3986_validator")
    path, _ = simple_workspace
    out = tmp_path / "readium"
    export_workspace(path, out)
    schema = json.loads((SCHEMA / "readium-locator.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    assert "uri-reference" in validator.format_checker.checkers
    for hit in json.loads((out / "occurrences.json").read_text())["occurrences"]:
        validator.validate(hit["readium_locator"])


def test_official_web_annotation_context_expands_without_network(simple_workspace, tmp_path):
    jsonld = pytest.importorskip("pyld.jsonld")
    path, _ = simple_workspace
    hit = collect(path)[2][0]
    label(path, hit["id"], senses=["capacity"])
    out = tmp_path / "jsonld"
    export_workspace(path, out)
    context = json.loads((SCHEMA / "w3c-anno.context.jsonld").read_text())
    def loader(url, options=None):
        assert url == "http://www.w3.org/ns/anno.jsonld", "Unexpected remote context; test must stay offline"
        return {"contextUrl": None, "documentUrl": url, "document": context}
    expanded = jsonld.expand(json.loads((out / "annotations.jsonld").read_text()), options={"documentLoader": loader})
    page, = expanded
    assert "http://www.w3.org/ns/activitystreams#OrderedCollectionPage" in page["@type"]
    items = page["http://www.w3.org/ns/activitystreams#items"][0]["@list"]
    assert len(items) == 4
    assert all("http://www.w3.org/ns/oa#hasTarget" in item for item in items)
    assert any("http://www.w3.org/ns/oa#hasBody" in item for item in items)


def test_independent_research_decisions_have_distinct_annotation_identities(tmp_path, make_book, concepts_file):
    source = make_book()
    a, b = tmp_path / "a", tmp_path / "b"
    for workspace in [a, b]:
        initialize([source], concepts_file(), workspace)
    hits_a, hits_b = collect(a)[2], collect(b)[2]
    assert [h["id"] for h in hits_a] == [h["id"] for h in hits_b]
    for workspace, sense in [(a, "capacity"), (b, "authority")]:
        label(workspace, hits_a[0]["id"], senses=[sense])
        export_workspace(workspace, tmp_path / f"export-{workspace.name}")
    def items(name):
        return json.loads((tmp_path / f"export-{name}" / "annotations.jsonld").read_text())["items"]
    assert items("a")[0]["target"]["source"] == items("b")[0]["target"]["source"]
    assert {i["id"] for i in items("a")}.isdisjoint({i["id"] for i in items("b")})


def test_tag_definitions_survive_export(simple_workspace, tmp_path):
    from taguette import database as d
    path, _ = simple_workspace
    hit = collect(path)[2][0]
    label(path, hit["id"], senses=["capacity"])
    definition = "Ability to act, distinguished by the researcher from a right to command."
    with taguette_session(path / "project.sqlite3") as db:
        db.query(d.Tag).filter_by(path="sense/capacity").one().description = definition
        db.commit()
    out = tmp_path / "codebook"
    export_workspace(path, out)
    codebook = json.loads((out / "codebook.json").read_text())
    assert next(tag for tag in codebook if tag["path"] == "sense/capacity")["description"] == definition
    assert definition in (out / "index.html").read_text()


def test_valid_parent_relative_package_resources(tmp_path, make_book):
    # Actual archive members are normalized, while the package legitimately
    # refers from EPUB/pkg/content.opf to ../text/chapter.xhtml.
    source = make_book(filename="text/chapter.xhtml")
    with ZipFile(source) as archive:
        members = [(item, archive.read(item)) for item in archive.infolist()]
    from lxml import etree
    with ZipFile(source, "w") as archive:
        for item, data in members:
            name = item.filename
            if name == "META-INF/container.xml":
                root = etree.fromstring(data)
                root.find("{*}rootfiles/{*}rootfile").set("full-path", "EPUB/pkg/content.opf")
                data = etree.tostring(root)
            elif name == "EPUB/content.opf":
                name = "EPUB/pkg/content.opf"
                root = etree.fromstring(data)
                for entry in root.findall("{*}manifest/{*}item"):
                    entry.set("href", "../" + entry.get("href"))
                data = etree.tostring(root)
            archive.writestr(name, data)
    chapter, = read_book(source).chapters
    assert chapter.href == "../text/chapter.xhtml"
    assert chapter.archive_path == "EPUB/text/chapter.xhtml"


def test_toc_title_used_when_prose_has_no_heading(make_book):
    source = make_book(content="<p>Some power.</p>")
    chapter, = read_book(source).chapters
    assert chapter.title == "First chapter"
