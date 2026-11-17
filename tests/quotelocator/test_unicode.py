from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import hashlib
from pathlib import Path

import pytest
import regex

pytest.importorskip("hypothesis")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from reanchor import normalize  # noqa: E402

from readwrench.quotelocator import Document, load_document, locate  # noqa: E402
from readwrench.quotelocator.documents import Unit  # noqa: E402
from readwrench.quotelocator.matching import PROFILES, differences  # noqa: E402


@pytest.mark.parametrize(
    "text,quote,profile,status,found",
    [
        ("Cafe\u0301", "Cafe", "typographic", "unmatched", None),
        ("Cafe\u0301", "Café", "typographic", "unique", "Cafe\u0301"),
        ("Cafe\u0301", "Cafe", "loose", "unique", "Cafe\u0301"),
        ("Cafe\u0301", "Cafe", "strict", "unmatched", None),
        ("👩\u200d🔬", "👩", "typographic", "unmatched", None),
        ("👩\u200d🔬", "👩\u200d🔬", "typographic", "unique", "👩\u200d🔬"),
        ("🇩🇪", "🇩", "strict", "unmatched", None),
        ("研究者应当核对原始文献。", "核对原始文献", "typographic", "unique", "核对原始文献"),
        ("before\u2028after", "before after", "typographic", "unique", "before\u2028after"),
        ("a\r\nb", "a\r\nb", "strict", "unique", "a\r\nb"),
    ],
)
def test_unicode_boundaries_and_normalization(tmp_path, text, quote, profile, status, found):
    path = tmp_path / "unicode.txt"
    path.write_bytes(text.encode("utf-8"))
    review = locate(load_document(path), quote, profile=profile)
    assert review["status"] == status
    if found is not None:
        assert review["candidates"][0]["text"] == found


@settings(max_examples=150, deadline=500)
@given(
    st.text(alphabet="abc éﬁ\n\t\u0301中👩\u200d🔬", min_size=1, max_size=60),
    st.sampled_from(["typographic", "loose"]),
)
def test_no_candidate_can_invent_text_or_split_a_grapheme(text, profile):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    doc = Document(
        Path(__file__).resolve(),
        digest,
        digest,
        "text/plain",
        text,
        (Unit("test", "", 0, len(text), 1),),
        {},
    )
    options = PROFILES[profile]
    quote = text
    expected = " ".join(normalize(quote, options).text.split())
    if not quote.strip() or not expected:
        return
    review = locate(doc, quote, profile=profile)
    assert review["candidate_count"] > 0
    boundaries = {0, *(m.end() for m in regex.finditer(r"\X", text))}
    for candidate in review["candidates"]:
        assert candidate["text"] == text[candidate["start"] : candidate["end"]]
        assert " ".join(normalize(candidate["text"], options).text.split()) == expected
        assert candidate["start"] in boundaries and candidate["end"] in boundaries


def test_long_repetitive_diff_is_complete():
    quote, source = "a " * 2500, "a  " * 2500
    changes = differences(quote, source)
    # The edits must reconstruct the actual source, including repeated whitespace.
    rebuilt = quote
    for change in reversed(changes):
        rebuilt = (
            rebuilt[: change["query_start"]] + change["source"] + rebuilt[change["query_end"] :]
        )
    assert rebuilt == source
