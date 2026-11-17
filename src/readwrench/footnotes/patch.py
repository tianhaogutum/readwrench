"""Only navigation insertions are permitted; original bytes stay in order."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from html import escape

from .audit import Audit
from .epub import Book, EpubError, local_name, parse_xml, relative_href


@dataclass(frozen=True)
class Insertion:
    at: int
    data: bytes
    reason: str


@dataclass
class Patch:
    changes: dict[str, bytes]
    manifest: dict
    diff: str


def validate_decisions(book: Book, fresh: Audit, decisions: dict) -> list[dict]:
    if not isinstance(decisions, dict) or type(decisions.get("schema_version")) is not int or decisions.get("schema_version") != 1:
        raise EpubError("Unsupported review schema; expected schema_version 1")
    if decisions.get("book_sha256") != book.sha256:
        raise EpubError("The EPUB changed since review; scan and confirm the current file again")
    entries = decisions.get("references")
    if not isinstance(entries, list):
        raise EpubError("Review references must be a list")
    current = {r["key"]: r for r in fresh.plan["references"]}
    seen: set[str] = set()
    approved = []
    for decision in entries:
        if not isinstance(decision, dict) or not isinstance(decision.get("key"), str):
            raise EpubError("Every review decision must have a reference key")
        key = decision["key"]
        if key in seen or key not in current:
            raise EpubError(f"Duplicate or unknown reference key: {key}")
        seen.add(key)
        if type(decision.get("approved")) is not bool:
            raise EpubError(f"approved must be true or false: {key}")
        action = decision.get("action")
        if not isinstance(action, str) or action not in {"skip", "keep", "add_backlink"}:
            raise EpubError(f"Invalid review action: {key}")
        if not decision["approved"] or action == "skip":
            continue
        record = current[key]
        if decision.get("note_key") != record["note_key"]:
            raise EpubError(f"Association differs from the actual forward link: {key}; repair it in an editor and rescan")
        if action == "add_backlink" and record["status"] not in {"missing-return", "ancestor-return", "exact-return"}:
            raise EpubError(f"Cannot add a return for {record['status']}: {key}")
        label = decision.get("backlink_label")
        if not isinstance(label, str) or not label.strip() or len(label) > 256 or any(
            not (0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in label
        ):
            raise EpubError(f"Use a nonempty printable backlink label of at most 256 characters: {key}")
        approved.append({**record, "action": action, "backlink_label": label.strip(), "approved": True})
    if not approved:
        raise EpubError("No approved keep/add_backlink decisions; confirm individual references first")
    return approved


def make_patch(book: Book, fresh: Audit, decisions: dict) -> Patch:
    approved = validate_decisions(book, fresh, decisions)
    inserts: dict[str, list[Insertion]] = {}
    returns: dict[str, list[tuple[dict, str]]] = {}
    paths: list[dict] = []
    reserved = {p: set(d.ids) for p, d in book.documents.items()}
    for record in approved:
        doc, reference = fresh.references[record["key"]]
        route = {"reference_key": record["key"], "number": record["number"],
                 "source": record["location"], "forward_href": record["href"],
                 "note_key": record["note_key"], "action": record["action"],
                 "previous_status": record["status"], "context": record["context"],
                 "context_language": record.get("context_language"),
                 "label": record["backlink_label"], "existing_returns": record["returns"]}
        if record["action"] == "keep" or record["status"] == "exact-return":
            route["result"] = "kept-existing" if record["status"] == "exact-return" else "kept-without-exact-return"
            if record["status"] in {"exact-return", "ancestor-return"}:
                kind = "exact" if record["status"] == "exact-return" else "ancestor"
                existing = next(r for r in record["returns"] if r["kind"] == kind)
                route.update({"label": existing["label"], "return_href": existing["href"],
                              "return_language": existing.get("language"), "return_location": existing["location"],
                              "return_id": existing["location"].get("id")})
                if kind == "ancestor":
                    route["result"] = "kept-ancestor-return"
            paths.append(route)
            continue
        identity = reference.get("id")
        if identity is not None and (not identity or len(doc.ids.get(identity, [])) != 1):
            raise EpubError(f"Reference ID is empty or duplicated: {record['key']}")
        if identity is None:
            stem = "fnrt-" + hashlib.sha256(record["key"].encode()).hexdigest()[:12]
            identity = stem
            suffix = 2
            while identity in reserved[doc.path]:
                identity = f"{stem}-{suffix}"
                suffix += 1
            reserved[doc.path].add(identity)
            span = doc.span(reference)
            inserts.setdefault(doc.path, []).append(Insertion(
                span.attribute_at, doc.encode(f' id="{identity}"'), "reference-id"))
        note_doc, note = fresh.notes[record["note_key"]]
        href = relative_href(note_doc.path, doc.path, identity)
        returns.setdefault(record["note_key"], []).append((record, href))
        route.update({"result": "added-exact-return", "return_href": href, "return_id": identity,
                      "return_language": fresh.plan["scan_options"]["label_lang"] or next(n for n in fresh.plan["notes"] if n["key"] == record["note_key"]).get("language"),
                      "note": next(n for n in fresh.plan["notes"] if n["key"] == record["note_key"])["location"]})
        paths.append(route)

    for key, group in returns.items():
        doc, note = fresh.notes[key]
        span = doc.span(note)
        if span.empty:
            raise EpubError(f"An empty, self-closing note needs author editing before a return can be inserted: {key}")
        links = []
        for record, href in group:
            role = ' role="doc-backlink"' if book.version.startswith("3.") else ""
            language = fresh.plan["scan_options"]["label_lang"]
            lang = f' xml:lang="{language}"' if language else ""
            if language and book.version.startswith("3."):
                lang += f' lang="{language}"'
            links.append(f'<a xmlns="http://www.w3.org/1999/xhtml" class="fnrt-backlink" href="{escape(href, quote=True)}"{role}{lang}>{escape(record["backlink_label"])}</a>')
        body = " ".join(links)
        if local_name(note) in {"p", "span"}:
            body = " " + body
        else:
            body = f'<p xmlns="http://www.w3.org/1999/xhtml" class="fnrt-returns">{body}</p>'
        inserts.setdefault(doc.path, []).append(Insertion(span.close, doc.encode(body), "return-navigation"))

    changes: dict[str, bytes] = {}
    edits = []
    diffs = []
    for member, insertions in inserts.items():
        doc = book.documents[member]
        ordered = sorted(insertions, key=lambda i: i.at)
        if len({i.at for i in ordered}) != len(ordered):
            raise EpubError(f"Overlapping navigation edits in {member}")
        segments = []
        cursor = 0
        for item in ordered:
            if not cursor <= item.at <= len(doc.data):
                raise EpubError("Invalid XML source position")
            segments.extend((doc.data[cursor:item.at], item.data))
            edits.append({"path": member, "byte_offset": item.at, "inserted_bytes": len(item.data),
                          "kind": item.reason, "insertion_sha256": hashlib.sha256(item.data).hexdigest()})
            cursor = item.at
        segments.append(doc.data[cursor:])
        changed = b"".join(segments)
        parse_xml(changed, member)
        changes[member] = changed
        lines = difflib.unified_diff(
            doc.data.decode(doc.encoding).splitlines(keepends=True),
            changed.decode(doc.encoding).splitlines(keepends=True),
            fromfile="a/" + member, tofile="b/" + member,
        )
        for line in lines:
            diffs.append(line)
            if not line.endswith("\n"):
                diffs.append("\n\\ No newline at end of file\n")
    manifest = {
        "schema_version": 1, "source_sha256": book.sha256, "book_title": book.title,
        "approved_references": len(approved), "total_references": len(fresh.plan["references"]),
        "unreviewed_or_skipped": len(fresh.plan["references"]) - len(approved),
        "changed_members": [{"path": path, "before_sha256": hashlib.sha256(book.documents[path].data).hexdigest(),
                             "after_sha256": hashlib.sha256(data).hexdigest()} for path, data in changes.items()],
        "edits": edits, "paths": paths, "preservation": "Only listed byte insertions. All original member payload bytes remain in order; other members are copied unchanged.",
        "accessibility_status": "not_assessed",
    }
    return Patch(changes, manifest, "".join(diffs))
