import base64
import json

import pytest

from readwrench.cluejoin.importers import from_jsonl, from_rga, from_text
from readwrench.cluejoin.model import CluejoinError


def write(tmp_path, data, name="input.jsonl"):
    path = tmp_path / name
    path.write_text(data, encoding="utf-8")
    return path


def test_plain_text_has_no_invented_pages(tmp_path):
    path = write(tmp_path, "first line\nwrapped line\n\nsecond paragraph\n", "notes.txt")
    fragments = list(from_text(path))
    assert [item.page for item in fragments] == [None, None]
    assert [item.paragraph for item in fragments] == [1, 2]
    assert [item.line for item in fragments] == [1, 4]
    assert fragments[0].text == "first line\nwrapped line"


def test_form_feeds_count_empty_pages_and_no_trailing_phantom_fragment(tmp_path):
    path = write(tmp_path, "first\f\fthird\n\nmore\f", "pages.txt")
    fragments = list(from_text(path))
    assert [item.page for item in fragments] == [1, 3, 3]
    assert [item.paragraph for item in fragments] == [1, 2, 3]
    assert [item.text for item in fragments] == ["first", "third", "more"]


def test_utf8_bom_crlf_and_context_are_preserved(tmp_path):
    path = write(tmp_path, "\ufeffalpha\r\nbeta\r\n\r\ngamma", "windows.txt")
    assert list(from_text(path))[0].text == "alpha\r\nbeta"


@pytest.mark.parametrize("patch", [
    {"page": True}, {"page": 0}, {"page": -1}, {"page": 1.5}, {"page": "1"},
    {"page": None}, {"page": 2**32}, {"text": ""}, {"text": "a\x00b"},
    {"text": "\ud800"}, {"paragraphs": 2}, {"document_id": []},
])
def test_invalid_jsonl_record_is_explained(tmp_path, patch):
    row = {"document_id": "book", "page": 1, "text": "original words"}
    row.update(patch)
    path = write(tmp_path, json.dumps(row))
    with pytest.raises(CluejoinError, match="input.jsonl:1"):
        list(from_jsonl(path))


def rga_event(path="book.pdf", text="Page 7: alpha\n", line=None):
    return {"type": "match", "data": {
        "path": {"text": path}, "lines": {"text": text}, "line_number": line,
    }}


def test_rga_base64_fields_and_multiline_pdf_pages(tmp_path):
    event = rga_event(text="Page 7: αλφα\nPage 8: omega\n", line=50)
    for key in ("path", "lines"):
        event["data"][key] = {"bytes": base64.b64encode(
            event["data"][key]["text"].encode("utf-8")).decode("ascii")}
    fragments = list(from_rga(write(tmp_path, json.dumps(event))))
    assert [item.page for item in fragments] == [7, 8]
    assert [item.line for item in fragments] == [50, 51]
    assert [item.text for item in fragments] == ["αλφα", "omega"]


def test_sparse_rga_plain_text_keeps_line_not_paragraph(tmp_path):
    event = rga_event(path="notes.txt", text="alpha\n", line=42)
    fragment = list(from_rga(write(tmp_path, json.dumps(event))))[0]
    assert fragment.line == 42
    assert fragment.paragraph is None
    assert fragment.page is None


def test_rga_multiline_plain_event_has_real_line_distance(tmp_path, indexed):
    from readwrench.cluejoin.search import search

    event = rga_event(path="notes.txt", text="alpha\nmiddle\nomega\n", line=42)
    fragments = list(from_rga(write(tmp_path, json.dumps(event))))
    assert len(fragments) == 1
    assert fragments[0].line == 42
    assert fragments[0].text == "alpha\nmiddle\nomega"
    doc = search(indexed(fragments), [["alpha"], ["omega"]])["documents"][0]
    assert doc["distance_unit"] == "line"
    assert doc["distance"] == 2
    assert doc["paragraph_span"] is None


def test_pdf_unicode_line_separator_remains_original_content(tmp_path):
    event = rga_event(text="Page 2: alpha\u2028omega\n")
    fragment = list(from_rga(write(tmp_path, json.dumps(event))))[0]
    assert fragment.text == "alpha\u2028omega"


def test_adjacent_numbered_lines_preserve_crlf_and_record_range(tmp_path, indexed):
    from readwrench.cluejoin.search import search

    events = [rga_event(path="notes.txt", text="alpha\r\n", line=4),
              rga_event(path="notes.txt", text="beta end\r\n", line=5)]
    path = write(tmp_path, "\n".join(json.dumps(event) for event in events))
    fragments = list(from_rga(path))
    assert len(fragments) == 1
    assert fragments[0].text == "alpha\r\nbeta end"
    assert fragments[0].record == 1
    assert fragments[0].record_end == 2
    doc = search(indexed(fragments), [["alpha beta"], ["end"]])["documents"][0]
    assert doc["evidence"][0]["match"] == "alpha\r\nbeta"
    assert doc["evidence"][1]["line"] == 5


@pytest.mark.parametrize("events", [
    [rga_event(path="notes.txt", text="alpha\n", line=1),
     rga_event(path="notes.txt", text="beta end\n", line=3)],
    [rga_event(text="Page 2: alpha\n", line=1),
     rga_event(text="Page 3: beta end\n", line=2)],
    [rga_event(text="Page 2: alpha\n"), rga_event(text="Page 2: beta end\n")],
])
def test_never_join_across_a_gap_page_or_unknown_adjacency(tmp_path, indexed, events):
    from readwrench.cluejoin.search import search

    path = write(tmp_path, "\n".join(json.dumps(event) for event in events))
    connection = indexed(list(from_rga(path)))
    assert search(connection, [["alpha beta"], ["end"]])["matched_documents"] == 0
    assert search(connection, [["alpha"], ["beta"]])["matched_documents"] == 1


def test_overlapping_line_captures_are_rejected(tmp_path, indexed):
    events = [rga_event(path="notes.txt", text="alpha\nbeta\n", line=1),
              rga_event(path="notes.txt", text="different\n", line=2)]
    path = write(tmp_path, "\n".join(json.dumps(event) for event in events))
    with pytest.raises(CluejoinError, match="overlapping original line ranges"):
        indexed(list(from_rga(path)))


def test_rga_line_numbers_follow_lf_records_not_bare_cr(tmp_path, indexed):
    from readwrench.cluejoin.search import search

    events = [rga_event(path="notes.txt", text="alpha\rbeta\n", line=1),
              rga_event(path="notes.txt", text="omega\n", line=2)]
    path = write(tmp_path, "\n".join(json.dumps(event) for event in events))
    connection = indexed(list(from_rga(path)))
    doc = search(connection, [["alpha"], ["beta"]])["documents"][0]
    assert [item["line"] for item in doc["evidence"]] == [1, 1]
    assert doc["line_span"] == 0
    other = search(connection, [["beta"], ["omega"]])["documents"][0]
    assert [item["line"] for item in other["evidence"]] == [1, 2]


@pytest.mark.parametrize("data", [
    '{"document_id":"a","document_id":"b","page":1,"text":"x"}',
    '{"document_id":"a","page":1,"text":"x","source":NaN}',
    '{"document_id":"a","page":1,"text":"alpha\\fomega"}',
])
def test_ambiguous_json_is_not_silently_accepted(tmp_path, data):
    with pytest.raises(CluejoinError, match="input.jsonl:1"):
        list(from_jsonl(write(tmp_path, data)))


@pytest.mark.parametrize("event", [
    rga_event(path="archive.zip", text="first.pdf: Page 2: alpha"),
    rga_event(text="missing page marker"),
    rga_event(path="notes.txt", text="alpha", line=None),
    rga_event(text="Page 7: alpha\nno page marker"),
    {"type": "made-up", "data": {}},
    {"type": "match", "data": {"path": {"bytes": "%%%"}}},
])
def test_rga_rejects_ambiguous_or_invalid_locations(tmp_path, event):
    with pytest.raises(CluejoinError, match="input.jsonl:1"):
        list(from_rga(write(tmp_path, json.dumps(event))))
