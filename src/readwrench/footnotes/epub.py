"""Conservative archive/XML adapter. No member is extracted to disk."""

from __future__ import annotations

import codecs
import copy
import hashlib
import posixpath
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from html.entities import name2codepoint
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from xml.parsers import expat

from lxml import etree

from .base import EpubError

XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
OPF = "http://www.idpf.org/2007/opf"
CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
NS = {"x": XHTML, "epub": EPUB, "opf": OPF}
MAX_ENTRIES = 5000
MAX_EXPANDED = 128 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_DOCUMENTS_BYTES = 24 * 1024 * 1024
MAX_XML_NODES = 50_000
MAX_BOOK_NODES = 100_000


class OfflineResolver(etree.Resolver):
    def resolve(self, url, public_id, context):
        # no_network alone does not stop file:// resolution, and libxml2 builds
        # differ in whether they inspect a referenced XHTML external subset.
        return self.resolve_string("", context)


def guard_xml(data: bytes, name: str) -> None:
    """Bound node/depth growth before lxml allocates a tree; refuse DTD defaults."""
    def doctype(root_name, system_id, public_id, internal):
        if internal:
            raise EpubError(f"Internal DTD subsets are unsupported (entities/default attributes): {name}")

    nodes, depth = 0, 0

    def bump(amount=1):
        nonlocal nodes
        nodes += amount
        if nodes > MAX_XML_NODES:
            raise EpubError(f"XML member exceeds the {MAX_XML_NODES} node memory bound: {name}")

    def count(*args):
        bump()

    def start(qname, attributes):
        nonlocal depth
        bump(1 + len(attributes))
        depth += 1
        if depth > 256:
            raise EpubError(f"XML nesting exceeds the 256-level bound: {name}")

    def end(*args):
        nonlocal depth
        depth -= 1

    utf16 = data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, b"<\x00", b"\x00<"))
    parser = expat.ParserCreate(encoding="UTF-16" if utf16 else None)
    parser.StartDoctypeDeclHandler = doctype
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CommentHandler = count
    parser.ProcessingInstructionHandler = count
    parser.SkippedEntityHandler = count
    parser.ExternalEntityRefHandler = lambda *args: 1
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(data, True)
    except EpubError:
        raise
    except (expat.ExpatError, ValueError) as exc:
        raise EpubError(f"Cannot safely parse XML in {name}: {exc}") from exc


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_member(name: str) -> bool:
    stripped = name.removesuffix("/")
    return bool(stripped) and not any(
        (name.startswith("/"), "\\" in name, "\x00" in name,
         ":" in name, posixpath.normpath(stripped) != stripped,
         stripped == "..", stripped.startswith("../"))
    )


def parse_xml(data: bytes, name: str) -> etree._Element:
    if len(data) > MAX_XML:
        raise EpubError(f"XML member exceeds {MAX_XML} bytes: {name}")
    guard_xml(data, name)
    try:
        parser = etree.XMLParser(
            no_network=True, resolve_entities=False, load_dtd=False,
            recover=False, huge_tree=False, remove_blank_text=False,
            strip_cdata=False, collect_ids=False,
        )
        parser.resolvers.add(OfflineResolver())
        root = etree.fromstring(data, parser)
        dtd = root.getroottree().docinfo.internalDTD
        if dtd is not None and list(dtd.iterentities()):
            raise EpubError(f"Internal entity declarations are unsupported: {name}")
        if root.xpath("//@xml:base", namespaces={"xml": "http://www.w3.org/XML/1998/namespace"}):
            raise EpubError(f"xml:base is unsupported; resolve it in an editor first: {name}")
        if root.xpath("//x:base", namespaces=NS):
            raise EpubError(f"HTML base URLs are unsupported: {name}")
        return root
    except etree.XMLSyntaxError as exc:
        raise EpubError(f"Malformed XML in {name}: {exc}") from exc


def local_name(element: etree._Element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def tokens(element: etree._Element, attribute: str) -> set[str]:
    return set(element.get(attribute, "").split())


def excerpt(element: etree._Element, limit: int = 240) -> str:
    def chunks(node):
        if isinstance(node, etree._Entity):
            codepoint = name2codepoint.get(node.name)
            yield chr(codepoint) if codepoint is not None else node.text or ""
        elif isinstance(node.tag, str):
            if node.text:
                yield node.text
            for child in node:
                yield from chunks(child)
                if child.tail:
                    yield child.tail
    return " ".join("".join(chunks(element)).split())[:limit]


def inherited_language(element: etree._Element) -> str | None:
    for node in [element, *element.iterancestors()]:
        for attribute in ("{http://www.w3.org/XML/1998/namespace}lang", "lang"):
            if attribute in node.attrib:
                return node.get(attribute) or None
    return None


@dataclass(frozen=True)
class Span:
    start: int
    attribute_at: int
    close: int
    empty: bool
    qname: str


class Document:
    def __init__(self, path: str, data: bytes):
        self.path, self.data = path, data
        self.root = parse_xml(data, path)
        if self.root.tag != f"{{{XHTML}}}html":
            raise EpubError(f"Expected an XHTML html root: {path}")
        self.elements = [e for e in self.root.iter() if isinstance(e.tag, str)]
        self.node_count = sum(1 + (len(e.attrib) if isinstance(e.tag, str) else 0) for e in self.root.iter())
        self.node_count += sum(1 for _ in self.root.itersiblings(preceding=True))
        self.node_count += sum(1 for _ in self.root.itersiblings())
        self.ordinals = {e: i for i, e in enumerate(self.elements)}
        self.ids: dict[str, list[etree._Element]] = {}
        for element in self.elements:
            if element.get("id") is not None:
                self.ids.setdefault(element.get("id"), []).append(element)
        title = self.root.find(f"{{{XHTML}}}head/{{{XHTML}}}title")
        self.title = excerpt(title, 256) if title is not None else ""
        self.title = self.title or path
        encoding = self.root.getroottree().docinfo.encoding or "UTF-8"
        try:
            normalized = codecs.lookup(encoding).name
        except LookupError as exc:
            raise EpubError(f"Unsupported XML encoding {encoding} in {path}; use UTF-8 or UTF-16") from exc
        if normalized == "utf-16":
            self.encoding = "utf-16-le" if data.startswith(codecs.BOM_UTF16_LE) else "utf-16-be"
        elif normalized in {"utf-8", "utf-16-le", "utf-16-be", "ascii"}:
            self.encoding = normalized
        else:
            raise EpubError(f"Unsupported XML encoding {encoding} in {path}; use UTF-8 or UTF-16")
        self._spans: list[Span] | None = None

    def key(self, element: etree._Element) -> str:
        return f"{self.path}::e{self.ordinals[element]}"

    def encode(self, text: str) -> bytes:
        # ASCII XML may still receive a localized label through character references.
        return text.encode(self.encoding, errors="xmlcharrefreplace")

    def spans(self) -> list[Span]:
        if self._spans is not None:
            return self._spans
        # Expat's built-in UTF-16 decoder accepts LE/BE spellings without a
        # hyphen; Python's codec fallback rejects the hyphenated multibyte names.
        parser = expat.ParserCreate(encoding="UTF-16" if self.encoding.startswith("utf-16") else None)
        spans: list[Span | None] = []
        stack: list[tuple[int, int, str, int, bool]] = []
        unit = 2 if self.encoding.startswith("utf-16") else 1

        def start(qname: str, attributes: dict) -> None:
            at = parser.CurrentByteIndex
            attr_at = at + len(self.encode("<" + qname))
            # Expat supplies the element position; only locate the terminator of
            # that validated start tag, respecting quoted '>' attribute values.
            cursor, quoted = attr_at, None
            quotes = {self.encode("'"), self.encode('"')}
            while cursor < len(self.data):
                char = self.data[cursor:cursor + unit]
                if quoted:
                    if char == quoted:
                        quoted = None
                elif char in quotes:
                    quoted = char
                elif char == self.encode(">"):
                    break
                cursor += unit
            empty = self.data[cursor - unit:cursor] == self.encode("/")
            index = len(spans)
            spans.append(None)
            stack.append((index, at, qname, attr_at, empty))

        def end(qname: str) -> None:
            index, at, opening_name, attr_at, empty = stack.pop()
            spans[index] = Span(at, attr_at, parser.CurrentByteIndex, empty, opening_name)

        def reject_entity(*args) -> None:
            raise EpubError(f"Entity declarations cannot be patched: {self.path}")

        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.EntityDeclHandler = reject_entity
        parser.ExternalEntityRefHandler = lambda *args: 1
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        try:
            parser.Parse(self.data, True)
        except (expat.ExpatError, ValueError) as exc:
            raise EpubError(f"Cannot locate XML source positions in {self.path}: {exc}") from exc
        if len(spans) != len(self.elements) or any(s is None for s in spans):
            raise EpubError(f"XML parser position disagreement: {self.path}")
        self._spans = spans  # type: ignore[assignment]
        return self._spans

    def span(self, element: etree._Element) -> Span:
        return self.spans()[self.ordinals[element]]


@dataclass(frozen=True)
class Target:
    path: str | None
    fragment: str | None
    error: str | None = None


def resolve_href(source: str, href: str) -> Target:
    try:
        if re.search(r"%(?![0-9A-Fa-f]{2})", href):
            return Target(None, None, "invalid-percent-escape")
        parts = urlsplit(href)
        if parts.scheme or parts.netloc:
            return Target(None, None, "external-link")
        if parts.query:
            return Target(None, None, "query-in-link")
        path = unquote(parts.path, encoding="utf-8", errors="strict")
        fragment = unquote(parts.fragment, encoding="utf-8", errors="strict")
        if path.startswith("/") or "\\" in path or "\x00" in path or "\x00" in fragment:
            return Target(None, None, "unsafe-link-path")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), path)) if path else source
        if not safe_member(target):
            return Target(None, None, "link-escapes-archive")
        if fragment.startswith("epubcfi("):
            return Target(target, fragment, "epub-cfi-unsupported")
        return Target(target, fragment or None)
    except (ValueError, UnicodeError):
        return Target(None, None, "invalid-link")


def relative_href(source: str, target: str, fragment: str) -> str:
    path = "" if source == target else posixpath.relpath(target, posixpath.dirname(source) or ".")
    return quote(path, safe="/.-_") + "#" + quote(fragment, safe="-._~")


class Book:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.archive: zipfile.ZipFile | None = None
        try:
            self.sha256 = sha256_file(self.path)
            self.archive = zipfile.ZipFile(self.path)
            self._load()
        except Exception:
            self.close()
            raise

    def _load(self) -> None:
        assert self.archive is not None
        entries = self.archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise EpubError(f"Archive has more than {MAX_ENTRIES} members")
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise EpubError("Duplicate archive member names are ambiguous")
        if any(not safe_member(n) for n in names):
            raise EpubError("Archive contains unsafe or noncanonical member paths")
        if any(entry.flag_bits & 1 for entry in entries):
            raise EpubError("Encrypted ZIP members are unsupported")
        if any(stat.S_ISLNK(entry.external_attr >> 16) for entry in entries):
            raise EpubError("Symbolic links in EPUB archives are unsupported")
        if any(entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} for entry in entries):
            raise EpubError("Only stored and deflated EPUB members are supported")
        if any(e.file_size > MAX_MEMBER for e in entries) or sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise EpubError("Archive exceeds the bounded expanded-size limit")
        self.names = set(names)
        if not entries or entries[0].filename != "mimetype" or entries[0].compress_type != zipfile.ZIP_STORED:
            raise EpubError("EPUB mimetype must be the first, uncompressed member")
        if self.read("mimetype") != b"application/epub+zip":
            raise EpubError("Invalid EPUB mimetype")
        if "META-INF/signatures.xml" in self.names:
            raise EpubError("Signed EPUBs are unsupported; patching would invalidate the signature")
        if "META-INF/encryption.xml" in self.names:
            encryption = parse_xml(self.read("META-INF/encryption.xml"), "META-INF/encryption.xml")
            if encryption.xpath('//*[local-name()="EncryptedData"]'):
                raise EpubError("Encrypted or font-obfuscated EPUBs are unsupported")
        container = parse_xml(self.read("META-INF/container.xml"), "META-INF/container.xml")
        roots = container.findall(f"{{{CONTAINER}}}rootfiles/{{{CONTAINER}}}rootfile")
        if len(roots) != 1:
            raise EpubError("Exactly one package rendition is required")
        self.opf_path = roots[0].get("full-path", "")
        if not safe_member(self.opf_path):
            raise EpubError("Invalid package document path")
        package = parse_xml(self.read(self.opf_path), self.opf_path)
        if package.tag != f"{{{OPF}}}package":
            raise EpubError("Invalid package document root")
        self.version = package.get("version", "")
        if not self.version.startswith(("2.", "3.")):
            raise EpubError(f"Unsupported EPUB package version: {self.version!r}")
        self.title = " ".join(package.xpath('//*[local-name()="metadata"]/*[local-name()="title"]/text()')).strip()
        manifest: dict[str, str] = {}
        xhtml: list[str] = []
        self.nav_paths: set[str] = set()
        for item in package.findall(f"{{{OPF}}}manifest/{{{OPF}}}item"):
            target = resolve_href(self.opf_path, item.get("href", ""))
            if target.error or target.fragment or not target.path or target.path not in self.names:
                raise EpubError(f"Missing or invalid manifest item: {item.get('href')!r}")
            identity = item.get("id", "")
            if not identity or identity in manifest:
                raise EpubError("Manifest item IDs must be unique and nonempty")
            manifest[identity] = target.path
            if item.get("media-type") == "application/xhtml+xml":
                if target.path in xhtml:
                    raise EpubError(f"Duplicate XHTML manifest path: {target.path}")
                xhtml.append(target.path)
                if "nav" in tokens(item, "properties"):
                    self.nav_paths.add(target.path)
        order: list[str] = []
        self.nonlinear_paths: set[str] = set()
        for item in package.findall(f"{{{OPF}}}spine/{{{OPF}}}itemref"):
            identity = item.get("idref", "")
            if identity not in manifest:
                raise EpubError(f"Unknown spine idref: {identity}")
            member = manifest[identity]
            if member in order:
                raise EpubError(f"Repeated spine item: {member}")
            order.append(member)
            if item.get("linear") == "no":
                self.nonlinear_paths.add(member)
        self.spine_paths = order
        order.extend(member for member in xhtml if member not in order)
        self.document_paths = [member for member in order if member in xhtml]
        if sum(self.archive.getinfo(n).file_size for n in self.document_paths) > MAX_DOCUMENTS_BYTES:
            raise EpubError("XHTML document total exceeds the 24 MiB memory bound")
        self.documents = {}
        nodes = 0
        for member in self.document_paths:
            document = Document(member, self.read(member))
            nodes += document.node_count
            if nodes > MAX_BOOK_NODES:
                raise EpubError(f"XHTML exceeds the {MAX_BOOK_NODES} total-node memory bound")
            self.documents[member] = document
        self.verify_unchanged()

    def read(self, name: str) -> bytes:
        assert self.archive is not None
        if name not in self.archive.namelist():
            raise EpubError(f"Required member is missing: {name}")
        try:
            return self.archive.read(name)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise EpubError(f"Cannot read member {name}: {exc}") from exc

    def target(self, source: str, href: str) -> tuple[Target, etree._Element | None]:
        target = resolve_href(source, href)
        if target.error:
            return target, None
        doc = self.documents.get(target.path)
        if doc is None:
            error = "target-not-xhtml" if target.path in self.names else "missing-target-file"
            return Target(target.path, target.fragment, error), None
        if not target.fragment:
            body = doc.root.find(f"{{{XHTML}}}body")
            return (target, body) if body is not None else (Target(target.path, None, "missing-target-body"), None)
        matches = doc.ids.get(target.fragment, [])
        if len(matches) != 1:
            error = "duplicate-target-id" if matches else "missing-target-id"
            return Target(target.path, target.fragment, error), None
        if etree.QName(matches[0]).namespace != XHTML:
            return Target(target.path, target.fragment, "target-not-xhtml-element"), None
        return target, matches[0]

    def write_candidate(self, path: Path, changes: dict[str, bytes]) -> None:
        assert self.archive is not None
        if path.resolve() == self.path.resolve():
            raise EpubError("The candidate path must differ from the source EPUB")
        if path.exists() or path.is_symlink():
            raise EpubError(f"Candidate already exists: {path}")
        self.verify_unchanged()
        if not changes:
            with self.path.open("rb") as source, path.open("xb") as target:
                shutil.copyfileobj(source, target, length=64 * 1024)
            self.verify_unchanged()
            return
        with zipfile.ZipFile(path, "x") as output:
            output.comment = self.archive.comment
            for entry in self.archive.infolist():
                info = copy.copy(entry)
                if entry.filename in changes:
                    output.writestr(info, changes[entry.filename])
                else:
                    with self.archive.open(entry) as source, output.open(info, "w") as target:
                        shutil.copyfileobj(source, target, length=64 * 1024)
        self.verify_unchanged()

    def verify_unchanged(self) -> None:
        if sha256_file(self.path) != self.sha256:
            raise EpubError("The source EPUB changed during processing; retry from a fresh scan")

    def close(self) -> None:
        if self.archive is not None:
            self.archive.close()
            self.archive = None

    def __enter__(self) -> Book:
        return self

    def __exit__(self, *args) -> None:
        self.close()
