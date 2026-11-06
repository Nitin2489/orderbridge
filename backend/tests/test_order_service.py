"""Tests for order preview and confirmation (app/services/order_service.py).

Every test runs in the dedicated test database through db_session
(tests/conftest.py); all changes are rolled back afterwards. Sample data comes
from seed_all(), i.e. the client brief's catalogue:

    Biscuit   SKU-BS-075  Rs 777.60/case   stock 340   buy 10 get 1 free
    Detergent SKU-DT-1KG  Rs 2030.00/case  stock 85    3% off 5+ cases
    Tea       SKU-TE-250  Rs 4720.00/case  stock 0     no scheme

    Sharma General Store   credit 50000.00, outstanding 12000.00
    Patel Provision Store  credit 30000.00, outstanding  5000.00
"""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import (
    Order,
    OrderItem,
    OrderSource,
    OrderStatus,
    Product,
    ProductStatus,
    Retailer,
    RetailerProductPrice,
    User,
    UserRole,
)
from app.services import order_service
from app.services.order_service import (
    ConfirmationOutcome,
    InvalidOrderStateError,
    LineIssueCode,
    OrderValidationError,
    PreviewLine,
    PreviewOutdatedError,
    RetailerUnavailableError,
    UnsavedChangesError,
    _fingerprint,
    confirm_order,
    preview_order_confirmation,
)
from app.services.pricing_service import calculate_retailer_product_price
from seed import seed_all

BISCUIT = "SKU-BS-075"
DETERGENT = "SKU-DT-1KG"
TEA = "SKU-TE-250"

BISCUIT_SCHEME = "Buy 10 cases, get 1 free"
DETERGENT_SCHEME = "3% off on 5 or more cases"

SHARMA = "Sharma General Store"
GUPTA = "Gupta Kirana Store"
PATEL = "Patel Provision Store"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Catalogue:
    retailers: dict[str, Retailer]
    products: dict[str, Product]
    desk_user: User


@pytest.fixture
def catalogue(db_session: Session) -> Catalogue:
    """Seed the brief's sample data into the test transaction."""
    seed_all(db_session)

    desk_user = db_session.scalar(select(User).where(User.role == UserRole.ORDER_DESK))
    assert desk_user is not None

    return Catalogue(
        retailers={r.business_name: r for r in db_session.scalars(select(Retailer))},
        products={p.sku: p for p in db_session.scalars(select(Product))},
        desk_user=desk_user,
    )


def priced_item(
    session: Session,
    retailer: Retailer,
    product: Product,
    quantity: int,
    **overrides,
) -> OrderItem:
    """An order line snapshotted at current prices, as order entry would.

    overrides replace individual stored snapshot values, to simulate an order
    placed when prices or schemes were different.
    """
    pricing = calculate_retailer_product_price(
        session,
        retailer_id=retailer.id,
        product_id=product.id,
        quantity=quantity,
        product=product,
    )
    values = {
        "product_id": product.id,
        "product_name": product.name,
        "unit_name": product.unit_name,
        "sku": product.sku,
        "quantity": quantity,
        "free_quantity": pricing.result.free_quantity,
        "unit_price": pricing.result.unit_price,
        "discount_amount": pricing.result.discount_amount,
        "line_total": pricing.result.grand_total,
        "scheme_name": pricing.scheme_name,
    }
    values.update(overrides)
    return OrderItem(**values)


def stored_item(
    product: Product,
    quantity: int,
    *,
    unit_price: Decimal,
    line_total: Decimal,
    free_quantity: int = 0,
    discount_amount: Decimal = Decimal("0.00"),
    scheme_name: str | None = None,
) -> OrderItem:
    """An order line with explicit stored values (for lines that cannot be
    priced now, such as out-of-stock products)."""
    return OrderItem(
        product_id=product.id,
        product_name=product.name,
        unit_name=product.unit_name,
        sku=product.sku,
        quantity=quantity,
        free_quantity=free_quantity,
        unit_price=unit_price,
        discount_amount=discount_amount,
        line_total=line_total,
        scheme_name=scheme_name,
    )


def create_order(
    session: Session,
    retailer: Retailer,
    items: list[OrderItem],
    *,
    status: OrderStatus = OrderStatus.PENDING_REVIEW,
) -> Order:
    # order_items.line_number is required: number the lines in given order.
    for line_number, item in enumerate(items, start=1):
        item.line_number = line_number

    order = Order(
        order_number=f"TEST-{uuid4().hex[:12].upper()}",
        retailer_id=retailer.id,
        source=OrderSource.RETAILER_APP,
        status=status,
        items=items,
    )
    session.add(order)
    session.flush()
    return order


def db_state(session: Session, catalogue: Catalogue, order: Order) -> dict:
    """Stock, balances, order and line values as stored in the database.

    expire_all() forces every value to be re-read from the database, so this
    reflects what was actually written, not in-memory state. Only call it on
    a session without pending changes.
    """
    session.expire_all()
    items = session.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id)
    ).all()

    return {
        "stock": {
            sku: product.stock_quantity for sku, product in catalogue.products.items()
        },
        "balances": {
            name: retailer.outstanding_balance
            for name, retailer in catalogue.retailers.items()
        },
        "order": {
            "status": order.status,
            "subtotal": order.subtotal,
            "discount_total": order.discount_total,
            "grand_total": order.grand_total,
            "confirmed_at": order.confirmed_at,
            "confirmed_by_user_id": order.confirmed_by_user_id,
        },
        "items": {
            item.id: (
                item.sku,
                item.product_name,
                item.quantity,
                item.free_quantity,
                item.unit_price,
                item.discount_amount,
                item.line_total,
                item.scheme_name,
            )
            for item in items
        },
    }


def price_row(
    session: Session, retailer: Retailer, product: Product
) -> RetailerProductPrice:
    row = session.scalar(
        select(RetailerProductPrice).where(
            RetailerProductPrice.retailer_id == retailer.id,
            RetailerProductPrice.product_id == product.id,
        )
    )
    assert row is not None
    return row


def assert_session_clean(session: Session) -> None:
    assert not session.new
    assert not session.dirty
    assert not session.deleted


@pytest.fixture
def sharma_standard_order(db_session: Session, catalogue: Catalogue) -> Order:
    """10 cases of biscuits (1 free) and 5 of detergent (3% off) for Sharma.

    Totals: subtotal 17926.00, discount 304.50, grand total 17621.50, within
    Sharma's available credit of 38000.00.
    """
    sharma = catalogue.retailers[SHARMA]
    return create_order(
        db_session,
        sharma,
        [
            priced_item(db_session, sharma, catalogue.products[BISCUIT], 10),
            priced_item(db_session, sharma, catalogue.products[DETERGENT], 5),
        ],
    )


# ---------------------------------------------------------------------------
# A. Preview
# ---------------------------------------------------------------------------


def test_preview_prices_lines_with_schemes_and_totals(
    db_session, catalogue, sharma_standard_order
):
    preview = preview_order_confirmation(db_session, sharma_standard_order.id)
    lines = {line.sku: line for line in preview.lines}

    biscuit = lines[BISCUIT]
    assert biscuit.quantity == 10
    assert biscuit.unit_price == Decimal("777.60")
    assert biscuit.free_quantity == 1
    assert biscuit.stock_required == 11
    assert biscuit.subtotal == Decimal("7776.00")
    assert biscuit.discount_amount == Decimal("0.00")
    assert biscuit.line_total == Decimal("7776.00")
    assert biscuit.scheme_name == BISCUIT_SCHEME
    assert biscuit.price_changed is False

    detergent = lines[DETERGENT]
    assert detergent.quantity == 5
    assert detergent.unit_price == Decimal("2030.00")
    assert detergent.free_quantity == 0
    assert detergent.stock_required == 5
    assert detergent.subtotal == Decimal("10150.00")
    assert detergent.discount_amount == Decimal("304.50")
    assert detergent.line_total == Decimal("9845.50")
    assert detergent.scheme_name == DETERGENT_SCHEME
    assert detergent.price_changed is False

    assert preview.subtotal == Decimal("17926.00")
    assert preview.discount_total == Decimal("304.50")
    assert preview.grand_total == Decimal("17621.50")
    assert preview.available_credit == Decimal("38000.00")
    assert preview.exceeds_credit_limit is False
    assert preview.issues == ()
    assert preview.price_changes == ()
    assert preview.can_confirm is True
    assert preview.fingerprint is not None
    assert len(preview.fingerprint) == 64


def test_preview_does_not_mutate_database_or_session(
    db_session, catalogue, sharma_standard_order
):
    before = db_state(db_session, catalogue, sharma_standard_order)

    preview_order_confirmation(db_session, sharma_standard_order.id)

    assert_session_clean(db_session)
    assert db_state(db_session, catalogue, sharma_standard_order) == before


def test_preview_detects_unit_price_change(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    # Only the stored unit price differs from current pricing.
    order = create_order(
        db_session,
        sharma,
        [
            priced_item(
                db_session,
                sharma,
                catalogue.products[BISCUIT],
                10,
                unit_price=Decimal("750.00"),
            )
        ],
    )

    preview = preview_order_confirmation(db_session, order.id)
    (line,) = preview.lines

    assert line.stored_unit_price == Decimal("750.00")
    assert line.unit_price == Decimal("777.60")
    assert line.price_changed is True
    assert preview.price_changes == (line,)


def test_preview_detects_free_quantity_change(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    # Only the stored free quantity differs (stored without the free case).
    order = create_order(
        db_session,
        sharma,
        [
            priced_item(
                db_session,
                sharma,
                catalogue.products[BISCUIT],
                10,
                free_quantity=0,
            )
        ],
    )

    (line,) = preview_order_confirmation(db_session, order.id).lines

    assert line.stored_free_quantity == 0
    assert line.free_quantity == 1
    assert line.unit_price == line.stored_unit_price
    assert line.line_total == line.stored_line_total
    assert line.price_changed is True


def test_preview_detects_discount_change(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    # Only the stored discount differs (stored without the 3% slab).
    order = create_order(
        db_session,
        sharma,
        [
            priced_item(
                db_session,
                sharma,
                catalogue.products[DETERGENT],
                5,
                discount_amount=Decimal("0.00"),
            )
        ],
    )

    (line,) = preview_order_confirmation(db_session, order.id).lines

    assert line.stored_discount_amount == Decimal("0.00")
    assert line.discount_amount == Decimal("304.50")
    assert line.unit_price == line.stored_unit_price
    assert line.line_total == line.stored_line_total
    assert line.price_changed is True


def test_preview_detects_scheme_change_with_unchanged_money(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    # 5 cases is below the buy-10 threshold, so the scheme gives nothing and
    # the money is identical. The order was stored with no scheme; the
    # biscuit scheme now applies to the product.
    order = create_order(
        db_session,
        sharma,
        [
            priced_item(
                db_session,
                sharma,
                catalogue.products[BISCUIT],
                5,
                scheme_name=None,
            )
        ],
    )

    (line,) = preview_order_confirmation(db_session, order.id).lines

    assert line.unit_price == line.stored_unit_price == Decimal("777.60")
    assert line.free_quantity == line.stored_free_quantity == 0
    assert line.discount_amount == line.stored_discount_amount
    assert line.line_total == line.stored_line_total == Decimal("3888.00")
    assert line.stored_scheme_name is None
    assert line.scheme_name == BISCUIT_SCHEME
    assert line.price_changed is True


def test_totals_cover_all_lines_for_valid_order(db_session, sharma_standard_order):
    preview = preview_order_confirmation(db_session, sharma_standard_order.id)

    assert preview.totals_cover_all_lines is True
    assert len(preview.lines) == 2


def test_totals_do_not_cover_all_lines_when_a_line_has_an_issue(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    tea = catalogue.products[TEA]
    order = create_order(
        db_session,
        sharma,
        [
            priced_item(db_session, sharma, catalogue.products[DETERGENT], 5),
            stored_item(
                tea,
                1,
                unit_price=Decimal("4720.00"),
                line_total=Decimal("4720.00"),
            ),
        ],
    )

    preview = preview_order_confirmation(db_session, order.id)

    assert preview.totals_cover_all_lines is False
    assert preview.can_confirm is False
    assert [issue.code for issue in preview.issues] == [
        LineIssueCode.INSUFFICIENT_STOCK
    ]


def test_partial_totals_include_only_valid_lines(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    tea = catalogue.products[TEA]
    order = create_order(
        db_session,
        sharma,
        [
            priced_item(db_session, sharma, catalogue.products[BISCUIT], 10),
            stored_item(
                tea,
                1,
                unit_price=Decimal("4720.00"),
                line_total=Decimal("4720.00"),
            ),
        ],
    )
    full_stored_total = sum((item.line_total for item in order.items), Decimal(0))

    preview = preview_order_confirmation(db_session, order.id)

    # Only the biscuit line is priced; the tea line is reported as an issue.
    assert [line.sku for line in preview.lines] == [BISCUIT]
    assert [issue.sku for issue in preview.issues] == [TEA]
    assert preview.subtotal == Decimal("7776.00")
    assert preview.discount_total == Decimal("0.00")
    assert preview.grand_total == Decimal("7776.00")

    # The totals are flagged as partial and are not the order total.
    assert full_stored_total == Decimal("12496.00")
    assert preview.grand_total != full_stored_total
    assert preview.totals_cover_all_lines is False
    assert preview.fingerprint is None


# ---------------------------------------------------------------------------
# B. Confirmation
# ---------------------------------------------------------------------------


def test_confirmation_updates_stock_credit_snapshots_and_confirmer(
    db_session, catalogue, sharma_standard_order
):
    preview = preview_order_confirmation(db_session, sharma_standard_order.id)

    result = confirm_order(
        db_session,
        sharma_standard_order.id,
        approved_fingerprint=preview.fingerprint,
        confirmed_by_user_id=catalogue.desk_user.id,
    )

    assert result.outcome == ConfirmationOutcome.CONFIRMED
    assert result.already_confirmed is False
    assert_session_clean(db_session)

    state = db_state(db_session, catalogue, sharma_standard_order)

    # Free goods consume stock: 10 ordered + 1 free.
    assert state["stock"] == {BISCUIT: 329, DETERGENT: 80, TEA: 0}
    assert state["balances"][SHARMA] == Decimal("29621.50")  # 12000 + 17621.50
    assert state["balances"][GUPTA] == Decimal("25000.00")
    assert state["balances"][PATEL] == Decimal("5000.00")

    order = state["order"]
    assert order["status"] == OrderStatus.CONFIRMED
    assert order["subtotal"] == Decimal("17926.00")
    assert order["discount_total"] == Decimal("304.50")
    assert order["grand_total"] == Decimal("17621.50")
    assert isinstance(order["confirmed_at"], datetime)
    assert order["confirmed_at"].tzinfo is not None
    assert order["confirmed_by_user_id"] == catalogue.desk_user.id

    snapshots = {values[0]: values for values in state["items"].values()}
    assert snapshots[BISCUIT] == (
        BISCUIT,
        "Glucose Biscuit 75g",
        10,
        1,
        Decimal("777.60"),
        Decimal("0.00"),
        Decimal("7776.00"),
        BISCUIT_SCHEME,
    )
    assert snapshots[DETERGENT] == (
        DETERGENT,
        "Detergent Powder 1kg",
        5,
        0,
        Decimal("2030.00"),
        Decimal("304.50"),
        Decimal("9845.50"),
        DETERGENT_SCHEME,
    )


def test_outdated_fingerprint_is_rejected_without_changes(
    db_session, catalogue, sharma_standard_order
):
    sharma = catalogue.retailers[SHARMA]
    preview = preview_order_confirmation(db_session, sharma_standard_order.id)

    # The biscuit price changes after the desk approved the preview.
    price_row(db_session, sharma, catalogue.products[BISCUIT]).unit_price = Decimal(
        "800.00"
    )
    db_session.flush()
    before = db_state(db_session, catalogue, sharma_standard_order)

    with pytest.raises(PreviewOutdatedError) as raised:
        confirm_order(
            db_session,
            sharma_standard_order.id,
            approved_fingerprint=preview.fingerprint,
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    new_preview = raised.value.preview
    assert new_preview.fingerprint is not None
    assert new_preview.fingerprint != preview.fingerprint
    biscuit = next(line for line in new_preview.lines if line.sku == BISCUIT)
    assert biscuit.unit_price == Decimal("800.00")
    assert biscuit.price_changed is True

    assert db_state(db_session, catalogue, sharma_standard_order) == before


def test_credit_limit_exceeded_puts_order_on_hold_without_deduction(
    db_session, catalogue
):
    patel = catalogue.retailers[PATEL]
    # 13 detergent cases: 26390.00 - 3% (791.70) = 25598.30 > 25000.00.
    order = create_order(
        db_session,
        patel,
        [priced_item(db_session, patel, catalogue.products[DETERGENT], 13)],
    )
    before = db_state(db_session, catalogue, order)
    preview = preview_order_confirmation(db_session, order.id)
    assert preview.grand_total == Decimal("25598.30")
    assert preview.available_credit == Decimal("25000.00")
    assert preview.exceeds_credit_limit is True

    result = confirm_order(
        db_session,
        order.id,
        approved_fingerprint=preview.fingerprint,
        confirmed_by_user_id=catalogue.desk_user.id,
    )

    assert result.outcome == ConfirmationOutcome.ON_HOLD_CREDIT_LIMIT
    assert result.required_credit == Decimal("25598.30")
    assert result.available_credit == Decimal("25000.00")

    after = db_state(db_session, catalogue, order)
    assert after["order"]["status"] == OrderStatus.ON_HOLD
    assert after["stock"] == before["stock"]
    assert after["balances"] == before["balances"]
    assert after["items"] == before["items"]
    assert after["order"] == {**before["order"], "status": OrderStatus.ON_HOLD}


def test_duplicate_product_lines_are_rejected(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    biscuit = catalogue.products[BISCUIT]
    order = create_order(
        db_session,
        sharma,
        [
            priced_item(db_session, sharma, biscuit, 5),
            priced_item(db_session, sharma, biscuit, 5),
        ],
    )
    before = db_state(db_session, catalogue, order)

    preview = preview_order_confirmation(db_session, order.id)
    assert {issue.code for issue in preview.issues} == {LineIssueCode.DUPLICATE_PRODUCT}

    with pytest.raises(OrderValidationError) as raised:
        # Line validation runs before the fingerprint check.
        confirm_order(
            db_session,
            order.id,
            approved_fingerprint="not-approved",
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    issues = raised.value.issues
    assert {issue.order_item_id for issue in issues} == set(before["items"])
    assert {issue.code for issue in issues} == {LineIssueCode.DUPLICATE_PRODUCT}
    assert db_state(db_session, catalogue, order) == before


def test_all_invalid_lines_are_collected_in_one_error(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    biscuit = catalogue.products[BISCUIT]
    tea = catalogue.products[TEA]
    tea_item = stored_item(
        tea, 1, unit_price=Decimal("4720.00"), line_total=Decimal("4720.00")
    )
    biscuit_item = priced_item(db_session, sharma, biscuit, 10)
    detergent_item = priced_item(db_session, sharma, catalogue.products[DETERGENT], 5)
    order = create_order(db_session, sharma, [tea_item, biscuit_item, detergent_item])
    # Sharma's biscuit price is withdrawn after the order was placed.
    price_row(db_session, sharma, biscuit).is_active = False
    db_session.flush()
    before = db_state(db_session, catalogue, order)

    with pytest.raises(OrderValidationError) as raised:
        confirm_order(
            db_session,
            order.id,
            approved_fingerprint="not-approved",
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    codes = {issue.order_item_id: issue.code for issue in raised.value.issues}
    assert codes == {
        tea_item.id: LineIssueCode.INSUFFICIENT_STOCK,
        biscuit_item.id: LineIssueCode.PRICE_NOT_CONFIGURED,
    }
    assert db_state(db_session, catalogue, order) == before


def test_inactive_product_is_reported(db_session, catalogue, sharma_standard_order):
    catalogue.products[DETERGENT].status = ProductStatus.INACTIVE
    db_session.flush()
    before = db_state(db_session, catalogue, sharma_standard_order)

    with pytest.raises(OrderValidationError) as raised:
        confirm_order(
            db_session,
            sharma_standard_order.id,
            approved_fingerprint="not-approved",
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    assert [(i.sku, i.code) for i in raised.value.issues] == [
        (DETERGENT, LineIssueCode.PRODUCT_UNAVAILABLE)
    ]
    assert db_state(db_session, catalogue, sharma_standard_order) == before


def test_missing_retailer_price_is_reported(
    db_session, catalogue, sharma_standard_order
):
    sharma = catalogue.retailers[SHARMA]
    price_row(db_session, sharma, catalogue.products[DETERGENT]).is_active = False
    db_session.flush()
    before = db_state(db_session, catalogue, sharma_standard_order)

    with pytest.raises(OrderValidationError) as raised:
        confirm_order(
            db_session,
            sharma_standard_order.id,
            approved_fingerprint="not-approved",
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    assert [(i.sku, i.code) for i in raised.value.issues] == [
        (DETERGENT, LineIssueCode.PRICE_NOT_CONFIGURED)
    ]
    assert db_state(db_session, catalogue, sharma_standard_order) == before


def test_insufficient_stock_counts_free_goods(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    biscuit = catalogue.products[BISCUIT]
    # 340 ordered + 34 free = 374 cases required, but only 340 in stock.
    order = create_order(
        db_session,
        sharma,
        [
            stored_item(
                biscuit,
                340,
                unit_price=Decimal("777.60"),
                line_total=Decimal("264384.00"),
                free_quantity=34,
                scheme_name=BISCUIT_SCHEME,
            )
        ],
    )
    before = db_state(db_session, catalogue, order)

    with pytest.raises(OrderValidationError) as raised:
        confirm_order(
            db_session,
            order.id,
            approved_fingerprint="not-approved",
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    (issue,) = raised.value.issues
    assert issue.code == LineIssueCode.INSUFFICIENT_STOCK
    assert "Required: 374" in issue.message
    assert db_state(db_session, catalogue, order) == before


def test_inactive_retailer_cannot_confirm(db_session, catalogue):
    sharma = catalogue.retailers[SHARMA]
    order = create_order(
        db_session,
        sharma,
        [
            priced_item(db_session, sharma, catalogue.products[BISCUIT], 10),
            priced_item(db_session, sharma, catalogue.products[DETERGENT], 5),
        ],
    )
    # The retailer is deactivated after placing the order. Retailer has no
    # status field; is_active is the model's inactive flag.
    sharma.is_active = False
    db_session.flush()
    before = db_state(db_session, catalogue, order)

    with pytest.raises(RetailerUnavailableError, match="Retailer is unavailable"):
        confirm_order(
            db_session,
            order.id,
            approved_fingerprint="not-approved",
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    after = db_state(db_session, catalogue, order)
    assert after["stock"] == before["stock"]
    assert after["balances"] == before["balances"]
    assert after["order"] == before["order"]
    assert after["items"] == before["items"]
    assert after["order"]["status"] == OrderStatus.PENDING_REVIEW


@pytest.mark.parametrize("status", [OrderStatus.CONFIRMED, OrderStatus.EXPORTED])
def test_already_confirmed_statuses_return_without_changes(
    db_session, catalogue, status
):
    sharma = catalogue.retailers[SHARMA]
    order = create_order(
        db_session,
        sharma,
        [priced_item(db_session, sharma, catalogue.products[BISCUIT], 10)],
        status=status,
    )
    before = db_state(db_session, catalogue, order)

    result = confirm_order(
        db_session,
        order.id,
        approved_fingerprint="not-checked-for-confirmed-orders",
        confirmed_by_user_id=catalogue.desk_user.id,
    )

    assert result.outcome == ConfirmationOutcome.ALREADY_CONFIRMED
    assert result.already_confirmed is True
    assert db_state(db_session, catalogue, order) == before


def test_second_confirmation_does_not_deduct_twice(
    db_session, catalogue, sharma_standard_order
):
    preview = preview_order_confirmation(db_session, sharma_standard_order.id)
    confirm_order(
        db_session,
        sharma_standard_order.id,
        approved_fingerprint=preview.fingerprint,
        confirmed_by_user_id=catalogue.desk_user.id,
    )
    after_first = db_state(db_session, catalogue, sharma_standard_order)

    result = confirm_order(
        db_session,
        sharma_standard_order.id,
        approved_fingerprint=preview.fingerprint,
        confirmed_by_user_id=catalogue.desk_user.id,
    )

    assert result.outcome == ConfirmationOutcome.ALREADY_CONFIRMED
    assert db_state(db_session, catalogue, sharma_standard_order) == after_first
    assert after_first["stock"][BISCUIT] == 329


def test_empty_order_is_rejected(db_session, catalogue):
    order = create_order(db_session, catalogue.retailers[SHARMA], [])

    with pytest.raises(InvalidOrderStateError, match="empty"):
        preview_order_confirmation(db_session, order.id)

    with pytest.raises(InvalidOrderStateError, match="empty"):
        confirm_order(
            db_session,
            order.id,
            approved_fingerprint="not-approved",
            confirmed_by_user_id=catalogue.desk_user.id,
        )


@pytest.mark.parametrize(
    "status",
    [OrderStatus.DRAFT, OrderStatus.ON_HOLD, OrderStatus.REJECTED],
)
def test_orders_not_pending_review_are_rejected(db_session, catalogue, status):
    sharma = catalogue.retailers[SHARMA]
    order = create_order(
        db_session,
        sharma,
        [priced_item(db_session, sharma, catalogue.products[BISCUIT], 10)],
        status=status,
    )
    before = db_state(db_session, catalogue, order)

    with pytest.raises(InvalidOrderStateError, match=str(status)):
        preview_order_confirmation(db_session, order.id)

    with pytest.raises(InvalidOrderStateError, match=str(status)):
        confirm_order(
            db_session,
            order.id,
            approved_fingerprint="not-approved",
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    assert db_state(db_session, catalogue, order) == before


# ---------------------------------------------------------------------------
# C. Transaction safety
# ---------------------------------------------------------------------------


class _FailingClock:
    """Stands in for datetime in order_service; fails when the apply phase
    stamps confirmed_at, after stock, lines and balance were modified."""

    @staticmethod
    def now(tz=None):
        raise RuntimeError("simulated failure while applying confirmation")


def test_failure_before_flush_in_apply_phase_leaves_no_changes(
    db_session, catalogue, sharma_standard_order, monkeypatch
):
    preview = preview_order_confirmation(db_session, sharma_standard_order.id)
    before = db_state(db_session, catalogue, sharma_standard_order)
    monkeypatch.setattr(order_service, "datetime", _FailingClock)

    with pytest.raises(RuntimeError, match="simulated failure"):
        confirm_order(
            db_session,
            sharma_standard_order.id,
            approved_fingerprint=preview.fingerprint,
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    # Even if the caller commits after the failure, none of the in-memory
    # changes made before it may be written.
    assert db_session.in_transaction()
    db_session.commit()
    assert db_state(db_session, catalogue, sharma_standard_order) == before


def test_failure_after_flush_in_apply_phase_is_rolled_back_by_savepoint(
    db_session, catalogue, sharma_standard_order
):
    preview = preview_order_confirmation(db_session, sharma_standard_order.id)
    before = db_state(db_session, catalogue, sharma_standard_order)
    flushes = []

    def fail_after_first_flush(session, flush_context):
        # The savepoint's flush has already sent the UPDATEs for stock,
        # lines, balance and order; failing now must undo them.
        if not flushes:
            flushes.append(True)
            raise RuntimeError("simulated failure after flushing confirmation")

    event.listen(db_session, "after_flush", fail_after_first_flush)
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            confirm_order(
                db_session,
                sharma_standard_order.id,
                approved_fingerprint=preview.fingerprint,
                confirmed_by_user_id=catalogue.desk_user.id,
            )
    finally:
        event.remove(db_session, "after_flush", fail_after_first_flush)

    assert flushes == [True]
    assert db_session.in_transaction()
    db_session.commit()
    assert db_state(db_session, catalogue, sharma_standard_order) == before


def test_caller_owns_outer_transaction(
    db_session, catalogue, sharma_standard_order, monkeypatch
):
    # Make the seeded data and order the caller's committed baseline.
    db_session.commit()
    before = db_state(db_session, catalogue, sharma_standard_order)

    def forbid_commit():
        raise AssertionError("confirm_order must not commit")

    monkeypatch.setattr(db_session, "commit", forbid_commit)
    preview = preview_order_confirmation(db_session, sharma_standard_order.id)

    result = confirm_order(
        db_session,
        sharma_standard_order.id,
        approved_fingerprint=preview.fingerprint,
        confirmed_by_user_id=catalogue.desk_user.id,
    )

    assert result.outcome == ConfirmationOutcome.CONFIRMED
    assert db_session.in_transaction()

    # The caller decides: rolling back discards the whole confirmation.
    db_session.rollback()
    assert db_state(db_session, catalogue, sharma_standard_order) == before


def _pending_new(session: Session, catalogue: Catalogue):
    user = User(
        phone_number="+919000000099",
        full_name="Unsaved Desk User",
        role=UserRole.ORDER_DESK,
        is_active=True,
    )
    session.add(user)
    return lambda: user in session.new


def _pending_dirty(session: Session, catalogue: Catalogue):
    retailer = catalogue.retailers[PATEL]
    retailer.address = "Unsaved address change"
    return lambda: (
        retailer in session.dirty and retailer.address == "Unsaved address change"
    )


def _pending_deleted(session: Session, catalogue: Catalogue):
    row = price_row(session, catalogue.retailers[GUPTA], catalogue.products[TEA])
    session.delete(row)
    return lambda: row in session.deleted


@pytest.mark.parametrize(
    "make_pending",
    [_pending_new, _pending_dirty, _pending_deleted],
    ids=["new", "dirty", "deleted"],
)
def test_confirmation_rejects_session_with_unsaved_changes(
    db_session, catalogue, sharma_standard_order, make_pending
):
    preview = preview_order_confirmation(db_session, sharma_standard_order.id)
    still_pending = make_pending(db_session, catalogue)

    with pytest.raises(UnsavedChangesError, match="no unsaved changes"):
        confirm_order(
            db_session,
            sharma_standard_order.id,
            approved_fingerprint=preview.fingerprint,
            confirmed_by_user_id=catalogue.desk_user.id,
        )

    # The caller's change was neither flushed nor discarded.
    assert still_pending()
    assert sharma_standard_order.status == OrderStatus.PENDING_REVIEW


def test_preview_works_with_unsaved_changes(
    db_session, catalogue, sharma_standard_order
):
    still_pending = _pending_dirty(db_session, catalogue)

    preview = preview_order_confirmation(db_session, sharma_standard_order.id)

    assert preview.can_confirm is True
    assert preview.grand_total == Decimal("17621.50")
    assert still_pending()


# ---------------------------------------------------------------------------
# D. Fingerprint
# ---------------------------------------------------------------------------

ORDER_ID = UUID(int=100)

BISCUIT_LINE = PreviewLine(
    order_item_id=UUID(int=1),
    product_id=UUID(int=11),
    sku=BISCUIT,
    product_name="Glucose Biscuit 75g",
    quantity=10,
    stored_free_quantity=1,
    free_quantity=1,
    stock_required=11,
    stored_unit_price=Decimal("777.60"),
    unit_price=Decimal("777.60"),
    subtotal=Decimal("7776.00"),
    stored_discount_amount=Decimal("0.00"),
    discount_amount=Decimal("0.00"),
    stored_line_total=Decimal("7776.00"),
    line_total=Decimal("7776.00"),
    stored_scheme_name=BISCUIT_SCHEME,
    scheme_name=BISCUIT_SCHEME,
)

DETERGENT_LINE = PreviewLine(
    order_item_id=UUID(int=2),
    product_id=UUID(int=12),
    sku=DETERGENT,
    product_name="Detergent Powder 1kg",
    quantity=5,
    stored_free_quantity=0,
    free_quantity=0,
    stock_required=5,
    stored_unit_price=Decimal("2030.00"),
    unit_price=Decimal("2030.00"),
    subtotal=Decimal("10150.00"),
    stored_discount_amount=Decimal("304.50"),
    discount_amount=Decimal("304.50"),
    stored_line_total=Decimal("9845.50"),
    line_total=Decimal("9845.50"),
    stored_scheme_name=DETERGENT_SCHEME,
    scheme_name=DETERGENT_SCHEME,
)

TOTALS = {
    "subtotal": Decimal("17926.00"),
    "discount_total": Decimal("304.50"),
    "grand_total": Decimal("17621.50"),
}


def fingerprint_of(lines, order_id=ORDER_ID, **totals) -> str:
    values = {**TOTALS, **totals}
    return _fingerprint(
        order_id,
        tuple(lines),
        values["subtotal"],
        values["discount_total"],
        values["grand_total"],
    )


BASELINE = fingerprint_of([BISCUIT_LINE, DETERGENT_LINE])


def test_fingerprint_is_independent_of_line_order():
    assert fingerprint_of([DETERGENT_LINE, BISCUIT_LINE]) == BASELINE
    assert fingerprint_of([BISCUIT_LINE, DETERGENT_LINE]) == BASELINE
    assert len(BASELINE) == 64


def test_fingerprint_normalises_money_representation():
    # 777.6 and 777.60 are the same amount and must hash identically.
    line = replace(BISCUIT_LINE, unit_price=Decimal("777.6"))
    assert fingerprint_of([line, DETERGENT_LINE]) == BASELINE


def test_preview_fingerprint_is_stable_between_calls(db_session, sharma_standard_order):
    first = preview_order_confirmation(db_session, sharma_standard_order.id)
    second = preview_order_confirmation(db_session, sharma_standard_order.id)

    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("order_item_id", UUID(int=3)),
        ("product_id", UUID(int=13)),
        ("quantity", 11),
        ("free_quantity", 2),
        ("unit_price", Decimal("777.61")),
        ("subtotal", Decimal("7776.01")),
        ("discount_amount", Decimal("0.01")),
        ("line_total", Decimal("7776.01")),
        ("scheme_name", "Buy 12 cases, get 1 free"),
        ("scheme_name", None),
        ("scheme_name", ""),
    ],
)
def test_fingerprint_changes_when_a_line_field_changes(field, value):
    changed = replace(BISCUIT_LINE, **{field: value})

    assert fingerprint_of([changed, DETERGENT_LINE]) != BASELINE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subtotal", Decimal("17926.01")),
        ("discount_total", Decimal("304.51")),
        ("grand_total", Decimal("17621.51")),
    ],
)
def test_fingerprint_changes_when_a_total_changes(field, value):
    assert fingerprint_of([BISCUIT_LINE, DETERGENT_LINE], **{field: value}) != (
        BASELINE
    )


def test_fingerprint_changes_with_order_id():
    assert (
        fingerprint_of([BISCUIT_LINE, DETERGENT_LINE], order_id=UUID(int=101))
        != BASELINE
    )


def test_fingerprint_distinguishes_no_scheme_from_empty_scheme_name():
    no_scheme = replace(BISCUIT_LINE, scheme_name=None)
    empty_name = replace(BISCUIT_LINE, scheme_name="")

    assert fingerprint_of([no_scheme, DETERGENT_LINE]) != fingerprint_of(
        [empty_name, DETERGENT_LINE]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sku", "SKU-RENAMED"),
        ("product_name", "Renamed biscuit"),
        ("stock_required", 99),
        ("stored_unit_price", Decimal("1.00")),
        ("stored_free_quantity", 0),
        ("stored_discount_amount", Decimal("9.99")),
        ("stored_line_total", Decimal("1.00")),
        ("stored_scheme_name", "Old scheme"),
    ],
)
def test_fingerprint_ignores_display_and_stored_fields(field, value):
    # The desk approves the figures confirmation will write. Display text,
    # the derived stock_required and the old stored snapshot are not part of
    # that approval (stock is re-validated separately under lock).
    changed = replace(BISCUIT_LINE, **{field: value})

    assert fingerprint_of([changed, DETERGENT_LINE]) == BASELINE
