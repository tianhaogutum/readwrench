"""Find the reading material under PATH by its content, not its name.

Generic kinds are recognised here from the first bytes of each file (EPUB, PDF,
fonts, HTML / MHTML / WARC web-page captures, plain text and Markdown). Each tool's
check adapter (``readwrench/<tool>/_check.py``) may recognise its own saved files
first (a review plan, a manifest, saved locators) through ``classify(path, head)``.

Nothing is written, nothing is executed, no symlink is followed and no network is
used. Hidden entries and dependency/cache folders are skipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import io
import os
from pathlib import Path
import stat
from typing import Callable, Iterable

from .model import HEAD_BYTES, INFO, WARNING, Finding, Source

IGNORE_DIRS = frozenset({"node_modules", "__pycache__", "venv", "site-packages"})
MAX_DEPTH = 12
MAX_FILES = 50_000

KINDS = {
    "epub": "EPUB book",
    "pdf": "PDF document",
    "font": "font file",
    "html": "saved web page (HTML)",
    "mhtml": "saved web page (MHTML)",
    "warc": "web archive (WARC)",
    "text": "text or Markdown file",
}

FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1", b"wOFF", b"wOF2", b"ttcf")
FONT_SUFFIXES = frozenset({".ttf", ".otf", ".woff", ".woff2", ".ttc", ".otc"})
TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".text"})
HTML_SUFFIXES = frozenset({".html", ".htm", ".xhtml"})
MHTML_SUFFIXES = frozenset({".mht", ".mhtml"})
# A file with one of these names is expected to be that kind; if its bytes say otherwise,
# discovery reports it instead of silently skipping it.
EXPECTED = {".epub": "epub", ".pdf": "pdf", **{suffix: "font" for suffix in FONT_SUFFIXES},
            ".warc": "warc", **{suffix: "mhtml" for suffix in MHTML_SUFFIXES}}

Classifier = Callable[[Path, bytes], "tuple[str, str] | None"]


class DiscoveryError(ValueError):
    """A path given on the command line cannot be used (exit 2)."""


@dataclass
class Discovery:
    sources: list[Source] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    scanned: int = 0
    truncated: bool = False

    def of(self, *kinds: str) -> list[Source]:
        return [source for source in self.sources if source.kind in kinds]


def _is_epub(head: bytes) -> bool:
    # OCF: the first zip member is an uncompressed "mimetype" file holding the media type.
    if not head.startswith(b"PK\x03\x04"):
        return False
    return head[30:38] == b"mimetype" and b"application/epub+zip" in head[38:100]


def _is_epub_zip(path: Path) -> bool:
    """A zip whose ``mimetype`` member is not first (common in hand-made EPUBs)."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("mimetype")
            if info.file_size > 100:
                return False
            return archive.read(info).strip() == b"application/epub+zip"
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return False


def _is_warc(path: Path, head: bytes) -> bool:
    if head.startswith(b"WARC/"):
        return True
    if head.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(head)) as stream:
                return stream.read(5) == b"WARC/"
        except (OSError, EOFError):
            return False
    return False


def _sniff_text(head: bytes) -> str | None:
    if b"\x00" in head[:4096]:
        return None
    for encoding in ("utf-8", "utf-16"):
        try:
            return head[:8192].decode(encoding, errors="strict")
        except UnicodeDecodeError:
            # A multi-byte character cut at the end of the sample is not an error.
            try:
                return head[:8188].decode(encoding, errors="strict")
            except UnicodeDecodeError:
                continue
    return None


def classify_generic(path: Path, head: bytes) -> tuple[str, str] | None:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if head.startswith(b"PK\x03\x04"):
        if _is_epub(head) or (suffix == ".epub" and _is_epub_zip(path)):
            return "epub", KINDS["epub"]
        return None
    if b"%PDF-" in head[:1024]:
        return "pdf", KINDS["pdf"]
    if head[:4] in FONT_MAGIC and (suffix in FONT_SUFFIXES or head[:4] in (b"OTTO", b"wOFF", b"wOF2", b"ttcf")):
        return "font", KINDS["font"]
    if suffix in (".warc", ".gz") or name.endswith(".warc.gz"):
        if _is_warc(path, head):
            return "warc", KINDS["warc"]
        return None
    text = _sniff_text(head)
    if text is None:
        return None
    sample = text.lstrip("﻿ \t\r\n")[:4096]
    lower = sample.lower()
    if suffix in MHTML_SUFFIXES or ("mime-version:" in lower and "multipart/related" in lower):
        return "mhtml", KINDS["mhtml"]
    if suffix in HTML_SUFFIXES or lower.startswith(("<!doctype html", "<html")):
        return "html", KINDS["html"]
    if suffix in TEXT_SUFFIXES:
        return "text", KINDS["text"]
    return None


def _display(root_display: str, root: Path, path: Path) -> str:
    if path == root:
        return root_display
    relative = path.relative_to(root)
    return relative.as_posix() if root_display in (".", "") else os.path.join(root_display, *relative.parts)


class _Walker:
    def __init__(self, classifiers: Iterable[Classifier], discovery: Discovery):
        self.classifiers = list(classifiers)
        self.discovery = discovery
        self.seen: set[str] = set()

    def file(self, path: Path, display: str, *, explicit: bool) -> None:
        real = os.path.realpath(path)
        if real in self.seen:
            return
        self.seen.add(real)
        self.discovery.scanned += 1
        try:
            with open(path, "rb") as handle:
                head = handle.read(HEAD_BYTES)
        except OSError as exc:
            self.discovery.findings.append(Finding(
                "inputs", "unreadable", WARNING, f"cannot be read ({exc.strerror or type(exc).__name__})", display))
            return
        if not head:
            if explicit or path.suffix.lower() in EXPECTED:
                self.discovery.findings.append(Finding("inputs", "empty", WARNING, "the file is empty", display))
            return
        for classify in self.classifiers:
            try:
                found = classify(path, head)
            except Exception:  # noqa: BLE001 - an adapter bug must not stop discovery
                found = None
            if found:
                kind, label = found
                self.discovery.sources.append(Source(kind, path, display, label))
                return
        expected = EXPECTED.get(path.suffix.lower())
        if expected is None and path.name.lower().endswith(".warc.gz"):
            expected = "warc"
        if expected is not None:
            self.discovery.findings.append(Finding(
                "inputs", "misnamed", WARNING,
                f"named like a {KINDS[expected]}, but its content is not one", display,
                hint="check that the download or export finished; readwrench recognises files by content"))
        elif explicit:
            self.discovery.findings.append(Finding(
                "inputs", "unrecognised", INFO, "not a file readwrench recognises; skipped", display))

    def folder(self, folder: Path, root: Path, root_display: str, depth: int) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError as exc:
            self.discovery.findings.append(Finding(
                "inputs", "unreadable", WARNING, f"folder cannot be listed ({exc.strerror or type(exc).__name__})",
                _display(root_display, root, folder)))
            return
        for entry in entries:
            if self.discovery.scanned >= MAX_FILES:
                self.discovery.truncated = True
                return
            if entry.name.startswith("."):
                continue
            path = Path(entry.path)
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                continue
            if stat.S_ISDIR(mode):
                if entry.name in IGNORE_DIRS or entry.name.endswith(".egg-info"):
                    continue
                if depth >= MAX_DEPTH:
                    self.discovery.truncated = True
                    continue
                self.folder(path, root, root_display, depth + 1)
            elif stat.S_ISREG(mode):
                self.file(path, _display(root_display, root, path), explicit=False)


def discover(paths: Iterable[str], classifiers: Iterable[Classifier] = ()) -> Discovery:
    """Classify every file under ``paths``; raise DiscoveryError for a missing path."""
    paths = list(paths)
    missing = [raw for raw in paths if not os.path.lexists(raw)]
    if missing:
        raise DiscoveryError("; ".join(f"{raw}: no such file or directory" for raw in missing))
    discovery = Discovery()
    walker = _Walker([*classifiers, classify_generic], discovery)
    for raw in paths:
        path = Path(raw)
        display = os.path.normpath(raw)
        if path.is_dir():
            walker.folder(path, path, display, 1)
        elif path.is_file():
            walker.file(path, display, explicit=True)
        else:
            raise DiscoveryError(f"{raw}: not a regular file or folder")
    discovery.sources.sort(key=lambda source: source.display)
    if discovery.truncated:
        discovery.findings.append(Finding(
            "inputs", "scan-truncated", INFO,
            f"stopped after {MAX_FILES:,} files or {MAX_DEPTH} folder levels; some files were not looked at",
            hint="pass the sub-folders you care about directly"))
    return discovery
