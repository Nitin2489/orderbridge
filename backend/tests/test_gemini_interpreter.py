"""Gemini interpreter: grounding, validation, fallback, privacy and matching.

No real API key or network is used: every Gemini answer comes from an
httpx.MockTransport. The database tests use the seeded client data and roll
back.
"""

import json
import logging
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.models import CaptureLineResolution, Product, Retailer, User, UserRole
from app.models.enums import UnitHint
from app.schemas.captures import CaptureDetail
from app.services import capture_service
from app.services.capture_interpreter import (
    CatalogueEntry,
    InterpreterKind,
    RulesInterpreter,
    extract_lines,
)
from app.services.capture_matcher import (
    AI_ONLY_MAX_SCORE,
    AI_SUGGESTION_SCORE,
    ALIAS_SCORE,
    SYNONYM_SCORE,
    CatalogueProduct,
    MatchBand,
    MatchingContext,
    match_extraction,
)
from app.services.capture_service import CaptureActor
from app.services.gemini_interpreter import (
    GEMINI_ENDPOINT,
    GEMINI_INTERPRETER_NAME,
    GeminiInterpreter,
    configured_interpreter,
)
from seed import seed_all

API_KEY = "test-gemini-key-not-real-0123456789"
MODEL = "gemini-test-model"
BISCUIT, DETERGENT, TEA = "SKU-BS-075", "SKU-DT-1KG", "SKU-TE-250"
CATALOGUE = (
    CatalogueEntry(BISCUIT, "Glucose Biscuit 75g"),
    CatalogueEntry(DETERGENT, "Detergent Powder 1kg"),
    CatalogueEntry(TEA, "Tea 250g"),
)
DEMO = (
    "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, "
    "chai patti 250 wala 1 peti"
)
DEMO_LINES = [
    {
        "line_text": "5 peti biscuit chhota",
        "product_text": "biscuit chhota",
        "quantity_text": "5",
        "unit_text": "peti",
        "size_texts": [],
        "catalogue_sku": BISCUIT,
    },
    {
        "line_text": "2 peti washing powder 1 kilo",
        "product_text": "washing powder",
        "quantity_text": "2",
        "unit_text": "peti",
        "size_texts": ["1 kilo"],
        "catalogue_sku": DETERGENT,
    },
    {
        "line_text": "chai patti 250 wala 1 peti",
        "product_text": "chai patti",
        "quantity_text": "1",
        "unit_text": "peti",
        "size_texts": ["250 wala"],
        "catalogue_sku": TEA,
    },
]


def reply(lines=None, *, finish="STOP", text=None, status=200) -> httpx.Response:
    payload = text if text is not None else json.dumps({"lines": lines or []})
    return httpx.Response(
        status,
        json={
            "candidates": [
                {"content": {"parts": [{"text": payload}]}, "finishReason": finish}
            ]
        },
    )


class FakeGemini:
    """Answers every request with one response (or raises one error)."""

    def __init__(self, answer):
        self.answer = answer
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def interpreter(self) -> GeminiInterpreter:
        return GeminiInterpreter(
            api_key=API_KEY,
            model=MODEL,
            timeout_seconds=8,
            transport=httpx.MockTransport(self),
        )


def read(message, answer, catalogue=CATALOGUE):
    fake = FakeGemini(answer)
    return fake.interpreter().extract(message, catalogue=catalogue), fake


def context(
    history: tuple[str, ...] = (), **aliases
) -> tuple[MatchingContext, dict[str, CatalogueProduct]]:
    products = {
        sku: CatalogueProduct(id=uuid4(), sku=sku, name=n) for sku, n in CATALOGUE
    }
    alias_map = {phrase: products[sku].id for phrase, sku in aliases.items()}
    return MatchingContext(
        products=tuple(products.values()),
        aliases=alias_map,
        history_product_ids=frozenset(products[sku].id for sku in history),
    ), products


# ---------------------------------------------------------------------------
# Grounded extraction
# ---------------------------------------------------------------------------


def test_sample_message_is_grounded_and_read_by_the_rules_helpers():
    extraction, _ = read(DEMO, reply(DEMO_LINES))

    assert extraction.interpreter == GEMINI_INTERPRETER_NAME
    assert extraction.kind == InterpreterKind.LANGUAGE_MODEL
    assert extraction.model == MODEL
    assert extraction.ai_fallback is None
    assert [line.span.text for line in extraction.lines] == [
        "5 peti biscuit chhota",
        "2 peti washing powder 1 kilo",
        "chai patti 250 wala 1 peti",
    ]
    for line in extraction.lines:
        assert DEMO[line.span.start : line.span.end] == line.span.text
    assert [line.suggested_quantity for line in extraction.lines] == [5, 2, 1]
    assert [line.unit_hint for line in extraction.lines] == [UnitHint.CASE_WORD] * 3
    assert [line.suggested_sku for line in extraction.lines] == [
        BISCUIT,
        DETERGENT,
        TEA,
    ]
    detergent_size = extraction.lines[1].sizes[0]
    assert (detergent_size.value, detergent_size.unit) == (1, "kg")
    tea_size = extraction.lines[2].sizes[0]
    assert (tea_size.value, tea_size.unit) == (250, None)
    assert [(span.text, span.reason) for span in extraction.ignored] == [
        ("bhaiya", "greeting")
    ]


def test_sample_message_matches_like_the_rules_with_out_of_stock_tea_kept():
    extraction, _ = read(DEMO, reply(DEMO_LINES))
    ctx, _ = context()
    matches = match_extraction(extraction, ctx)

    assert [m.proposal.sku for m in matches] == [BISCUIT, DETERGENT, TEA]
    assert all(len(m.alternatives) == 2 for m in matches)
    # Same scores as the rules: the AI agrees, so it adds a reason, not points.
    assert [m.proposal.score for m in matches] == [60, 85, 85]
    assert "The AI reader also suggested this product." in matches[2].proposal.reasons


def test_hindi_devanagari_message_with_separator_word():
    message = "पाँच पेटी बिस्कुट और दो पेटी चाय पत्ती"
    lines = [
        {"line_text": "पाँच पेटी बिस्कुट", "product_text": "बिस्कुट",
         "quantity_text": "पाँच", "unit_text": "पेटी", "catalogue_sku": BISCUIT},
        {"line_text": "दो पेटी चाय पत्ती", "product_text": "चाय पत्ती",
         "quantity_text": "दो", "unit_text": "पेटी", "catalogue_sku": TEA},
    ]  # fmt: skip
    extraction, _ = read(message, reply(lines))

    assert extraction.ai_fallback is None
    assert [line.suggested_quantity for line in extraction.lines] == [5, 2]
    assert [line.unit_hint for line in extraction.lines] == [UnitHint.CASE_WORD] * 2
    # The rules know no Devanagari product words; the AI signal proposes,
    # weakly, and alternatives are still offered.
    matches = match_extraction(extraction, context()[0])
    assert [m.proposal.sku for m in matches] == [BISCUIT, TEA]
    assert all(m.proposal.score == AI_SUGGESTION_SCORE for m in matches)
    assert all(m.proposal.weak_match for m in matches)
    assert all(len(m.alternatives) == 2 for m in matches)


def test_hinglish_products_in_unusual_order_with_filler_words():
    message = "washing powder 1 kilo wala do peti bhejna, aur biscuit bhi 5 peti please"
    lines = [
        {"line_text": "washing powder 1 kilo wala do peti bhejna",
         "product_text": "washing powder", "quantity_text": "do",
         "unit_text": "peti", "size_texts": ["1 kilo"], "catalogue_sku": DETERGENT},
        {"line_text": "biscuit bhi 5 peti please", "product_text": "biscuit",
         "quantity_text": "5", "unit_text": "peti", "catalogue_sku": BISCUIT},
    ]  # fmt: skip
    extraction, _ = read(message, reply(lines))

    assert extraction.ai_fallback is None
    assert [line.suggested_quantity for line in extraction.lines] == [2, 5]
    assert [line.product.text for line in extraction.lines] == [
        "washing powder",
        "biscuit",
    ]
    matches = match_extraction(extraction, context()[0])
    assert [m.proposal.sku for m in matches] == [DETERGENT, BISCUIT]


def test_brand_word_unknown_to_the_rules_gets_a_weak_ai_proposal():
    message = "surf 2 peti"
    lines = [
        {"line_text": "surf 2 peti", "product_text": "surf", "quantity_text": "2",
         "unit_text": "peti", "catalogue_sku": DETERGENT},
    ]  # fmt: skip
    extraction, _ = read(message, reply(lines))
    rules_match = match_extraction(extract_lines(message), context()[0])[0]
    ai_match = match_extraction(extraction, context()[0])[0]

    assert rules_match.proposal is None
    assert ai_match.proposal.sku == DETERGENT
    assert ai_match.proposal.score == AI_SUGGESTION_SCORE
    assert ai_match.proposal.weak_match is True
    assert "Suggested by the AI reader (a weak signal; check it)." in (
        ai_match.proposal.reasons
    )


def test_incorrect_pack_size_outweighs_the_ai_suggestion():
    message = "surf 500 gram 2 peti"
    lines = [
        {"line_text": message, "product_text": "surf", "quantity_text": "2",
         "unit_text": "peti", "size_texts": ["500 gram"], "catalogue_sku": DETERGENT},
    ]  # fmt: skip
    extraction, _ = read(message, reply(lines))
    match = match_extraction(extraction, context()[0])[0]

    detergent = next(c for c in match.candidates if c.sku == DETERGENT)
    assert detergent.score == AI_SUGGESTION_SCORE - 30
    assert "Pack size '500 gram' does not match 1kg." in detergent.reasons
    assert match.proposal is None  # below the proposal threshold: choose one
    assert len(match.alternatives) == 2


def test_valid_but_wrong_sku_does_not_beat_recognised_words_and_size():
    lines = [dict(DEMO_LINES[1], catalogue_sku=TEA)]
    message = "2 peti washing powder 1 kilo"
    extraction, _ = read(message, reply(lines))
    match = match_extraction(extraction, context()[0])[0]

    assert match.proposal.sku == DETERGENT
    tea = next(c for c in match.candidates if c.sku == TEA)
    assert tea.score < match.proposal.score
    assert "Suggested by the AI reader (a weak signal; check it)." in tea.reasons
    assert TEA in [c.sku for c in match.alternatives]


def ai_line(message, product, sku, size=None):
    """A one-line Gemini answer for a message "<product> [<size>] 2 peti"."""
    return reply(
        [
            {
                "line_text": message,
                "product_text": product,
                "quantity_text": "2",
                "unit_text": "peti",
                "size_texts": [size] if size else [],
                "catalogue_sku": sku,
            }
        ]
    )


def candidate(match, sku):
    return next(c for c in match.candidates if c.sku == sku)


CAP_REASON = "Capped as a weak match: only the AI reader suggested this product."


def test_ai_only_suggestion_with_matching_size_and_history_stays_weak():
    message = "surf 1 kilo 2 peti"
    extraction, _ = read(message, ai_line(message, "surf", DETERGENT, "1 kilo"))
    match = match_extraction(extraction, context(history=(DETERGENT,))[0])[0]

    detergent = candidate(match, DETERGENT)
    # 40 + 25 (size) + 10 (history) = 75, capped to the top of the weak band.
    assert detergent.score == AI_ONLY_MAX_SCORE == 59
    assert detergent.band == MatchBand.WEAK
    assert detergent.weak_match is True
    assert detergent.reasons == (
        "Suggested by the AI reader (a weak signal; check it).",
        "Pack size '1 kilo' matches 1kg.",
        "Ordered by this retailer in recent orders.",
        CAP_REASON,
    )
    assert match.proposal == detergent  # still proposed, as a weak match
    assert len(match.alternatives) == 2


def test_wrong_but_valid_sku_with_matching_size_and_history_stays_weak():
    # "surf" is detergent; Gemini says tea, and the size and history happen
    # to fit tea. It must not look like a possible match.
    message = "surf 250 gram 2 peti"
    extraction, _ = read(message, ai_line(message, "surf", TEA, "250 gram"))
    match = match_extraction(extraction, context(history=(TEA,))[0])[0]

    tea = candidate(match, TEA)
    assert tea.score == AI_ONLY_MAX_SCORE
    assert tea.band == MatchBand.WEAK
    assert tea.weak_match is True
    assert CAP_REASON in tea.reasons
    assert "Pack size '250 gram' matches 250g." in tea.reasons
    assert "Ordered by this retailer in recent orders." in tea.reasons


def test_ai_only_suggestion_below_the_cap_is_not_marked_capped():
    message = "surf 2 peti"
    extraction, _ = read(message, ai_line(message, "surf", DETERGENT))
    match = match_extraction(extraction, context(history=(DETERGENT,))[0])[0]

    detergent = candidate(match, DETERGENT)
    assert detergent.score == AI_SUGGESTION_SCORE + 10
    assert CAP_REASON not in detergent.reasons


def test_recognised_words_outrank_an_ai_only_suggestion_with_bonuses():
    message = "washing powder 2 peti"
    extraction, _ = read(message, ai_line(message, "washing powder", TEA))
    match = match_extraction(extraction, context(history=(TEA,))[0])[0]

    assert match.proposal.sku == DETERGENT
    assert match.proposal.score == SYNONYM_SCORE
    assert candidate(match, TEA).score < SYNONYM_SCORE


def test_recognised_words_with_bonuses_are_not_capped():
    message = "washing powder 1 kilo 2 peti"
    extraction, _ = read(
        message, ai_line(message, "washing powder", DETERGENT, "1 kilo")
    )
    match = match_extraction(extraction, context(history=(DETERGENT,))[0])[0]

    # 60 + 25 + 10: the rules' own evidence, unchanged by the AI agreeing.
    assert match.proposal.sku == DETERGENT
    assert match.proposal.score == 95
    assert match.proposal.band == MatchBand.STRONG
    assert CAP_REASON not in match.proposal.reasons


CONFLICT = "washing powder 250 gram 2 peti"
CONFLICT_REASON = (
    "The words name Detergent Powder 1kg, but its pack size does not match; "
    "the AI reader suggested Tea 250g. Choose one."
)


@pytest.mark.parametrize("history", [(), (TEA,)], ids=["no-history", "tea-history"])
def test_ai_only_suggestion_does_not_replace_a_named_product_with_a_size_conflict(
    history,
):
    extraction, _ = read(CONFLICT, ai_line(CONFLICT, "washing powder", TEA, "250 gram"))
    match = match_extraction(extraction, context(history=history)[0])[0]

    detergent, tea = candidate(match, DETERGENT), candidate(match, TEA)
    # The size-conflict penalty is kept and explained.
    assert detergent.score == SYNONYM_SCORE - 30
    assert "Recognised 'washing powder' as detergent." in detergent.reasons
    assert "Pack size '250 gram' does not match 1kg." in detergent.reasons
    # The AI suggestion stays capped and weak, and is not proposed.
    assert tea.score == AI_ONLY_MAX_SCORE
    assert tea.weak_match is True
    assert match.proposal is None
    assert CONFLICT_REASON in match.reasons
    # Both are offered for a person to choose.
    assert [c.sku for c in match.alternatives] == [TEA, DETERGENT]


def test_size_conflict_without_an_ai_suggestion_is_unchanged():
    match = match_extraction(extract_lines(CONFLICT), context()[0])[0]

    assert match.proposal.sku == DETERGENT
    assert match.proposal.score == SYNONYM_SCORE - 30
    assert not any("AI reader" in reason for reason in match.reasons)


def test_ai_agreeing_with_a_named_product_with_a_size_conflict_is_unchanged():
    extraction, _ = read(
        CONFLICT, ai_line(CONFLICT, "washing powder", DETERGENT, "250 gram")
    )
    match = match_extraction(extraction, context()[0])[0]

    assert match.proposal.sku == DETERGENT
    assert match.proposal.score == SYNONYM_SCORE - 30
    assert "The AI reader also suggested this product." in match.proposal.reasons


def test_named_product_without_conflict_is_still_proposed_over_ai_only():
    message = "washing powder 1 kilo 2 peti"
    extraction, _ = read(message, ai_line(message, "washing powder", TEA, "1 kilo"))
    match = match_extraction(extraction, context(history=(TEA,))[0])[0]

    assert match.proposal.sku == DETERGENT
    assert match.proposal.score == SYNONYM_SCORE + 25
    assert TEA in [c.sku for c in match.alternatives]
    assert not any("Choose one" in reason for reason in match.reasons)


def test_a_saved_alias_outranks_an_ai_only_suggestion_with_bonuses():
    message = "surf 250 gram 2 peti"
    extraction, _ = read(message, ai_line(message, "surf", TEA, "250 gram"))
    ctx, _ = context(history=(TEA,), surf=DETERGENT)
    match = match_extraction(extraction, ctx)[0]

    assert match.proposal.sku == DETERGENT
    assert match.proposal.score == ALIAS_SCORE
    assert candidate(match, TEA).score == AI_ONLY_MAX_SCORE


def test_a_saved_alias_always_beats_the_ai_suggestion():
    message = "surf 2 peti"
    lines = [
        {"line_text": message, "product_text": "surf", "quantity_text": "2",
         "unit_text": "peti", "catalogue_sku": DETERGENT},
    ]  # fmt: skip
    extraction, _ = read(message, reply(lines))
    ctx, _ = context(surf=BISCUIT)
    match = match_extraction(extraction, ctx)[0]

    assert match.proposal.sku == BISCUIT
    assert match.proposal.score == ALIAS_SCORE


def test_fabricated_sku_is_dropped_with_a_note():
    lines = [dict(DEMO_LINES[0], catalogue_sku="SKU-XX-999")]
    extraction, _ = read("5 peti biscuit chhota", reply(lines))

    line = extraction.lines[0]
    assert extraction.ai_fallback is None
    assert line.suggested_sku is None
    assert any("not in the catalogue" in note for note in line.notes)


def test_unknown_unit_word_is_reported_not_guessed():
    message = "biscuit 5 dabba"
    lines = [
        {"line_text": message, "product_text": "biscuit", "quantity_text": "5",
         "unit_text": "dabba", "catalogue_sku": BISCUIT},
    ]  # fmt: skip
    extraction, _ = read(message, reply(lines))

    line = extraction.lines[0]
    assert line.unit is None
    assert line.unit_hint == UnitHint.NONE
    assert "The unit word 'dabba' is not recognised; choose the unit." in line.notes


# ---------------------------------------------------------------------------
# Unusable answers fall back to the rules
# ---------------------------------------------------------------------------

RULES_SPANS = [line.span.text for line in extract_lines(DEMO).lines]


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        pytest.param(reply([]), "empty", id="empty-extraction"),
        pytest.param(reply(DEMO_LINES[:1]), "incomplete", id="partial-extraction"),
        pytest.param(
            reply([dict(DEMO_LINES[0], price="10.00"), *DEMO_LINES[1:]]),
            "invalid_output",
            id="unexpected-line-field",
        ),
        pytest.param(
            reply(text=json.dumps({"lines": DEMO_LINES, "total": 5})),
            "invalid_output",
            id="unexpected-top-level-field",
        ),
        pytest.param(
            reply([dict(DEMO_LINES[0], line_text="10 peti biscuit"), *DEMO_LINES[1:]]),
            "invalid_output",
            id="phrase-not-in-message",
        ),
        pytest.param(
            reply([dict(DEMO_LINES[0], product_text="Parle-G"), *DEMO_LINES[1:]]),
            "invalid_output",
            id="product-not-in-line",
        ),
        pytest.param(
            reply([dict(DEMO_LINES[0], quantity_text=5), *DEMO_LINES[1:]]),
            "invalid_output",
            id="wrong-json-type",
        ),
        pytest.param(reply(text="not json"), "invalid_output", id="malformed-json"),
        pytest.param(
            reply(DEMO_LINES, finish="MAX_TOKENS"), "invalid_output", id="cut-off"
        ),
        pytest.param(
            httpx.Response(200, json={}), "invalid_output", id="no-candidates"
        ),
        pytest.param(httpx.Response(429, json={}), "unavailable", id="rate-limited"),
        pytest.param(
            httpx.Response(500, text="boom"), "unavailable", id="server-error"
        ),
        pytest.param(httpx.ReadTimeout("slow"), "timeout", id="timeout"),
        pytest.param(httpx.ConnectError("down"), "unavailable", id="unreachable"),
    ],
)
def test_unusable_answers_fall_back_to_the_rules(answer, reason):
    extraction, _ = read(DEMO, answer)

    assert extraction.ai_fallback == reason
    assert extraction.interpreter == "rules@1"
    assert extraction.kind == InterpreterKind.RULES
    assert [line.span.text for line in extraction.lines] == RULES_SPANS
    assert all(line.suggested_sku is None for line in extraction.lines)


def test_a_number_inside_a_longer_number_is_not_grounded():
    message = "biscuit 50 peti"
    lines = [
        {"line_text": message, "product_text": "biscuit", "quantity_text": "5",
         "unit_text": "peti"},
    ]  # fmt: skip
    extraction, _ = read(message, reply(lines))

    assert extraction.ai_fallback == "invalid_output"
    assert extraction.lines[0].suggested_quantity == 50


def test_overlapping_or_reordered_lines_fall_back():
    extraction, _ = read(DEMO, reply([DEMO_LINES[1], DEMO_LINES[0], DEMO_LINES[2]]))
    assert extraction.ai_fallback == "invalid_output"


def test_more_lines_than_a_capture_may_hold_fall_back():
    lines = [DEMO_LINES[0]] * 51
    extraction, _ = read(DEMO, reply(lines))
    assert extraction.ai_fallback == "invalid_output"


# ---------------------------------------------------------------------------
# Privacy and secrets
# ---------------------------------------------------------------------------


def test_request_carries_only_the_message_and_product_codes_and_names():
    _, fake = read(DEMO, reply(DEMO_LINES))

    (request,) = fake.requests
    assert str(request.url) == GEMINI_ENDPOINT.format(model=MODEL)
    assert API_KEY not in str(request.url)
    assert request.headers["x-goog-api-key"] == API_KEY
    body = json.loads(request.content)
    user = json.loads(body["contents"][0]["parts"][0]["text"])
    assert set(user) == {"catalogue", "message"}
    assert user["message"] == DEMO
    assert all(set(entry) == {"sku", "name"} for entry in user["catalogue"])
    assert body["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.parametrize(
    "answer",
    [httpx.ReadTimeout("slow"), httpx.Response(500, text=DEMO), reply(text="x")],
)
def test_logs_never_contain_the_key_or_the_message(caplog, answer):
    caplog.set_level(logging.DEBUG)
    read(DEMO, answer)

    assert caplog.records
    for record in caplog.records:
        logged = record.getMessage() + repr(record.__dict__)
        assert API_KEY not in logged
        assert "chai patti" not in logged


def test_repr_hides_the_key():
    assert API_KEY not in repr(FakeGemini(reply()).interpreter())


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def settings(**values) -> Settings:
    base = {
        "app_env": "testing",
        "database_url": "postgresql+psycopg://unused:unused@localhost:5432/unused",
    }
    return Settings(_env_file=None, **{**base, **values})


def test_rules_are_the_default_interpreter():
    assert isinstance(configured_interpreter(settings()), RulesInterpreter)


def test_gemini_is_used_when_configured_with_a_key():
    interpreter = configured_interpreter(
        settings(capture_interpreter="gemini", gemini_api_key=API_KEY)
    )
    assert isinstance(interpreter, GeminiInterpreter)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_gemini_without_a_key_is_refused_at_startup(key):
    with pytest.raises(ValidationError, match="requires GEMINI_API_KEY"):
        settings(capture_interpreter="gemini", gemini_api_key=key)


@pytest.mark.parametrize("model", ["../models/x", "a/b", "", "x" * 65, "model?key=1"])
def test_model_names_that_could_change_the_url_are_refused(model):
    with pytest.raises(ValidationError):
        settings(gemini_model=model)


def test_settings_errors_never_echo_the_key():
    with pytest.raises(ValidationError) as error:
        settings(capture_interpreter="gemini", gemini_api_key=API_KEY, gemini_model="/")
    assert API_KEY not in str(error.value)


# ---------------------------------------------------------------------------
# Through the capture service (database)
# ---------------------------------------------------------------------------


@pytest.fixture
def sharma(db_session):
    seed_all(db_session)
    db_session.flush()
    retailer = db_session.scalar(
        select(Retailer).where(Retailer.business_name == "Sharma General Store")
    )
    desk = db_session.scalar(select(User).where(User.phone_number == "+919000000010"))
    return retailer, CaptureActor(user_id=desk.id, role=UserRole.ORDER_DESK)


def create(db_session, sharma, interpreter, request_id=None):
    retailer, desk = sharma
    return capture_service.create_capture(
        db_session,
        desk,
        message=DEMO,
        client_request_id=request_id or uuid4(),
        retailer_id=retailer.id,
        interpreter=interpreter,
    )


def test_ai_capture_keeps_every_decision_for_a_person(db_session, sharma):
    capture = create(
        db_session, sharma, FakeGemini(reply(DEMO_LINES)).interpreter()
    ).capture
    products = {p.id: p.sku for p in db_session.scalars(select(Product))}

    assert capture.interpreter == GEMINI_INTERPRETER_NAME
    assert capture.extracted_data["model"] == MODEL
    assert [products[line.proposed_product_id] for line in capture.lines] == [
        BISCUIT,
        DETERGENT,
        TEA,  # out of stock, still shown
    ]
    for line in capture.lines:
        assert line.unit_confirmed is False
        assert line.resolution == CaptureLineResolution.PENDING
        assert line.selected_product_id is None
    assert API_KEY not in json.dumps(capture.extracted_data)

    detail = CaptureDetail.from_capture(capture)
    assert detail.interpretation.is_ai_model is True
    assert detail.interpretation.fallback_note is None
    assert detail.can_submit is False


def test_timeout_creates_the_capture_with_the_rules(db_session, sharma):
    capture = create(
        db_session, sharma, FakeGemini(httpx.ReadTimeout("slow")).interpreter()
    ).capture

    assert capture.interpreter == "rules@1"
    assert capture.extracted_data["ai_fallback"] == "timeout"
    assert len(capture.lines) == 3
    detail = CaptureDetail.from_capture(capture)
    assert detail.interpretation.is_ai_model is False
    assert detail.interpretation.fallback_note == (
        "The AI reader did not answer in time. The rule-based reader was used instead."
    )


def test_a_repeated_request_does_not_call_gemini_again(db_session, sharma):
    fake = FakeGemini(reply(DEMO_LINES))
    request_id = uuid4()
    first = create(db_session, sharma, fake.interpreter(), request_id)
    second = create(db_session, sharma, fake.interpreter(), request_id)

    assert first.created is True
    assert second.created is False
    assert second.capture.id == first.capture.id
    assert len(fake.requests) == 1
