"""Retailer ordering: submit, repeat, list and read a retailer's own orders.

Submission creates a PENDING_REVIEW order for the order desk to confirm
(order_service.confirm_order). It never deducts stock or reserves credit;
stock is checked here only so the retailer gets early feedback, and is
checked again, under lock, at confirmation.

Prices, schemes, free quantities, discounts and totals always come from
pricing_service.calculate_retailer_product_price(), the same function
confirm_order() uses, and every line stores exactly that snapshot. Nothing
monetary is taken from the client. If any line is invalid, every problem is
reported and nothing is created.

Idempotency and concurrency
---------------------------
Every submission carries a client_request_id. The retailer row is locked
(SELECT ... FOR UPDATE) before the id is looked up, so concurrent
submissions for one retailer are serialised:

- same id and same normalised request (fingerprint): the original order is
  returned and nothing new is created;
- same id, different request: IdempotencyConflictError;
- new id: a new order.

The unique constraint on (retailer_id, client_request_id) backs this up in
the database. Lock order: this module only locks the retailers row, which
is compatible with auth_service (users -> retailers) and with
order_service.confirm_order (... -> retailers -> products ...).

The service never commits; the caller owns the transaction.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ORDER_NUMBER_SEQUENCE,
    Order,
    OrderEventType,
    OrderItem,
    OrderSource,
    OrderStatus,
    Product,
    Retailer,
)
from app.services.order_events import record_order_event
from app.services.order_service import LineIssueCode
from app.services.pricing_service import (
    ApplicableSchemeError,
    InsufficientStockError,
    ProductNotFoundError,
    RetailerPriceNotFoundError,
    RetailerProductPricing,
    calculate_retailer_product_price,
)

MAX_ORDER_LINES = 50
ZERO = Decimal("0.00")

# The year in an order number is the Indian business year.
BUSINESS_TIMEZONE = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Errors and results
# ---------------------------------------------------------------------------


class OrderSubmissionError(Exception):
    """Base exception for retailer order submission."""


class RetailerNotOrderableError(OrderSubmissionError):
    """The retailer record is missing or inactive."""


class SourceOrderNotFoundError(OrderSubmissionError):
    """The order to repeat does not exist or belongs to another retailer."""


class IdempotencyConflictError(OrderSubmissionError):
    """The client_request_id was already used for a different request."""


class InvalidOrderRequestError(OrderSubmissionError):
    """The order as a whole is invalid (no lines, or too many lines).

    The request schema prevents this for submissions; it can occur when
    repeating an order that was not created through the retailer app.
    """


@dataclass(frozen=True)
class SubmissionIssue:
    line_number: int
    product_id: UUID
    code: LineIssueCode
    message: str


class OrderLinesInvalidError(OrderSubmissionError):
    """One or more lines cannot be ordered. Lists every issue."""

    def __init__(self, issues: tuple[SubmissionIssue, ...]) -> None:
        self.issues = issues
        super().__init__(f"{len(issues)} order line(s) cannot be ordered.")


@dataclass(frozen=True)
class OrderLineInput:
    product_id: UUID
    quantity: int


@dataclass(frozen=True)
class SubmissionResult:
    order: Order
    created: bool  # False when an earlier identical submission was returned


@dataclass(frozen=True)
class OrderPage:
    orders: list[tuple[Order, int]]  # (order, number of lines)
    total: int


@dataclass(frozen=True)
class _PricedLine:
    line: OrderLineInput
    product: Product
    pricing: RetailerProductPricing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _normalise_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    return notes.strip() or None


def request_fingerprint(payload: dict) -> str:
    """SHA-256 of a request's canonical JSON (sorted keys, no whitespace)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _submission_fingerprint(lines: list[OrderLineInput], notes: str | None) -> str:
    """Normalised: line order does not matter, notes are trimmed."""
    return request_fingerprint(
        {
            "kind": "submit",
            "items": sorted([str(line.product_id), line.quantity] for line in lines),
            "notes": notes,
        }
    )


def _repeat_fingerprint(source_order_id: UUID) -> str:
    return request_fingerprint(
        {"kind": "repeat", "source_order_id": str(source_order_id)}
    )


def _capture_fingerprint(capture_id: UUID) -> str:
    return request_fingerprint({"kind": "capture", "capture_id": str(capture_id)})


def _lock_retailer(session: Session, retailer_id: UUID) -> Retailer:
    retailer = session.scalar(
        select(Retailer)
        .where(Retailer.id == retailer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if retailer is None or not retailer.is_active:
        raise RetailerNotOrderableError("Retailer is unavailable.")

    return retailer


def _existing_submission(
    session: Session, retailer_id: UUID, client_request_id: UUID, fingerprint: str
) -> Order | None:
    """The order already created for this client_request_id, if any."""
    order = session.scalar(
        select(Order)
        .where(
            Order.retailer_id == retailer_id,
            Order.client_request_id == client_request_id,
        )
        .options(selectinload(Order.items))
    )

    if order is not None and order.request_fingerprint != fingerprint:
        raise IdempotencyConflictError(
            "This client_request_id was already used for a different order."
        )

    return order


def _issue(
    line_number: int, line: OrderLineInput, code: LineIssueCode, message: str
) -> SubmissionIssue:
    return SubmissionIssue(
        line_number=line_number,
        product_id=line.product_id,
        code=code,
        message=message,
    )


def _price_lines(
    session: Session, retailer: Retailer, lines: list[OrderLineInput]
) -> list[_PricedLine]:
    """Price every line with the shared pricing function; collect all issues.

    Products are loaded in one query. Pricing then reads each line's price
    and scheme (bounded by MAX_ORDER_LINES), exactly as confirmation does.
    """
    products = {
        product.id: product
        for product in session.scalars(
            select(Product).where(Product.id.in_({line.product_id for line in lines}))
        )
    }
    counts = Counter(line.product_id for line in lines)
    priced: list[_PricedLine] = []
    issues: list[SubmissionIssue] = []

    for line_number, line in enumerate(lines, start=1):
        if counts[line.product_id] > 1:
            issues.append(
                _issue(
                    line_number,
                    line,
                    LineIssueCode.DUPLICATE_PRODUCT,
                    "This product appears on more than one line.",
                )
            )
            continue

        product = products.get(line.product_id)

        if product is None:
            issues.append(
                _issue(
                    line_number,
                    line,
                    LineIssueCode.PRODUCT_UNAVAILABLE,
                    "Product does not exist or is inactive.",
                )
            )
            continue

        try:
            pricing = calculate_retailer_product_price(
                session,
                retailer_id=retailer.id,
                product_id=product.id,
                quantity=line.quantity,
                product=product,
            )
        except ProductNotFoundError as error:
            code, message = LineIssueCode.PRODUCT_UNAVAILABLE, str(error)
        except RetailerPriceNotFoundError as error:
            code, message = LineIssueCode.PRICE_NOT_CONFIGURED, str(error)
        except ApplicableSchemeError as error:
            code, message = LineIssueCode.SCHEME_MISCONFIGURED, str(error)
        except InsufficientStockError as error:
            code, message = LineIssueCode.INSUFFICIENT_STOCK, str(error)
        except ValueError as error:
            code, message = LineIssueCode.INVALID_QUANTITY, str(error)
        else:
            if pricing.result.free_quantity > line.quantity:
                code = LineIssueCode.FREE_QUANTITY_EXCEEDS_QUANTITY
                message = "The scheme gives more free units than were ordered."
            else:
                priced.append(_PricedLine(line=line, product=product, pricing=pricing))
                continue

        issues.append(_issue(line_number, line, code, message))

    if issues:
        raise OrderLinesInvalidError(tuple(issues))

    return priced


def _next_order_number(session: Session, now: datetime) -> str:
    """OB-YYYY-NNNNNN from the database sequence (unique, increasing).

    YYYY is the year of `now` in Asia/Kolkata, so an order placed just after
    midnight IST on 1 January carries the new year even though it is still
    the previous year in UTC. NNNNNN comes from one global sequence that is
    never reset, so numbers stay unique across years.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware.")

    business_year = now.astimezone(BUSINESS_TIMEZONE).year
    value = session.scalar(select(ORDER_NUMBER_SEQUENCE.next_value()))
    return f"OB-{business_year:04d}-{value:06d}"


def _create_order(
    session: Session,
    retailer: Retailer,
    lines: list[OrderLineInput],
    *,
    client_request_id: UUID,
    fingerprint: str,
    notes: str | None,
    now: datetime,
    source_order_id: UUID | None = None,
    source: OrderSource = OrderSource.RETAILER_APP,
    actor_user_id: UUID | None = None,
    event_details: dict | None = None,
) -> Order:
    if not lines:
        raise InvalidOrderRequestError("The order has no lines.")
    if len(lines) > MAX_ORDER_LINES:
        raise InvalidOrderRequestError(
            f"An order may have at most {MAX_ORDER_LINES} lines."
        )

    priced = _price_lines(session, retailer, lines)

    items = [
        OrderItem(
            line_number=line_number,
            product_id=entry.product.id,
            product_name=entry.product.name,
            unit_name=entry.product.unit_name,
            sku=entry.product.sku,
            quantity=entry.line.quantity,
            free_quantity=entry.pricing.result.free_quantity,
            unit_price=entry.pricing.result.unit_price,
            discount_amount=entry.pricing.result.discount_amount,
            line_total=entry.pricing.result.grand_total,
            scheme_name=entry.pricing.scheme_name,
        )
        for line_number, entry in enumerate(priced, start=1)
    ]

    order = Order(
        order_number=_next_order_number(session, now),
        retailer_id=retailer.id,
        source=source,
        status=OrderStatus.PENDING_REVIEW,
        subtotal=sum((e.pricing.result.subtotal for e in priced), ZERO),
        discount_total=sum((e.pricing.result.discount_amount for e in priced), ZERO),
        grand_total=sum((e.pricing.result.grand_total for e in priced), ZERO),
        notes=notes,
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        created_at=now,
        items=items,
    )
    session.add(order)

    # Only reached for a newly created order, never for an idempotent retry.
    # The actor is the retailer's own user, from the locked retailer row,
    # unless the caller acted for the retailer (a captured message).
    details = {"client_request_id": str(client_request_id)}
    if source_order_id is not None:
        details["repeat_of_order_id"] = str(source_order_id)
    if event_details:
        details.update(event_details)
    record_order_event(
        session,
        order,
        event_type=OrderEventType.SUBMITTED,
        from_status=None,
        to_status=OrderStatus.PENDING_REVIEW,
        actor_user_id=actor_user_id or retailer.user_id,
        details=details,
        now=now,
    )

    session.flush()
    return order


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def submit_order(
    session: Session,
    retailer: Retailer,
    lines: list[OrderLineInput],
    *,
    client_request_id: UUID,
    notes: str | None = None,
    now: datetime | None = None,
) -> SubmissionResult:
    """Create a PENDING_REVIEW order, or return the one already created.

    Raises OrderLinesInvalidError (every line issue), IdempotencyConflictError,
    InvalidOrderRequestError or RetailerNotOrderableError. The caller commits
    on success.
    """
    notes = _normalise_notes(notes)
    fingerprint = _submission_fingerprint(lines, notes)
    retailer = _lock_retailer(session, retailer.id)

    existing = _existing_submission(
        session, retailer.id, client_request_id, fingerprint
    )
    if existing is not None:
        return SubmissionResult(order=existing, created=False)

    order = _create_order(
        session,
        retailer,
        lines,
        client_request_id=client_request_id,
        fingerprint=fingerprint,
        notes=notes,
        now=now or _utcnow(),
    )
    return SubmissionResult(order=order, created=True)


def repeat_order(
    session: Session,
    retailer: Retailer,
    source_order_id: UUID,
    *,
    client_request_id: UUID,
    now: datetime | None = None,
) -> SubmissionResult:
    """Submit a new order with the same products and quantities as one of
    the retailer's own orders (any status), at current prices, schemes and
    stock. Lines that can no longer be ordered are reported, never dropped.

    Raises SourceOrderNotFoundError plus everything submit_order() raises.

    The idempotency key is checked before the source order is read, so a
    retry of a completed repeat returns the order it created even if the
    source order is no longer available.
    """
    fingerprint = _repeat_fingerprint(source_order_id)
    retailer = _lock_retailer(session, retailer.id)

    existing = _existing_submission(
        session, retailer.id, client_request_id, fingerprint
    )
    if existing is not None:
        return SubmissionResult(order=existing, created=False)

    source = get_order(session, retailer, source_order_id)
    if source is None:
        raise SourceOrderNotFoundError("Order not found.")

    lines = [
        OrderLineInput(product_id=item.product_id, quantity=item.quantity)
        for item in source.items
    ]
    order = _create_order(
        session,
        retailer,
        lines,
        client_request_id=client_request_id,
        fingerprint=fingerprint,
        notes=None,
        now=now or _utcnow(),
        source_order_id=source.id,
    )
    return SubmissionResult(order=order, created=True)


def submit_capture_order(
    session: Session,
    retailer_id: UUID,
    lines: list[OrderLineInput],
    *,
    capture_id: UUID,
    client_request_id: UUID,
    actor_user_id: UUID,
    now: datetime | None = None,
) -> SubmissionResult:
    """Create the PENDING_REVIEW order for a reviewed captured message.

    The same path as submit_order(): the retailer row is locked, the
    client_request_id is idempotent per retailer, and every line is priced
    by the shared pricing service (every issue reported, nothing created on
    any). Only the source (ai_text), the event actor (the person who
    submitted the capture) and the event details (capture_id) differ. Stock
    is not deducted and credit is not used: the order desk confirms the
    order through the existing confirmation workflow.

    Raises everything submit_order() raises. The caller commits.
    """
    fingerprint = _capture_fingerprint(capture_id)
    retailer = _lock_retailer(session, retailer_id)

    existing = _existing_submission(
        session, retailer.id, client_request_id, fingerprint
    )
    if existing is not None:
        return SubmissionResult(order=existing, created=False)

    order = _create_order(
        session,
        retailer,
        lines,
        client_request_id=client_request_id,
        fingerprint=fingerprint,
        notes=None,
        now=now or _utcnow(),
        source=OrderSource.AI_TEXT,
        actor_user_id=actor_user_id,
        event_details={"capture_id": str(capture_id)},
    )
    return SubmissionResult(order=order, created=True)


def list_orders(
    session: Session,
    retailer: Retailer,
    *,
    status: OrderStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> OrderPage:
    """The retailer's orders, newest first (created_at, then id)."""
    filters = [Order.retailer_id == retailer.id]
    if status is not None:
        filters.append(Order.status == status)

    total = session.scalar(select(func.count(Order.id)).where(*filters))
    orders = list(
        session.scalars(
            select(Order)
            .where(*filters)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )

    counts: dict[UUID, int] = {}
    if orders:
        counts = dict(
            session.execute(
                select(OrderItem.order_id, func.count(OrderItem.id))
                .where(OrderItem.order_id.in_([order.id for order in orders]))
                .group_by(OrderItem.order_id)
            ).all()
        )

    return OrderPage(
        orders=[(order, counts.get(order.id, 0)) for order in orders],
        total=total or 0,
    )


def get_order(session: Session, retailer: Retailer, order_id: UUID) -> Order | None:
    """One of the retailer's orders with its lines, or None.

    None for missing orders and other retailers' orders alike.
    """
    return session.scalar(
        select(Order)
        .where(Order.id == order_id, Order.retailer_id == retailer.id)
        .options(selectinload(Order.items))
    )
