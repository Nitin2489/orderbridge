"""Deterministic product matching for captured order lines.

Given the lines an interpreter extracted, rank the active catalogue products
for each line with a fixed, explainable heuristic, and propose one only
when it is clearly ahead. Everything here is a suggestion for a person to
review: the matcher never confirms a product, never touches the unit or
quantity of a line, and never prices, reserves or orders anything.

The match score is a deterministic heuristic (0-100) for ranking and
explanation. **It is not a probability or a confidence percentage.**

Scoring, per product (approved Phase A contract):

    exact active alias of this retailer for the line's product phrase  95
    otherwise: a recognised product word or synonym                    60
               pack size stated in the line matches the product        +25
               pack size stated in the line contradicts the product   -30
               no pack size stated                                     +0
    product in this retailer's recent orders (any phrase)              +10
    then clamped to 0-100

Bands: strong 85-100, possible 60-84, weak 30-59, very weak 0-29.
A product is proposed only when its score is at least 30 and no other
product has the same top score; otherwise there is no proposal and the
reasons say why. Every product is still returned as a ranked candidate.

The module has two parts: pure matching (no database; deterministic for a
given MatchingContext) and load_matching_context(), the only database read,
which collects active products, the retailer's active aliases and the
products in the retailer's recent orders.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductStatus,
    RetailerProductAlias,
)
from app.services.capture_interpreter import ExtractedLine, Extraction, SizeMention
from app.services.capture_text import (
    SizeUnit,
    adjacent,
    normalize_phrase,
    number_value,
    size_unit,
    tokenize,
)

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

ALIAS_SCORE = 95
SYNONYM_SCORE = 60
SIZE_MATCH_BONUS = 25
SIZE_CONFLICT_PENALTY = -30
HISTORY_BONUS = 10
PROPOSAL_THRESHOLD = 30
MAX_SCORE = 100
MAX_ALTERNATIVES = 2
#: Recent orders considered for the history preference.
HISTORY_ORDER_LIMIT = 20

#: [Assumption] Words and phrases that name a kind of product, mapped to a
#: word that appears in the catalogue product's name. The approved
#: synonyms are "biscuit", "washing powder" and "chai patti"; "detergent"
#: and "tea" are the catalogue's own name words. A hand-written list, not a
#: learned model. Keys are normalised phrases (see normalize_phrase).
PRODUCT_WORDS: dict[str, str] = {
    "biscuit": "biscuit",
    "washing powder": "detergent",
    "detergent": "detergent",
    "chai patti": "tea",
    "tea": "tea",
}

#: Descriptive words that sound like a size but do not identify a pack size.
DESCRIPTIVE_SIZE_WORDS: frozenset[str] = frozenset(
    {
        "chhota",
        "chota",
        "chhoti",
        "choti",
        "bada",
        "bara",
        "badi",
        "bari",
        "small",
        "big",
        "large",
    }
)

# Mass and volume in a common base unit for comparing pack sizes.
_BASE: dict[SizeUnit, tuple[str, Decimal]] = {
    "g": ("mass", Decimal(1)),
    "kg": ("mass", Decimal(1000)),
    "ml": ("volume", Decimal(1)),
    "l": ("volume", Decimal(1000)),
}


class MatchBand(StrEnum):
    """Band of a heuristic match score (not a probability)."""

    STRONG = "strong"  # 85-100
    POSSIBLE = "possible"  # 60-84
    WEAK = "weak"  # 30-59
    VERY_WEAK = "very_weak"  # 0-29


def band_for(score: int) -> MatchBand:
    if not 0 <= score <= MAX_SCORE:
        raise ValueError("A match score is between 0 and 100.")
    if score >= 85:
        return MatchBand.STRONG
    if score >= 60:
        return MatchBand.POSSIBLE
    if score >= PROPOSAL_THRESHOLD:
        return MatchBand.WEAK
    return MatchBand.VERY_WEAK


def clamp(score: int) -> int:
    return max(0, min(MAX_SCORE, score))


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductSize:
    value: Decimal
    unit: SizeUnit

    @property
    def label(self) -> str:
        return f"{self.value.normalize():f}{self.unit}"


@dataclass(frozen=True)
class CatalogueProduct:
    """What the matcher needs to know about one active product."""

    id: UUID
    sku: str
    name: str
    name_words: tuple[str, ...] = field(init=False)
    size: ProductSize | None = field(init=False)

    def __post_init__(self) -> None:
        words = tuple(normalize_phrase(self.name).split())
        object.__setattr__(self, "name_words", words)
        object.__setattr__(self, "size", product_size(self.name))


@dataclass(frozen=True)
class MatchingContext:
    """Everything matching depends on, for one retailer."""

    products: tuple[CatalogueProduct, ...]
    # Active aliases of this retailer: normalised phrase -> product id.
    aliases: Mapping[str, UUID] = field(default_factory=dict)
    # Products in this retailer's recent non-rejected orders.
    history_product_ids: frozenset[UUID] = frozenset()


def product_size(name: str) -> ProductSize | None:
    """The pack size written in a product name ("Tea 250g" -> 250 g)."""
    tokens = tokenize(name)
    for current, following in pairwise(tokens):
        unit = size_unit(following)
        value = number_value(current) if current.kind == "number" else None
        joined = adjacent(name, current, following)
        if unit is not None and value is not None and joined:
            return ProductSize(value=value, unit=unit)
    return None


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Candidate(BaseModel):
    """One product ranked for a line, with the reasons for its score."""

    model_config = _FROZEN

    product_id: UUID
    sku: str
    name: str
    # Heuristic match score, 0-100. Not a probability.
    score: int = Field(ge=0, le=MAX_SCORE)
    band: MatchBand
    # True for the weak and very weak bands (below 60).
    weak_match: bool
    reasons: tuple[str, ...]


class LineMatch(BaseModel):
    """Matching result for one extracted line. Suggestions only.

    There is deliberately no unit, quantity or confirmation here: matching
    a product and confirming a unit are separate decisions made by a person.
    """

    model_config = _FROZEN

    line_number: int = Field(ge=1)
    proposal: Candidate | None
    # Up to two candidates after the proposal (or the top two when there is
    # no proposal).
    alternatives: tuple[Candidate, ...] = Field(max_length=MAX_ALTERNATIVES)
    # Every active product, ranked; nothing is discarded.
    candidates: tuple[Candidate, ...]
    # Why this line has its proposal, or why it has none.
    reasons: tuple[str, ...]


# ---------------------------------------------------------------------------
# Matching (pure)
# ---------------------------------------------------------------------------


def _contains(words: Sequence[str], phrase: Sequence[str]) -> bool:
    size = len(phrase)
    return any(
        tuple(words[i : i + size]) == tuple(phrase)
        for i in range(len(words) - size + 1)
    )


def recognised_product_words(phrase_words: Sequence[str]) -> dict[str, str]:
    """{phrase as written in PRODUCT_WORDS: name word} found in the phrase."""
    return {
        key: name_word
        for key, name_word in PRODUCT_WORDS.items()
        if _contains(phrase_words, key.split())
    }


def _base(value: Decimal, unit: SizeUnit) -> tuple[str, Decimal]:
    dimension, factor = _BASE[unit]
    return dimension, value * factor


def _size_agrees(mention: SizeMention, size: ProductSize) -> bool:
    if mention.unit is None:
        # "250 wala": the number alone, in the product's own unit.
        return mention.value == size.value
    return _base(mention.value, mention.unit) == _base(size.value, size.unit)


def size_adjustment(
    sizes: Sequence[SizeMention], size: ProductSize | None
) -> tuple[int, str]:
    """(score change, reason) for the pack sizes stated in a line.

    One adjustment per product however many sizes the line states, so the
    same evidence is never counted twice.
    """
    if not sizes:
        return 0, "Pack size not stated."
    stated = ", ".join(f"'{mention.text}'" for mention in sizes)
    if size is None:
        return 0, f"Pack size {stated} cannot be compared: the product has none."
    agree = [mention for mention in sizes if _size_agrees(mention, size)]
    if agree and len(agree) == len(sizes):
        return SIZE_MATCH_BONUS, f"Pack size {stated} matches {size.label}."
    if not agree:
        return (
            SIZE_CONFLICT_PENALTY,
            f"Pack size {stated} does not match {size.label}.",
        )
    return 0, f"Pack sizes {stated} disagree with each other."


def _score_product(
    product: CatalogueProduct,
    line: ExtractedLine,
    phrase: str | None,
    recognised: Mapping[str, str],
    context: MatchingContext,
) -> Candidate:
    reasons: list[str] = []

    if phrase is not None and context.aliases.get(phrase) == product.id:
        score = ALIAS_SCORE
        reasons.append(f"Saved alias for this retailer: '{phrase}'.")
    else:
        matched = [
            key for key, word in recognised.items() if word in product.name_words
        ]
        if matched:
            score = SYNONYM_SCORE
            reasons += [f"Recognised '{key}' as {recognised[key]}." for key in matched]
        else:
            score = 0
            reasons.append("No recognised product words for this product.")
        change, reason = size_adjustment(line.sizes, product.size)
        score += change
        reasons.append(reason)

    if product.id in context.history_product_ids:
        score += HISTORY_BONUS
        reasons.append("Ordered by this retailer in recent orders.")

    score = clamp(score)
    band = band_for(score)
    return Candidate(
        product_id=product.id,
        sku=product.sku,
        name=product.name,
        score=score,
        band=band,
        weak_match=score < SYNONYM_SCORE,
        reasons=tuple(reasons),
    )


def match_line(line: ExtractedLine, context: MatchingContext) -> LineMatch:
    """Rank every product for one line and propose one if clearly ahead."""
    phrase = normalize_phrase(line.product.text) if line.product else None
    phrase = phrase or None
    phrase_words = phrase.split() if phrase else []
    recognised = recognised_product_words(phrase_words)

    candidates = sorted(
        (
            _score_product(product, line, phrase, recognised, context)
            for product in context.products
        ),
        key=lambda candidate: (-candidate.score, candidate.sku),
    )

    line_reasons: list[str] = []
    if line.product is None:
        line_reasons.append("The line has no product words.")
    descriptive = [word for word in phrase_words if word in DESCRIPTIVE_SIZE_WORDS]
    if descriptive:
        line_reasons.append(
            ", ".join(f"'{word}'" for word in descriptive)
            + " does not identify a pack size."
        )

    proposal: Candidate | None = None
    if not candidates:
        line_reasons.append("There are no active products to match.")
    elif candidates[0].score < PROPOSAL_THRESHOLD:
        line_reasons.append(
            f"No product scored {PROPOSAL_THRESHOLD} or more; choose one."
        )
    elif len(candidates) > 1 and candidates[1].score == candidates[0].score:
        tied = [c for c in candidates if c.score == candidates[0].score]
        line_reasons.append(
            f"Tied at {candidates[0].score}: "
            + " and ".join(c.name for c in tied)
            + "; choose one."
        )
    else:
        proposal = candidates[0]

    others = candidates[1:] if proposal is not None else candidates
    return LineMatch(
        line_number=line.line_number,
        proposal=proposal,
        alternatives=tuple(others[:MAX_ALTERNATIVES]),
        candidates=tuple(candidates),
        reasons=tuple(line_reasons),
    )


def match_extraction(
    extraction: Extraction, context: MatchingContext
) -> tuple[LineMatch, ...]:
    """One LineMatch per extracted line, in line order. Never drops a line."""
    return tuple(match_line(line, context) for line in extraction.lines)


# ---------------------------------------------------------------------------
# Context (the only database read)
# ---------------------------------------------------------------------------


def load_matching_context(session: Session, retailer_id: UUID) -> MatchingContext:
    """Active products, this retailer's active aliases and the products in
    its HISTORY_ORDER_LIMIT most recent non-rejected orders.

    Read-only: no locks and no writes. Aliases of other retailers and aliases
    to inactive products are never included.
    """
    products = tuple(
        CatalogueProduct(id=product_id, sku=sku, name=name)
        for product_id, sku, name in session.execute(
            select(Product.id, Product.sku, Product.name)
            .where(Product.status == ProductStatus.ACTIVE)
            .order_by(Product.sku)
        )
    )
    active_ids = {product.id for product in products}

    aliases = {
        normalized: product_id
        for normalized, product_id in session.execute(
            select(
                RetailerProductAlias.normalized_alias, RetailerProductAlias.product_id
            ).where(
                RetailerProductAlias.retailer_id == retailer_id,
                RetailerProductAlias.is_active.is_(True),
            )
        )
        if product_id in active_ids
    }

    recent_orders = (
        select(Order.id)
        .where(
            Order.retailer_id == retailer_id,
            Order.status != OrderStatus.REJECTED,
        )
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(HISTORY_ORDER_LIMIT)
        .subquery()
    )
    history = frozenset(
        product_id
        for product_id in session.scalars(
            select(OrderItem.product_id)
            .where(OrderItem.order_id.in_(select(recent_orders.c.id)))
            .distinct()
        )
        if product_id in active_ids
    )

    return MatchingContext(
        products=products, aliases=aliases, history_product_ids=history
    )
