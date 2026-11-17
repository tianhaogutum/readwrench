"""Validate persisted state with jsonschema before trusting cross references."""

from urllib.parse import quote, unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from .books import InputError, safe_resource, stable_id


def obj(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def array(items, *, maximum=5000, minimum=0):
    return {"type": "array", "items": items, "maxItems": maximum, "minItems": minimum}


TEXT = {"type": "string", "maxLength": 8192}
NONEMPTY = dict(TEXT, minLength=1)
ID = {"type": "string", "pattern": "^[a-f0-9]{24}$"}
SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
POSITION = {"type": "integer", "minimum": 0}
CONCEPT = obj({
    "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
    "label": {"type": "string", "minLength": 1, "maxLength": 200},
    "aliases": array({"type": "string", "minLength": 1, "maxLength": 200}, maximum=100, minimum=1),
    "boundary": {"enum": ["word", "substring"]}, "case_sensitive": {"type": "boolean"},
})
BOOK = obj({
    "id": ID, "sha256": SHA, "path": NONEMPTY, "title": NONEMPTY,
    "authors": array(TEXT, maximum=1000), "identifiers": array(TEXT, maximum=1000),
    "language": array(TEXT, maximum=1000), "edition": array(TEXT, maximum=1000), "epub_version": NONEMPTY,
})
CHAPTER = obj({
    "id": ID, "book_id": ID, "href": NONEMPTY, "archive_path": NONEMPTY, "title": NONEMPTY,
    "spine_index": {"type": "integer", "minimum": 1}, "media_type": NONEMPTY,
    "headings": array(obj({"start": POSITION, "title": NONEMPTY}), maximum=100000),
    "anchors": array(obj({"start": POSITION, "fragment": NONEMPTY}), maximum=100000),
    "direction": {"enum": ["LEFT_TO_RIGHT", "RIGHT_TO_LEFT"]}, "text_sha256": SHA,
    "text_length": {"type": "integer", "minimum": 1, "maximum": 8 * 1024 * 1024},
    "text_file": {"type": "string", "pattern": r"^text/[a-f0-9]{24}\.txt$"},
})
WORKSPACE_SCHEMA = dict(obj({
    "schema_version": {"const": 1}, "canonical_text_version": {"const": 1},
    "workspace_id": {"type": "string", "format": "uuid"},
    "name": {"type": "string", "minLength": 1, "maxLength": 50},
    "concepts": array(CONCEPT, maximum=100, minimum=1),
    "books": array(BOOK, maximum=5, minimum=1),
    "chapters": array(CHAPTER, maximum=5000, minimum=1),
    "candidate_count": {"type": "integer", "minimum": 0, "maximum": 50000},
    "include_nonlinear": {"type": "boolean"},
}), **{"$schema": "https://json-schema.org/draft/2020-12/schema"})
VALIDATOR = Draft202012Validator(WORKSPACE_SCHEMA, format_checker=FormatChecker())


def validate_manifest(manifest) -> None:
    error = next(VALIDATOR.iter_errors(manifest), None)
    if error:
        location = "/".join(map(str, error.absolute_path)) or "root"
        raise InputError(f"Invalid workspace manifest at {location}: {error.message[:240]}")
    for field in ["books", "chapters", "concepts"]:
        ids = [entry["id"] for entry in manifest[field]]
        if len(set(ids)) != len(ids):
            raise InputError(f"Duplicate {field} identities in workspace manifest")
    books = {book["id"]: book for book in manifest["books"]}
    for book in books.values():
        if book["sha256"][:24] != book["id"]:
            raise InputError("Book identity does not match its edition digest")
    for chapter in manifest["chapters"]:
        if chapter["book_id"] not in books:
            raise InputError("Chapter refers to an unknown book identity")
        book = books[chapter["book_id"]]
        if stable_id(book["sha256"], chapter["href"]) != chapter["id"]:
            raise InputError("Chapter identity does not match its edition/resource")
        uri = urlsplit(chapter["href"])
        if uri.scheme or uri.netloc or uri.query or uri.fragment or quote(unquote(chapter["href"]), safe="/") != chapter["href"]:
            raise InputError("Invalid canonical EPUB resource URI")
        safe_resource(chapter["archive_path"])
        for field in ["headings", "anchors"]:
            positions = [entry["start"] for entry in chapter[field]]
            if positions != sorted(positions) or any(pos > chapter["text_length"] for pos in positions):
                raise InputError(f"Invalid {field} text positions")
