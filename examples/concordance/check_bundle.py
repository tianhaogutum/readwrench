"""Independently verify a local export's quotations, source hashes and links.

Run from a clone after `readwrench concordance export ... --out EXPORT`:

    python examples/concordance/check_bundle.py EXPORT

Needs jsonschema and rfc3986-validator (for URI format checks). The Readium
Locator schema defaults to the copy under tests/concordance/fixtures/schema/.
"""

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from jsonschema import Draft7Validator, FormatChecker


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            assert attrs["id"] not in self.ids, "Duplicate HTML anchor"
            self.ids.add(attrs["id"])
        if "href" in attrs:
            self.links.append(attrs["href"])
        assert tag not in {"script", "iframe"}, "Unexpected active content"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--schema", type=Path, default=Path(__file__).resolve().parents[2]
                        / "tests/concordance/fixtures/schema/readium-locator.schema.json",
                        help="Readium Locator JSON Schema (strict-JSON copy)")
    args = parser.parse_args()
    bundle = args.bundle.resolve()

    def load(name):
        return json.loads((bundle / name).read_text(encoding="utf-8"))

    records = load("occurrences.json")
    hits = records["occurrences"]
    sources = {source["id"]: source for source in load("sources.json")}
    texts = {}
    for ident, source in sources.items():
        path = (bundle / source["snapshot"]).resolve()
        assert path.is_relative_to(bundle)
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == source["text_sha256"]
        texts[ident] = data.decode("utf-8")
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    checker = FormatChecker()
    if "uri-reference" not in checker.checkers:
        parser.error("Install rfc3986-validator to enable URI format validation")
    validator = Draft7Validator(schema, format_checker=checker)
    annotations = load("annotations.jsonld")["items"]
    assert len(annotations) == len(hits)
    assert len({hit["id"] for hit in hits}) == len(hits)
    by_id = {hit["id"]: hit for hit in hits}
    for hit, annotation in zip(hits, annotations):
        text = texts[hit["canonical_source"]]
        assert text[hit["start"]:hit["end"]] == hit["exact"]
        assert text[:hit["start"]].endswith(hit["before"])
        assert text[hit["end"]:].startswith(hit["after"])
        assert annotation["target"]["source"]["id"] == hit["canonical_source"]
        position, quote = annotation["target"]["selector"]
        assert (position["start"], position["end"], quote["exact"]) == (hit["start"], hit["end"], hit["exact"])
        validator.validate(hit["readium_locator"])
    for row in load("matrix.json")["rows"]:
        for cell in row["cells"]:
            assert cell["count"] == len(cell["occurrences"])
            for item in cell["occurrences"]:
                hit = by_id[item["id"]]
                assert hit["book_id"] == cell["book_id"] and hit["concept_id"] == row["concept_id"]
                assert hit["status"] == row["status"]
                if row["status"] == "classified":
                    assert row["sense"] in hit["senses"]
    pages = {}
    for path in bundle.rglob("*.html"):
        parsed = Links()
        parsed.feed(path.read_text(encoding="utf-8"))
        pages[path.resolve()] = parsed
    count = 0
    for path, page in pages.items():
        for href in page.links:
            uri = urlsplit(href)
            assert not uri.scheme and not uri.netloc, "Unexpected external page dependency"
            target = (path.parent / unquote(uri.path)).resolve() if uri.path else path
            assert target.is_relative_to(bundle) and target.is_file(), f"Broken link: {href}"
            if uri.fragment:
                assert uri.fragment in pages[target].ids, f"Missing source anchor: {href}"
            count += 1
    print(json.dumps({"valid": True, "books": len(records["books"]), "occurrences": len(hits),
                      "source_snapshots": len(sources), "html_pages": len(pages), "checked_links": count}, indent=2))


if __name__ == "__main__":
    main()
