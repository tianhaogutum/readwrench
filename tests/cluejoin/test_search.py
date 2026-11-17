import itertools
import random
from pathlib import Path

import pytest

from readwrench.cluejoin.model import CluejoinError, Fragment, normalize, normalize_mapped
from readwrench.cluejoin.search import _cover_score, fragment_hits, search, select_cover, validate_groups

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "clues"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fragment(text, *, document="book", page=None, paragraph=None, key="p1"):
    return Fragment(document, f"{document}.pdf", key, text, page, paragraph)


def test_original_id_join_and_closest_pages(indexed):
    connection = indexed(inputs=[EXAMPLES / "fragments.jsonl"])
    result = search(connection, [["amber lantern", "bronze lantern"], ["silver compass"]])
    by_id = {doc["document_id"]: doc for doc in result["documents"]}
    assert set(by_id) == {"field-notes", "catalogue"}
    assert by_id["field-notes"]["page_span"] == 1
    assert [item["page"] for item in by_id["field-notes"]["evidence"]] == [18, 19]
    assert all(item["paragraph_span"] is None for item in by_id.values())


def test_chinese_short_clues_are_not_lost_by_trigram(indexed):
    connection = indexed(inputs=[EXAMPLES / "fragments.jsonl"])
    result = search(connection, [["记忆"], ["原文"]])
    assert result["matched_documents"] == 1
    doc = result["documents"][0]
    assert doc["document_id"] == "中文笔记"
    assert doc["distance"] == 6
    assert [item["match"] for item in doc["evidence"]] == ["记忆", "原文"]


@pytest.mark.parametrize(
    ("text", "groups", "expected"),
    [
        ("Straße then final", [["STRASSE"], ["FINAL"]], ["Straße", "final"]),
        ("A\t wrapped\r\n phrase then end", [["a wrapped phrase"], ["end"]],
         ["A\t wrapped\r\n phrase", "end"]),
        ('literal OR "x"* then [a-z]', [['OR "x"*'], ["[a-z]"]], ['OR "x"*', "[a-z]"]),
        ("👩🏽‍💻 plus 𠮷野 and done", [["👩🏽‍💻"], ["𠮷野"]], ["👩🏽‍💻", "𠮷野"]),
        ("İstanbul / ΔΕΛΤΑ", [["i̇stanbul"], ["δελτα"]], ["İstanbul", "ΔΕΛΤΑ"]),
        ("banana final", [["ana"], ["final"]], ["ana", "final"]),
    ],
)
def test_literal_matching_and_original_offsets(indexed, text, groups, expected):
    result = search(indexed([fragment(text, paragraph=1)]), groups, context=3)
    evidence = result["documents"][0]["evidence"]
    assert [item["match"] for item in evidence] == expected
    for item in evidence:
        assert text[item["match_start"]:item["match_end"]] == item["match"]
        assert item["context"][item["highlight_start"]:item["highlight_end"]] == item["match"]


def test_casefold_half_character_does_not_establish_a_clue(indexed):
    result = search(indexed([fragment("ß end", paragraph=1)]), [["s"], ["end"]])
    assert result["matched_documents"] == 0


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_line_locations_point_to_match_not_paragraph_start(indexed, newline):
    text = newline.join(["heading", "alpha clue", "middle", "omega clue"])
    material = Fragment("book", "book.txt", "p1", text, paragraph=1, line=40)
    doc = search(indexed([material]), [["alpha clue"], ["omega clue"]])["documents"][0]
    assert [item["line"] for item in doc["evidence"]] == [41, 43]
    assert [item["fragment_line"] for item in doc["evidence"]] == [40, 40]
    assert doc["line_span"] == 2


def test_clue_spanning_newline_reports_start_line(indexed):
    material = Fragment("book", "book.txt", "p1", "heading\nalpha\nbeta\nend",
                        paragraph=1, line=40)
    doc = search(indexed([material]), [["alpha beta"], ["end"]])["documents"][0]
    assert doc["evidence"][0]["line"] == 41
    assert doc["evidence"][0]["match"] == "alpha\nbeta"
    assert doc["evidence"][0]["line_end"] == 42


def test_line_cover_includes_full_multiline_quote_not_only_start(indexed):
    # The longer alternative begins at the same line as clue 2, but ends much
    # farther away. The shorter alternative gives the genuinely compact cover.
    material = Fragment("book", "book.txt", "p1", "alpha\nbeta\n\n\nomega", line=1)
    doc = search(indexed([material]), [["alpha beta omega", "beta"], ["alpha"]])["documents"][0]
    assert doc["distance_unit"] == "line"
    assert doc["line_span"] == 1
    assert doc["evidence"][0]["match"] == "beta"
    assert doc["evidence"][0]["line_end"] == 2


def test_same_multiline_quote_has_nonzero_line_extent(indexed):
    material = Fragment("book", "book.txt", "p1", "alpha\n\n\nomega", line=10)
    doc = search(indexed([material]), [["alpha omega"], ["alpha omega"]])["documents"][0]
    assert doc["line_span"] == 3
    assert doc["evidence"][0]["line"] == 10
    assert doc["evidence"][0]["line_end"] == 13


def test_terminal_newline_does_not_occupy_the_following_fragment_line(indexed):
    materials = [Fragment("book", "book.txt", "p1", "alpha\n", line=1),
                 Fragment("book", "book.txt", "p2", "omega", line=2)]
    doc = search(indexed(materials), [["alpha"], ["omega"]])["documents"][0]
    assert doc["line_span"] == 1


def test_three_groups_allow_one_original_occurrence(indexed):
    result = search(indexed([fragment("alpha beta", paragraph=1)]),
                    [["alpha"], ["alpha"], ["beta"]])
    assert result["documents"][0]["paragraph_span"] == 0
    assert len(result["documents"][0]["evidence"]) == 3


def test_missing_one_of_three_groups_excludes_material(indexed):
    result = search(indexed([fragment("alpha beta", paragraph=1)]),
                    [["alpha"], ["beta"], ["gamma"]])
    assert result["matched_documents"] == 0


def test_paragraph_distance_uses_original_coordinates_not_hit_count(indexed):
    connection = indexed([
        fragment("alpha", paragraph=2),
        fragment("unrelated", paragraph=5, key="p2"),
        fragment("omega", paragraph=19, key="p3"),
    ])
    doc = search(connection, [["alpha"], ["omega"]])["documents"][0]
    assert doc["distance_unit"] == "paragraph"
    assert doc["paragraph_span"] == 17
    assert doc["page_span"] is None


def test_result_limit_counts_all_materials_and_keeps_nearest(indexed):
    inputs = []
    for number in range(25):
        inputs.extend([
            fragment("alpha", document=str(number), page=1),
            fragment("omega", document=str(number), page=number + 2, key="p2"),
        ])
    result = search(indexed(inputs), [["alpha"], ["omega"]], limit=3)
    assert result["matched_documents"] == 25
    assert result["returned_documents"] == 3
    assert [doc["distance"] for doc in result["documents"]] == [1, 2, 3]


def test_candidates_use_one_sql_statement_for_many_documents(indexed):
    connection = indexed([
        fragment("alpha omega", document=str(number), page=1) for number in range(40)
    ])
    statements = []
    connection.set_trace_callback(statements.append)
    try:
        result = search(connection, [["alpha"], ["omega"]], limit=2)
    finally:
        connection.set_trace_callback(None)
    assert result["matched_documents"] == 40
    # FTS5 issues its own shadow-table statements; count application candidate queries.
    candidate_queries = [
        item for item in statements
        if not item.startswith("--") and "FROM FRAGMENTS" in item.upper()
    ]
    assert len(candidate_queries) == 1  # Guards the demonstrated N+1 FTS regression.


@pytest.mark.parametrize("groups", [[], [["a"]], [["a"]] * 4, [[], ["b"]],
                                   [["  "], ["b"]], [[True], ["b"]]])
def test_reject_invalid_queries(groups):
    with pytest.raises(CluejoinError):
        validate_groups(groups)


def test_normalization_maps_equal_fast_path():
    rng = random.Random(24)
    alphabet = "AbcßİςΣ\n\r\t\u2003\u00a0零散𠮷👩\u0301"
    for _ in range(1000):
        text = "".join(rng.choices(alphabet, k=rng.randrange(100)))
        norm, starts, ends = normalize_mapped(text)
        assert norm == normalize(text)
        assert len(norm) == len(starts) == len(ends)
        for start, end in zip(starts, ends, strict=True):
            assert 0 <= start < end <= len(text)


def test_sweep_against_cartesian_oracle(indexed):
    """Independently enumerate all covers on small, overlapping real strings."""
    rng = random.Random(968)
    groups = [["a", "abc"], ["b", "bc"], ["c", "abca"]]
    for case in range(120):
        fragments = [
            fragment("".join(rng.choices("abc xyz", k=rng.randrange(4, 25))),
                     paragraph=number + 1, key=str(number))
            for number in range(rng.randrange(1, 5))
        ]
        connection = indexed(fragments)
        rows = list(connection.execute("SELECT * FROM fragments ORDER BY paragraph"))
        buckets = [[] for _ in groups]
        for order, row in enumerate(rows):
            for hit in fragment_hits(row, order, groups):
                buckets[hit.group].append(hit)
        expected = min((_cover_score(cover, "paragraph")
                        for cover in itertools.product(*buckets)), default=None)
        actual = select_cover(iter(rows), groups, "paragraph")
        assert (actual[1] if actual else None) == expected, f"random case {case}"


@pytest.mark.parametrize("filename", ["rfc2616.txt", "rfc2616-layout-crlf.txt"])
def test_real_rfc_pdf_extraction(indexed, filename):
    connection = indexed(inputs=[FIXTURES / filename], format="text")
    doc = search(connection, [["a cache behaves"], ["non-idempotent sequences"]])["documents"][0]
    assert doc["source"].endswith(filename)
    assert doc["page_span"] == abs(doc["evidence"][0]["page"] - doc["evidence"][1]["page"])
    assert [item["page"] for item in doc["evidence"]] == [10, 30]
    assert doc["page_span"] == 20
    assert doc["paragraph_span"] is not None


def test_real_rga_output_has_identical_best_page_span(indexed):
    groups = [["idempotent"], ["semantically"]]
    text_index = indexed(inputs=[FIXTURES / "rfc2616.txt"], format="text")
    rga_index = indexed(inputs=[FIXTURES / "rga-rfc2616.jsonl"], format="rga-json")
    text_doc = search(text_index, groups)["documents"][0]
    rga_doc = search(rga_index, groups)["documents"][0]
    assert rga_doc["page_span"] == text_doc["page_span"]
    assert rga_doc["source"] == "fixtures/upstream/rfc2616.pdf"
    assert rga_doc["paragraph_span"] is None


def test_real_rga_context_events_keep_page_and_extracted_line(indexed):
    connection = indexed(inputs=[FIXTURES / "rga-rfc2616-context.jsonl"], format="rga-json")
    doc = search(connection, [["a cache behaves"], ["non-idempotent sequences"]])["documents"][0]
    assert [item["page"] for item in doc["evidence"]] == [10, 30]
    assert [item["line"] for item in doc["evidence"]] == [827, 2285]
    assert doc["page_span"] == 20
