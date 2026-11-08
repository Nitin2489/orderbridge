"""Unit tests for the rule-based message interpreter (no database).

The golden test pins the client's demonstration message exactly: every
line, product phrase, quantity, unit and pack size, with offsets into the
unmodified message. The interpreter only proposes; nothing here confirms a
unit or chooses a product.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.capture import MAX_CAPTURE_LINES, MAX_CAPTURE_MESSAGE_LENGTH
from app.models.enums import UnitHint
from app.services.capture_interpreter import (
    RULES_INTERPRETER_NAME,
    ExtractedLine,
    Extraction,
    InterpreterKind,
    InvalidMessageError,
    MessageInterpreter,
    RulesInterpreter,
    TextSpan,
    TooManyLinesError,
    extract_lines,
)

DEMO = (
    "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, "
    "chai patti 250 wala 1 peti"
)


def spans_of(extraction: Extraction) -> list[TextSpan]:
    spans: list[TextSpan] = list(extraction.ignored)
    for line in extraction.lines:
        spans += [line.span, *line.sizes]
        spans += [s for s in (line.product, line.quantity, line.unit) if s]
    return spans


def assert_exact_substrings(extraction: Extraction, message: str) -> None:
    assert extraction.message == message
    for span in spans_of(extraction):
        assert message[span.start : span.end] == span.text


def only_line(message: str) -> ExtractedLine:
    (line,) = extract_lines(message).lines
    return line


def text(span: TextSpan | None) -> str | None:
    return span.text if span else None


# ---------------------------------------------------------------------------
# The client's demonstration message
# ---------------------------------------------------------------------------


def test_golden_demonstration_message():
    extraction = RulesInterpreter().extract(DEMO)

    assert extraction.interpreter == "rules@1"
    assert extraction.kind == InterpreterKind.RULES
    assert extraction.message == DEMO
    assert_exact_substrings(extraction, DEMO)

    # "bhaiya" is kept in the message but is not part of any line.
    assert [(s.start, s.end, s.text, s.reason) for s in extraction.ignored] == [
        (0, 6, "bhaiya", "greeting")
    ]

    first, second, third = extraction.lines

    assert first.line_number == 1
    assert (first.span.start, first.span.end) == (7, 28)
    assert first.span.text == "5 peti biscuit chhota"
    quantity = first.quantity
    assert (quantity.start, quantity.end, quantity.text) == (7, 8, "5")
    assert first.suggested_quantity == 5
    assert (first.unit.start, first.unit.end, first.unit.text) == (9, 13, "peti")
    assert first.unit_hint == UnitHint.CASE_WORD
    assert (first.product.start, first.product.end) == (14, 28)
    assert first.product.text == "biscuit chhota"
    assert first.sizes == ()
    assert first.notes == ()

    assert second.line_number == 2
    assert (second.span.start, second.span.end) == (30, 58)
    assert second.span.text == "2 peti washing powder 1 kilo"
    assert (second.quantity.start, second.quantity.text) == (30, "2")
    assert second.suggested_quantity == 2
    assert (second.unit.start, second.unit.text) == (32, "peti")
    assert second.unit_hint == UnitHint.CASE_WORD
    assert (second.product.start, second.product.end) == (37, 58)
    assert second.product.text == "washing powder 1 kilo"
    (size,) = second.sizes
    assert (size.start, size.end, size.text) == (52, 58, "1 kilo")
    assert (size.value, size.unit) == (Decimal(1), "kg")
    assert second.notes == ()

    assert third.line_number == 3
    assert (third.span.start, third.span.end) == (60, 86)
    assert third.span.text == "chai patti 250 wala 1 peti"
    assert (third.quantity.start, third.quantity.text) == (80, "1")
    assert third.suggested_quantity == 1
    assert (third.unit.start, third.unit.text) == (82, "peti")
    assert third.unit_hint == UnitHint.CASE_WORD
    assert (third.product.start, third.product.end) == (60, 79)
    assert third.product.text == "chai patti 250 wala"
    (size,) = third.sizes
    assert (size.start, size.end, size.text) == (71, 79, "250 wala")
    assert (size.value, size.unit) == (Decimal(250), None)
    assert third.notes == ()

    assert len(DEMO) == 86


def test_product_phrases_exclude_quantity_and_unit():
    """Aliases store the product phrase only (decision 3)."""
    products = [line.product.text for line in extract_lines(DEMO).lines]

    assert products == [
        "biscuit chhota",
        "washing powder 1 kilo",
        "chai patti 250 wala",
    ]
    for phrase in products:
        assert "peti" not in phrase


def test_extraction_never_confirms_a_unit_or_chooses_a_product():
    fields = set(ExtractedLine.model_fields)

    for confirmed in ("unit_confirmed", "quantity_cases", "product_id", "sku"):
        assert confirmed not in fields


def test_interpretation_is_deterministic():
    assert extract_lines(DEMO) == extract_lines(DEMO)
    from_class = RulesInterpreter().extract(DEMO)
    assert from_class.model_dump() == extract_lines(DEMO).model_dump()


def test_rules_interpreter_is_labelled_as_rules_not_a_model():
    interpreter: MessageInterpreter = RulesInterpreter()

    assert interpreter.name == RULES_INTERPRETER_NAME == "rules@1"
    assert interpreter.kind == InterpreterKind.RULES
    assert InterpreterKind.RULES.value == "rules"


def test_message_is_not_modified():
    message = "  BHAIYA 5 Peti Biscuit Chhota ,\n\n2 peti surf  "
    copy = str(message)

    extraction = extract_lines(message)

    assert message == copy
    assert extraction.message == message
    assert_exact_substrings(extraction, message)
    assert [line.span.text for line in extraction.lines] == [
        "5 Peti Biscuit Chhota",
        "2 peti surf",
    ]


# ---------------------------------------------------------------------------
# Quantities and sizes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "quantity", "unit", "product", "sizes"),
    [
        ("biscuit 75g 10 peti", 10, "peti", "biscuit 75g", [("75g", "75", "g")]),
        ("1kg detergent 3 case", 3, "case", "1kg detergent", [("1kg", "1", "kg")]),
        ("3 peti 500 gm chai", 3, "peti", "500 gm chai", [("500 gm", "500", "g")]),
        (
            "chai 250 wali 2 peti",
            2,
            "peti",
            "chai 250 wali",
            [("250 wali", "250", None)],
        ),
        (
            "2 peti tel 1.5 litre",
            2,
            "peti",
            "tel 1.5 litre",
            [("1.5 litre", "1.5", "l")],
        ),
        ("5-peti biscuit", 5, "peti", "biscuit", []),
        ("5peti biscuit", 5, "peti", "biscuit", []),
    ],
)
def test_quantity_is_distinguished_from_pack_size(
    message, quantity, unit, product, sizes
):
    line = only_line(message)

    assert line.suggested_quantity == quantity
    assert text(line.unit) == unit
    assert text(line.product) == product
    assert [(s.text, str(s.value), s.unit) for s in line.sizes] == sizes


@pytest.mark.parametrize(
    ("message", "quantity", "unit"),
    [
        ("do peti biscuit", 2, "peti"),
        ("teen case chai", 3, "case"),
        ("paanch peti biscuit", 5, "peti"),
        ("पाँच पेटी बिस्कुट", 5, "पेटी"),
        ("दो पेटी चाय", 2, "पेटी"),
        ("५ पेटी चाय", 5, "पेटी"),
        ("chai patti ११ peti", 11, "peti"),
    ],
)
def test_number_words_and_devanagari_digits(message, quantity, unit):
    line = only_line(message)

    assert line.suggested_quantity == quantity
    assert text(line.unit) == unit
    assert line.unit_hint == UnitHint.CASE_WORD


def test_a_number_word_without_a_unit_is_not_taken_as_a_quantity():
    # "do" also means "give": without a unit it is not assumed to be 2.
    line = only_line("do biscuit")

    assert line.quantity is None
    assert line.suggested_quantity is None
    assert text(line.product) == "do biscuit"
    assert any("'do'" in note for note in line.notes)


def test_a_number_word_followed_by_a_size_unit_is_a_size():
    line = only_line("ek kilo surf 2 peti")

    assert [(s.text, s.value, s.unit) for s in line.sizes] == [
        ("ek kilo", Decimal(1), "kg")
    ]
    assert line.suggested_quantity == 2


# ---------------------------------------------------------------------------
# Units (only hinted, never confirmed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "hint"),
    [
        ("5 peti biscuit", UnitHint.CASE_WORD),
        ("5 carton biscuit", UnitHint.CASE_WORD),
        ("20 packet biscuit", UnitHint.PIECE_WORD),
        ("12 pcs chai", UnitHint.PIECE_WORD),
        ("20 पैकेट बिस्कुट", UnitHint.PIECE_WORD),
    ],
)
def test_unit_vocabulary_gives_a_hint(message, hint):
    line = only_line(message)

    assert line.unit_hint == hint
    assert line.suggested_quantity is not None


def test_missing_unit_is_reported_not_guessed():
    line = only_line("biscuit 5")

    assert line.suggested_quantity == 5
    assert line.unit is None
    assert line.unit_hint == UnitHint.NONE
    assert "No unit was given." in line.notes


def test_unit_without_a_quantity():
    line = only_line("peti biscuit")

    assert line.quantity is None
    assert text(line.unit) == "peti"
    assert line.unit_hint == UnitHint.CASE_WORD
    assert text(line.product) == "biscuit"
    assert any("has no quantity" in note for note in line.notes)


# ---------------------------------------------------------------------------
# Unclear lines are kept and explained, never dropped
# ---------------------------------------------------------------------------


def test_fractional_and_zero_quantities_are_not_suggested():
    fraction = only_line("1.5 peti chai")
    zero = only_line("0 peti chai")

    assert text(fraction.quantity) == "1.5"
    assert fraction.suggested_quantity is None
    assert any("not a whole number" in note for note in fraction.notes)
    assert text(zero.quantity) == "0"
    assert zero.suggested_quantity is None


def test_quantity_above_the_limit_is_noted():
    line = only_line("20000 peti chai")

    assert line.suggested_quantity == 20000
    assert any("above the limit" in note for note in line.notes)


def test_extra_numbers_are_noted():
    line = only_line("5 peti biscuit 2 peti")

    assert line.suggested_quantity == 5
    assert any("Other numbers" in note and "2" in note for note in line.notes)


def test_product_words_on_both_sides_of_the_quantity():
    line = only_line("biscuit 5 peti chhota wala")

    assert line.suggested_quantity == 5
    assert text(line.product) == "chhota wala"
    assert any("'biscuit'" in note for note in line.notes)


def test_line_without_product_words():
    line = only_line("5 peti")

    assert line.product is None
    assert line.suggested_quantity == 5
    assert "No product words were found." in line.notes


def test_unrecognised_words_still_become_a_line():
    line = only_line("lal wala dabba")

    assert text(line.span) == "lal wala dabba"
    assert text(line.product) == "lal wala dabba"
    assert line.quantity is None
    assert line.unit is None


def test_greetings_and_punctuation_are_listed_as_ignored():
    message = "Namaste bhaiya, 2 peti surf bhejo; !!! ; ji"

    extraction = extract_lines(message)

    assert_exact_substrings(extraction, message)
    assert [line.span.text for line in extraction.lines] == ["2 peti surf"]
    assert [(s.text, s.reason) for s in extraction.ignored] == [
        ("Namaste bhaiya", "greeting"),
        ("bhejo", "greeting"),
        ("!!!", "no_words"),
        ("ji", "greeting"),
    ]


def test_message_with_only_greetings_has_no_lines_but_lists_them():
    extraction = extract_lines("bhaiya namaste")

    assert extraction.lines == ()
    assert [(s.text, s.reason) for s in extraction.ignored] == [
        ("bhaiya namaste", "greeting")
    ]


@pytest.mark.parametrize(
    "message",
    [
        "5 peti biscuit\n2 peti surf\n1 peti chai",
        "5 peti biscuit; 2 peti surf aur 1 peti chai",
        "5 पेटी बिस्कुट। 2 पेटी सर्फ और 1 पेटी चाय",
    ],
)
def test_separators_split_lines(message):
    extraction = extract_lines(message)

    assert [line.suggested_quantity for line in extraction.lines] == [5, 2, 1]
    assert [line.line_number for line in extraction.lines] == [1, 2, 3]
    assert_exact_substrings(extraction, message)


# ---------------------------------------------------------------------------
# Message limits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", ["", "   ", "\n\t "])
def test_blank_message_is_refused(message):
    with pytest.raises(InvalidMessageError):
        extract_lines(message)


def test_overlong_message_is_refused():
    with pytest.raises(InvalidMessageError):
        extract_lines("a" * (MAX_CAPTURE_MESSAGE_LENGTH + 1))


def test_too_many_lines_are_refused_never_truncated():
    within_limit = ", ".join(["1 peti chai"] * MAX_CAPTURE_LINES)
    over_limit = ", ".join(["1 peti chai"] * (MAX_CAPTURE_LINES + 1))

    assert len(extract_lines(within_limit).lines) == MAX_CAPTURE_LINES
    with pytest.raises(TooManyLinesError):
        extract_lines(over_limit)


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------


def test_spans_that_do_not_match_the_message_are_refused():
    good = extract_lines(DEMO)
    first = good.lines[0]
    product = first.product
    # The original product span's own offsets (inside the line), with text of
    # the same length that is not what the message says there.
    tampered = TextSpan(start=product.start, end=product.end, text="biscuit CHHOTA")
    assert DEMO[product.start : product.end] == "biscuit chhota"
    assert len(tampered.text) == product.end - product.start
    assert first.span.start <= tampered.start and tampered.end <= first.span.end
    altered = first.model_copy(update={"product": tampered})

    with pytest.raises(ValidationError, match="does not match the original message"):
        Extraction(
            interpreter=good.interpreter,
            kind=good.kind,
            message=DEMO,
            lines=(altered, *good.lines[1:]),
            ignored=good.ignored,
        )


def test_span_text_must_match_its_length():
    with pytest.raises(ValidationError):
        TextSpan(start=0, end=3, text="ab")


def test_parts_must_lie_inside_their_line():
    with pytest.raises(ValidationError, match="within the line"):
        ExtractedLine(
            line_number=1,
            span=TextSpan(start=7, end=13, text="5 peti"),
            product=TextSpan(start=14, end=21, text="biscuit"),
            quantity=None,
            unit=None,
            unit_hint=UnitHint.NONE,
        )


def test_lines_must_be_numbered_in_order():
    good = extract_lines(DEMO)
    renumbered = good.lines[1].model_copy(update={"line_number": 5})

    with pytest.raises(ValidationError, match="numbered"):
        Extraction(
            interpreter=good.interpreter,
            kind=good.kind,
            message=DEMO,
            lines=(good.lines[0], renumbered),
        )


def test_extraction_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        TextSpan(start=0, end=1, text="b", confidence=0.9)
