"""Database rules for captured messages, capture lines and retailer aliases.

These tests exercise the constraints of migration d8f3b6a2c915 directly,
against the dedicated test database (db_session rolls everything back).
They check that the database itself refuses inconsistent review states,
independently of the service code that normally prevents them.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    OrderCapture,
    OrderCaptureLine,
    Product,
    Retailer,
    RetailerProductAlias,
    User,
)
from app.services.retailer_order_service import OrderLineInput, submit_order
from seed import seed_all

# The client's demonstration message, stored exactly as received.
MESSAGE = (
    "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, "
    "chai patti 250 wala 1 peti"
)
# "5 peti biscuit chhota" and its product phrase "biscuit chhota".
LINE_START, LINE_END = 7, 28
PRODUCT_START, PRODUCT_END = 14, 28
NOW = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)


@pytest.fixture
def data(db_session):
    seed_all(db_session)
    desk = db_session.scalar(select(User).where(User.phone_number == "+919000000010"))
    sharma = db_session.scalar(
        select(Retailer).where(Retailer.business_name == "Sharma General Store")
    )
    gupta = db_session.scalar(
        select(Retailer).where(Retailer.business_name == "Gupta Kirana Store")
    )
    products = {p.sku: p for p in db_session.scalars(select(Product))}
    return {
        "desk": desk,
        "sharma": sharma,
        "gupta": gupta,
        "biscuit": products["SKU-BS-075"],
        "detergent": products["SKU-DT-1KG"],
    }


def capture(data, **overrides) -> OrderCapture:
    # The id is set here so lines can refer to it before anything is flushed.
    values = {
        "id": uuid4(),
        "retailer_id": data["sharma"].id,
        "created_by_user_id": data["desk"].id,
        "channel": "text",
        "original_message": MESSAGE,
        "interpreter": "rules@1",
        "extracted_data": {"interpreter": "rules@1", "lines": []},
        "status": "needs_review",
        "version": 1,
        "client_request_id": uuid4(),
        "request_fingerprint": "0" * 64,
    }
    values.update(overrides)
    return OrderCapture(**values)


def line(parent: OrderCapture, data, **overrides) -> OrderCaptureLine:
    """A line linked by capture_id only.

    Not by the capture relationship: assigning `capture=parent` also appends
    the line to parent.lines in memory, before any flush. When a rejected()
    attempt rolls its savepoint back, the line leaves the session but stays
    in parent.lines (the parent was changed before the savepoint began), and
    every later flush of that parent warns about it and skips it. Linking by
    id keeps each attempt to exactly the objects it adds.
    """
    values = {
        "capture_id": parent.id,
        "line_number": 1,
        "start_offset": LINE_START,
        "end_offset": LINE_END,
        "original_text": MESSAGE[LINE_START:LINE_END],
        "product_start_offset": PRODUCT_START,
        "product_end_offset": PRODUCT_END,
        "product_text": MESSAGE[PRODUCT_START:PRODUCT_END],
        "quantity_text": "5",
        "suggested_quantity": 5,
        "unit_text": "peti",
        "unit_hint": "case_word",
        "proposed_product_id": data["biscuit"].id,
        "proposed_score": 60,
        "match_reasons": ["synonym: biscuit", "Pack size not stated"],
        "candidates": [],
    }
    values.update(overrides)
    return OrderCaptureLine(**values)


def unit_decision(data, unit: str = "case", quantity: int = 5) -> dict:
    """A complete, explicit unit decision by the order desk user."""
    return {
        "unit_confirmed": True,
        "confirmed_unit": unit,
        "confirmed_quantity": quantity,
        "unit_confirmed_by_user_id": data["desk"].id,
        "unit_confirmed_at": NOW,
    }


def included(parent, data, **overrides) -> OrderCaptureLine:
    """A line a reviewer included with a confirmed case quantity."""
    values = {
        **unit_decision(data),
        "quantity_cases": 5,
        "resolution": "included",
        "selected_product_id": data["biscuit"].id,
        "reviewed_by_user_id": data["desk"].id,
        "reviewed_at": NOW,
    }
    values.update(overrides)
    return line(parent, data, **values)


def alias(data, **overrides) -> RetailerProductAlias:
    values = {
        "retailer_id": data["sharma"].id,
        "product_id": data["biscuit"].id,
        "alias_text": "biscuit chhota",
        "normalized_alias": "biscuit chhota",
        "is_active": True,
        "created_by_user_id": data["desk"].id,
    }
    values.update(overrides)
    return RetailerProductAlias(**values)


def saved(db_session, *objects) -> None:
    db_session.add_all(objects)
    assert all(obj in db_session for obj in objects)
    db_session.flush()


def rejected(db_session, *objects) -> None:
    """Flush exactly `objects` in a savepoint; the database must refuse them."""
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add_all(objects)
        # The objects under test are really part of this flush.
        assert all(obj in db_session for obj in objects)
        db_session.flush()


def an_order(db_session, data):
    return submit_order(
        db_session,
        data["sharma"],
        [OrderLineInput(product_id=data["biscuit"].id, quantity=1)],
        client_request_id=uuid4(),
    ).order


# ---------------------------------------------------------------------------
# Captures
# ---------------------------------------------------------------------------


def test_capture_with_line_and_alias_is_stored_exactly(db_session, data):
    parent = capture(data, original_message="  " + MESSAGE + "\n")
    saved(db_session, parent, line(parent, data))
    db_session.expire_all()

    stored = db_session.get(OrderCapture, parent.id)
    assert stored.original_message == "  " + MESSAGE + "\n"  # never trimmed
    assert stored.version == 1
    (stored_line,) = stored.lines
    assert stored_line.original_text == "5 peti biscuit chhota"
    assert stored_line.product_text == "biscuit chhota"
    assert stored_line.resolution == "pending"
    assert stored_line.unit_confirmed is False

    saved(db_session, alias(data, source_capture_line_id=stored_line.id))


@pytest.mark.parametrize("status", ["processing", "confirmed", "ready_for_review", ""])
def test_unknown_capture_status_is_rejected(db_session, data, status):
    rejected(db_session, capture(data, status=status))


@pytest.mark.parametrize("message", ["", "   ", " \n\t ", "x" * 2001])
def test_blank_or_overlong_message_is_rejected(db_session, data, message):
    rejected(db_session, capture(data, original_message=message))


def test_unknown_channel_is_rejected(db_session, data):
    rejected(db_session, capture(data, channel="voice"))


def test_request_id_is_unique_per_creating_user(db_session, data):
    request_id = uuid4()
    saved(db_session, capture(data, client_request_id=request_id))

    rejected(db_session, capture(data, client_request_id=request_id))
    # Another user may use the same id.
    saved(
        db_session,
        capture(
            data,
            client_request_id=request_id,
            created_by_user_id=data["sharma"].user_id,
        ),
    )


def test_submitted_requires_order_and_submission_record(db_session, data):
    order = an_order(db_session, data)

    rejected(db_session, capture(data, status="submitted"))
    rejected(db_session, capture(data, status="submitted", order_id=order.id))
    # An order link without the submitted status is also inconsistent.
    rejected(
        db_session,
        capture(
            data,
            order_id=order.id,
            submitted_at=NOW,
            submitted_by_user_id=data["desk"].id,
        ),
    )
    saved(
        db_session,
        capture(
            data,
            status="submitted",
            order_id=order.id,
            submitted_at=NOW,
            submitted_by_user_id=data["desk"].id,
        ),
    )


def test_an_order_belongs_to_at_most_one_capture(db_session, data):
    order = an_order(db_session, data)
    submitted = {
        "status": "submitted",
        "order_id": order.id,
        "submitted_at": NOW,
        "submitted_by_user_id": data["desk"].id,
    }
    saved(db_session, capture(data, **submitted))

    rejected(db_session, capture(data, **submitted))


def test_discarded_requires_reason_and_record(db_session, data):
    discarded = {"discarded_at": NOW, "discarded_by_user_id": data["desk"].id}

    rejected(db_session, capture(data, status="discarded", **discarded))
    rejected(
        db_session,
        capture(data, status="discarded", discard_reason="  ab  ", **discarded),
    )
    # A complete discard record without the discarded status.
    rejected(db_session, capture(data, discard_reason="Duplicate message", **discarded))
    saved(
        db_session,
        capture(
            data, status="discarded", discard_reason="Duplicate message", **discarded
        ),
    )


def test_version_must_be_positive(db_session, data):
    rejected(db_session, capture(data, version=0))


# ---------------------------------------------------------------------------
# Capture lines
# ---------------------------------------------------------------------------


def test_line_numbers_are_unique_within_a_capture(db_session, data):
    parent = capture(data)
    saved(db_session, parent, line(parent, data))

    rejected(db_session, line(parent, data))
    saved(db_session, line(parent, data, line_number=2))


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_offset": -1},
        {"end_offset": LINE_START},
        {"line_number": 0},
        # Product phrase outside the line, or partly missing.
        {"product_start_offset": LINE_START - 1},
        {"product_end_offset": LINE_END + 1},
        {"product_text": None},
        {"product_start_offset": None},
        {"suggested_quantity": 0},
        {"unit_hint": "kilo"},
    ],
)
def test_malformed_interpretation_is_rejected(db_session, data, overrides):
    parent = capture(data)
    saved(db_session, parent)

    rejected(db_session, line(parent, data, **overrides))


def test_a_line_without_a_product_phrase_is_allowed(db_session, data):
    parent = capture(data)
    saved(
        db_session,
        parent,
        line(
            parent,
            data,
            product_text=None,
            product_start_offset=None,
            product_end_offset=None,
            proposed_product_id=None,
            proposed_score=None,
        ),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"proposed_score": None},  # proposal without a score
        {"proposed_product_id": None},  # score without a proposal
        {"proposed_score": 101},
        {"proposed_score": -1},
    ],
)
def test_proposal_and_score_go_together(db_session, data, overrides):
    parent = capture(data)
    saved(db_session, parent)

    rejected(db_session, line(parent, data, **overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        # Confirmed without the unit, the quantity, a confirmer or a time.
        {"unit_confirmed": True},
        {"unit_confirmed": True, "confirmed_unit": "case", "confirmed_quantity": 5},
        # A unit or quantity without confirmation.
        {"confirmed_unit": "case"},
        {"confirmed_quantity": 5},
    ],
)
def test_unit_confirmation_is_complete(db_session, data, overrides):
    parent = capture(data)
    saved(db_session, parent)

    rejected(db_session, line(parent, data, **overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"confirmed_unit": "box"},
        {"confirmed_unit": "peti"},  # never stored as a unit decision
        {"confirmed_quantity": 0},
        {"confirmed_quantity": 1_000_001},
    ],
)
def test_unit_decision_values_are_valid(db_session, data, overrides):
    parent = capture(data)
    saved(db_session, parent)

    rejected(db_session, line(parent, data, **{**unit_decision(data), **overrides}))


@pytest.mark.parametrize("quantity_cases", [5, 0, 10_001])
def test_case_quantity_exists_only_on_included_lines(db_session, data, quantity_cases):
    parent = capture(data)
    saved(db_session, parent)

    rejected(
        db_session,
        line(parent, data, **unit_decision(data), quantity_cases=quantity_cases),
    )


@pytest.mark.parametrize("quantity_cases", [0, 10_001])
def test_included_case_quantity_is_in_range(db_session, data, quantity_cases):
    parent = capture(data)
    saved(db_session, parent)

    rejected(db_session, included(parent, data, quantity_cases=quantity_cases))


@pytest.mark.parametrize(("unit", "quantity"), [("case", 5), ("piece", 480)])
def test_unit_can_be_confirmed_while_the_line_is_still_pending(
    db_session, data, unit, quantity
):
    """Confirming "peti = cases" does not accept a product (independent)."""
    parent = capture(data)
    saved(
        db_session,
        parent,
        line(parent, data, **unit_decision(data, unit=unit, quantity=quantity)),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        # Included without a confirmed unit.
        {
            "unit_confirmed": False,
            "confirmed_unit": None,
            "confirmed_quantity": None,
            "unit_confirmed_by_user_id": None,
            "unit_confirmed_at": None,
        },
        # Included without its case quantity.
        {"quantity_cases": None},
        # Included without a product.
        {"selected_product_id": None},
        # Included without a review record.
        {"reviewed_by_user_id": None, "reviewed_at": None},
    ],
)
def test_included_line_must_be_complete(db_session, data, overrides):
    parent = capture(data)
    saved(db_session, parent)

    rejected(db_session, included(parent, data, **overrides))


def test_pending_line_has_no_selection_or_review(db_session, data):
    parent = capture(data)
    saved(db_session, parent)

    rejected(db_session, line(parent, data, selected_product_id=data["biscuit"].id))
    rejected(
        db_session,
        line(parent, data, reviewed_by_user_id=data["desk"].id, reviewed_at=NOW),
    )


def test_excluded_line_needs_a_reason_of_three_to_five_hundred_characters(
    db_session, data
):
    parent = capture(data)
    saved(db_session, parent)
    review = {"reviewed_by_user_id": data["desk"].id, "reviewed_at": NOW}

    rejected(db_session, line(parent, data, resolution="excluded", **review))
    rejected(
        db_session,
        line(parent, data, resolution="excluded", exclusion_reason=" ab ", **review),
    )
    rejected(
        db_session,
        line(parent, data, resolution="excluded", exclusion_reason="x" * 501, **review),
    )
    # A reason on a line that is not excluded.
    rejected(db_session, line(parent, data, exclusion_reason="Out of stock"))
    saved(
        db_session,
        line(
            parent,
            data,
            resolution="excluded",
            exclusion_reason="Out of stock; retailer will reorder.",
            **review,
        ),
    )


def test_alias_only_for_a_corrected_line(db_session, data):
    parent = capture(data)
    saved(db_session, parent)

    # Selected product equals the proposal: not a correction.
    rejected(db_session, included(parent, data, remember_alias=True))
    # Not included.
    rejected(db_session, line(parent, data, remember_alias=True))
    # Replacement without opting in to an alias.
    rejected(
        db_session,
        included(
            parent,
            data,
            selected_product_id=data["detergent"].id,
            replace_existing_alias=True,
        ),
    )
    # Corrected line: allowed, with or without an explicit replacement.
    saved(
        db_session,
        included(
            parent,
            data,
            selected_product_id=data["detergent"].id,
            remember_alias=True,
            replace_existing_alias=True,
        ),
    )


def test_alias_allowed_when_there_was_no_proposal(db_session, data):
    parent = capture(data)
    saved(
        db_session,
        parent,
        included(
            parent,
            data,
            proposed_product_id=None,
            proposed_score=None,
            remember_alias=True,
        ),
    )


# ---------------------------------------------------------------------------
# Retailer aliases
# ---------------------------------------------------------------------------


def test_one_active_alias_per_retailer_and_phrase(db_session, data):
    saved(db_session, alias(data))

    rejected(db_session, alias(data, product_id=data["detergent"].id))
    # The same phrase for another retailer is independent.
    saved(db_session, alias(data, retailer_id=data["gupta"].id))


def test_replaced_alias_is_kept_inactive_with_its_history(db_session, data):
    old = alias(data)
    saved(db_session, old)

    # Deactivate first, then add the replacement, then link them. The old
    # row keeps its product, so the history shows what it used to mean.
    old.is_active = False
    old.deactivated_at = NOW
    old.deactivated_by_user_id = data["desk"].id
    db_session.flush()
    new = alias(data, product_id=data["detergent"].id)
    saved(db_session, new)
    old.replaced_by_alias_id = new.id
    db_session.flush()

    db_session.expire_all()
    rows = list(
        db_session.scalars(
            select(RetailerProductAlias).where(
                RetailerProductAlias.retailer_id == data["sharma"].id
            )
        )
    )
    assert len(rows) == 2
    (active,) = [row for row in rows if row.is_active]
    (inactive,) = [row for row in rows if not row.is_active]
    assert active.product_id == data["detergent"].id
    assert inactive.replaced_by_alias_id == active.id
    assert inactive.deactivated_by_user_id == data["desk"].id


@pytest.mark.parametrize(
    "overrides",
    [
        # Inactive without a deactivation record.
        {"is_active": False},
        # Deactivation recorded on an active alias.
        {"deactivated_at": NOW},
        {"is_active": False, "deactivated_at": NOW},
        # Blank phrases.
        {"alias_text": "   "},
        {"normalized_alias": ""},
    ],
)
def test_alias_records_are_consistent(db_session, data, overrides):
    rejected(db_session, alias(data, **overrides))


def test_an_active_alias_cannot_point_to_a_replacement(db_session, data):
    first = alias(data, alias_text="lal packet", normalized_alias="lal packet")
    saved(db_session, first)

    rejected(db_session, alias(data, replaced_by_alias_id=first.id))
