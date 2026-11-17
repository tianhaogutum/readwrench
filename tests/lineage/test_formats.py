"""WARC, MHTML, encoding and preflight behaviour (from the original formats suite)."""
import gzip
import json

import pytest

from readwrench.lineage.archivebox import import_archivebox
from readwrench.lineage.extract import extract_capture
from readwrench.lineage.fileio import InputError
from readwrench.lineage.warc import extract_warc

from .helpers import FIXTURES, SOURCE


def response_record(text, *, url=SOURCE, id="<urn:uuid:one>", date="2026-01-01T00:00:00Z",
                    content_type="text/html; charset=utf-8", status=200, extra_length=0, content_encoding=None,
                    transfer_encoding=None) -> bytes:
    body = text if isinstance(text, bytes) else text.encode("utf-8")
    if transfer_encoding and transfer_encoding.lower() == "chunked":
        wire = format(len(body), "x").encode() + b"\r\n" + body + b"\r\n0\r\n\r\n"
    else:
        wire = body
    head = (f"HTTP/1.1 {status} OK\r\nContent-Type: {content_type}\r\n"
            + (f"Transfer-Encoding: {transfer_encoding}\r\n" if transfer_encoding else f"Content-Length: {len(body)}\r\n")
            + (f"Content-Encoding: {content_encoding}\r\n" if content_encoding else "") + "\r\n")
    payload = head.encode() + wire
    return (f"WARC/1.1\r\nWARC-Type: response\r\nWARC-Target-URI: {url}\r\nWARC-Date: {date}\r\nWARC-Record-ID: {id}\r\n"
            f"Content-Type: application/http; msgtype=response\r\nContent-Length: {len(payload) + extra_length}\r\n\r\n"
            ).encode() + payload + b"\r\n\r\n"


def test_real_gzip_warc_decodes_outer_and_http_gzip():
    result = extract_capture({"file": str(FIXTURES / "example.warc.gz"), "inputIndex": 0}, "http://example.com/")
    assert result["title"] == "Example Domain"
    assert any("illustrative examples" in b["text"] for b in result["blocks"])
    assert result["captureTime"]["at"] == "2017-03-06T04:02:06.000Z"
    assert "WARC-Date" in result["captureTime"]["source"]
    assert any(claim["value"] == "Fri, 09 Aug 2013 23:54:35 GMT" for claim in result["claimedModified"])
    assert result["containerRecord"]["id"] == "<urn:uuid:a9c51e3e-0221-11e7-bf66-0242ac120005>"
    assert any("revisit" in w for w in result["warnings"])


def test_real_mhtml_and_converted_html_yield_identical_blocks():
    source = "http://msindwan.bitbucket.org/"
    original = extract_capture({"file": str(FIXTURES / "portfolio.mhtml"), "inputIndex": 0}, source)
    converted = extract_capture({"file": str(FIXTURES / "portfolio.html"), "inputIndex": 1}, source)
    assert converted["title"] == original["title"]
    assert converted["blocks"] == original["blocks"]
    assert converted["captureTime"]["at"] is None, "format conversion is not a new historical capture date"


def test_real_chunked_warc_is_dechunked_and_transport_encoding_recorded():
    result = extract_capture({"file": str(FIXTURES / "iana-chunked.warc"), "inputIndex": 0}, "http://www.iana.org/")
    assert "Internet Assigned Numbers Authority" in result["title"]
    assert result["extraction"]["encoding"] == "UTF-8"
    assert result["extraction"]["encodingSource"] == "WARC HTTP Content-Type"
    assert result["captureTime"]["at"] == "2017-03-06T16:54:09.000Z"


def test_multiple_warc_captures_require_explicit_record_identity(tmp_path):
    file = tmp_path / "multiple.warc"
    file.write_bytes(response_record("<article><p>First paragraph capture.</p></article>")
                     + response_record("<article><p>Second paragraph capture.</p></article>", id="<urn:uuid:two>",
                                       date="2026-02-01T00:00:00Z"))
    with pytest.raises(InputError, match="multiple successful"):
        extract_capture({"file": str(file), "inputIndex": 0}, SOURCE)
    second = extract_capture({"file": str(file), "inputIndex": 0, "recordId": "<urn:uuid:two>"}, SOURCE)
    assert second["blocks"][0]["text"] == "Second paragraph capture."
    assert second["captureTime"]["at"] == "2026-02-01T00:00:00.000Z"
    with pytest.raises(InputError, match="No self-contained"):
        extract_capture({"file": str(file), "inputIndex": 0, "recordId": "<urn:uuid:missing>"}, SOURCE)


def test_warc_rejects_truncation_redirects_wrong_urls_and_bad_gzip():
    with pytest.raises(InputError, match="Truncated WARC"):
        extract_warc(response_record("<p>A partial article.</p>", extra_length=100), SOURCE)
    with pytest.raises(InputError, match="No self-contained"):
        extract_warc(response_record("<p>Redirect page.</p>", status=301), SOURCE)
    with pytest.raises(InputError, match="different URL"):
        extract_warc(response_record("<p>Another page.</p>", url="https://elsewhere.example/"), SOURCE, "<urn:uuid:one>")
    with pytest.raises(InputError, match="Cannot parse WARC"):
        extract_warc(bytes([0x1F, 0x8B, 0, 1, 2]), SOURCE)


def test_decompression_limits_stop_unbounded_expansion():
    with pytest.raises(InputError, match="50 MiB"):
        extract_warc(gzip.compress(b"x" * (51 * 1024 * 1024)), SOURCE)


def test_nested_chunked_http_gzip_is_decoded_case_insensitively_and_bounded():
    valid = response_record(gzip.compress(b"<article><p>A compressed article.</p></article>"), content_encoding="GZip",
                            transfer_encoding="Chunked")
    assert b"A compressed article" in extract_warc(valid, SOURCE)["html"]
    oversized = response_record(gzip.compress(b"x" * (12 * 1024 * 1024)), content_encoding="GZip",
                                transfer_encoding="Chunked")
    with pytest.raises(InputError, match="Selected WARC HTML exceeds 10 MiB"):
        extract_warc(oversized, SOURCE)
    with pytest.raises(InputError, match="Unsupported WARC HTTP Content-Encoding"):
        extract_warc(response_record("not decoded", content_encoding="br"), SOURCE)


def test_encoding_override_fixes_missing_transport_metadata(tmp_path):
    file = tmp_path / "no-charset.html"
    file.write_text("<article><p>中文、café、←。</p></article>", encoding="utf-8")
    result = extract_capture({"file": str(file), "inputIndex": 0, "encoding": "utf-8", "selector": "article"}, SOURCE)
    assert result["blocks"][0]["text"] == "中文、café、←。"
    assert result["extraction"]["encoding"] == "UTF-8"
    assert result["extraction"]["encodingSource"] == "manifest override"
    with pytest.raises(InputError, match="Unknown HTML encoding"):
        extract_capture({"file": str(file), "inputIndex": 0, "encoding": "invented-encoding"}, SOURCE)


def test_utf16_bom_is_decoded_and_bounded(tmp_path):
    file = tmp_path / "utf16.html"
    file.write_bytes(b"\xff\xfe" + "<article><p>你好，café。</p></article>".encode("utf-16-le"))
    result = extract_capture({"file": str(file), "inputIndex": 0, "selector": "article"}, SOURCE)
    assert result["blocks"][0]["text"] == "你好，café。"
    assert result["extraction"]["encoding"] == "UTF-16LE"
    assert result["extraction"]["encodingSource"] == "HTML byte-order mark"
    overridden = extract_capture({"file": str(file), "inputIndex": 0, "selector": "article", "encoding": "utf-8"}, SOURCE)
    assert overridden["extraction"]["encodingSource"] == "HTML byte-order mark", "the BOM wins over an override"
    file.write_bytes(b"\xff\xfe" + ("<div>" * 300).encode("utf-16-le"))
    with pytest.raises(InputError, match="nesting exceeds"):
        extract_capture({"file": str(file), "inputIndex": 0}, SOURCE)


def test_invalid_warc_charset_falls_back_to_sniffing_with_warning(tmp_path):
    file = tmp_path / "bad-charset.warc"
    file.write_bytes(response_record('<meta charset="utf-8"><article><p>正确文本 café</p></article>',
                                     content_type="text/html; charset=invented-encoding"))
    result = extract_capture({"file": str(file), "inputIndex": 0, "selector": "article"}, SOURCE)
    assert result["blocks"][0]["text"] == "正确文本 café"
    assert result["extraction"]["encodingSource"] == "HTML encoding sniffing"
    assert any("charset is unrecognized" in w for w in result["warnings"])


def test_huge_invalid_mhtml_date_is_bounded(tmp_path):
    file = tmp_path / "large-date.mhtml"
    file.write_text("MIME-Version: 1.0\r\nDate: " + "x" * 50_000 + '\r\nContent-Type: multipart/related; boundary="boundary"'
                    "\r\n\r\n--boundary\r\nContent-Type: text/html; charset=utf-8\r\nContent-Location: "
                    "https://example.org/tutorial\r\n\r\n<article><p>A small actual article.</p></article>\r\n--boundary--\r\n",
                    newline="")
    result = extract_capture({"file": str(file), "inputIndex": 0}, SOURCE)
    assert result["captureTime"]["at"] is None
    assert any("warning truncated" in w for w in result["warnings"])
    assert all(len(w) < 2050 for w in result["warnings"])


def test_optional_paragraph_end_tags_pass_the_depth_bound(tmp_path):
    file = tmp_path / "optional-tags.html"
    file.write_text("<article>" + "<p>Paragraph with an omitted closing tag." * 1000 + "</article>")
    assert len(extract_capture({"file": str(file), "inputIndex": 0, "selector": "article"}, SOURCE)["blocks"]) == 1000


def test_template_nesting_is_bounded(tmp_path):
    file = tmp_path / "templates.html"
    file.write_text("<template>" * 300 + "<p>nested</p>" + "</template>" * 300)
    with pytest.raises(InputError, match="nesting exceeds"):
        extract_capture({"file": str(file), "inputIndex": 0}, SOURCE)


def test_archivebox_09_archive_results_follow_the_selected_output(tmp_path):
    (tmp_path / "singlefile.html").write_text("<p>A real file slot.</p>")
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"url": SOURCE, "archive_results": [
        {"plugin": "singlefile", "status": "succeeded", "output": "singlefile.html", "start_ts": "2026-04-01T10:00:00Z"}]}))
    result = import_archivebox([str(index)], str(tmp_path / "manifest.json"))
    assert result["manifest"]["captures"][0]["capturedAt"] == "2026-04-01T10:00:00.000Z"


def test_archivebox_chooses_newest_successful_run_not_array_order(tmp_path):
    (tmp_path / "singlefile.html").write_text("<p>Current capture.</p>")
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"url": SOURCE, "history": {"singlefile": [
        {"output": "singlefile.html", "status": "succeeded", "start_ts": "2026-04-01T10:00:00Z"},
        {"output": "singlefile.html", "status": "succeeded", "start_ts": "2026-01-01T10:00:00Z"}]}}))
    result = import_archivebox([str(index)], str(tmp_path / "manifest.json"))
    assert result["manifest"]["captures"][0]["capturedAt"] == "2026-04-01T10:00:00.000Z"
    assert any("overwritten" in w for w in result["warnings"])


def test_archivebox_never_dates_a_stale_default_file(tmp_path):
    (tmp_path / "singlefile.html").write_text("<p>Stale capture left behind.</p>")
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"url": SOURCE, "history": {"singlefile": [
        {"output": "missing-new-output.html", "status": "succeeded", "start_ts": "2026-04-01T10:00:00Z"}]}}))
    with pytest.raises(InputError, match="No supported existing HTML"):
        import_archivebox([str(index)], str(tmp_path / "manifest.json"))
