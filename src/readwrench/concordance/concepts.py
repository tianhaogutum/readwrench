"""Literal search candidates. A match is never a semantic classification."""

from __future__ import annotations

import re
from pathlib import Path

from .errors import InputError
from .jsonio import read_json


def load_concepts(path: Path) -> list[dict]:
    data = read_json(path, name="concepts JSON")
    if not isinstance(data, dict) or set(data) != {"concepts"}:
        raise InputError('Concepts JSON must be an object containing only "concepts"')
    concepts = data["concepts"]
    if not isinstance(concepts, list) or not 1 <= len(concepts) <= 100:
        raise InputError("Provide 1–100 concept queries")
    seen = set()
    result = []
    for concept in concepts:
        if not isinstance(concept, dict) or set(concept) - {"id", "label", "aliases", "boundary", "case_sensitive"}:
            raise InputError("Invalid concept query fields")
        ident = concept.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", ident) or ident in seen:
            raise InputError("Concept IDs must be unique ASCII letters/digits/underscore/hyphen (1–64)")
        seen.add(ident)
        label = concept.get("label", ident)
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise InputError("Concept labels must contain 1–200 characters")
        aliases = concept.get("aliases")
        if not isinstance(aliases, list) or not 1 <= len(aliases) <= 100:
            raise InputError(f"Concept {ident}: provide 1–100 literal aliases")
        if any(not isinstance(a, str) or not a.strip() or len(a) > 200 for a in aliases):
            raise InputError(f"Concept {ident}: aliases must contain 1–200 characters")
        if any("\x00" in alias for alias in aliases) or "\x00" in label:
            raise InputError("Concept labels and aliases cannot contain NUL characters")
        boundary = concept.get("boundary", "word")
        case = concept.get("case_sensitive", False)
        if not isinstance(boundary, str) or boundary not in {"word", "substring"} or not isinstance(case, bool):
            raise InputError("boundary must be word/substring; case_sensitive must be boolean")
        result.append({"id": ident, "label": label, "aliases": list(dict.fromkeys(" ".join(a.split()) for a in aliases)),
                       "boundary": boundary, "case_sensitive": case})
    return result


def compile_query(concept: dict) -> re.Pattern:
    # Escape all aliases: users cannot inject a catastrophic regular expression.
    alternatives = "|".join(re.escape(a) for a in sorted(concept["aliases"], key=len, reverse=True))
    pattern = f"(?:{alternatives})"
    if concept["boundary"] == "word":
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    return re.compile(pattern, 0 if concept["case_sensitive"] else re.IGNORECASE)


def candidates(text: str, concepts: list[dict]):
    for concept in concepts:
        for match in compile_query(concept).finditer(text):
            yield concept["id"], match.start(), match.end()
