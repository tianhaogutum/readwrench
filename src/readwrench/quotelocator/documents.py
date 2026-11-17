"""Established format readers, with bounded input and a documented text projection."""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import posixpath
import re
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote
from warnings import catch_warnings, simplefilter
from zipfile import ZipFile

from bs4 import BeautifulSoup, CData, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning
from ebooklib import epub
from pypdf import Configuration, PdfReader, apply_configuration

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_UNITS = 2000
SEPARATOR = "\n\n"
TEXT_POLICY = "quote-locator-text-v1"
PDF_LIMITS = {
    "maximum_declared_stream_length": MAX_FILE_BYTES,
    "array_based_stream_maximum_output_length": 8 * 1024 * 1024,
    "jbig2_maximum_output_length": 8 * 1024 * 1024,
    "lzw_maximum_output_length": 8 * 1024 * 1024,
    "run_length_maximum_output_length": 8 * 1024 * 1024,
    "zlib_maximum_output_length": 8 * 1024 * 1024,
    "brotli_maximum_output_length": 8 * 1024 * 1024,
    "image_maximum_buffer_size": 8 * 1024 * 1024,
    "stream_decoding_work_maximum_length": 32 * 1024 * 1024,
    "stream_parsing_work_maximum_count": 250_000,
    "page_tree_maximum_entries": MAX_UNITS * 4,
    "page_tree_maximum_page_entries": 250_000,
    "xform_maximum_invocations_per_extraction": 5000,
    "jbig2dec_binary": None,
}
_PDF_CAPTURE = ContextVar("quote_locator_pdf_capture", default=None)


@contextmanager
def _pdf_messages():
    messages = []

    class Capture(logging.Handler):
        def emit(self, record):
            if _PDF_CAPTURE.get() is messages and len(messages) < 200:
                messages.append(f"PDF extraction warning: {record.getMessage()[:1000]}")

    logger = logging.getLogger("pypdf")
    handler = Capture(level=logging.WARNING)
    token = _PDF_CAPTURE.set(messages)
    logger.addHandler(handler)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        _PDF_CAPTURE.reset(token)


class DocumentError(ValueError):
    """A document cannot be safely read under the supported extraction policy."""


@dataclass(frozen=True)
class Unit:
    label: str
    href: str
    start: int
    end: int
    number: int


@dataclass(frozen=True)
class Document:
    path: Path
    sha256: str
    text_sha256: str
    media_type: str
    text: str
    units: tuple[Unit, ...]
    extraction: dict
    warnings: tuple[str, ...] = ()

    def metadata(self) -> dict:
        return {
            "name": self.path.name,
            "uri": self.path.as_uri(),
            "sha256": self.sha256,
            "text_sha256": self.text_sha256,
            "media_type": self.media_type,
            "characters": len(self.text),
            "units": [asdict(unit) for unit in self.units],
            "extraction": deepcopy(self.extraction),
            "warnings": list(self.warnings),
        }


def _html_text(content: bytes) -> str:
    """Keep inline text contiguous; create paragraph breaks only at block boundaries."""
    if len(content) > MAX_HTML_BYTES:
        raise DocumentError("HTML/XHTML resource exceeds the 2 MiB parser limit")
    with catch_warnings():
        # XHTML deliberately uses the same tolerant, non-fetching text policy.
        simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(content, "html.parser")
    block_tags = {
        "p",
        "div",
        "section",
        "article",
        "aside",
        "header",
        "footer",
        "nav",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "blockquote",
        "pre",
        "tr",
        "table",
        "dl",
        "dt",
        "dd",
        "figure",
        "figcaption",
        "hr",
    }
    excluded = {"script", "style", "template", "noscript", "head", "rt", "rp", "rtc"}
    chunks = []
    # Traverse the parsed tree without quadratic sibling insertions or recursive
    # Python calls. Namespace prefixes in real XHTML do not change text semantics.
    stack = [(iter((soup.body or soup).children), "")]
    while stack:
        children, closing = stack[-1]
        node = next(children, None)
        if node is None:
            chunks.append(closing)
            stack.pop()
            continue
        if isinstance(node, Comment):
            continue
        if type(node) in {NavigableString, CData}:
            chunks.append(str(node))
        elif isinstance(node, Tag):
            name = node.name.rsplit(":", 1)[-1].lower()
            if (
                name in excluded
                or node.has_attr("hidden")
                or str(node.get("aria-hidden", "")).lower() == "true"
            ):
                continue
            if name == "br":
                chunks.append("\n")
                continue
            closing = "\n\n" if name in block_tags else "\t" if name in {"td", "th"} else ""
            if name in block_tags:
                chunks.append("\n\n")
            stack.append((iter(node.children), closing))
    text = "".join(chunks)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _epub_parts(path: Path) -> list[tuple[str, str, str]]:
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(entry.file_size for entry in entries) > MAX_FILE_BYTES:
                raise DocumentError("EPUB exceeds the 20 MiB expanded/3000-entry limit")
            if any(entry.file_size > MAX_FILE_BYTES for entry in entries):
                raise DocumentError("EPUB contains an oversized member")
            container_bytes = archive.read("META-INF/container.xml")
            if len(container_bytes) > 512 * 1024:
                raise DocumentError("EPUB container metadata exceeds the 512 KiB limit")
            container = BeautifulSoup(container_bytes, "xml")
            roots = container.find_all(
                "rootfile", attrs={"media-type": "application/oebps-package+xml"}
            )
            if not roots or not roots[-1].get("full-path"):
                raise DocumentError("EPUB has no package document in container.xml")
            if len({root.get("full-path") for root in roots}) > 1:
                raise DocumentError(
                    "EPUB has multiple package renditions; export the intended rendition as text"
                )
            # Match EbookLib's rootfile choice. Hrefs in the OPF are relative to
            # its directory, whereas exported locators identify ZIP-root resources.
            package_directory = posixpath.dirname(roots[-1]["full-path"])
            encrypted_resources = set()
            if "META-INF/encryption.xml" in archive.namelist():
                # Font obfuscation is common and harmless, but decrypting text is out of scope.
                encryption_bytes = archive.read("META-INF/encryption.xml")
                if len(encryption_bytes) > 512 * 1024:
                    raise DocumentError("EPUB encryption metadata exceeds the 512 KiB limit")
                soup = BeautifulSoup(encryption_bytes, "xml")
                for entry in soup.find_all("CipherReference"):
                    uri = entry.get("URI", "")
                    encrypted_resources.add(posixpath.normpath(unquote(uri)))
                    if not uri.lower().endswith((".otf", ".ttf", ".woff", ".woff2")):
                        raise DocumentError("EPUB has encrypted content; provide DRM-free text")
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
        parts = []
        for item_id, _linear in book.spine:
            item = book.get_item_with_id(item_id)
            if item is None or not isinstance(item, epub.EpubHtml):
                raise DocumentError(f"EPUB spine item {item_id!r} is missing or not XHTML")
            # Include non-linear items too: a quotation may be in an appendix/footnote.
            href = posixpath.normpath(posixpath.join(package_directory, item.get_name()))
            if href.startswith(("../", "/")) or href == "..":
                raise DocumentError("EPUB resource path escapes the container root")
            if href in encrypted_resources:
                raise DocumentError("EPUB spine content is encrypted; provide DRM-free text")
            parts.append((href, href, _html_text(item.get_content())))
            if sum(len(part[2]) for part in parts) > MAX_TEXT_CHARS:
                raise DocumentError("Extracted text exceeds the 2,000,000-character limit")
        if not parts:
            raise DocumentError("EPUB has no readable spine items")
        return parts
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"Cannot read EPUB: {type(exc).__name__}: {exc}") from exc


def _file_digest(path: Path) -> str:
    if not path.is_file():
        raise DocumentError(f"Source is not a regular file: {path}")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise DocumentError("Source exceeds the 20 MiB file limit")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise DocumentError("Source grew beyond the 20 MiB file limit while reading")
            digest.update(chunk)
    return digest.hexdigest()


def load_document(path: str | Path, *, encoding: str = "utf-8") -> Document:
    path = Path(path).expanduser().resolve()
    digest = _file_digest(path)
    suffix = path.suffix.lower()
    warnings = []
    versions = {}
    try:
        if suffix == ".pdf":
            media_type = "application/pdf"
            versions["pypdf"] = importlib.metadata.version("pypdf")
            with (
                _pdf_messages() as messages,
                apply_configuration(Configuration(**PDF_LIMITS)),
                path.open("rb") as stream,
            ):
                reader = PdfReader(stream)
                if reader.is_encrypted and reader.decrypt("") == 0:
                    raise DocumentError("PDF is encrypted; provide a decrypted local copy")
                if len(reader.pages) > MAX_UNITS:
                    raise DocumentError("PDF exceeds the 2000-page limit")
                parts = []
                count = 0
                for number, page in enumerate(reader.pages, 1):
                    text = page.extract_text(extraction_mode="plain") or ""
                    count += len(text)
                    if count > MAX_TEXT_CHARS:
                        raise DocumentError("Extracted text exceeds the 2,000,000-character limit")
                    parts.append((f"PDF page {number}", f"#page={number}", text))
                    if not text.strip():
                        warnings.append(f"PDF page {number} has no extractable text (OCR not run)")
            warnings.extend(dict.fromkeys(messages))
            warnings.append("PDF page numbers are physical 1-based pages, not printed page labels")
        elif suffix == ".epub":
            media_type = "application/epub+zip"
            versions = {
                name: importlib.metadata.version(name)
                for name in ("EbookLib", "beautifulsoup4", "lxml")
            }
            parts = _epub_parts(path)
            warnings.append(
                "EPUB searches static XHTML in spine order; "
                "CSS, scripts and images are not evaluated"
            )
        elif suffix in {".html", ".htm", ".xhtml"}:
            media_type = "application/xhtml+xml" if suffix == ".xhtml" else "text/html"
            versions["beautifulsoup4"] = importlib.metadata.version("beautifulsoup4")
            with path.open("rb") as stream:
                content = stream.read(MAX_HTML_BYTES + 1)
            parts = [("HTML document", "", _html_text(content))]
            warnings.append("HTML uses static text; external CSS and JavaScript are not evaluated")
        elif suffix in {".txt", ".text", ".md", ".markdown"}:
            media_type = "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"
            with path.open("r", encoding=encoding, newline="") as stream:
                text = stream.read(MAX_TEXT_CHARS + 1)
            parts = [("Text document", "", text)]
            if media_type == "text/markdown":
                warnings.append("Markdown is searched as literal source, including markup")
        else:
            raise DocumentError("Unsupported format: use TXT, MD, HTML, XHTML, PDF or EPUB")
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"Cannot extract {path.name}: {type(exc).__name__}: {exc}") from exc
    text_parts = []
    units = []
    offset = 0
    for number, (label, href, text) in enumerate(parts, 1):
        if number > MAX_UNITS:
            raise DocumentError("Document exceeds the 2000-unit limit")
        if number > 1:
            text_parts.append(SEPARATOR)
            offset += len(SEPARATOR)
        units.append(Unit(label, href, offset, offset + len(text), number))
        text_parts.append(text)
        offset += len(text)
        if offset > MAX_TEXT_CHARS:
            raise DocumentError("Extracted text exceeds the 2,000,000-character limit")
    text = "".join(text_parts)
    if _file_digest(path) != digest:
        raise DocumentError("Source changed during extraction; save it and retry")
    if not text.strip():
        warnings.append(
            "Document has no searchable text; unmatched does not prove absence in images"
        )
    return Document(
        path=path,
        sha256=digest,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        media_type=media_type,
        text=text,
        units=tuple(units),
        extraction={
            "policy": TEXT_POLICY,
            "encoding": encoding
            if media_type.startswith("text/") and suffix not in {".html", ".htm"}
            else None,
            "versions": versions,
            "separator": SEPARATOR,
            "offset_unit": "unicode-code-point",
            "end_exclusive": True,
            "pdf_limits": PDF_LIMITS.copy() if media_type == "application/pdf" else None,
        },
        warnings=tuple(warnings),
    )
