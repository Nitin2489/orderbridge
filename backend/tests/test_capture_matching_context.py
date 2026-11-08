"""load_matching_context() against the test database (db_session rolls back).

Only the seeded client data is used. Order history comes from orders placed
through the real retailer order service (and rejected through the real desk
service) inside each test; aliases are rows written in the test. Nothing is
added to the seed.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Product, ProductStatus, Retailer, RetailerProductAlias, User
from app.services.capture_interpreter import extract_lines
from app.services.capture_matcher import (
    HISTORY_ORDER_LIMIT,
    MatchBand,
    load_matching_context,
    match_extraction,
)
from app.services.desk_order_service import reject_order
from app.services.retailer_order_service import OrderLineInput, submit_order
from seed import seed_all

DEMO = (
    "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, "
    "chai patti 250 wala 1 peti"
)
BASE_TIME = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def data(db_session):
    seed_all(db_session)
    db_session.flush()

    def retailer(name: str) -> Retailer:
        return db_session.scalar(select(Retailer).where(Retailer.business_name == name))

    products = {p.sku: p for p in db_session.scalars(select(Product))}
    return {
        "desk": db_session.scalar(
            select(User).where(User.phone_number == "+919000000010")
        ),
        "sharma": retailer("Sharma General Store"),
        "gupta": retailer("Gupta Kirana Store"),
        "patel": retailer("Patel Provision Store"),
        "biscuit": products["SKU-BS-075"],
        "detergent": products["SKU-DT-1KG"],
        "tea": products["SKU-TE-250"],
    }


def place(db_session, retailer, product, *, minutes: int = 0):
    return submit_order(
        db_session,
        retailer,
        [OrderLineInput(product_id=product.id, quantity=1)],
        client_request_id=uuid4(),
        now=BASE_TIME + timedelta(minutes=minutes),
    ).order


def alias(data, retailer, product, phrase: str, *, active: bool = True):
    return RetailerProductAlias(
        retailer_id=retailer.id,
        product_id=product.id,
        alias_text=phrase,
        normalized_alias=phrase,
        is_active=active,
        created_by_user_id=data["desk"].id,
        deactivated_at=None if active else BASE_TIME,
        deactivated_by_user_id=None if active else data["desk"].id,
    )


def test_context_holds_only_the_seeded_products_and_nothing_invented(db_session, data):
    context = load_matching_context(db_session, data["sharma"].id)

    assert [p.sku for p in context.products] == [
        "SKU-BS-075",
        "SKU-DT-1KG",
        "SKU-TE-250",
    ]
    assert context.aliases == {}
    assert context.history_product_ids == frozenset()


def test_loading_the_context_changes_nothing(db_session, data):
    load_matching_context(db_session, data["sharma"].id)

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


def test_demonstration_message_with_the_seeded_data(db_session, data):
    context = load_matching_context(db_session, data["sharma"].id)

    results = match_extraction(extract_lines(DEMO), context)

    assert [(r.proposal.sku, r.proposal.score, r.proposal.band) for r in results] == [
        ("SKU-BS-075", 60, MatchBand.POSSIBLE),
        ("SKU-DT-1KG", 85, MatchBand.STRONG),
        ("SKU-TE-250", 85, MatchBand.STRONG),
    ]


def test_inactive_products_are_not_candidates(db_session, data):
    data["tea"].status = ProductStatus.INACTIVE
    db_session.flush()

    context = load_matching_context(db_session, data["sharma"].id)

    assert [p.sku for p in context.products] == ["SKU-BS-075", "SKU-DT-1KG"]


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------


def test_aliases_are_per_retailer_and_active_only(db_session, data):
    db_session.add_all(
        [
            alias(data, data["sharma"], data["biscuit"], "lal dabba"),
            alias(data, data["sharma"], data["detergent"], "purana naam", active=False),
            alias(data, data["gupta"], data["tea"], "lal dabba"),
        ]
    )
    db_session.flush()

    sharma = load_matching_context(db_session, data["sharma"].id)
    gupta = load_matching_context(db_session, data["gupta"].id)
    patel = load_matching_context(db_session, data["patel"].id)

    assert sharma.aliases == {"lal dabba": data["biscuit"].id}
    assert gupta.aliases == {"lal dabba": data["tea"].id}
    assert patel.aliases == {}

    message = "3 peti lal dabba"
    (for_sharma,) = match_extraction(extract_lines(message), sharma)
    (for_gupta,) = match_extraction(extract_lines(message), gupta)
    (for_patel,) = match_extraction(extract_lines(message), patel)
    assert (for_sharma.proposal.sku, for_sharma.proposal.score) == ("SKU-BS-075", 95)
    assert (for_gupta.proposal.sku, for_gupta.proposal.score) == ("SKU-TE-250", 95)
    assert for_patel.proposal is None


def test_alias_to_an_inactive_product_is_ignored(db_session, data):
    db_session.add(alias(data, data["sharma"], data["tea"], "lal dabba"))
    data["tea"].status = ProductStatus.INACTIVE
    db_session.flush()

    context = load_matching_context(db_session, data["sharma"].id)

    assert context.aliases == {}


# ---------------------------------------------------------------------------
# Order history
# ---------------------------------------------------------------------------


def test_history_comes_from_the_retailers_own_orders(db_session, data):
    place(db_session, data["sharma"], data["biscuit"])

    sharma = load_matching_context(db_session, data["sharma"].id)
    gupta = load_matching_context(db_session, data["gupta"].id)

    assert sharma.history_product_ids == frozenset({data["biscuit"].id})
    assert gupta.history_product_ids == frozenset()

    (result,) = match_extraction(extract_lines("5 peti biscuit"), sharma)
    assert (result.proposal.sku, result.proposal.score) == ("SKU-BS-075", 70)


def test_rejected_orders_are_not_history(db_session, data):
    place(db_session, data["sharma"], data["biscuit"])
    rejected = place(db_session, data["sharma"], data["detergent"], minutes=1)
    reject_order(
        db_session,
        rejected.id,
        reason="Duplicate order",
        rejected_by_user_id=data["desk"].id,
    )
    db_session.flush()

    context = load_matching_context(db_session, data["sharma"].id)

    assert context.history_product_ids == frozenset({data["biscuit"].id})


def test_history_is_limited_to_the_most_recent_orders(db_session, data):
    # The oldest order holds detergent; the newer ones hold biscuit.
    place(db_session, data["sharma"], data["detergent"], minutes=0)
    for minute in range(1, HISTORY_ORDER_LIMIT):
        place(db_session, data["sharma"], data["biscuit"], minutes=minute)

    within = load_matching_context(db_session, data["sharma"].id)
    assert data["detergent"].id in within.history_product_ids  # 20 orders in all

    place(db_session, data["sharma"], data["biscuit"], minutes=HISTORY_ORDER_LIMIT)
    beyond = load_matching_context(db_session, data["sharma"].id)

    # 21 orders: the detergent order is no longer among the 20 most recent.
    assert beyond.history_product_ids == frozenset({data["biscuit"].id})
