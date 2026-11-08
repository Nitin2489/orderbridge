"""Unit tests for the pure text helpers of message capture (no database)."""

from decimal import Decimal
from itertools import pairwise

import pytest

from app.models.enums import UnitHint
from app.services.capture_text import (
    CASE_WORDS,
    GREETING_WORDS,
    NUMBER_WORDS,
    PIECE_WORDS,
    SIZE_MARKERS,
    SIZE_UNITS,
    is_greeting,
    is_number,
    normalize_phrase,
    normalize_word,
    number_value,
    size_unit,
    split_segments,
    tokenize,
    unit_hint,
)

VOCABULARIES = {
    "NUMBER_WORDS": set(NUMBER_WORDS),
    "CASE_WORDS": set(CASE_WORDS),
    "PIECE_WORDS": set(PIECE_WORDS),
    "SIZE_UNITS": set(SIZE_UNITS),
    "SIZE_MARKERS": set(SIZE_MARKERS),
    "GREETING_WORDS": set(GREETING_WORDS),
}


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------


def test_vocabularies_do_not_overlap():
    names = list(VOCABULARIES)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            shared = VOCABULARIES[first] & VOCABULARIES[second]
            assert not shared, f"{first} and {second} share {sorted(shared)}"


@pytest.mark.parametrize("name", list(VOCABULARIES))
def test_every_entry_is_one_normalised_token(name):
    for entry in VOCABULARIES[name]:
        (token,) = tokenize(entry)
        assert token.text == entry
        assert token.norm == entry, f"{entry!r} is not in normalised form"


def test_do_is_a_number_word_not_a_greeting():
    # "do" means two, and also "give": it is never stripped as a greeting.
    assert NUMBER_WORDS["do"] == 2
    assert "do" not in GREETING_WORDS


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_tokens_keep_exact_offsets():
    message = "Bhaiya 5peti, १० पेटी 1.5kg"

    tokens = tokenize(message)

    assert [(t.kind, t.text) for t in tokens] == [
        ("word", "Bhaiya"),
        ("number", "5"),
        ("word", "peti"),
        ("number", "१०"),
        ("word", "पेटी"),
        ("number", "1.5"),
        ("word", "kg"),
    ]
    for token in tokens:
        assert message[token.start : token.end] == token.text
    assert tokens[0].norm == "bhaiya"
    assert tokens[3].norm == "10"


def test_tokenize_within_bounds_uses_absolute_offsets():
    message = "aaa 5 peti bbb"

    tokens = tokenize(message, 4, 10)

    assert [(t.text, t.start, t.end) for t in tokens] == [("5", 4, 5), ("peti", 6, 10)]


def test_devanagari_words_with_vowel_signs_are_single_tokens():
    message = "चाय पत्ती ग्राम छः"

    assert [t.text for t in tokenize(message)] == ["चाय", "पत्ती", "ग्राम", "छः"]


def test_normalize_word():
    assert normalize_word("PETI") == "peti"
    assert normalize_word("२५०") == "250"


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------


def segments(message: str) -> list[str]:
    return [message[start:end] for start, end in split_segments(message)]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("a, b; c", ["a", " b", " c"]),
        ("a\nb\r\nc", ["a", "b", "c"]),
        ("a | b", ["a ", " b"]),
        ("चाय । बिस्कुट", ["चाय ", " बिस्कुट"]),
        ("a aur b", ["a ", " b"]),
        ("a AND b", ["a ", " b"]),
        ("चाय और बिस्कुट", ["चाय ", " बिस्कुट"]),
        ("tea. biscuit", ["tea", " biscuit"]),
        # A decimal point is not a separator.
        ("1.5 kilo", ["1.5 kilo"]),
        # Words that merely contain "aur" or "and" are not separators.
        ("gaurav sandwich", ["gaurav sandwich"]),
    ],
)
def test_split_segments(message, expected):
    assert segments(message) == expected


def test_segments_and_separators_cover_the_whole_message():
    message = "bhaiya 5 peti, 2 peti; aur 1 peti।"
    spans = split_segments(message)

    # Segments are in order, do not overlap and stay inside the message.
    assert spans[0][0] == 0
    assert spans[-1][1] == len(message)
    for (_, end), (next_start, _) in pairwise(spans):
        assert end <= next_start


# ---------------------------------------------------------------------------
# Word classes
# ---------------------------------------------------------------------------


def token(text: str):
    (only,) = tokenize(text)
    return only


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("5", Decimal(5)),
        ("०५", Decimal(5)),
        ("250", Decimal(250)),
        ("1.5", Decimal("1.5")),
        ("do", Decimal(2)),
        ("दो", Decimal(2)),
        ("paanch", Decimal(5)),
        ("पाँच", Decimal(5)),
        ("पांच", Decimal(5)),
        ("das", Decimal(10)),
        ("sau", Decimal(100)),
    ],
)
def test_number_values(text, value):
    assert is_number(token(text))
    assert number_value(token(text)) == value


@pytest.mark.parametrize("text", ["biscuit", "chai", "peti", "tin"])
def test_ordinary_words_are_not_numbers(text):
    assert not is_number(token(text))
    assert number_value(token(text)) is None


@pytest.mark.parametrize(
    ("text", "hint"),
    [
        ("peti", UnitHint.CASE_WORD),
        ("PETI", UnitHint.CASE_WORD),
        ("पेटी", UnitHint.CASE_WORD),
        ("carton", UnitHint.CASE_WORD),
        ("packet", UnitHint.PIECE_WORD),
        ("pcs", UnitHint.PIECE_WORD),
        ("पैकेट", UnitHint.PIECE_WORD),
        ("biscuit", None),
        ("kilo", None),
    ],
)
def test_unit_hints(text, hint):
    assert unit_hint(token(text)) == hint


@pytest.mark.parametrize(
    ("text", "unit"),
    [
        ("g", "g"),
        ("gm", "g"),
        ("ग्राम", "g"),
        ("kilo", "kg"),
        ("KG", "kg"),
        ("किलो", "kg"),
        ("ml", "ml"),
        ("litre", "l"),
        ("peti", None),
    ],
)
def test_size_units(text, unit):
    assert size_unit(token(text)) == unit


def test_greetings():
    assert is_greeting(token("Bhaiya"))
    assert is_greeting(token("भैया"))
    assert not is_greeting(token("biscuit"))


# ---------------------------------------------------------------------------
# Phrases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("biscuit chhota", "biscuit chhota"),
        ("  Biscuit   CHHOTA! ", "biscuit chhota"),
        ("chai patti 250 wala", "chai patti 250 wala"),
        ("chai-patti २५० wala", "chai patti 250 wala"),
        ("चाय पत्ती", "चाय पत्ती"),
        ("!!!", ""),
    ],
)
def test_normalize_phrase(text, expected):
    assert normalize_phrase(text) == expected


def test_normalize_phrase_does_not_change_its_input():
    original = "  Biscuit   CHHOTA! "
    copy = str(original)

    normalize_phrase(original)

    assert original == copy
