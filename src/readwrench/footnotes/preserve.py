"""Independently verify candidate ZIP payloads against the insertion manifest."""

from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

from .epub import Book, EpubError, MAX_DOCUMENTS_BYTES, sha256_file


def verify_preservation(book: Book, candidate: Path, manifest: dict) -> dict:
    if manifest.get("source_sha256") != book.sha256 or sha256_file(candidate) != manifest.get("candidate_sha256"):
        raise EpubError("Preservation check: source/candidate hashes do not match the manifest")
    changes = {item["path"]: item for item in manifest["changed_members"]}
    edits: dict[str, list[dict]] = {}
    for item in manifest["edits"]:
        edits.setdefault(item["path"], []).append(item)
    if set(edits) != set(changes):
        raise EpubError("Preservation check: changed-member and insertion lists disagree")
    assert book.archive is not None
    with ZipFile(candidate) as output:
        if output.namelist() != book.archive.namelist() or output.comment != book.archive.comment:
            raise EpubError("Preservation check: archive membership/order/comment changed")
        if sum(output.getinfo(n).file_size for n in book.document_paths) > MAX_DOCUMENTS_BYTES:
            raise EpubError("Candidate XHTML exceeds the 24 MiB document memory bound")
        original_bytes = 0
        for info in book.archive.infolist():
            name = info.filename
            original_bytes += info.file_size
            if name not in changes:
                with book.archive.open(info) as before, output.open(name) as after:
                    if hashlib.file_digest(before, "sha256").digest() != hashlib.file_digest(after, "sha256").digest():
                        raise EpubError(f"Preservation check: an unlisted member changed: {name}")
                continue
            original = book.documents[name].data
            patched = output.read(name)
            member = changes[name]
            if hashlib.sha256(original).hexdigest() != member["before_sha256"] or hashlib.sha256(patched).hexdigest() != member["after_sha256"]:
                raise EpubError(f"Preservation check: member hash mismatch: {name}")
            original_at, candidate_at = 0, 0
            for edit in sorted(edits[name], key=lambda e: e["byte_offset"]):
                offset, size = edit["byte_offset"], edit["inserted_bytes"]
                if type(offset) is not int or type(size) is not int or size <= 0 or not original_at <= offset <= len(original):
                    raise EpubError(f"Preservation check: invalid insertion range: {name}")
                unchanged_size = offset - original_at
                if patched[candidate_at:candidate_at + unchanged_size] != original[original_at:offset]:
                    raise EpubError(f"Preservation check: original bytes were modified: {name}")
                candidate_at += unchanged_size
                inserted = patched[candidate_at:candidate_at + size]
                if hashlib.sha256(inserted).hexdigest() != edit["insertion_sha256"]:
                    raise EpubError(f"Preservation check: insertion hash mismatch: {name}")
                candidate_at += size
                original_at = offset
            if patched[candidate_at:] != original[original_at:]:
                raise EpubError(f"Preservation check: original tail bytes were modified: {name}")
    book.verify_unchanged()
    return {"status": "passed", "original_payload_bytes": original_bytes,
            "insertions_verified": len(manifest["edits"]), "unchanged_members": len(book.names) - len(changes),
            "meaning": "Original member bytes preserved; insertion content/reading semantics require author review."}
