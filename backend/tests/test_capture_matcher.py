"""Unit tests for deterministic product matching (no database).

The catalogue is the three client products exactly as seed.py defines them
(names and SKUs), with fixed ids; nothing else is invented. Aliases and
order history are supplied explicitly per test, so every score can be
traced to the approved contract. Scores are heuristic, not probabilities.
"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.enums import UnitHint
from app.services.capture_interpreter import extract_lines
from app.services.capture_matcher import (
    ALIAS_SCORE,
    HISTORY_BONUS,
    PROPOSAL_THRESHOLD,
    SIZE_CONFLICT_PENALTY,
    SIZE_MATCH_BONUS,
    SYNONYM_SCORE,
    Candidate,
    CatalogueProduct,
    LineMatch,
    MatchBand,
    MatchingContext,
    band_for,
    clamp,
    match_extraction,
    match_line,
)
from seed import PRODUCTS

DEMO = (
    "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, "
    "chai patti 250 wala 1 peti"
)

BISCUIT, DETERGENT, TEA = "SKU-BS-075", "SKU-DT-1KG", "SKU-TE-250"
IDS = {
    BISCUIT: UUID("00000000-0000-4000-8000-000000000001"),
    DETERGENT: UUID("00000000-0000-4000-8000-000000000002"),
    TEA: UUID("00000000-0000-4000-8000-000000000003"),
}
CATALOGUE = tuple(
    CatalogueProduct(id=IDS[data["sku"]], sku=data["sku"], name=data["name"])
    for data in PRODUCTS
)


def context(
    aliases: dict[str, str] | None = None, history: tuple[str, ...] = ()
) -> MatchingContext:
    return MatchingContext(
        products=CATALOGUE,
        aliases={phrase: IDS[sku] for phrase, sku in (aliases or {}).items()},
        history_product_ids=frozenset(IDS[sku] for sku in history),
    )


def match(message: str, ctx: MatchingContext | None = None) -> LineMatch:
    (line,) = extract_lines(message).lines
    return match_line(line, ctx or context())


def scores(result: LineMatch) -> dict[str, int]:
    return {candidate.sku: candidate.score for candidate in result.candidates}


def proposed(result: LineMatch) -> tuple[str, int, MatchBand] | None:
    if result.proposal is None:
        return None
    return result.proposal.sku, result.proposal.score, result.proposal.band


# ---------------------------------------------------------------------------
# The catalogue under test
# ---------------------------------------------------------------------------


def test_catalogue_is_exactly_the_three_client_products():
    assert {product.sku for product in CATALOGUE} == {BISCUIT, DETERGENT, TEA}
    sizes = {product.sku: product.size.label for product in CATALOGUE}
    assert sizes == {BISCUIT: "75g", DETERGENT: "1kg", TEA: "250g"}


# ---------------------------------------------------------------------------
# The demonstration message
# ---------------------------------------------------------------------------


def test_demonstration_message():
    extraction = extract_lines(DEMO)

    first, second, third = match_extraction(extraction, context())

    assert proposed(first) == (BISCUIT, 60, MatchBand.POSSIBLE)
    assert proposed(second) == (DETERGENT, 85, MatchBand.STRONG)
    assert proposed(third) == (TEA, 85, MatchBand.STRONG)

    assert "Recognised 'biscuit' as biscuit." in first.proposal.reasons
    assert "Pack size not stated." in first.proposal.reasons
    assert "'chhota' does not identify a pack size." in first.reasons
    assert "Recognised 'washing powder' as detergent." in second.proposal.reasons
    assert "Pack size '1 kilo' matches 1kg." in second.proposal.reasons
    assert "Recognised 'chai patti' as tea." in third.proposal.reasons
    assert "Pack size '250 wala' matches 250g." in third.proposal.reasons

    # Two alternatives each, weak, ordered by score then SKU.
    assert [(c.sku, c.score) for c in first.alternatives] == [(DETERGENT, 0), (TEA, 0)]
    assert [(c.sku, c.score) for c in second.alternatives] == [(BISCUIT, 0), (TEA, 0)]
    assert [(c.sku, c.score) for c in third.alternatives] == [
        (BISCUIT, 0),
        (DETERGENT, 0),
    ]
    for result in (first, second, third):
        for alternative in result.alternatives:
            assert alternative.band == MatchBand.VERY_WEAK
            assert alternative.weak_match is True


def test_no_history_or_alias_is_invented_for_the_demonstration():
    for result in match_extraction(extract_lines(DEMO), context()):
        for candidate in result.candidates:
            assert not any("alias" in reason for reason in candidate.reasons)
            assert not any("recent orders" in reason for reason in candidate.reasons)


# ---------------------------------------------------------------------------
# Product words and synonyms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "sku"),
    [
        ("5 peti biscuit", BISCUIT),
        ("5 peti glucose biscuit", BISCUIT),
        ("2 peti washing powder", DETERGENT),
        ("2 peti detergent", DETERGENT),
        ("1 peti chai patti", TEA),
        ("1 peti tea", TEA),
    ],
)
def test_each_product_is_recognised(message, sku):
    result = match(message)

    assert proposed(result) == (sku, SYNONYM_SCORE, MatchBand.POSSIBLE)
    assert "Pack size not stated." in result.proposal.reasons


def test_unrecognised_words_do_not_match():
    # "powder" alone, "chai" alone and "patti" alone are not approved words.
    for message in ("2 peti powder", "1 peti chai", "1 peti patti"):
        result = match(message)
        assert result.proposal is None
        assert set(scores(result).values()) == {0}


# ---------------------------------------------------------------------------
# Pack sizes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "sku"),
    [
        ("1 peti chai patti 250 gram", TEA),
        ("1 peti chai patti 250g", TEA),
        ("1 peti chai patti 0.25 kg", TEA),
        ("2 peti washing powder 1000 gm", DETERGENT),
        ("2 peti washing powder 1kg", DETERGENT),
        ("5 peti biscuit 75 gm", BISCUIT),
    ],
)
def test_matching_size_adds_twenty_five(message, sku):
    result = match(message)

    assert proposed(result) == (sku, SYNONYM_SCORE + SIZE_MATCH_BONUS, MatchBand.STRONG)


@pytest.mark.parametrize(
    ("message", "sku"),
    [
        ("1 peti chai patti 500 gram", TEA),
        ("2 peti washing powder 500 gm", DETERGENT),
        ("1 peti tea 1 litre", TEA),  # a volume against a weight
        ("5 peti biscuit 100 wala", BISCUIT),
    ],
)
def test_conflicting_size_subtracts_thirty(message, sku):
    result = match(message)

    # 60 - 30 = 30: still at the proposal threshold, in the weak band.
    assert proposed(result) == (sku, 30, MatchBand.WEAK)
    assert any("does not match" in reason for reason in result.proposal.reasons)


def test_size_is_counted_once_however_often_it_is_stated():
    result = match("2 peti washing powder 1 kilo 1kg")

    assert scores(result)[DETERGENT] == SYNONYM_SCORE + SIZE_MATCH_BONUS


def test_sizes_that_disagree_with_each_other_change_nothing():
    result = match("1 peti chai patti 250 gram 500 gram")

    assert scores(result)[TEA] == SYNONYM_SCORE
    assert any("disagree" in reason for reason in result.candidates[0].reasons)


def test_chhota_is_not_a_size():
    result = match("5 peti biscuit chhota")

    assert proposed(result) == (BISCUIT, 60, MatchBand.POSSIBLE)
    assert "Pack size not stated." in result.proposal.reasons
    assert "'chhota' does not identify a pack size." in result.reasons


def test_a_size_alone_can_only_be_very_weak():
    result = match("1 peti 250 gram")

    # Tea's size matches, but with no product words it scores only 25.
    assert scores(result) == {TEA: SIZE_MATCH_BONUS, BISCUIT: 0, DETERGENT: 0}
    assert result.proposal is None


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------


def test_exact_alias_of_this_retailer_scores_ninety_five():
    result = match("3 peti lal dabba", context(aliases={"lal dabba": BISCUIT}))

    assert proposed(result) == (BISCUIT, ALIAS_SCORE, MatchBand.STRONG)
    assert "Saved alias for this retailer: 'lal dabba'." in result.proposal.reasons


def test_alias_is_compared_in_normalised_form():
    result = match("3 peti  LAL   Dabba", context(aliases={"lal dabba": BISCUIT}))

    assert proposed(result) == (BISCUIT, ALIAS_SCORE, MatchBand.STRONG)


def test_alias_must_match_the_whole_phrase():
    result = match("3 peti lal dabba bada", context(aliases={"lal dabba": BISCUIT}))

    assert result.proposal is None


def test_without_this_retailers_alias_there_is_no_match():
    """Aliases are per retailer: another retailer's context has none."""
    result = match("3 peti lal dabba", context())

    assert result.proposal is None


def test_alias_to_a_product_outside_the_catalogue_is_ignored():
    ctx = MatchingContext(products=CATALOGUE, aliases={"lal dabba": uuid4()})

    result = match("3 peti lal dabba", ctx)

    assert result.proposal is None
    assert ALIAS_SCORE not in scores(result).values()


def test_alias_wins_over_a_synonym():
    # The retailer calls detergent "biscuit" (deliberately odd).
    result = match("2 peti biscuit", context(aliases={"biscuit": DETERGENT}))

    assert proposed(result) == (DETERGENT, ALIAS_SCORE, MatchBand.STRONG)
    assert result.alternatives[0].sku == BISCUIT
    assert result.alternatives[0].score == SYNONYM_SCORE


# ---------------------------------------------------------------------------
# Order history
# ---------------------------------------------------------------------------


def test_history_adds_ten_to_a_product_the_retailer_ordered():
    result = match("5 peti biscuit", context(history=(BISCUIT,)))

    expected = (BISCUIT, SYNONYM_SCORE + HISTORY_BONUS, MatchBand.POSSIBLE)
    assert proposed(result) == expected
    assert "Ordered by this retailer in recent orders." in result.proposal.reasons


def test_history_ranks_alternatives_but_does_not_create_a_proposal():
    result = match("5 peti biscuit", context(history=(TEA,)))

    assert proposed(result) == (BISCUIT, 60, MatchBand.POSSIBLE)
    alternatives = [(c.sku, c.score) for c in result.alternatives]
    assert alternatives == [(TEA, 10), (DETERGENT, 0)]


def test_history_alone_is_below_the_threshold():
    result = match("3 peti lal dabba", context(history=(BISCUIT,)))

    assert scores(result)[BISCUIT] == HISTORY_BONUS
    assert result.proposal is None


def test_no_history_means_no_bonus():
    result = match("5 peti biscuit", context())

    assert result.proposal.score == SYNONYM_SCORE
    assert not any("recent orders" in reason for reason in result.proposal.reasons)


# ---------------------------------------------------------------------------
# Ties, thresholds and unmatched lines
# ---------------------------------------------------------------------------


def test_tied_top_scores_give_no_proposal():
    result = match("2 peti biscuit chai patti")

    assert result.proposal is None
    assert scores(result)[BISCUIT] == scores(result)[TEA] == SYNONYM_SCORE
    assert any(reason.startswith("Tied at 60") for reason in result.reasons)
    assert [c.sku for c in result.alternatives] == [BISCUIT, TEA]


def test_a_size_can_break_a_tie():
    result = match("2 peti biscuit chai patti 250 gram")

    assert proposed(result) == (TEA, 85, MatchBand.STRONG)


def test_unknown_phrase_gives_no_proposal_but_keeps_every_candidate():
    result = match("3 peti lal dabba")

    assert result.proposal is None
    assert len(result.candidates) == len(CATALOGUE)
    assert [c.sku for c in result.alternatives] == [BISCUIT, DETERGENT]
    assert f"No product scored {PROPOSAL_THRESHOLD} or more; choose one." in (
        result.reasons
    )


def test_line_without_product_words_is_kept_with_a_reason():
    result = match("5 peti")

    assert result.proposal is None
    assert "The line has no product words." in result.reasons
    assert len(result.candidates) == len(CATALOGUE)


def test_empty_catalogue():
    result = match("5 peti biscuit", MatchingContext(products=()))

    assert result.proposal is None
    assert result.candidates == ()
    assert "There are no active products to match." in result.reasons


def test_every_line_gets_a_result_in_order():
    extraction = extract_lines("5 peti biscuit, 3 peti lal dabba, 5 peti")

    results = match_extraction(extraction, context())

    assert [r.line_number for r in results] == [1, 2, 3]
    assert [r.proposal is not None for r in results] == [True, False, False]


def test_alternatives_are_ordered_by_score_then_sku():
    # Biscuit has the product word but is not in this history: 60. Detergent
    # and tea have only the history bonus: tied at 10, so SKU order decides.
    result = match("5 peti biscuit", context(history=(DETERGENT, TEA)))

    assert [(c.sku, c.score) for c in result.candidates] == [
        (BISCUIT, 60),
        (DETERGENT, 10),
        (TEA, 10),
    ]
    assert [c.sku for c in result.alternatives] == [DETERGENT, TEA]


# ---------------------------------------------------------------------------
# Scores and bands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (0, MatchBand.VERY_WEAK),
        (29, MatchBand.VERY_WEAK),
        (30, MatchBand.WEAK),
        (59, MatchBand.WEAK),
        (60, MatchBand.POSSIBLE),
        (84, MatchBand.POSSIBLE),
        (85, MatchBand.STRONG),
        (100, MatchBand.STRONG),
    ],
)
def test_band_boundaries(score, band):
    assert band_for(score) == band


@pytest.mark.parametrize("score", [-1, 101])
def test_band_refuses_scores_outside_the_range(score):
    with pytest.raises(ValueError):
        band_for(score)


def test_clamp():
    assert clamp(SIZE_CONFLICT_PENALTY) == 0
    assert clamp(ALIAS_SCORE + HISTORY_BONUS) == 100
    assert clamp(42) == 42


def test_scores_never_go_below_zero():
    # Biscuit has no product word here and its size conflicts: 0 - 30 -> 0.
    result = match("1 peti chai patti 250 gram")

    assert scores(result)[BISCUIT] == 0


def test_alias_with_history_is_clamped_to_one_hundred():
    ctx = context(aliases={"lal dabba": BISCUIT}, history=(BISCUIT,))

    result = match("3 peti lal dabba", ctx)

    assert proposed(result) == (BISCUIT, 100, MatchBand.STRONG)


def test_candidate_score_is_bounded():
    with pytest.raises(ValidationError):
        Candidate(
            product_id=uuid4(),
            sku=BISCUIT,
            name="x",
            score=101,
            band=MatchBand.STRONG,
            weak_match=False,
            reasons=(),
        )


def test_weak_match_flag_covers_weak_and_very_weak():
    result = match("1 peti chai patti 500 gram", context(history=(BISCUIT,)))

    flags = {c.sku: (c.band, c.weak_match) for c in result.candidates}
    assert flags[TEA] == (MatchBand.WEAK, True)  # 30
    # 0 - 30 + 10 = -20, clamped to 0 (clamping applies to the total).
    assert flags[BISCUIT] == (MatchBand.VERY_WEAK, True)
    assert match("5 peti biscuit").proposal.weak_match is False


# ---------------------------------------------------------------------------
# Matching never decides units or changes its input
# ---------------------------------------------------------------------------


def test_matching_has_no_unit_or_quantity_decision():
    for model in (LineMatch, Candidate):
        fields = set(model.model_fields)
        for decision in ("unit", "unit_confirmed", "quantity", "quantity_cases"):
            assert decision not in fields


def test_peti_stays_an_unconfirmed_hint_after_matching():
    extraction = extract_lines(DEMO)

    match_extraction(extraction, context())

    for line in extraction.lines:
        assert line.unit is not None and line.unit.text == "peti"
        assert line.unit_hint == UnitHint.CASE_WORD


def test_matching_does_not_change_the_extraction_or_message():
    message = str(DEMO)
    extraction = extract_lines(message)
    before = extraction.model_dump()

    match_extraction(extraction, context())

    assert extraction.model_dump() == before
    assert extraction.message == DEMO
    assert message == DEMO


def test_matching_is_deterministic():
    extraction = extract_lines(DEMO)

    assert match_extraction(extraction, context()) == match_extraction(
        extraction, context()
    )
