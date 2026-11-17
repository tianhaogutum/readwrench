import random

import pytest

from readwrench.concordance.errors import InputError
from readwrench.concordance.positions import to_characters, to_utf8


def test_sparse_unicode_offsets_roundtrip_against_codec():
    randomizer = random.Random(511)
    alphabet = "自由倫理😀é\u0301a& \n𐐀"
    for _ in range(40):
        text = "".join(randomizer.choice(alphabet) for _ in range(200))
        positions = {0, len(text), *(randomizer.randrange(len(text)) for _ in range(10))}
        byte_positions = to_utf8(text, positions)
        assert byte_positions == {position: len(text[:position].encode("utf-8")) for position in positions}
        assert to_characters(text, byte_positions.values()) == {byte: char for char, byte in byte_positions.items()}


@pytest.mark.parametrize("positions", [[-1], [100], [True], [1.5]])
def test_invalid_sparse_offsets(positions):
    with pytest.raises(InputError):
        to_utf8("自由", positions)
    with pytest.raises(InputError):
        to_characters("自由", positions)


def test_utf8_interior_not_rounded_or_guessed():
    with pytest.raises(InputError, match="UTF-8"):
        to_characters("😀", [2])


def test_empty_requests_allocate_no_boundaries():
    assert to_utf8("long prose", []) == {}
    assert to_characters("long prose", []) == {}
