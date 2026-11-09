"""Interpretation of captured order messages into proposed lines.

An interpreter reads a message and proposes lines: where each line is in the
message, which words name the product, and what quantity, unit and pack
sizes the words suggest. It never chooses a SKU (the matcher does), never
prices anything, never confirms a unit and never touches the database.

Interpreters are replaceable behind MessageInterpreter. RulesInterpreter
uses deterministic, hand-written rules. **It is not an AI model**; its kind
is InterpreterKind.RULES and its name "rules@1". The optional Gemini
interpreter (app.services.gemini_interpreter) is a language model; it must
return the same validated Extraction, grounded in the message, and falls
back to the rules whenever its answer cannot be used.

Original text is never modified. Every span in an Extraction is checked
against the message it came from: span.text must equal
message[span.start:span.end]. Parts of a message that are not order lines
(greetings, punctuation-only stretches) are listed in Extraction.ignored
with a reason, never dropped silently.
"""

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Literal, NamedTuple, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.capture import (
    MAX_CAPTURE_LINES,
    MAX_CAPTURE_MESSAGE_LENGTH,
    MAX_CAPTURE_QUANTITY,
)
from app.models.enums import UnitHint
from app.services.capture_text import (
    SizeUnit,
    Token,
    adjacent,
    is_greeting,
    is_number,
    is_size_marker,
    number_value,
    size_unit,
    split_segments,
    tokenize,
    unit_hint,
)

RULES_INTERPRETER_NAME = "rules@1"


class InterpreterKind(StrEnum):
    """What produced an extraction, so the UI can say so plainly."""

    RULES = "rules"  # deterministic rules; not an AI model
    LANGUAGE_MODEL = "language_model"  # e.g. Gemini; see gemini_interpreter


#: Why a language-model reading was not used and the rules were used instead.
#: Stored with the extraction; never contains provider error details.
AiFallback = Literal["unavailable", "timeout", "invalid_output", "incomplete", "empty"]


class CatalogueEntry(NamedTuple):
    """The only product information an interpreter may see: code and name.
    Never prices, stock, retailers or order history."""

    sku: str
    name: str


class InvalidMessageError(ValueError):
    """The message is blank or longer than the limit."""


class TooManyLinesError(ValueError):
    """The message would produce more lines than one capture may hold."""


# ---------------------------------------------------------------------------
# Validated output
# ---------------------------------------------------------------------------

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class TextSpan(BaseModel):
    """A stretch of the original message: text == message[start:end]."""

    model_config = _FROZEN

    start: int = Field(ge=0)
    end: int
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> "TextSpan":
        if self.end <= self.start or self.end - self.start != len(self.text):
            raise ValueError("A span's offsets must match its text length.")
        return self


class SizeMention(TextSpan):
    """A pack size in a line, e.g. "1 kilo" (1, kg) or "250 wala" (250, None)."""

    value: Decimal = Field(gt=0)
    unit: SizeUnit | None


class IgnoredSpan(TextSpan):
    """Text that is not part of any order line, and why."""

    reason: Literal["greeting", "no_words"]


class ExtractedLine(BaseModel):
    """One proposed line. Everything here is a suggestion for a reviewer.

    There is deliberately no confirmed unit or case quantity: only a person
    confirms those (see app.models.capture.OrderCaptureLine).
    """

    model_config = _FROZEN

    line_number: int = Field(ge=1)
    span: TextSpan
    product: TextSpan | None
    quantity: TextSpan | None
    # A whole number from the quantity text, or None when there is none or
    # it is not a positive whole number (see notes).
    suggested_quantity: int | None = Field(default=None, ge=1)
    unit: TextSpan | None
    unit_hint: UnitHint
    sizes: tuple[SizeMention, ...] = ()
    # Plain-language observations for the reviewer.
    notes: tuple[str, ...] = ()
    # A language model's catalogue code for this line, already checked to
    # exist in the catalogue it was shown. Only a weak matching signal (see
    # capture_matcher); the rules interpreter never sets it.
    suggested_sku: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _parts_inside_line(self) -> "ExtractedLine":
        for part in (self.product, self.quantity, self.unit, *self.sizes):
            if part is not None and not (
                self.span.start <= part.start and part.end <= self.span.end
            ):
                raise ValueError("Every part of a line must lie within the line.")
        if (self.unit is None) != (self.unit_hint == UnitHint.NONE):
            raise ValueError("unit_hint must be NONE exactly when there is no unit.")
        if self.suggested_quantity is not None and self.quantity is None:
            raise ValueError("A suggested quantity needs its quantity text.")
        return self


class Extraction(BaseModel):
    """An interpreter's complete, validated output for one message."""

    model_config = _FROZEN

    interpreter: str = Field(min_length=1, max_length=40)
    kind: InterpreterKind
    message: str
    lines: tuple[ExtractedLine, ...]
    ignored: tuple[IgnoredSpan, ...] = ()
    # The provider's model name, for a language-model extraction.
    model: str | None = Field(default=None, max_length=64)
    # Set when a language model was configured but the rules were used.
    ai_fallback: AiFallback | None = None

    @model_validator(mode="after")
    def _spans_match_message(self) -> "Extraction":
        def check(span: TextSpan) -> None:
            if (
                span.end > len(self.message)
                or self.message[span.start : span.end] != span.text
            ):
                raise ValueError(
                    "A span does not match the original message at its offsets."
                )

        previous_end = 0
        for number, line in enumerate(self.lines, start=1):
            if line.line_number != number:
                raise ValueError("Lines must be numbered 1, 2, 3, ... in order.")
            if line.span.start < previous_end:
                raise ValueError("Lines must be in message order and not overlap.")
            previous_end = line.span.end
            parts = (line.span, line.product, line.quantity, line.unit, *line.sizes)
            for part in parts:
                if part is not None:
                    check(part)
        for span in self.ignored:
            check(span)
        if len(self.lines) > MAX_CAPTURE_LINES:
            raise ValueError(f"A capture may have at most {MAX_CAPTURE_LINES} lines.")
        return self


class MessageInterpreter(Protocol):
    """Turns a message into proposed lines. Implementations must be pure
    with respect to the database and must return a validated Extraction.

    `catalogue` lists the active products (code and name only) for an
    interpreter that suggests catalogue codes; others ignore it.
    """

    name: str
    kind: InterpreterKind

    def extract(
        self, message: str, *, catalogue: Sequence[CatalogueEntry] = ()
    ) -> Extraction: ...


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def validate_message(message: str) -> None:
    if not message.strip():
        raise InvalidMessageError("The message is empty.")
    if len(message) > MAX_CAPTURE_MESSAGE_LENGTH:
        raise InvalidMessageError(
            f"The message is longer than {MAX_CAPTURE_MESSAGE_LENGTH} characters."
        )


def _span(message: str, start: int, end: int) -> TextSpan:
    return TextSpan(start=start, end=end, text=message[start:end])


def _covering(message: str, tokens: list[Token]) -> TextSpan:
    return _span(message, tokens[0].start, tokens[-1].end)


def _whole_quantity(value: Decimal) -> int | None:
    if value > 0 and value == value.to_integral_value():
        return int(value)
    return None


def _contiguous_runs(indexes: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for index in indexes:
        if runs and runs[-1][-1] == index - 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


def _build_line(message: str, tokens: list[Token], line_number: int) -> ExtractedLine:
    """Propose quantity, unit, sizes and product words for one line.

    Rules, in order:
    - A number directly followed by a size unit ("1 kilo", "75g") or by
      "wala" ("250 wala") is a pack size, not a quantity.
    - A number directly followed by a unit word ("5 peti", "do peti",
      "20 packet") is a quantity with that unit.
    - Otherwise a digit number ("biscuit 5") is a quantity with no unit.
      A number word without a unit word is not taken as a quantity ("do"
      also means "give"); a note says it may be one.
    - The first quantity with a unit wins, then the first bare digit. Other
      numbers are reported in a note, never silently ignored.
    - A unit word with no number before it is still reported as the unit.
    - The product phrase is the remaining words; if they lie on both sides
      of the quantity, the longer part is used and the other is noted.
    """
    notes: list[str] = []
    sizes: list[SizeMention] = []
    used: set[int] = set()
    with_unit: list[tuple[int, int]] = []
    bare: list[int] = []

    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        joined = following is not None and adjacent(message, token, following)

        if is_number(token) and joined and following is not None:
            unit = size_unit(following)
            marker = token.kind == "number" and is_size_marker(following)
            value = number_value(token)
            if (unit is not None or marker) and value is not None and value > 0:
                span = _covering(message, [token, following])
                sizes.append(
                    SizeMention(
                        start=span.start,
                        end=span.end,
                        text=span.text,
                        value=value,
                        unit=unit,
                    )
                )
                index += 2
                continue
            if unit_hint(following) is not None:
                with_unit.append((index, index + 1))
                index += 2
                continue
        if token.kind == "number":
            bare.append(index)
        index += 1

    quantity_index: int | None = None
    unit_index: int | None = None
    if with_unit:
        quantity_index, unit_index = with_unit[0]
    elif bare:
        quantity_index = bare[0]

    unused_numbers = [
        tokens[i].text for i in [q for q, _ in with_unit] + bare if i != quantity_index
    ]
    if unused_numbers:
        notes.append(
            "Other numbers in this line were not used as the quantity: "
            + ", ".join(unused_numbers)
            + "."
        )

    if unit_index is None:
        for i, token in enumerate(tokens):
            if i != quantity_index and unit_hint(token) is not None:
                unit_index = i
                if quantity_index is None:
                    notes.append(f"The unit '{token.text}' has no quantity before it.")
                break

    quantity = suggested = None
    if quantity_index is not None:
        quantity_token = tokens[quantity_index]
        quantity = _span(message, quantity_token.start, quantity_token.end)
        value = number_value(quantity_token)
        suggested = _whole_quantity(value) if value is not None else None
        if suggested is None:
            notes.append(
                f"'{quantity_token.text}' is not a whole number of units above zero."
            )
        elif suggested > MAX_CAPTURE_QUANTITY:
            notes.append(
                f"{suggested} is above the limit of {MAX_CAPTURE_QUANTITY} cases "
                "per line."
            )

    unit = hint = None
    if unit_index is not None:
        unit_token = tokens[unit_index]
        unit = _span(message, unit_token.start, unit_token.end)
        hint = unit_hint(unit_token)
    if unit is None:
        notes.append("No unit was given.")

    if quantity_index is not None:
        used.add(quantity_index)
    if unit_index is not None:
        used.add(unit_index)

    if quantity_index is None:
        possible = [t.text for t in tokens if t.kind == "word" and is_number(t)]
        if possible:
            notes.append(
                "No quantity was found. These words may be quantities: "
                + ", ".join(f"'{word}'" for word in possible)
                + "."
            )

    product = None
    runs = _contiguous_runs([i for i in range(len(tokens)) if i not in used])
    if runs:
        # Longest by characters; the first wins a tie.
        chosen = max(runs, key=lambda run: tokens[run[-1]].end - tokens[run[0]].start)
        product = _covering(message, [tokens[i] for i in chosen])
        others = [
            _covering(message, [tokens[i] for i in run]).text
            for run in runs
            if run is not chosen
        ]
        if others:
            notes.append(
                "Other words in this line were not used as the product: "
                + ", ".join(f"'{text}'" for text in others)
                + "."
            )
    else:
        notes.append("No product words were found.")

    return ExtractedLine(
        line_number=line_number,
        span=_covering(message, tokens),
        product=product,
        quantity=quantity,
        suggested_quantity=suggested,
        unit=unit,
        unit_hint=hint or UnitHint.NONE,
        sizes=tuple(sizes),
        notes=tuple(notes),
    )


def extract_lines(message: str) -> Extraction:
    """Split a message into proposed lines with the deterministic rules.

    Each segment between separators (",", ";", "|", newline, "।", a full
    stop, "aur", "and", "और") that contains words becomes one line, after
    greeting words at its edges are set aside. Segments with only
    greetings, or only punctuation, are listed in `ignored`.

    Raises InvalidMessageError or TooManyLinesError; never truncates.
    """
    validate_message(message)
    lines: list[ExtractedLine] = []
    ignored: list[IgnoredSpan] = []

    def ignore(start: int, end: int, reason: Literal["greeting", "no_words"]) -> None:
        ignored.append(
            IgnoredSpan(start=start, end=end, text=message[start:end], reason=reason)
        )

    for segment_start, segment_end in split_segments(message):
        tokens = tokenize(message, segment_start, segment_end)
        if not tokens:
            text = message[segment_start:segment_end]
            if text.strip():
                stripped_start = segment_start + (len(text) - len(text.lstrip()))
                stripped_end = segment_end - (len(text) - len(text.rstrip()))
                ignore(stripped_start, stripped_end, "no_words")
            continue

        first = 0
        while first < len(tokens) and is_greeting(tokens[first]):
            first += 1
        last = len(tokens)
        while last > first and is_greeting(tokens[last - 1]):
            last -= 1

        if first == last:
            ignore(tokens[0].start, tokens[-1].end, "greeting")
            continue
        if first:
            ignore(tokens[0].start, tokens[first - 1].end, "greeting")
        if last < len(tokens):
            ignore(tokens[last].start, tokens[-1].end, "greeting")

        if len(lines) == MAX_CAPTURE_LINES:
            raise TooManyLinesError(
                f"The message has more than {MAX_CAPTURE_LINES} order lines."
            )
        lines.append(_build_line(message, tokens[first:last], len(lines) + 1))

    ignored.sort(key=lambda span: span.start)
    return Extraction(
        interpreter=RULES_INTERPRETER_NAME,
        kind=InterpreterKind.RULES,
        message=message,
        lines=tuple(lines),
        ignored=tuple(ignored),
    )


class RulesInterpreter:
    """Deterministic rule-based interpretation. Not an AI model."""

    name = RULES_INTERPRETER_NAME
    kind = InterpreterKind.RULES

    def extract(
        self, message: str, *, catalogue: Sequence[CatalogueEntry] = ()
    ) -> Extraction:
        return extract_lines(message)
