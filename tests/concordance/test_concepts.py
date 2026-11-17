import pytest

from readwrench.concordance.errors import InputError
from readwrench.concordance.concepts import candidates, load_concepts


def test_literal_longest_alias_case_and_boundaries(concepts_file):
    concepts = load_concepts(concepts_file([{"id": "a", "aliases": ["law", "law and order", "a+b"]}]))
    text = "Law and order is lawful. a+b aab LAW"
    assert [text[start:end] for _, start, end in candidates(text, concepts)] == ["Law and order", "a+b", "LAW"]


def test_cjk_substring_and_inline_search(concepts_file):
    concepts = load_concepts(concepts_file([{"id": "freedom", "aliases": ["自由"], "boundary": "substring"}]))
    assert list(candidates("论自由思想与自由", concepts)) == [("freedom", 1, 3), ("freedom", 6, 8)]


@pytest.mark.parametrize("concept", [
    {"id": "a", "aliases": []}, {"id": "a", "aliases": [""]},
    {"id": "a", "aliases": ["   "]}, {"id": "../x", "aliases": ["x"]},
    {"id": "a", "aliases": ["x"], "boundary": "regex"},
    {"id": "a", "aliases": ["x"], "boundary": ["word"]},
    {"id": "a", "aliases": ["x"], "case_sensitive": "false"},
    {"id": "a", "aliases": ["x"], "aliasses": ["typo"]},
])
def test_invalid_queries_fail(concepts_file, concept):
    with pytest.raises(InputError):
        load_concepts(concepts_file([concept]))


def test_duplicate_query_ids(concepts_file):
    with pytest.raises(InputError, match="unique"):
        load_concepts(concepts_file([{"id": "a", "aliases": ["x"]}, {"id": "a", "aliases": ["y"]}]))


def test_case_sensitive_and_whitespace_alias(concepts_file):
    concepts = load_concepts(concepts_file([{"id": "a", "aliases": ["Power", "civil\n  society"], "case_sensitive": True}]))
    assert [(a, b) for _, a, b in candidates("power Power civil society", concepts)] == [(6, 11), (12, 25)]
