"""Pure text helpers for captured order messages.

No database access and no I/O. Nothing here changes a message: functions
read the original text and report positions (start/end offsets) into it.
Normalised forms (case-folded, NFC, ASCII digits) exist only for comparing
words with the vocabularies below and are never stored in place of the
original text.

The vocabularies are a hand-written starting list for Hinglish and Hindi
order messages ([Assumption], to be labelled in the TDD). They describe
words, never products: product matching belongs to the matcher.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.models.enums import UnitHint

#: Canonical pack-size units.
SizeUnit = Literal["g", "kg", "ml", "l"]

# ---------------------------------------------------------------------------
# Vocabularies (all entries case-folded and NFC-normalised)
# ---------------------------------------------------------------------------

# fmt: off

#: Number words. A number word is used as a quantity only when a unit word
#: follows it ("do peti"), because several are also ordinary words: "do"
#: is "give" in Hindi. Digits need no unit word.
NUMBER_WORDS: dict[str, int] = {
    "ek": 1, "एक": 1, "one": 1,
    "do": 2, "दो": 2, "two": 2,
    "teen": 3, "तीन": 3, "three": 3,
    "char": 4, "chaar": 4, "चार": 4, "four": 4,
    "panch": 5, "paanch": 5, "paach": 5, "पांच": 5, "पाँच": 5, "five": 5,
    "chhe": 6, "chhah": 6, "छह": 6, "छः": 6, "six": 6,
    "saat": 7, "सात": 7, "seven": 7,
    "aath": 8, "आठ": 8, "eight": 8,
    "nau": 9, "नौ": 9, "nine": 9,
    "das": 10, "dus": 10, "दस": 10, "ten": 10,
    "gyarah": 11, "ग्यारह": 11,
    "barah": 12, "baarah": 12, "बारह": 12,
    "pandrah": 15, "पंद्रह": 15,
    "bees": 20, "बीस": 20,
    "pachees": 25, "pachchis": 25, "पच्चीस": 25,
    "tees": 30, "तीस": 30,
    "chalis": 40, "chaalis": 40, "चालीस": 40,
    "pachas": 50, "pachaas": 50, "पचास": 50,
    "sau": 100, "सौ": 100,
}

#: Words that usually mean a case. Only a hint: never applied automatically.
CASE_WORDS: frozenset[str] = frozenset(
    {"peti", "petti", "pethi", "पेटी", "case", "cases", "carton", "cartons",
     "ctn", "ctns"}
)

#: Words for single packs or pieces. Orders are placed in whole cases, so a
#: reviewer must convert these.
PIECE_WORDS: frozenset[str] = frozenset(
    {"packet", "packets", "pkt", "pkts", "pack", "packs", "piece", "pieces",
     "pc", "pcs", "nag", "पैकेट", "पीस", "नग"}
)

#: Pack-size units, mapped to a canonical unit.
SIZE_UNITS: dict[str, SizeUnit] = {
    "g": "g", "gm": "g", "gms": "g", "gr": "g", "gram": "g", "grams": "g",
    "ग्राम": "g",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "किलो": "kg",
    "ml": "ml",
    "l": "l", "ltr": "l", "litre": "l", "liter": "l", "लीटर": "l",
}

#: "250 wala" ("the 250 one"): the number before it is a pack size.
SIZE_MARKERS: frozenset[str] = frozenset(
    {"wala", "wale", "wali", "waala", "waale", "waali", "वाला", "वाले", "वाली"}
)

#: Greetings and requests at the edges of a segment ("bhaiya", "bhejo").
#: They are kept in the message but are not part of any order line.
GREETING_WORDS: frozenset[str] = frozenset(
    {"bhaiya", "bhaiyya", "bhaiyaa", "bhaiyaji", "bhai", "bhaisaab",
     "bhaisahab", "sir", "ji", "jee", "please", "pls", "plz", "namaste",
     "namaskar", "hello", "hi", "bhejo", "bhej", "bhejna", "bhejiye",
     "bhijwa", "dena", "dijiye", "chahiye", "भैया", "भइया", "भाई", "जी",
     "नमस्ते", "भेजो", "भेजना", "भेजिए", "दीजिए", "चाहिए"}
)

# fmt: on

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DIGIT = "0-9०-९"
# Devanagari letters, vowel signs, nukta and virama, excluding the danda
# (U+0964/0965, a sentence separator) and the digits (U+0966-096F).
_DEVANAGARI_LETTERS = "ऀ-ॣॱ-ॿ"

_TOKEN = re.compile(
    rf"(?P<number>[{_DIGIT}]+(?:\.[{_DIGIT}]+)?)"
    rf"|(?P<word>[A-Za-z{_DEVANAGARI_LETTERS}]+)"
)

_WORD_CHARS = rf"A-Za-z{_DIGIT}{_DEVANAGARI_LETTERS}"

# Segment separators: , ; | newlines, the Devanagari danda, a full stop
# that is not a decimal point, and the words "aur" / "and" / "और".
_SEPARATOR = re.compile(
    r"[,;|\r\n।॥]+"
    rf"|\.(?![{_DIGIT}])|(?<![{_DIGIT}])\."
    rf"|(?<![{_WORD_CHARS}])(?:aur|and|और)(?![{_WORD_CHARS}])",
    re.IGNORECASE,
)

# Characters allowed between a number and its unit: "5 peti", "5peti", "5-peti".
_NUMBER_UNIT_GAP = re.compile(r"[\s\-]*")


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    """A word or number in a message, located by offsets into it."""

    kind: Literal["number", "word"]
    text: str  # exactly message[start:end]
    start: int
    end: int
    norm: str  # comparison form only


def normalize_word(text: str) -> str:
    """NFC, case-folded, with Devanagari digits as ASCII digits."""
    return unicodedata.normalize("NFC", text).casefold().translate(_DEVANAGARI_DIGITS)


def tokenize(message: str, start: int = 0, end: int | None = None) -> list[Token]:
    """Words and numbers in message[start:end], with absolute offsets."""
    end = len(message) if end is None else end
    tokens = []
    for match in _TOKEN.finditer(message, start, end):
        kind: Literal["number", "word"] = (
            "number" if match.lastgroup == "number" else "word"
        )
        tokens.append(
            Token(
                kind=kind,
                text=match.group(),
                start=match.start(),
                end=match.end(),
                norm=normalize_word(match.group()),
            )
        )
    return tokens


def split_segments(message: str) -> list[tuple[int, int]]:
    """(start, end) of each stretch of the message between separators.

    Every character of the message lies in a segment or a separator; the
    caller decides what each segment contains. Segments may be blank.
    """
    spans = []
    position = 0
    for separator in _SEPARATOR.finditer(message):
        spans.append((position, separator.start()))
        position = separator.end()
    spans.append((position, len(message)))
    return spans


def adjacent(message: str, first: Token, second: Token) -> bool:
    """True when only spaces or a hyphen lie between two tokens."""
    return _NUMBER_UNIT_GAP.fullmatch(message, first.end, second.start) is not None


# ---------------------------------------------------------------------------
# Word classes
# ---------------------------------------------------------------------------


def is_number(token: Token) -> bool:
    """A digit token, or a number word."""
    return token.kind == "number" or token.norm in NUMBER_WORDS


def number_value(token: Token) -> Decimal | None:
    """The value of a digit token or number word, or None."""
    if token.kind == "number":
        try:
            return Decimal(token.norm)
        except InvalidOperation:  # pragma: no cover - the token regex forbids it
            return None
    value = NUMBER_WORDS.get(token.norm)
    return Decimal(value) if value is not None else None


def unit_hint(token: Token) -> UnitHint | None:
    """CASE_WORD or PIECE_WORD for a unit word, else None."""
    if token.kind != "word":
        return None
    if token.norm in CASE_WORDS:
        return UnitHint.CASE_WORD
    if token.norm in PIECE_WORDS:
        return UnitHint.PIECE_WORD
    return None


def size_unit(token: Token) -> SizeUnit | None:
    """The canonical pack-size unit of a word ("kilo" -> "kg"), or None."""
    return SIZE_UNITS.get(token.norm) if token.kind == "word" else None


def is_size_marker(token: Token) -> bool:
    return token.kind == "word" and token.norm in SIZE_MARKERS


def is_greeting(token: Token) -> bool:
    return token.kind == "word" and token.norm in GREETING_WORDS


# ---------------------------------------------------------------------------
# Phrases
# ---------------------------------------------------------------------------


def normalize_phrase(text: str) -> str:
    """Comparison form of a phrase: its words and numbers, normalised and
    joined by single spaces ("Biscuit  Chhota!" -> "biscuit chhota").

    Used to compare product phrases and aliases. Never stored instead of
    the original text.
    """
    return " ".join(token.norm for token in tokenize(text))
