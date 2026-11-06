"""Order confirmation: preview and confirm pending orders.

Flow
----
1. preview_order_confirmation() prices every line with current retailer
   prices and schemes, reports line issues and price changes, and returns a
   fingerprint of the exact figures shown. It takes no locks and changes
   nothing.
2. The caller (the order desk) reviews the preview and approves it.
3. confirm_order() re-validates everything under row locks and only confirms
   if the current figures still match the approved fingerprint. Changed
   prices are therefore never confirmed silently.

Lock order
----------
confirm_order() locks rows in this order. Any other code path that locks
more than one of these rows must use the same order to avoid deadlocks:

    1. orders row
    2. order_items rows of that order, sorted by id
    3. retailers row
    4. products rows, sorted by id
    5. retailer_product_prices rows for this retailer and those products,
       sorted by product_id (active or not)
    6. schemes rows for those products, sorted by product_id then id
       (active or not, any dates)

Every locking query uses populate_existing, so the locked database values
replace any copy already held in the session.

Steps 5 and 6 lock a superset of the rows the pricing service selects. The
pricing service is then called with for_update=True, and its own FOR UPDATE
queries only request rows this transaction already holds, so they acquire no
new locks out of order. Because the superset ignores is_active and dates,
another transaction cannot activate or re-date one of these rows while the
confirmation runs.

Limit: row locks cannot cover rows that do not exist yet. A price or scheme
row inserted and committed by another transaction after step 5/6 could be
seen, and locked, by the pricing service. If the pricing service selects
such a row, the confirmation is still rejected: either the figures differ
from the approved fingerprint (PreviewOutdatedError) or the line becomes
invalid, e.g. two active schemes (OrderValidationError).

Transactions
------------
The caller owns the outer transaction and must commit or roll back.
confirm_order() never commits, and it refuses to run in a session that holds
unsaved changes, so it never flushes or discards the caller's work. Stock,
credit and order changes are applied inside a SAVEPOINT, so an unexpected
failure part-way through leaves no partial changes behind, even if the
caller later commits.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Order,
    OrderEventType,
    OrderItem,
    OrderStatus,
    Product,
    Retailer,
    RetailerProductPrice,
    Scheme,
)
from app.services.order_events import money_detail, record_order_event
from app.services.pricing_service import (
    ApplicableSchemeError,
    InsufficientStockError,
    ProductNotFoundError,
    RetailerPriceNotFoundError,
    RetailerProductPricing,
    calculate_retailer_product_price,
)
from app.services.scheme_engine import money

ZERO = Decimal("0.00")

# Orders in these states have already been confirmed; confirming again must
# change nothing.
ALREADY_CONFIRMED_STATUSES = frozenset({OrderStatus.CONFIRMED, OrderStatus.EXPORTED})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OrderConfirmationError(Exception):
    """Base exception for order confirmation failures."""


class OrderNotFoundError(OrderConfirmationError):
    pass


class InvalidOrderStateError(OrderConfirmationError):
    pass


class RetailerUnavailableError(OrderConfirmationError):
    pass


class UnsavedChangesError(OrderConfirmationError):
    """The session holds unsaved changes, so confirmation cannot start."""


class LineIssueCode(StrEnum):
    DUPLICATE_PRODUCT = "duplicate_product"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    PRICE_NOT_CONFIGURED = "price_not_configured"
    SCHEME_MISCONFIGURED = "scheme_misconfigured"
    INSUFFICIENT_STOCK = "insufficient_stock"
    INVALID_QUANTITY = "invalid_quantity"
    FREE_QUANTITY_EXCEEDS_QUANTITY = "free_quantity_exceeds_quantity"


@dataclass(frozen=True)
class LineIssue:
    order_item_id: UUID
    product_id: UUID
    sku: str
    code: LineIssueCode
    message: str


class OrderValidationError(OrderConfirmationError):
    """One or more order lines cannot be confirmed. Lists every issue."""

    def __init__(self, issues: tuple[LineIssue, ...]) -> None:
        self.issues = issues
        super().__init__(
            f"Order has {len(issues)} line issue(s) and cannot be confirmed."
        )


class PreviewOutdatedError(OrderConfirmationError):
    """Current figures differ from the preview the caller approved."""

    def __init__(self, preview: "OrderConfirmationPreview") -> None:
        self.preview = preview
        super().__init__(
            "Order prices or quantities changed since the preview was "
            "approved. Review the new preview and approve it again."
        )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreviewLine:
    """One order line priced now, next to the values stored on the order.

    stored_* fields are the order item's current snapshot; the unprefixed
    fields are what confirmation would write.
    """

    order_item_id: UUID
    product_id: UUID
    sku: str
    product_name: str
    quantity: int
    stored_free_quantity: int
    free_quantity: int
    stock_required: int
    stored_unit_price: Decimal
    unit_price: Decimal
    subtotal: Decimal
    stored_discount_amount: Decimal
    discount_amount: Decimal
    stored_line_total: Decimal
    line_total: Decimal
    stored_scheme_name: str | None
    scheme_name: str | None

    @property
    def price_changed(self) -> bool:
        """True if current pricing differs from the stored snapshot.

        Covers unit price, free cases, discount, line total and the scheme
        applied, so a scheme change that leaves the money unchanged (for
        example a renamed or replaced scheme) is still reported.
        """
        return (
            self.unit_price != self.stored_unit_price
            or self.free_quantity != self.stored_free_quantity
            or self.discount_amount != self.stored_discount_amount
            or self.line_total != self.stored_line_total
            or self.scheme_name != self.stored_scheme_name
        )


@dataclass(frozen=True)
class OrderConfirmationPreview:
    """Current pricing for a pending order.

    When issues exist, lines and totals cover only the lines without issues,
    fingerprint is None and the order cannot be confirmed. Check
    totals_cover_all_lines before presenting subtotal, discount_total or
    grand_total as the order total.
    """

    order_id: UUID
    lines: tuple[PreviewLine, ...]
    issues: tuple[LineIssue, ...]
    subtotal: Decimal
    discount_total: Decimal
    grand_total: Decimal
    available_credit: Decimal
    fingerprint: str | None

    @property
    def can_confirm(self) -> bool:
        return not self.issues

    @property
    def totals_cover_all_lines(self) -> bool:
        """True if the totals are for the complete order.

        False means at least one line has an issue and was left out, so the
        totals cover only the valid lines and are not the order total.
        """
        return not self.issues

    @property
    def price_changes(self) -> tuple[PreviewLine, ...]:
        return tuple(line for line in self.lines if line.price_changed)

    @property
    def exceeds_credit_limit(self) -> bool:
        """Compares grand_total with available credit.

        Only conclusive when totals_cover_all_lines is True. With partial
        totals, True still means the full order would exceed the limit
        (line totals are never negative), but False proves nothing.
        """
        return self.grand_total > self.available_credit


class ConfirmationOutcome(StrEnum):
    CONFIRMED = "confirmed"
    ALREADY_CONFIRMED = "already_confirmed"
    ON_HOLD_CREDIT_LIMIT = "on_hold_credit_limit"


@dataclass(frozen=True)
class ConfirmationResult:
    order: Order
    outcome: ConfirmationOutcome
    required_credit: Decimal | None = None
    available_credit: Decimal | None = None

    @property
    def already_confirmed(self) -> bool:
        return self.outcome == ConfirmationOutcome.ALREADY_CONFIRMED


@dataclass(frozen=True)
class _LinePlan:
    item: OrderItem
    product: Product
    pricing: RetailerProductPricing


# ---------------------------------------------------------------------------
# Loading (locking variants use FOR UPDATE + populate_existing)
# ---------------------------------------------------------------------------


def _ensure_clean_session(session: Session) -> None:
    """Refuse to run if the caller's session holds unsaved changes.

    Confirmation flushes (savepoint, credit hold) and its locking queries use
    populate_existing. In a session with pending changes, the first would
    write the caller's unrelated work and the second could silently discard
    it. Requiring a clean session avoids both.
    """
    if session.new or session.dirty or session.deleted:
        raise UnsavedChangesError(
            "Order confirmation requires a session with no unsaved changes. "
            "Commit or roll back pending changes before confirming."
        )


def _load_order(session: Session, order_id: UUID, *, lock: bool) -> Order:
    statement = select(Order).where(Order.id == order_id)

    if lock:
        # populate_existing overwrites any stale copy already in the session
        # with the row just locked, so status checks see committed values.
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )

    order = session.scalar(statement)

    if order is None:
        raise OrderNotFoundError("Order was not found.")

    return order


def _load_items(session: Session, order_id: UUID, *, lock: bool) -> list[OrderItem]:
    """Load the order's items, sorted by id.

    When locking, this runs straight after the order row is locked, so item
    rows are always locked second and in id order. These freshly loaded items
    are used for all validation and confirmation; the Order.items collection
    is not relied on.
    """
    statement = (
        select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
    )

    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )

    return list(session.scalars(statement))


def _load_retailer(session: Session, retailer_id: UUID, *, lock: bool) -> Retailer:
    statement = select(Retailer).where(Retailer.id == retailer_id)

    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )

    retailer = session.scalar(statement)

    if retailer is None or not retailer.is_active:
        raise RetailerUnavailableError("Retailer is unavailable.")

    return retailer


def _load_products(
    session: Session, items: list[OrderItem], *, lock: bool
) -> dict[UUID, Product]:
    product_ids = {item.product_id for item in items}
    # ORDER BY id makes PostgreSQL lock the rows in a consistent order.
    statement = select(Product).where(Product.id.in_(product_ids)).order_by(Product.id)

    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )

    return {product.id: product for product in session.scalars(statement)}


def _lock_prices_and_schemes(
    session: Session, retailer_id: UUID, product_ids: set[UUID]
) -> None:
    """Lock every price and scheme row the pricing service could select.

    calculate_retailer_product_price(for_update=True) selects, per line:
    - the RetailerProductPrice row for (retailer_id, product_id) with
      is_active = true;
    - the Scheme rows for product_id with is_active = true and
      starts_at <= now <= ends_at (or ends_at IS NULL).

    These queries drop the is_active and date conditions, so they lock a
    superset of those rows, taken in a fixed order: prices by product_id
    (unique per retailer and product), then schemes by product_id and id.
    The rows are only locked here; the pricing service still decides which
    of them apply.
    """
    session.scalars(
        select(RetailerProductPrice)
        .where(
            RetailerProductPrice.retailer_id == retailer_id,
            RetailerProductPrice.product_id.in_(product_ids),
        )
        .order_by(RetailerProductPrice.product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()

    session.scalars(
        select(Scheme)
        .where(Scheme.product_id.in_(product_ids))
        .order_by(Scheme.product_id, Scheme.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()


def _require_pending_with_items(order: Order, items: list[OrderItem]) -> None:
    if order.status != OrderStatus.PENDING_REVIEW:
        raise InvalidOrderStateError(
            f"Cannot confirm an order with status: {order.status}"
        )

    if not items:
        raise InvalidOrderStateError("Cannot confirm an empty order.")


# ---------------------------------------------------------------------------
# Validation and pricing (no changes to database state)
# ---------------------------------------------------------------------------


def _issue(item: OrderItem, code: LineIssueCode, message: str) -> LineIssue:
    return LineIssue(
        order_item_id=item.id,
        product_id=item.product_id,
        sku=item.sku,
        code=code,
        message=message,
    )


def _evaluate_lines(
    session: Session,
    retailer: Retailer,
    items: list[OrderItem],
    products_by_id: dict[UUID, Product],
    *,
    lock: bool,
) -> tuple[list[_LinePlan], list[LineIssue]]:
    """Price every line and collect every issue. Modifies nothing.

    With lock=True the pricing service runs with for_update=True. The caller
    must already hold the price and scheme locks (_lock_prices_and_schemes),
    so those requests acquire nothing new.
    """
    plans: list[_LinePlan] = []
    issues: list[LineIssue] = []
    product_line_counts = Counter(item.product_id for item in items)

    for item in items:
        if product_line_counts[item.product_id] > 1:
            issues.append(
                _issue(
                    item,
                    LineIssueCode.DUPLICATE_PRODUCT,
                    "This product appears on more than one line. Merge the "
                    "lines before confirming.",
                )
            )
            continue

        product = products_by_id.get(item.product_id)

        if product is None:
            issues.append(
                _issue(
                    item,
                    LineIssueCode.PRODUCT_UNAVAILABLE,
                    "Product does not exist.",
                )
            )
            continue

        try:
            pricing = calculate_retailer_product_price(
                session,
                retailer_id=retailer.id,
                product_id=product.id,
                quantity=item.quantity,
                product=product,
                for_update=lock,
            )
        except ProductNotFoundError as error:
            issues.append(_issue(item, LineIssueCode.PRODUCT_UNAVAILABLE, str(error)))
            continue
        except RetailerPriceNotFoundError as error:
            issues.append(_issue(item, LineIssueCode.PRICE_NOT_CONFIGURED, str(error)))
            continue
        except ApplicableSchemeError as error:
            issues.append(_issue(item, LineIssueCode.SCHEME_MISCONFIGURED, str(error)))
            continue
        except InsufficientStockError as error:
            issues.append(_issue(item, LineIssueCode.INSUFFICIENT_STOCK, str(error)))
            continue
        except ValueError as error:
            code = (
                LineIssueCode.INVALID_QUANTITY
                if item.quantity <= 0
                else LineIssueCode.SCHEME_MISCONFIGURED
            )
            issues.append(_issue(item, code, str(error)))
            continue

        if pricing.result.free_quantity > item.quantity:
            # order_items has CHECK (free_quantity <= quantity); report it
            # here instead of failing later with an IntegrityError.
            issues.append(
                _issue(
                    item,
                    LineIssueCode.FREE_QUANTITY_EXCEEDS_QUANTITY,
                    "The scheme gives more free cases than were ordered.",
                )
            )
            continue

        plans.append(_LinePlan(item=item, product=product, pricing=pricing))

    return plans, issues


def _fingerprint(
    order_id: UUID,
    lines: tuple[PreviewLine, ...],
    subtotal: Decimal,
    discount_total: Decimal,
    grand_total: Decimal,
) -> str:
    """SHA-256 of every figure the desk approves. Any change alters it.

    The figures are serialised as canonical JSON: keys sorted, no whitespace,
    lines sorted by item id, money as fixed-point strings. JSON quotes and
    escapes every string, so free text such as a scheme name cannot be
    mistaken for structure, and None (no scheme) stays distinct from "".
    """
    payload = {
        "order_id": str(order_id),
        "lines": [
            {
                "order_item_id": str(line.order_item_id),
                "product_id": str(line.product_id),
                "quantity": line.quantity,
                "free_quantity": line.free_quantity,
                "unit_price": _money_text(line.unit_price),
                "subtotal": _money_text(line.subtotal),
                "discount_amount": _money_text(line.discount_amount),
                "line_total": _money_text(line.line_total),
                "scheme_name": line.scheme_name,
            }
            for line in sorted(lines, key=lambda line: str(line.order_item_id))
        ],
        "subtotal": _money_text(subtotal),
        "discount_total": _money_text(discount_total),
        "grand_total": _money_text(grand_total),
    }

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _money_text(value: Decimal) -> str:
    """Two-decimal fixed-point text, never exponent notation."""
    return format(money(value), "f")


def _build_preview(
    order: Order,
    retailer: Retailer,
    plans: list[_LinePlan],
    issues: list[LineIssue],
) -> OrderConfirmationPreview:
    lines = tuple(
        PreviewLine(
            order_item_id=plan.item.id,
            product_id=plan.product.id,
            sku=plan.product.sku,
            product_name=plan.product.name,
            quantity=plan.item.quantity,
            stored_free_quantity=plan.item.free_quantity,
            free_quantity=plan.pricing.result.free_quantity,
            stock_required=plan.pricing.result.stock_required,
            stored_unit_price=plan.item.unit_price,
            unit_price=plan.pricing.result.unit_price,
            subtotal=plan.pricing.result.subtotal,
            stored_discount_amount=plan.item.discount_amount,
            discount_amount=plan.pricing.result.discount_amount,
            stored_line_total=plan.item.line_total,
            line_total=plan.pricing.result.grand_total,
            stored_scheme_name=plan.item.scheme_name,
            scheme_name=plan.pricing.scheme_name,
        )
        for plan in plans
    )

    subtotal = sum((line.subtotal for line in lines), ZERO)
    discount_total = sum((line.discount_amount for line in lines), ZERO)
    grand_total = sum((line.line_total for line in lines), ZERO)

    return OrderConfirmationPreview(
        order_id=order.id,
        lines=lines,
        issues=tuple(issues),
        subtotal=subtotal,
        discount_total=discount_total,
        grand_total=grand_total,
        available_credit=retailer.credit_limit - retailer.outstanding_balance,
        fingerprint=(
            None
            if issues
            else _fingerprint(order.id, lines, subtotal, discount_total, grand_total)
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def preview_order_confirmation(
    session: Session, order_id: UUID
) -> OrderConfirmationPreview:
    """Price a pending order with current data. Takes no locks, changes nothing.

    The returned fingerprint must be passed to confirm_order() to approve
    exactly these figures.
    """
    order = _load_order(session, order_id, lock=False)
    items = _load_items(session, order.id, lock=False)
    _require_pending_with_items(order, items)
    retailer = _load_retailer(session, order.retailer_id, lock=False)
    products_by_id = _load_products(session, items, lock=False)

    plans, issues = _evaluate_lines(
        session, retailer, items, products_by_id, lock=False
    )

    return _build_preview(order, retailer, plans, issues)


def confirm_order(
    session: Session,
    order_id: UUID,
    *,
    approved_fingerprint: str,
    confirmed_by_user_id: UUID | None,
) -> ConfirmationResult:
    """Confirm a pending order whose preview the caller approved.

    The session must hold no unsaved changes (UnsavedChangesError otherwise).

    Outcomes:
    - ALREADY_CONFIRMED: the order is CONFIRMED or EXPORTED; nothing changes.
    - ON_HOLD_CREDIT_LIMIT: the total exceeds available credit; the order
      moves to ON_HOLD and stock, balance and lines are left untouched. A
      placed_on_hold event records the credit figures.
    - CONFIRMED: stock deducted, balance increased, line snapshots written,
      and a confirmed event recorded, all inside one savepoint.

    Events record confirmed_by_user_id as the actor. ALREADY_CONFIRMED
    records nothing.

    Raises OrderValidationError (all line issues), PreviewOutdatedError
    (current figures differ from the approved preview), or another
    OrderConfirmationError. The caller must roll back on any exception.
    """
    # Checked before any lock or load, so the caller's work is never flushed
    # or overwritten.
    _ensure_clean_session(session)

    order = _load_order(session, order_id, lock=True)

    if order.status in ALREADY_CONFIRMED_STATUSES:
        return ConfirmationResult(
            order=order, outcome=ConfirmationOutcome.ALREADY_CONFIRMED
        )

    items = _load_items(session, order.id, lock=True)
    _require_pending_with_items(order, items)
    retailer = _load_retailer(session, order.retailer_id, lock=True)
    products_by_id = _load_products(session, items, lock=True)
    _lock_prices_and_schemes(session, retailer.id, {item.product_id for item in items})

    # Validate every line before anything is modified.
    plans, issues = _evaluate_lines(session, retailer, items, products_by_id, lock=True)

    if issues:
        raise OrderValidationError(tuple(issues))

    preview = _build_preview(order, retailer, plans, issues)

    if preview.fingerprint != approved_fingerprint:
        raise PreviewOutdatedError(preview)

    # Credit is checked before inventory is touched.
    if preview.exceeds_credit_limit:
        previous_status = order.status
        order.status = OrderStatus.ON_HOLD
        # Recorded with the status change: the figures that caused the hold,
        # so it can be explained later even after balances change.
        record_order_event(
            session,
            order,
            event_type=OrderEventType.PLACED_ON_HOLD,
            from_status=previous_status,
            to_status=OrderStatus.ON_HOLD,
            actor_user_id=confirmed_by_user_id,
            reason="Order total exceeds the retailer's available credit.",
            details={
                "required_credit": money_detail(preview.grand_total),
                "available_credit": money_detail(preview.available_credit),
                "credit_limit": money_detail(retailer.credit_limit),
                "outstanding_balance": money_detail(retailer.outstanding_balance),
                "shortfall": money_detail(
                    preview.grand_total - preview.available_credit
                ),
                "subtotal": money_detail(preview.subtotal),
                "discount_total": money_detail(preview.discount_total),
                "grand_total": money_detail(preview.grand_total),
            },
        )
        session.flush()
        return ConfirmationResult(
            order=order,
            outcome=ConfirmationOutcome.ON_HOLD_CREDIT_LIMIT,
            required_credit=preview.grand_total,
            available_credit=preview.available_credit,
        )

    # Apply all changes inside a SAVEPOINT: if anything fails here, every
    # change made in this block is rolled back together.
    with session.begin_nested():
        for plan in plans:
            result = plan.pricing.result
            plan.product.stock_quantity -= result.stock_required

            item = plan.item
            item.product_name = plan.product.name
            item.sku = plan.product.sku
            item.free_quantity = result.free_quantity
            item.unit_price = result.unit_price
            item.discount_amount = result.discount_amount
            item.line_total = result.grand_total
            item.scheme_name = plan.pricing.scheme_name

        retailer.outstanding_balance += preview.grand_total

        previous_status = order.status
        order.subtotal = preview.subtotal
        order.discount_total = preview.discount_total
        order.grand_total = preview.grand_total
        order.status = OrderStatus.CONFIRMED
        order.confirmed_at = datetime.now(UTC)
        order.confirmed_by_user_id = confirmed_by_user_id

        # Inside the savepoint: the event exists if and only if the
        # confirmation does.
        record_order_event(
            session,
            order,
            event_type=OrderEventType.CONFIRMED,
            from_status=previous_status,
            to_status=OrderStatus.CONFIRMED,
            actor_user_id=confirmed_by_user_id,
            details={
                "subtotal": money_detail(preview.subtotal),
                "discount_total": money_detail(preview.discount_total),
                "grand_total": money_detail(preview.grand_total),
            },
            now=order.confirmed_at,
        )

    return ConfirmationResult(order=order, outcome=ConfirmationOutcome.CONFIRMED)
