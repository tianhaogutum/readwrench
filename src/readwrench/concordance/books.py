"""Book parsing is delegated to EbookLib and Beautiful Soup/html5lib.

Canonical text v1 collapses whitespace but preserves Unicode code points and
inline text adjacency. It removes active/non-prose content, separates blocks,
and retains heading and fragment offsets. These are not EPUB CFIs.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit
from zipfile import BadZipFile, ZipFile

import ebooklib
from bs4 import BeautifulSoup, Comment, NavigableString, Tag, UnicodeDammit
from ebooklib import epub
from lxml import etree

from .errors import InputError

__all__ = ["Book", "Chapter", "InputError", "canonical_html", "digest_file", "digest_text",
           "read_book", "safe_resource", "stable_id"]


MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
BLOCKS = set("address article aside blockquote br caption dd div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr li main ol p pre section table tbody td tfoot th thead tr ul".split())
HEADINGS = set("h1 h2 h3 h4 h5 h6".split())


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_id(*parts: str) -> str:
    # Length prefixes prevent ambiguous concatenations.
    return hashlib.sha256("".join(f"{len(p)}:{p}" for p in parts).encode()).hexdigest()[:24]


def safe_resource(href: str, *, allow_parent: bool = False) -> str:
    """Validate a literal ZIP path, not a URI (EbookLib already unquotes hrefs)."""
    path = PurePosixPath(href)
    decoded = PurePosixPath(unquote(href))
    if (not href or href.startswith("/") or "\\" in href or any(ord(c) < 32 for c in href)
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", href)
            or (not allow_parent and (".." in path.parts or ".." in decoded.parts))):
        raise InputError(f"Unsafe EPUB resource path: {href!r}")
    return str(path)


@dataclass
class Chapter:
    id: str
    book_id: str
    href: str
    archive_path: str
    title: str
    spine_index: int
    media_type: str
    text: str
    headings: list[dict]
    anchors: list[dict]
    direction: str = "LEFT_TO_RIGHT"

    def metadata(self) -> dict:
        data = asdict(self)
        del data["text"]
        data["text_sha256"] = digest_text(self.text)
        data["text_length"] = len(self.text)
        data["text_file"] = f"text/{self.id}.txt"
        return data


@dataclass
class Book:
    id: str
    sha256: str
    path: str
    title: str
    authors: list[str]
    identifiers: list[str]
    language: list[str]
    edition: list[str]
    epub_version: str
    chapters: list[Chapter]

    def metadata(self) -> dict:
        return {key: value for key, value in asdict(self).items() if key != "chapters"}


def canonical_html(content: bytes | str) -> tuple[str, list[dict], list[dict]]:
    # html5lib's HTML encoding sniffing does not honor an XML encoding
    # declaration. EPUB XHTML must be decoded before HTML5 tree normalization.
    if isinstance(content, bytes):
        decoded = UnicodeDammit(content, is_html=False)
        if decoded.unicode_markup is None or decoded.contains_replacement_characters:
            raise InputError("EPUB chapter encoding cannot be decoded without replacement characters")
        declaration = re.match(r'^\s*<\?xml[^>]*\bencoding\s*=\s*[\'"]([^\'"]+)[\'"]', decoded.unicode_markup, re.IGNORECASE)
        if declaration:
            try:
                # Do not let a failed declared encoding silently fall back to
                # an unrelated single-byte encoding and produce a bad quote.
                content.decode(declaration[1], errors="strict")
            except (UnicodeError, LookupError) as exc:
                raise InputError(f"Invalid declared chapter encoding: {declaration[1]}") from exc
        content = decoded.unicode_markup
    # Avoid a misleading XML-as-HTML warning: the HTML5 parser is intentional,
    # matching Taguette's tree model, after XML-aware Unicode decoding above.
    content = re.sub(r"^\s*<\?xml[^>]*\?>", "", content, count=1)
    soup = BeautifulSoup(content, "html5lib")
    for unwanted in soup.find_all(["script", "style", "svg", "iframe", "object", "noscript", "template"]):
        unwanted.decompose()
    body = soup.body or soup
    pieces: list[str] = []
    length = 0
    pending_space = False
    headings: list[dict] = []
    anchors: list[dict] = []

    def append(value: str) -> None:
        nonlocal length, pending_space
        for match in re.finditer(r"\s+|\S+", value):
            chunk = match.group()
            if chunk.isspace():
                pending_space = True
            else:
                if pending_space and length:
                    pieces.append(" ")
                    length += 1
                pieces.append(chunk)
                length += len(chunk)
                pending_space = False

    # Iterative traversal also handles deeply nested, imperfect real HTML.
    stack: list[tuple[object, bool]] = [(body, False)]
    while stack:
        node, closing = stack.pop()
        if isinstance(node, Comment):
            continue
        if isinstance(node, NavigableString):
            append(str(node))
        elif isinstance(node, Tag):
            if node.name in BLOCKS:
                pending_space = True
            if closing:
                continue
            position = length + int(pending_space and length > 0)
            if node.get("id"):
                anchors.append({"start": position, "fragment": str(node["id"])})
            if node.name in HEADINGS:
                title = " ".join(node.get_text().split())
                if title:
                    headings.append({"start": position, "title": title})
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(node.contents))
    text = "".join(pieces)
    for anchor in anchors:
        anchor["start"] = min(anchor["start"], len(text))
    return text, headings, anchors


def _preflight_epub(path: Path) -> set[str]:
    if not path.is_file():
        raise InputError(f"Book does not exist: {path}")
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise InputError("EPUB exceeds the 32 MiB compressed size limit")
    encrypted_resources: set[str] = set()
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 5000:
                raise InputError("EPUB has more than 5000 archive members")
            if sum(m.file_size for m in members) > MAX_EXPANDED_BYTES:
                raise InputError("EPUB exceeds the 32 MiB expanded size limit")
            names: set[str] = set()
            for member in members:
                safe_resource(member.filename)
                if member.filename in names:
                    raise InputError("EPUB contains duplicate archive paths")
                names.add(member.filename)
                if member.file_size > MAX_MEMBER_BYTES:
                    raise InputError("EPUB member exceeds the 8 MiB size limit")
                if member.flag_bits & 1:
                    raise InputError("Encrypted EPUB archive members are unsupported")
            if "META-INF/encryption.xml" in names:
                # Fonts may be obfuscated without DRM. Never silently search
                # encrypted prose; unencrypted resources remain supported.
                try:
                    encryption = etree.fromstring(archive.read("META-INF/encryption.xml"),
                        parser=etree.XMLParser(resolve_entities=False, no_network=True))
                except etree.XMLSyntaxError as exc:
                    raise InputError(f"Invalid EPUB encryption metadata: {exc}") from exc
                for entry in encryption.iter():
                    if not isinstance(entry.tag, str) or etree.QName(entry).localname != "EncryptedData":
                        continue
                    if etree.QName(entry).namespace != "http://www.w3.org/2001/04/xmlenc#":
                        raise InputError("Invalid XML namespace in EPUB encryption metadata")
                    method = entry.find("{*}EncryptionMethod")
                    if method is None or method.get("Algorithm") not in {
                        "http://www.idpf.org/2008/embedding",
                        "http://ns.adobe.com/pdf/enc#RC",
                    }:
                        raise InputError("DRM/encrypted text EPUB is unsupported")
                    references = entry.findall("{*}CipherData/{*}CipherReference")
                    if not references:
                        raise InputError("Missing resource reference in EPUB encryption metadata")
                    for reference in references:
                        uri = reference.get("URI", "")
                        parts = urlsplit(uri)
                        if parts.scheme or parts.netloc or parts.query or parts.fragment:
                            raise InputError("Invalid resource URI in EPUB encryption metadata")
                        target = safe_resource(unquote(parts.path))
                        if target not in names:
                            raise InputError("Missing resource in EPUB encryption metadata")
                        encrypted_resources.add(target)
    except (BadZipFile, OSError) as exc:
        raise InputError(f"Cannot read EPUB archive: {path.name}: {exc}") from exc
    return encrypted_resources


def read_book(path: Path, *, include_nonlinear: bool = False) -> Book:
    path = path.resolve()
    if path.suffix.lower() != ".epub":
        raise InputError(f"Expected an EPUB2/EPUB3 file: {path.name}")
    encrypted_resources = _preflight_epub(path)
    sha = digest_file(path)
    try:
        # EpubBook.FOLDER_NAME is a writer default, not the source OPF folder.
        # Use the same reader lifecycle as read_epub, retaining its real base.
        reader = epub.EpubReader(path)
        publication = reader.load()
        reader.process()
    except Exception as exc:
        raise InputError(f"EbookLib could not parse {path.name}: {exc}") from exc

    def meta(key: str) -> list[str]:
        return [str(value) for value, _ in publication.get_metadata("DC", key) if value]

    title = next(iter(meta("title")), path.stem)
    toc_titles = {}
    pending = list(reversed(publication.toc))
    while pending:
        node = pending.pop()
        if isinstance(node, (tuple, list)):
            pending.extend(reversed(node))
        elif getattr(node, "href", None) and getattr(node, "title", None):
            resource = posixpath.normpath(unquote(urlsplit(node.href).path))
            toc_titles.setdefault(resource, str(node.title))
    book_id = sha[:24]
    chapters = []
    seen = set()
    for index, (item_id, linear) in enumerate(publication.spine, 1):
        item = publication.get_item_with_id(item_id)
        if item is None:
            raise InputError(f"Missing spine item {item_id!r} in {path.name}")
        if item.get_type() != ebooklib.ITEM_DOCUMENT or isinstance(item, epub.EpubNav) or "nav" in getattr(item, "properties", []):
            continue
        if str(linear).lower() == "no" and not include_nonlinear:
            continue
        resource_path = safe_resource(item.get_name(), allow_parent=True)
        archive_path = safe_resource(posixpath.normpath(posixpath.join(reader.opf_dir, resource_path)))
        if archive_path in encrypted_resources:
            raise InputError("EPUB declares encrypted prose; only unencrypted text is searchable")
        # URI syntax and literal ZIP names are separate: a literal '#', '?',
        # '%' or non-ASCII name must remain the same archive member.
        href = quote(resource_path, safe="/")
        if href in seen:
            continue
        seen.add(href)
        text, headings, anchors = canonical_html(item.content)
        if not text:
            continue
        chapter_title = headings[0]["title"] if headings else (toc_titles.get(resource_path) or getattr(item, "title", ""))
        if not chapter_title:
            head_title = BeautifulSoup(item.content, "xml").find("title")
            chapter_title = " ".join(head_title.get_text().split()) if head_title else href
        chapters.append(Chapter(
            id=stable_id(sha, href), book_id=book_id, href=href,
            archive_path=archive_path, title=chapter_title, spine_index=index,
            media_type=item.media_type, text=text, headings=headings, anchors=anchors,
            direction="RIGHT_TO_LEFT" if (getattr(item, "direction", None) or publication.direction) == "rtl" else "LEFT_TO_RIGHT",
        ))
    if not chapters:
        raise InputError(f"No searchable prose in the EPUB spine: {path.name}")
    if digest_file(path) != sha:
        raise InputError("Source EPUB changed during import; retry with a stable file")
    return Book(book_id, sha, str(path), title, meta("creator"), meta("identifier"),
                meta("language"), meta("date") + meta("publisher") + meta("contributor"),
                str(publication.version), chapters)
