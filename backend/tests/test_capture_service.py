"""Capture service: creation, review, discard and submission.

Runs against the dedicated test database (db_session rolls everything back)
with the seeded client data only. Orders are created through the real
retailer order service; nothing is added to the seed.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    CaptureStatus,
    Order,
    OrderCapture,
    OrderEvent,
    OrderSource,
    OrderStatus,
    Product,
    Retailer,
    RetailerProductAlias,
    User,
    UserRole,
)
from app.services import capture_service
from app.services.capture_interpreter import InvalidMessageError
from app.services.capture_service import (
    AliasConflictError,
    AliasNotPermittedError,
    CaptureActor,
    CaptureLinesInvalidError,
    CaptureNotFoundError,
    CaptureNotReadyError,
    CaptureRequestConflictError,
    CaptureStateError,
    InvalidCaptureRequestError,
    InvalidReviewError,
    LineDecision,
    NoOrderLinesError,
    RetailerInactiveError,
    RetailerNotFoundError,
    StaleCaptureVersionError,
)
from app.services.order_service import LineIssueCode
from app.services.retailer_order_service import (
    IdempotencyConflictError,
    OrderLineInput,
    submit_order,
)
from seed import seed_all

DEMO = (
    "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, "
    "chai patti 250 wala 1 peti"
)
BISCUIT, DETERGENT, TEA = "SKU-BS-075", "SKU-DT-1KG", "SKU-TE-250"


@pytest.fixture
def data(db_session):
    seed_all(db_session)
    db_session.flush()

    def retailer(name: str) -> Retailer:
        return db_session.scalar(select(Retailer).where(Retailer.business_name == name))

    desk = db_session.scalar(select(User).where(User.phone_number == "+919000000010"))
    sharma = retailer("Sharma General Store")
    gupta = retailer("Gupta Kirana Store")
    products = {p.sku: p for p in db_session.scalars(select(Product))}
    return {
        "desk": CaptureActor(user_id=desk.id, role=UserRole.ORDER_DESK),
        "desk_user": desk,
        "sharma": sharma,
        "gupta": gupta,
        "sharma_actor": CaptureActor(
            user_id=sharma.user_id, role=UserRole.RETAILER, retailer_id=sharma.id
        ),
        "gupta_actor": CaptureActor(
            user_id=gupta.user_id, role=UserRole.RETAILER, retailer_id=gupta.id
        ),
        "products": products,
    }


def create(
    db_session, data, message=DEMO, *, actor=None, retailer=None, request_id=None
):
    actor = actor or data["desk"]
    retailer_id = None
    if actor.is_desk:
        retailer_id = (retailer or data["sharma"]).id
    return capture_service.create_capture(
        db_session,
        actor,
        message=message,
        client_request_id=request_id or uuid4(),
        retailer_id=retailer_id,
    ).capture


def review(db_session, data, capture, line_number, *, actor=None, **decision):
    return capture_service.review_line(
        db_session,
        actor or data["desk"],
        capture.id,
        line_number,
        LineDecision(**decision),
        expected_version=capture.version,
    )


def confirm_unit(db_session, data, capture, number, quantity, unit="case"):
    return review(
        db_session,
        data,
        capture,
        number,
        action="confirm_unit",
        unit=unit,
        quantity=quantity,
    )


def include(db_session, data, capture, number, sku):
    """Include with a product only: the unit must already be confirmed."""
    return review(
        db_session,
        data,
        capture,
        number,
        action="include",
        product_id=product_id(data, sku),
    )


def product_id(data, sku):
    return data["products"][sku].id


def line(capture, number):
    return next(item for item in capture.lines if item.line_number == number)


def ready_demo(db_session, data, *, actor=None):
    """The demo capture reviewed the way the desk would: units confirmed as
    cases, biscuit and detergent included, tea excluded (out of stock)."""
    capture = create(db_session, data)
    for number, sku in ((1, BISCUIT), (2, DETERGENT)):
        capture = review(
            db_session,
            data,
            capture,
            number,
            actor=actor,
            action="include",
            product_id=product_id(data, sku),
            unit="case",
            quantity=line(capture, number).suggested_quantity,
        )
    capture = review(
        db_session,
        data,
        capture,
        3,
        actor=actor,
        action="exclude",
        reason="Tea is out of stock; retailer will reorder.",
    )
    assert capture.status == CaptureStatus.READY
    return capture


def submit(db_session, data, capture, *, actor=None, request_id=None):
    return capture_service.submit_capture(
        db_session,
        actor or data["desk"],
        capture.id,
        expected_version=capture.version,
        client_request_id=request_id or uuid4(),
    )


def count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_create_demonstration_capture(db_session, data):
    capture = create(db_session, data)

    assert capture.status == CaptureStatus.NEEDS_REVIEW
    assert capture.version == 1
    assert capture.original_message == DEMO
    assert capture.interpreter == "rules@1"
    assert capture.retailer_id == data["sharma"].id
    assert capture.created_by_user_id == data["desk"].user_id
    assert "message" not in capture.extracted_data
    assert [
        (item.original_text, item.product_text, item.unit_text, item.suggested_quantity)
        for item in capture.lines
    ] == [
        ("5 peti biscuit chhota", "biscuit chhota", "peti", 5),
        ("2 peti washing powder 1 kilo", "washing powder 1 kilo", "peti", 2),
        ("chai patti 250 wala 1 peti", "chai patti 250 wala", "peti", 1),
    ]
    for item in capture.lines:
        assert DEMO[item.start_offset : item.end_offset] == item.original_text
        assert item.unit_hint == "case_word"
        # "peti" is a hint only: nothing is confirmed or decided.
        assert item.unit_confirmed is False
        assert item.confirmed_unit is None
        assert item.quantity_cases is None
        assert item.resolution == "pending"
        assert len(item.candidates) == 3
    assert [
        (item.proposed_product_id, item.proposed_score) for item in capture.lines
    ] == [
        (product_id(data, BISCUIT), 60),
        (product_id(data, DETERGENT), 85),
        (product_id(data, TEA), 85),
    ]


def test_retailer_captures_for_itself_only(db_session, data):
    capture = create(db_session, data, actor=data["sharma_actor"])

    assert capture.retailer_id == data["sharma"].id
    with pytest.raises(InvalidCaptureRequestError):
        capture_service.create_capture(
            db_session,
            data["sharma_actor"],
            message=DEMO,
            client_request_id=uuid4(),
            retailer_id=data["gupta"].id,
        )


def test_desk_must_name_an_existing_active_retailer(db_session, data):
    with pytest.raises(InvalidCaptureRequestError):
        capture_service.create_capture(
            db_session, data["desk"], message=DEMO, client_request_id=uuid4()
        )
    with pytest.raises(RetailerNotFoundError):
        capture_service.create_capture(
            db_session,
            data["desk"],
            message=DEMO,
            client_request_id=uuid4(),
            retailer_id=uuid4(),
        )
    data["gupta"].is_active = False
    db_session.flush()
    with pytest.raises(RetailerInactiveError):
        create(db_session, data, retailer=data["gupta"])


def test_creation_is_idempotent_per_request_id(db_session, data):
    request_id = uuid4()
    first = create(db_session, data, request_id=request_id)

    again = capture_service.create_capture(
        db_session,
        data["desk"],
        message=DEMO,
        client_request_id=request_id,
        retailer_id=data["sharma"].id,
    )

    assert again.created is False
    assert again.capture.id == first.id
    assert count(db_session, OrderCapture) == 1
    with pytest.raises(CaptureRequestConflictError):
        create(db_session, data, "2 peti surf", request_id=request_id)


@pytest.mark.parametrize("message", ["bhaiya namaste", "!!! , ji"])
def test_message_without_order_lines_is_refused_and_not_stored(
    db_session, data, message
):
    with pytest.raises(NoOrderLinesError):
        create(db_session, data, message)

    assert count(db_session, OrderCapture) == 0


def test_blank_message_is_refused(db_session, data):
    with pytest.raises(InvalidMessageError):
        create(db_session, data, "   ")

    assert count(db_session, OrderCapture) == 0


def test_unknown_product_phrase_is_kept_without_a_proposal(db_session, data):
    capture = create(db_session, data, "3 peti lal dabba")

    (only,) = capture.lines
    assert only.product_text == "lal dabba"
    assert only.proposed_product_id is None
    assert len(only.candidates) == 3
    assert any("choose one" in reason for reason in only.match_reasons)


# ---------------------------------------------------------------------------
# Review: units and products are independent
# ---------------------------------------------------------------------------


def test_including_a_product_does_not_confirm_its_unit(db_session, data):
    capture = create(db_session, data)

    with pytest.raises(InvalidReviewError, match="Confirm the unit"):
        review(
            db_session,
            data,
            capture,
            1,
            action="include",
            product_id=product_id(data, BISCUIT),
        )

    assert line(capture, 1).unit_confirmed is False
    assert line(capture, 1).resolution == "pending"
    assert capture.version == 1


def test_confirming_a_unit_does_not_choose_a_product(db_session, data):
    capture = create(db_session, data)

    capture = confirm_unit(db_session, data, capture, 1, 5)

    first = line(capture, 1)
    assert (first.unit_confirmed, first.confirmed_unit, first.confirmed_quantity) == (
        True,
        "case",
        5,
    )
    assert first.unit_confirmed_by_user_id == data["desk"].user_id
    assert first.resolution == "pending"
    assert first.selected_product_id is None
    assert first.quantity_cases is None
    assert capture.status == CaptureStatus.NEEDS_REVIEW
    assert capture.version == 2


def test_include_after_confirming_the_unit(db_session, data):
    capture = create(db_session, data)
    capture = confirm_unit(db_session, data, capture, 1, 5)

    capture = include(db_session, data, capture, 1, BISCUIT)

    first = line(capture, 1)
    assert first.resolution == "included"
    assert first.quantity_cases == 5
    assert first.selected_product_id == product_id(data, BISCUIT)


@pytest.mark.parametrize(
    ("sku", "pieces", "cases"),
    [(BISCUIT, 192, 2), (BISCUIT, 96, 1), (DETERGENT, 40, 2), (TEA, 400, 10)],
)
def test_pieces_convert_to_whole_cases_of_the_selected_product(
    db_session, data, sku, pieces, cases
):
    capture = create(db_session, data)

    capture = review(
        db_session,
        data,
        capture,
        1,
        action="include",
        product_id=product_id(data, sku),
        unit="piece",
        quantity=pieces,
    )

    first = line(capture, 1)
    assert (first.confirmed_unit, first.confirmed_quantity) == ("piece", pieces)
    assert first.quantity_cases == cases


def test_pieces_that_are_not_whole_cases_are_refused(db_session, data):
    capture = create(db_session, data)

    with pytest.raises(InvalidReviewError, match="not a whole number of cases"):
        review(
            db_session,
            data,
            capture,
            1,
            action="include",
            product_id=product_id(data, BISCUIT),
            unit="piece",
            quantity=100,
        )


def test_changing_the_product_reconverts_pieces(db_session, data):
    capture = create(db_session, data)
    capture = confirm_unit(db_session, data, capture, 1, 480, unit="piece")

    biscuit = include(db_session, data, capture, 1, BISCUIT)
    assert line(biscuit, 1).quantity_cases == 5  # 480 / 96
    detergent = include(db_session, data, biscuit, 1, DETERGENT)
    assert line(detergent, 1).quantity_cases == 24  # 480 / 20


def test_inactive_product_cannot_be_included(db_session, data):
    capture = create(db_session, data)
    data["products"][TEA].status = "inactive"
    db_session.flush()

    with pytest.raises(InvalidReviewError, match="not available"):
        review(
            db_session,
            data,
            capture,
            3,
            action="include",
            product_id=product_id(data, TEA),
            unit="case",
            quantity=1,
        )


def test_exclusion_needs_a_reason(db_session, data):
    capture = create(db_session, data)

    for reason in (None, "", "  ab  ", "x" * 501):
        with pytest.raises(InvalidReviewError):
            review(db_session, data, capture, 3, action="exclude", reason=reason)

    capture = review(
        db_session, data, capture, 3, action="exclude", reason="  Out of stock.  "
    )
    assert line(capture, 3).resolution == "excluded"
    assert line(capture, 3).exclusion_reason == "Out of stock."


def test_fields_that_do_not_belong_to_the_action_are_refused(db_session, data):
    capture = create(db_session, data)

    with pytest.raises(InvalidReviewError, match="does not take"):
        review(
            db_session,
            data,
            capture,
            1,
            action="exclude",
            reason="Not needed",
            product_id=product_id(data, BISCUIT),
        )


def test_ready_state(db_session, data):
    capture = ready_demo(db_session, data)
    assert capture.status == CaptureStatus.READY

    # Undo one decision: back to needs_review.
    capture = review(db_session, data, capture, 2, action="reset")
    assert capture.status == CaptureStatus.NEEDS_REVIEW
    assert line(capture, 2).selected_product_id is None
    # The unit decision is kept; only the line decision was undone.
    assert line(capture, 2).unit_confirmed is True


def test_all_lines_excluded_is_not_ready(db_session, data):
    capture = create(db_session, data, "3 peti lal dabba")

    capture = review(db_session, data, capture, 1, action="exclude", reason="Not ours")

    assert capture.status == CaptureStatus.NEEDS_REVIEW


def test_included_line_keeps_its_unit(db_session, data):
    capture = ready_demo(db_session, data)

    with pytest.raises(InvalidReviewError):
        review(db_session, data, capture, 1, action="clear_unit")


def test_stale_version_is_refused(db_session, data):
    capture = create(db_session, data)
    review(db_session, data, capture, 1, action="confirm_unit", unit="case", quantity=5)

    with pytest.raises(StaleCaptureVersionError) as error:
        capture_service.review_line(
            db_session,
            data["desk"],
            capture.id,
            2,
            LineDecision(action="confirm_unit", unit="case", quantity=2),
            expected_version=1,
        )
    assert error.value.current_version == 2


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def test_retailers_see_only_their_own_captures(db_session, data):
    sharma_capture = create(db_session, data, actor=data["sharma_actor"])
    create(db_session, data, "2 peti surf", actor=data["gupta_actor"])

    assert (
        capture_service.get_capture(
            db_session, data["sharma_actor"], sharma_capture.id
        ).id
        == sharma_capture.id
    )
    with pytest.raises(CaptureNotFoundError):
        capture_service.get_capture(db_session, data["gupta_actor"], sharma_capture.id)
    with pytest.raises(CaptureNotFoundError):
        review(
            db_session,
            data,
            sharma_capture,
            1,
            actor=data["gupta_actor"],
            action="reset",
        )

    sharma_page = capture_service.list_captures(db_session, data["sharma_actor"])
    desk_page = capture_service.list_captures(db_session, data["desk"])
    assert [c.retailer_id for c in sharma_page.captures] == [data["sharma"].id]
    assert desk_page.total == 2


def test_order_desk_reviews_retailer_captures(db_session, data):
    capture = create(db_session, data, actor=data["sharma_actor"])

    capture = confirm_unit(db_session, data, capture, 1, 5)

    assert line(capture, 1).unit_confirmed_by_user_id == data["desk"].user_id


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def test_submit_creates_a_pending_ai_text_order_with_existing_pricing(db_session, data):
    biscuit, detergent = data["products"][BISCUIT], data["products"][DETERGENT]
    stock_before = (biscuit.stock_quantity, detergent.stock_quantity)
    outstanding_before = data["sharma"].outstanding_balance
    capture = ready_demo(db_session, data)

    outcome = submit(db_session, data, capture)

    order = outcome.order
    assert outcome.created is True
    assert order.status == OrderStatus.PENDING_REVIEW
    assert order.source == OrderSource.AI_TEXT
    assert order.retailer_id == data["sharma"].id
    # Priced by the shared pricing service: 5 x 777.60 and 2 x 2030.00, no
    # scheme reached (biscuit needs 10 cases, detergent 5).
    assert [
        (item.sku, item.quantity, item.free_quantity, item.unit_price, item.line_total)
        for item in order.items
    ] == [
        (BISCUIT, 5, 0, Decimal("777.60"), Decimal("3888.00")),
        (DETERGENT, 2, 0, Decimal("2030.00"), Decimal("4060.00")),
    ]
    assert order.grand_total == Decimal("7948.00")

    # Nothing deducted: only the desk's confirmation does that.
    db_session.refresh(biscuit)
    db_session.refresh(detergent)
    db_session.refresh(data["sharma"])
    assert (biscuit.stock_quantity, detergent.stock_quantity) == stock_before
    assert data["sharma"].outstanding_balance == outstanding_before

    capture = outcome.capture
    assert capture.status == CaptureStatus.SUBMITTED
    assert capture.order_id == order.id
    assert capture.submitted_by_user_id == data["desk"].user_id
    assert capture.submitted_at is not None

    (event,) = db_session.scalars(
        select(OrderEvent).where(OrderEvent.order_id == order.id)
    ).all()
    assert event.event_type == "submitted"
    assert event.actor_user_id == data["desk"].user_id
    assert event.details["capture_id"] == str(capture.id)


def test_scheme_is_applied_by_the_existing_engine(db_session, data):
    capture = create(db_session, data, "10 peti biscuit")
    capture = review(
        db_session,
        data,
        capture,
        1,
        action="include",
        product_id=product_id(data, BISCUIT),
        unit="case",
        quantity=10,
    )

    (item,) = submit(db_session, data, capture).order.items

    assert (item.quantity, item.free_quantity) == (10, 1)
    assert item.scheme_name == "Buy 10 cases, get 1 free"


def test_out_of_stock_line_blocks_submission_until_excluded(db_session, data):
    capture = create(db_session, data)
    for number, sku in ((1, BISCUIT), (2, DETERGENT), (3, TEA)):
        capture = review(
            db_session,
            data,
            capture,
            number,
            action="include",
            product_id=product_id(data, sku),
            unit="case",
            quantity=line(capture, number).suggested_quantity,
        )

    with pytest.raises(CaptureLinesInvalidError) as error:
        submit(db_session, data, capture)

    (issue,) = error.value.issues
    assert (issue.line_number, issue.code) == (3, LineIssueCode.INSUFFICIENT_STOCK)
    assert count(db_session, Order) == 0


def test_submission_requires_a_ready_capture(db_session, data):
    capture = create(db_session, data)
    capture = confirm_unit(db_session, data, capture, 1, 5)

    with pytest.raises(CaptureNotReadyError) as error:
        submit(db_session, data, capture)

    assert error.value.unresolved_lines == (1, 2, 3)
    assert count(db_session, Order) == 0


def test_submission_requires_the_current_version(db_session, data):
    capture = ready_demo(db_session, data)

    with pytest.raises(StaleCaptureVersionError):
        capture_service.submit_capture(
            db_session,
            data["desk"],
            capture.id,
            expected_version=capture.version - 1,
            client_request_id=uuid4(),
        )


def test_repeated_submission_returns_the_same_order(db_session, data):
    capture = ready_demo(db_session, data)
    first = submit(db_session, data, capture)

    again = capture_service.submit_capture(
        db_session,
        data["desk"],
        capture.id,
        expected_version=first.capture.version,
        client_request_id=uuid4(),
    )

    assert again.created is False
    assert again.order.id == first.order.id
    assert count(db_session, Order) == 1


def test_request_id_of_another_order_conflicts(db_session, data):
    request_id = uuid4()
    submit_order(
        db_session,
        data["sharma"],
        [OrderLineInput(product_id=product_id(data, BISCUIT), quantity=1)],
        client_request_id=request_id,
    )
    capture = ready_demo(db_session, data)

    with pytest.raises(IdempotencyConflictError):
        submit(db_session, data, capture, request_id=request_id)


def test_retailer_can_submit_its_own_capture(db_session, data):
    capture = ready_demo(db_session, data, actor=data["sharma_actor"])

    outcome = submit(db_session, data, capture, actor=data["sharma_actor"])

    assert outcome.order.status == OrderStatus.PENDING_REVIEW
    assert outcome.capture.submitted_by_user_id == data["sharma"].user_id


def test_no_changes_after_submission_or_discard(db_session, data):
    submitted = submit(db_session, data, ready_demo(db_session, data)).capture
    discarded = capture_service.discard_capture(
        db_session,
        data["desk"],
        create(db_session, data).id,
        expected_version=1,
        reason="Duplicate of an earlier message",
    )
    assert discarded.status == CaptureStatus.DISCARDED
    assert discarded.discarded_by_user_id == data["desk"].user_id

    for capture in (submitted, discarded):
        with pytest.raises(CaptureStateError):
            review(db_session, data, capture, 1, action="reset")
        with pytest.raises(CaptureStateError):
            capture_service.discard_capture(
                db_session,
                data["desk"],
                capture.id,
                expected_version=capture.version,
                reason="Second thoughts",
            )
    with pytest.raises(CaptureStateError):
        submit(db_session, data, discarded)


def test_discard_needs_a_reason(db_session, data):
    capture = create(db_session, data)

    with pytest.raises(InvalidReviewError):
        capture_service.discard_capture(
            db_session, data["desk"], capture.id, expected_version=1, reason=" x "
        )


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------


def include_lal_dabba(db_session, data, capture, **flags):
    return review(
        db_session,
        data,
        capture,
        1,
        action="include",
        product_id=product_id(data, BISCUIT),
        unit="case",
        quantity=3,
        **flags,
    )


def active_aliases(db_session, retailer):
    return db_session.scalars(
        select(RetailerProductAlias).where(
            RetailerProductAlias.retailer_id == retailer.id,
            RetailerProductAlias.is_active.is_(True),
        )
    ).all()


def test_alias_is_saved_only_after_explicit_opt_in_and_submission(db_session, data):
    capture = create(db_session, data, "3 peti lal dabba")
    capture = include_lal_dabba(db_session, data, capture, remember_alias=True)
    assert active_aliases(db_session, data["sharma"]) == []

    submit(db_session, data, capture)

    (alias,) = active_aliases(db_session, data["sharma"])
    assert alias.alias_text == "lal dabba"  # the product phrase only
    assert alias.product_id == product_id(data, BISCUIT)
    assert alias.created_by_user_id == data["desk"].user_id
    assert alias.source_capture_line_id == line(capture, 1).id

    # The next message from this retailer is matched by the alias; another
    # retailer's is not.
    for_sharma = create(db_session, data, "2 peti lal dabba")
    for_gupta = create(db_session, data, "2 peti lal dabba", retailer=data["gupta"])
    # Alias 95, plus 10 because the submitted order is now in this retailer's
    # history, clamped to 100.
    assert line(for_sharma, 1).proposed_product_id == product_id(data, BISCUIT)
    assert line(for_sharma, 1).proposed_score == 100
    assert line(for_gupta, 1).proposed_product_id is None


def test_without_opt_in_no_alias_is_saved(db_session, data):
    capture = create(db_session, data, "3 peti lal dabba")
    capture = include_lal_dabba(db_session, data, capture)

    submit(db_session, data, capture)

    assert active_aliases(db_session, data["sharma"]) == []


def test_only_the_order_desk_may_save_an_alias(db_session, data):
    capture = create(db_session, data, "3 peti lal dabba", actor=data["sharma_actor"])

    with pytest.raises(AliasNotPermittedError):
        review(
            db_session,
            data,
            capture,
            1,
            actor=data["sharma_actor"],
            action="include",
            product_id=product_id(data, BISCUIT),
            unit="case",
            quantity=3,
            remember_alias=True,
        )


def test_alias_only_for_a_corrected_line(db_session, data):
    capture = create(db_session, data)

    with pytest.raises(InvalidReviewError, match="corrected"):
        review(
            db_session,
            data,
            capture,
            1,
            action="include",
            product_id=product_id(data, BISCUIT),  # the proposal
            unit="case",
            quantity=5,
            remember_alias=True,
        )


def test_existing_alias_is_never_replaced_silently(db_session, data):
    old = RetailerProductAlias(
        retailer_id=data["sharma"].id,
        product_id=product_id(data, TEA),
        alias_text="lal dabba",
        normalized_alias="lal dabba",
        is_active=True,
        created_by_user_id=data["desk"].user_id,
    )
    db_session.add(old)
    db_session.flush()
    capture = create(db_session, data, "3 peti lal dabba")
    assert line(capture, 1).proposed_product_id == product_id(data, TEA)

    with pytest.raises(AliasConflictError) as error:
        include_lal_dabba(db_session, data, capture, remember_alias=True)
    assert error.value.product_id == product_id(data, TEA)

    capture = include_lal_dabba(
        db_session, data, capture, remember_alias=True, replace_existing_alias=True
    )
    submit(db_session, data, capture)

    db_session.refresh(old)
    (new,) = active_aliases(db_session, data["sharma"])
    assert new.product_id == product_id(data, BISCUIT)
    assert old.is_active is False
    assert old.product_id == product_id(data, TEA)  # history kept
    assert old.deactivated_by_user_id == data["desk"].user_id
    assert old.replaced_by_alias_id == new.id
