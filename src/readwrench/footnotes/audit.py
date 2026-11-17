"""Spine-ordered note paths and author-review decisions, not an a11y verdict."""

from __future__ import annotations

from dataclasses import dataclass
import re
from string import Formatter

from lxml import etree

from .base import DEFAULT_LABEL_TEMPLATE
from .epub import Book, Document, EPUB, EpubError, NS, XHTML, excerpt, inherited_language, local_name, tokens

NOTE_TYPES = {"footnote", "endnote", "rearnote"}
NOTE_CLASSES = {"footnote", "endnote", "rearnote", "note"}
REF_CLASSES = {"noteref", "noteanchor", "footnote-ref", "footnote-reference", "fnanchor", "fnref"}
SAFE_NOTES = {"p", "div", "aside", "section", "li", "dd", "td", "th", "blockquote", "span", "body"}


def is_note(element: etree._Element) -> bool:
    return etree.QName(element).namespace == XHTML and bool(
        tokens(element, f"{{{EPUB}}}type") & NOTE_TYPES
        or "doc-footnote" in tokens(element, "role")
        or {c.lower() for c in tokens(element, "class")} & NOTE_CLASSES
    ) and local_name(element) in SAFE_NOTES


def locate_note(target: etree._Element, candidates: set[etree._Element]) -> etree._Element | None:
    chain = [target, *target.iterancestors()]
    for element in chain:
        if element in candidates:
            return element
    for element in chain:
        if local_name(element) in SAFE_NOTES - {"span", "body"} and etree.QName(element).namespace == XHTML:
            return element
    return None


def location(doc: Document, element: etree._Element) -> dict:
    return {"path": doc.path, "element": doc.ordinals[element], "line": element.sourceline,
            "id": element.get("id"), "tag": local_name(element)}


def normalize_options(options: dict | None = None) -> dict:
    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - {"all_links", "ref_xpath", "note_xpath", "label_template", "label_lang"}:
        raise EpubError("Invalid scan options")
    all_links = options.get("all_links", False)
    if type(all_links) is not bool:
        raise EpubError("all_links must be a boolean")
    result = {"all_links": all_links}
    template = options.get("label_template", DEFAULT_LABEL_TEMPLATE)
    if not isinstance(template, str) or not 1 <= len(template) <= 256 or any(ord(c) < 32 for c in template):
        raise EpubError("Return label template must be printable text of 1–256 characters")
    try:
        fields = set()
        for literal, field, spec, conversion in Formatter().parse(template):
            if field is not None:
                if field not in {"number", "chapter", "label"} or spec or conversion:
                    raise EpubError("Return label fields are {number}, {chapter}, {label}; format specs/conversions are unsupported")
                fields.add(field)
        if "number" not in fields:
            raise EpubError("Return label template must include {number} for distinct reading destinations")
    except EpubError:
        raise
    except ValueError as exc:
        raise EpubError(f"Invalid return label template: {exc}") from exc
    language = options.get("label_lang")
    if language is not None and (not isinstance(language, str) or len(language) > 63 or not re.fullmatch(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*", language)):
        raise EpubError("Return label language must be a language tag such as en or zh-Hans")
    result.update({"label_template": template, "label_lang": language})
    for field in ("ref_xpath", "note_xpath"):
        value = options.get(field, [])
        if not isinstance(value, list) or len(value) > 16 or any(
            not isinstance(s, str) or not s or len(s) > 2048 for s in value
        ):
            raise EpubError(f"{field} must be a list of up to 16 XPath strings")
        result[field] = value
    return result


def selected(doc: Document, expressions: list[str], kind: str) -> set[etree._Element]:
    result: set[etree._Element] = set()
    for expression in expressions:
        try:
            matches = doc.root.xpath(expression, namespaces=NS)
        except etree.XPathError as exc:
            raise EpubError(f"Invalid {kind} XPath: {exc}") from exc
        if not isinstance(matches, list) or any(
            not isinstance(m, etree._Element) or not isinstance(m.tag, str) for m in matches
        ):
            raise EpubError(f"{kind} XPath must select elements")
        for match in matches:
            if match not in doc.ordinals:
                raise EpubError(f"{kind} XPath selected a node outside the content document")
            if kind == "reference" and match.tag != f"{{{XHTML}}}a":
                raise EpubError("Reference XPath must select XHTML a elements; create forward links in an editor first")
            if kind == "note" and (local_name(match) not in SAFE_NOTES or etree.QName(match).namespace != XHTML):
                raise EpubError("Note XPath must select a supported XHTML note container")
            result.add(match)
    return result


@dataclass
class Audit:
    plan: dict
    references: dict[str, tuple[Document, etree._Element]]
    notes: dict[str, tuple[Document, etree._Element]]


def audit(book: Book, options: dict | None = None) -> Audit:
    options = normalize_options(options)
    warnings: list[dict] = []
    known: dict[str, set[etree._Element]] = {}
    forced_refs: dict[str, set[etree._Element]] = {}
    note_records: dict[str, dict] = {}
    note_elements: dict[str, tuple[Document, etree._Element]] = {}
    references: dict[str, tuple[Document, etree._Element]] = {}
    records: list[dict] = []
    return_indexes: dict[str, tuple[dict, list]] = {}
    excerpt_cache: dict[etree._Element, str] = {}

    def describe(element: etree._Element, limit: int = 240) -> str:
        if element not in excerpt_cache:
            excerpt_cache[element] = excerpt(element)
        return excerpt_cache[element][:limit]

    def register_note(doc: Document, element: etree._Element) -> str:
        key = doc.key(element)
        if key not in note_records:
            links = []
            for link in element.iter(f"{{{XHTML}}}a"):
                if link.get("href") is None:
                    continue
                target, destination = book.target(doc.path, link.get("href"))
                links.append({"location": location(doc, link), "href": link.get("href"),
                              "label": describe(link, 160), "language": inherited_language(link),
                              "target": {"path": target.path, "fragment": target.fragment, "error": target.error},
                              "destination": location(book.documents[target.path], destination) if destination is not None else None})
            note_records[key] = {"key": key, "location": location(doc, element), "excerpt": describe(element),
                                 "language": inherited_language(element), "links": links}
            note_elements[key] = (doc, element)
        return key

    def observed_returns(note_doc: Document, note: etree._Element, source_doc: Document, reference: etree._Element) -> list[dict]:
        key = note_doc.key(note)
        if key not in return_indexes:
            index: dict[tuple[str, etree._Element], list[dict]] = {}
            broken = []
            for order, backlink in enumerate(note.iter(f"{{{XHTML}}}a")):
                if backlink.get("href") is None:
                    continue
                target, destination = book.target(note_doc.path, backlink.get("href"))
                if destination is None:
                    if "doc-backlink" in tokens(backlink, "role"):
                        broken.append({"href": backlink.get("href"), "kind": "broken-backlink", "error": target.error, "order": order})
                    continue
                index.setdefault((target.path, destination), []).append({
                    "href": backlink.get("href"), "label": describe(backlink, 160),
                    "language": inherited_language(backlink),
                    "location": location(book.documents[target.path], destination), "backlink_location": location(note_doc, backlink), "order": order,
                })
            return_indexes[key] = index, broken
        index, broken = return_indexes[key]
        result = list(broken)
        for destination in [reference, *reference.iterancestors()]:
            kind = "exact" if destination is reference else "ancestor"
            result.extend({**item, "kind": kind} for item in index.get((source_doc.path, destination), []))
        return [{k: v for k, v in item.items() if k != "order"} for item in sorted(result, key=lambda i: i["order"])]

    for doc in book.documents.values():
        known[doc.path] = {e for e in doc.elements if is_note(e)} | selected(doc, options["note_xpath"], "note")
        forced_refs[doc.path] = selected(doc, options["ref_xpath"], "reference")
        for element in doc.elements:
            if element in known[doc.path]:
                register_note(doc, element)
            identity = element.get("id")
            if identity == "" or (identity is not None and len(doc.ids[identity]) > 1):
                warnings.append({"code": "invalid-or-duplicate-id", "path": doc.path,
                                 "line": element.sourceline, "id": identity})
            if local_name(element) == "sup" and "noteanchor" in tokens(element, "class") and not element.findall(f".//{{{XHTML}}}a"):
                warnings.append({"code": "unlinked-note-marker", "path": doc.path,
                                 "line": element.sourceline, "text": describe(element),
                                 "message": "Use Sigil FootnoteLinker or an editor to create the forward link first."})

    for doc in book.documents.values():
        for element in doc.elements:
            if element.tag != f"{{{XHTML}}}a":
                continue
            roles = tokens(element, "role")
            types = tokens(element, f"{{{EPUB}}}type")
            explicit = "doc-noteref" in roles or "noteref" in types
            forced = element in forced_refs[doc.path]
            if doc.path in book.nav_paths and not forced:
                continue
            within_note = any(e in known[doc.path] for e in element.iterancestors())
            if ("doc-backlink" in roles or "fnrt-backlink" in tokens(element, "class") or within_note) and not (explicit or forced):
                continue
            href = element.get("href", "")
            target, destination = book.target(doc.path, href)
            note = None
            note_error = None
            association = "fragment-target"
            note_doc = book.documents.get(target.path)
            if destination is not None and note_doc is not None:
                if target.fragment:
                    note = locate_note(destination, known[note_doc.path])
                else:
                    association = "whole-document-body"
                    if destination in known[note_doc.path]:
                        note = destination
                    else:
                        choices = [n for n in note_doc.elements if n in known[note_doc.path]
                                   and destination in n.iterancestors()
                                   and not any(a in known[note_doc.path] for a in n.iterancestors())]
                        if len(choices) > 1:
                            note_error = "ambiguous-document-note"
                        elif choices:
                            note = choices[0]
                            association = "whole-document-single-note"
                        else:
                            note = destination
            reason = []
            if explicit:
                reason.append("semantic-noteref")
            if forced:
                reason.append("author-xpath")
            if {c.lower() for c in tokens(element, "class")} & REF_CLASSES:
                reason.append("reference-class")
            if element.getparent() is not None and local_name(element.getparent()) == "sup":
                reason.append("superscript-link")
            if note is not None and note in known[note_doc.path] and (target.fragment or note is destination):
                reason.append("note-target")
            if options["all_links"] and not target.error:
                reason.append("all-internal-xhtml-links")
            if not reason:
                continue
            number = len(records) + 1
            key = doc.key(element)
            note_key = register_note(note_doc, note) if note is not None else None
            context = element.getparent() if element.getparent() is not None else element
            if local_name(context) in {"sup", "span"} and context.getparent() is not None:
                context = context.getparent()
            label = options["label_template"].format(number=number, chapter=doc.title[:96], label=describe(element, 48))
            if len(label) > 256:
                raise EpubError("Formatted return label exceeds 256 characters; shorten --label-template")
            record = {
                "key": key, "number": number, "location": location(doc, element),
                "chapter": doc.title, "label": describe(element, 100), "context": describe(context),
                "context_language": inherited_language(context),
                "href": href, "target": {"path": target.path, "fragment": target.fragment, "error": target.error},
                "reason": reason, "note_key": note_key,
                "association_basis": association,
                "approved": False, "action": "add_backlink", "returns": [],
                "backlink_label": label,
                "in_linear_spine": doc.path in book.spine_paths and doc.path not in book.nonlinear_paths,
            }
            if target.error:
                record["status"] = target.error
                record["action"] = "skip"
            elif note is None:
                record["status"] = note_error or "no-safe-note-container"
                record["action"] = "skip"
            elif destination is element or destination in element.iterancestors() or element is note or note in element.iterancestors():
                record["status"] = "self-reference"
                record["action"] = "skip"
            else:
                record["returns"] = observed_returns(note_doc, note, doc, element)
                if any(r["kind"] == "exact" for r in record["returns"]):
                    record["status"] = "exact-return"
                    record["action"] = "keep"
                elif any(r["kind"] == "ancestor" for r in record["returns"]):
                    record["status"] = "ancestor-return"
                else:
                    record["status"] = "missing-return"
            records.append(record)
            references[key] = (doc, element)

    notes = list(note_records.values())
    plan = {
        "schema_version": 1, "book_sha256": book.sha256, "book_title": book.title,
        "package_path": book.opf_path, "epub_version": book.version, "scan_options": options,
        "references": records, "notes": notes, "warnings": warnings,
        "scope": "Navigation review only. EPUBCheck is format validation; actual reading and accessibility require human testing.",
    }
    return Audit(plan, references, note_elements)
