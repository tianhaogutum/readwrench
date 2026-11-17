"""Read one capture (HTML, MHTML or WARC) and extract its article text blocks."""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata

from . import dom
from .encoding import bom_encoding, decode, label_to_name, sniff
from .fileio import LIMITS, InputError, parse_json, read_bounded
from .jsutil import WS_RUN, js_len, js_slice, js_trim
from .readability import Readability
from .selectors import SelectorError, query, query_all
from .times import iso_time, legacy_time
from .urls import normalize_url

BLOCKS = frozenset(("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "td", "th", "dt", "dd",
                    "figcaption", "div", "section", "article", "main", "ul", "ol", "table", "tr"))
OMIT = 'script,style,noscript,template,svg,canvas,iframe,object,embed,[hidden],[aria-hidden="true"]'
MODIFIED_CLAIMS = 'meta[property="article:modified_time"],meta[name="last-modified"],meta[itemprop="dateModified"]'
READABILITY_VERSION = "0.6.0"


def normalized_text(text: str) -> str:
    return unicodedata.normalize("NFC", js_trim(WS_RUN.sub(" ", text)))


def _hidden(node: dom.Node) -> bool:
    return node.style("display") == "none" or node.style("visibility") == "hidden"


def blocks_from_dom(root: dom.Node) -> list[dict]:
    """Text blocks in document order.

    Container direct text and nested block content stay in order, without counting
    list/table/blockquote text twice. ``<pre>`` keeps its whitespace and line breaks.
    """
    blocks: list[dict] = []
    state = {"parts": [], "kind": root.tag or "p"}

    def flush(preserve: bool = False) -> None:
        joined = "".join(state["parts"])
        text = re.sub(r"\r\n?", "\n", joined) if preserve else normalized_text(joined)
        state["parts"] = []
        if not text:
            return
        if js_len(text) > LIMITS["blockChars"]:
            raise InputError(f"Extracted block exceeds {LIMITS['blockChars']} characters")
        blocks.append({"kind": state["kind"], "text": text})
        if len(blocks) > LIMITS["blocks"]:
            raise InputError(f"Extracted article exceeds {LIMITS['blocks']} blocks")

    def parent_kind(node: dom.Node) -> str:
        parent = node.parent_element
        return parent.tag if parent is not None else "p"

    def collect(child: dom.Node) -> str:
        if child.type == dom.TEXT:
            return child.data
        if child.type != dom.ELEMENT or _hidden(child):
            return ""
        if child.tag == "br":
            return "\n"
        return "".join(collect(grandchild) for grandchild in child.children)

    def walk(node: dom.Node) -> None:
        if node.type == dom.TEXT:
            state["parts"].append(node.data)
            return
        if node.type != dom.ELEMENT or _hidden(node):
            return
        if node.tag == "pre":
            flush()
            state["kind"] = "pre"
            state["parts"].append(collect(node))
            flush(True)
            state["kind"] = parent_kind(node)
            return
        boundary = node.tag in BLOCKS
        if boundary:
            flush()
            state["kind"] = node.tag
        if node.tag == "br":
            flush()
            return
        for child in list(node.children):
            walk(child)
        if boundary:
            flush()
            state["kind"] = parent_kind(node)

    walk(root)
    flush()
    if sum(js_len(block["text"]) for block in blocks) > LIMITS["totalChars"]:
        raise InputError(f"Extracted article exceeds {LIMITS['totalChars']} text characters")
    return blocks


def _metadata_from_html(document: dom.Node) -> dict:
    metadata = {"urls": [], "times": [], "warnings": [], "claimedModified": []}
    # SingleFile stamps its comment near the beginning. Never scan arbitrary visible
    # article text for a date or URL and call that capture metadata.
    comments = (node for node in document.iter_nodes() if node.type == dom.COMMENT)
    for count, node in enumerate(comments):
        if count >= 30:
            break
        if not re.search("Page saved with SingleFile", node.data, re.IGNORECASE):
            continue
        url = re.search(r"^\s*url:\s*(https?://\S+)\s*$", node.data, re.IGNORECASE | re.MULTILINE)
        saved = re.search(r"^\s*saved date:\s*(.+?)\s*$", node.data, re.IGNORECASE | re.MULTILINE)
        if url:
            metadata["urls"].append({"url": url.group(1), "source": "SingleFile comment url"})
        if saved:
            time = legacy_time(saved.group(1), "SingleFile saved-date comment")
            if time:
                metadata["times"].append(time)
            else:
                metadata["warnings"].append("SingleFile saved date has no recognized timezone or is invalid; capture "
                                            "time remains unknown unless supplied explicitly.")
    claims = query_all(document, MODIFIED_CLAIMS)
    for element in claims[:20]:
        source = element.get_attribute("property") or element.get_attribute("name") or "dateModified"
        metadata["claimedModified"].append({"value": js_slice(element.get_attribute("content") or "", 300),
                                            "source": js_slice(source, 200)})
    if len(claims) > 20:
        metadata["warnings"].append("More than 20 page-provided modification claims; only the first 20 are shown.")
    return metadata


def _sidecar_metadata(file: str, sha256: str) -> dict | None:
    filename = f"{file}.manifest.json"
    try:
        data = read_bounded(filename, LIMITS["manifestBytes"])
    except InputError as error:
        if isinstance(error.__cause__, FileNotFoundError):
            return None
        raise
    manifest = parse_json(data, filename)
    if not isinstance(manifest, dict) or manifest.get("format") != "singlefile-manifest" or \
            manifest.get("version") != 1 or isinstance(manifest.get("version"), bool):
        raise InputError(f"Unsupported SingleFile sidecar: {filename}")
    output = manifest.get("output")
    digest = output.get("sha256") if isinstance(output, dict) else None
    if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise InputError(f"SingleFile sidecar lacks a valid output SHA-256: {filename}")
    if digest.lower() != sha256:
        raise InputError(f"SingleFile sidecar SHA-256 mismatch: {file}")
    times = manifest.get("time")
    started = times.get("captureStarted") if isinstance(times, dict) else None
    at = iso_time(started, filename)
    if times.get("captureEnded") and iso_time(times["captureEnded"], filename) < at:
        raise InputError(f"SingleFile captureEnded precedes captureStarted: {filename}")
    subject = manifest.get("subject")
    url = subject.get("url") if isinstance(subject, dict) else None
    if not isinstance(url, str):
        raise InputError(f"SingleFile sidecar lacks subject URL: {filename}")
    final_url = subject.get("finalUrl")
    if "finalUrl" in subject and not isinstance(final_url, str):
        raise InputError(f"Invalid SingleFile finalUrl in {filename}")
    return {"filename": filename, "url": url, "finalURL": final_url,
            "time": {"at": at, "source": "SingleFile manifest time.captureStarted (output hash verified)",
                     "raw": started}}


def detect_format(entry: dict) -> str:
    if entry.get("format"):
        return entry["format"]
    if re.search(r"\.m(?:ht|html)$", entry["file"], re.IGNORECASE):
        return "mhtml"
    if re.search(r"\.warc(?:\.gz)?$", entry["file"], re.IGNORECASE):
        return "warc"
    return "html"


def _parse_document(html, encoding: str | None) -> dom.Node:
    """Decode (if bytes) and build the tree; element count and depth are bounded while parsing."""
    if isinstance(html, str):
        return dom.parse_html(html, "UTF-8")
    name = sniff(html, encoding)
    return dom.parse_html(decode(html, name), name)


def extract_capture(entry: dict, source_url: str) -> dict:
    data = read_bounded(entry["file"])
    sha256 = hashlib.sha256(data).hexdigest()
    fmt = detect_format(entry)
    warnings: list[str] = []
    times: list[dict] = []
    urls: list[dict] = []
    protected = [entry["file"]]
    html = data
    encoding = entry.get("encoding")
    container_record = None
    if encoding and not label_to_name(encoding):
        raise InputError(f"Unknown HTML encoding: {encoding}")
    if entry.get("recordId") and fmt != "warc":
        raise InputError("recordId is supported only for WARC captures")
    if fmt == "warc":
        from .warc import extract_warc
        result = extract_warc(data, source_url, entry.get("recordId"))
        html = result["html"]
        encoding = encoding or result["encoding"]
        if encoding and not label_to_name(encoding):
            warnings.append(f"WARC HTTP charset is unrecognized and was ignored: {js_slice(encoding, 100)}")
            encoding = None
        container_record = result["record"]
        times.append(result["time"])
        urls.append({"url": container_record["targetURI"], "source": "WARC-Target-URI"})
        warnings.extend(result["warnings"])
    if fmt == "mhtml":
        from .mhtml import parse_mhtml
        try:
            parsed = parse_mhtml(data)
        except (ValueError, LookupError) as error:
            raise InputError(f"Cannot parse MHTML {entry['file']}: {error}") from error
        root = parsed["resources"].get(parsed["index"]) if parsed["index"] is not None else None
        if not root or not re.match(r"^text/html(?:;|$)", root["contentType"], re.IGNORECASE) or \
                any(anomaly["type"] == "index-synthesized" for anomaly in parsed["anomalies"]):
            raise InputError(f"MHTML has no main HTML document: {entry['file']}")
        html = root["data"]
        if encoding:
            raise InputError("MHTML MIME charsets are decoded by the upstream parser; encoding overrides apply only "
                             "to HTML/WARC bytes")
        if re.match(r"^https?://", parsed["index"], re.IGNORECASE):
            urls.append({"url": parsed["index"], "source": "MHTML main Content-Location"})
        if parsed["headers"].get("snapshot-content-location"):
            urls.append({"url": parsed["headers"]["snapshot-content-location"],
                         "source": "MHTML Snapshot-Content-Location"})
        if parsed["headers"].get("date"):
            time = legacy_time(parsed["headers"]["date"], "MHTML MIME Date header")
            if time:
                times.append(time)
            else:
                warnings.append("MHTML Date is invalid, inconsistent, or has no recognized timezone; ignored: "
                                f"{parsed['headers']['date']}")
        warnings.extend(f"MHTML parser recovered: {anomaly['type']}"
                        f"{' (' + anomaly['id'] + ')' if anomaly.get('id') else ''}" for anomaly in parsed["anomalies"])
    sidecar = _sidecar_metadata(entry["file"], sha256)
    if sidecar:
        protected.append(sidecar["filename"])
        times.insert(0, sidecar["time"])
        urls.append({"url": sidecar["url"], "source": "SingleFile sidecar subject.url (requested URL)"})
        if sidecar["finalURL"]:
            urls.append({"url": sidecar["finalURL"], "source": "SingleFile sidecar subject.finalUrl (redirect claim)"})

    document = _parse_document(html, encoding)
    if len(document.get_elements_by_tag_name("*")) > LIMITS["elements"]:
        raise InputError(f"HTML exceeds {LIMITS['elements']} elements: {entry['file']}")
    metadata = _metadata_from_html(document)
    if container_record and container_record.get("httpLastModified"):
        metadata["claimedModified"].append({"value": container_record["httpLastModified"],
                                            "source": "WARC HTTP Last-Modified (server claim)"})
    times.extend(metadata["times"])
    urls.extend(metadata["urls"])
    warnings.extend(metadata["warnings"])
    accepted = {source_url}
    if sidecar and sidecar["finalURL"]:
        requested, final = normalize_url(sidecar["url"]), normalize_url(sidecar["finalURL"])
        if source_url in (requested, final):
            accepted.update((requested, final))
            if requested != final:
                warnings.append(f"SingleFile records a redirect claim: {requested} → {final}. Both URLs are retained "
                                "as capture provenance.")
    for candidate in urls:
        observed = normalize_url(candidate["url"])
        if observed not in accepted:
            message = f"{candidate['source']} identifies {observed}, not the requested {source_url}"
            if not entry.get("allowUrlMismatch"):
                raise InputError(f"{message}; set allowUrlMismatch on this capture only if these are intentional "
                                 "aliases")
            warnings.append(f"Explicit URL alias override: {message}")
    if entry.get("capturedAt"):
        capture_time = {"at": iso_time(entry["capturedAt"], "manifest"),
                        "source": entry.get("captureSource") or "manifest capturedAt (user supplied)",
                        "raw": entry["capturedAt"]}
    else:
        capture_time = times[0] if times else {"at": None, "source": "unknown; manifest order only", "raw": None}
    for time in times:
        if time["at"] != capture_time["at"]:
            warnings.append(f"Capture time conflict: selected {_js_string(capture_time['at'])} "
                            f"({capture_time['source']}); also found {time['at']} ({time['source']}).")
    for element in query_all(document, OMIT):
        element.remove()
    if query(document, "mjx-container,.MathJax"):
        warnings.append("This capture contains MathJax. Readability 0.6.0 can omit formulas; verify the original or "
                        "use an explicit article selector. Math layout is not represented by this text report.")
    title = dom.document_title(document) or os.path.basename(entry["file"])
    try:
        if entry.get("selector"):
            try:
                root_node = query(document, entry["selector"])
            except SelectorError as error:
                raise InputError(f"Invalid CSS selector in {entry['file']}: {error}") from None
            if root_node is None:
                raise InputError(f"CSS selector matched nothing in {entry['file']}: {entry['selector']}")
        else:
            article = Readability(document, max_elems_to_parse=LIMITS["elements"]).parse()
            if not article or article.get("content") is None:
                raise InputError(f"Readability found no article in {entry['file']}; supply a capture selector for a "
                                 "known article container")
            title = article["title"] or title
            root_node = article["content"]
            for element in query_all(root_node, OMIT):
                element.remove()
        if js_len(title) > 1000:
            title = js_slice(title, 1000)
            if title and 0xD800 <= ord(title[-1]) <= 0xDBFF:
                title = title[:-1]
            title += "…"
            warnings.append("Page title exceeded 1,000 characters and was truncated.")
        blocks = blocks_from_dom(root_node)
        if not blocks:
            raise InputError(f"No readable text blocks in {entry['file']}")
        if sum(js_len(block["text"]) for block in blocks) < 200:
            warnings.append("Extracted article is short; inspect the full extracted capture for missing content.")
        bounded = [warning if js_len(warning) <= 2000 else js_slice(warning, 2000) + "… [warning truncated]"
                   for warning in warnings[:100]]
        if len(warnings) > 100:
            bounded.append(f"{len(warnings) - 100} additional capture warnings omitted.")
        bom = bom_encoding(html) if not isinstance(html, str) else None
        if bom:
            encoding_source = "HTML byte-order mark"
        elif entry.get("encoding"):
            encoding_source = "manifest override"
        elif fmt == "mhtml":
            encoding_source = "MHTML MIME decoding by mhtml-to-html"
        elif encoding:
            encoding_source = "WARC HTTP Content-Type"
        else:
            encoding_source = "HTML encoding sniffing"
        if entry.get("selector"):
            extraction = {"method": "explicit CSS selector", "selector": entry["selector"]}
        else:
            extraction = {"method": "Mozilla Readability", "version": READABILITY_VERSION}
        extraction.update({"encoding": document.character_set, "encodingSource": encoding_source})
        capture = {
            "id": f"c{entry['inputIndex'] + 1}",
            "file": os.path.realpath(entry["file"]), "label": entry.get("label") or os.path.basename(entry["file"]),
            "format": fmt, "inputIndex": entry["inputIndex"], "sha256": sha256, "bytes": len(data), "title": title,
            "captureTime": capture_time, "observedTimes": times, "observedURLs": urls,
            "claimedModified": metadata["claimedModified"], "extraction": extraction,
        }
        if container_record:
            capture["containerRecord"] = container_record
        capture.update({"blocks": blocks, "warnings": bounded, "protectedFiles": protected})
        return capture
    except InputError:
        raise
    except Exception as error:  # noqa: BLE001 - mirrors the original's wrapping of extraction failures
        raise InputError(f"Article extraction failed in {entry['file']}: {error}") from error


def _js_string(value) -> str:
    return "null" if value is None else str(value)
