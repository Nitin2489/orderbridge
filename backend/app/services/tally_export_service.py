"""Tally-oriented CSV export of confirmed orders.

The CSV is a generic, line-level sales register meant to be mapped into
Tally (for example through TallyPrime's import mapping). It is NOT a native
Tally import format: ledger mappings, GST configuration, HSN codes, voucher
types and invoice numbering must be set up in Tally. This module never
invents any of them. Amounts exclude GST (prototype assumption), and every
row says so in tax_treatment.

Eligible orders: status confirmed and no export_batch_id. Exporting sets
status exported, links the order to its batch and records one exported
OrderEvent per order, in the same transaction as the batch and its CSV.

Idempotency and locking
-----------------------
create_export() takes a transaction-scoped advisory lock derived from the
client_request_id, so requests sharing an id are serialised and the second
sees the first's committed batch (the unique constraint on
client_request_id backs this up). It then locks the selected orders rows in
ascending id order (FOR UPDATE + populate_existing). Retailers and order
items are read without locks: a confirmed order's lines and totals no
longer change. confirm_order() and reject_order() each lock a single order
first and never wait for a second order, so they cannot deadlock with this.

The service never commits; the caller owns the transaction.
"""

import csv
import hashlib
import io
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, defer

from app.models import (
    EXPORT_BATCH_NUMBER_SEQUENCE,
    ExportBatch,
    Order,
    OrderEventType,
    OrderItem,
    OrderStatus,
    Retailer,
    User,
)
from app.services.order_events import money_detail, record_order_event
from app.services.order_service import _ensure_clean_session
from app.services.retailer_order_service import (
    BUSINESS_TIMEZONE,
    request_fingerprint,
)

CSV_COLUMNS = (
    "export_batch_number",
    "order_number",
    "order_date",
    "confirmed_at",
    "party_name",
    "party_address",
    "line_number",
    "sku",
    "item_name",
    "quantity",
    "free_quantity",
    "unit",
    "rate",
    "gross_amount",
    "discount_amount",
    "net_amount",
    "scheme_name",
    "order_subtotal",
    "order_discount_total",
    "order_net_total",
    "tax_treatment",
)
TAX_TREATMENT = "exclusive (GST not calculated)"
MAX_EXPLICIT_ORDERS = 500
CSV_BOM = "﻿"
CENT = Decimal("0.01")

# Spreadsheet formula triggers (OWASP CSV injection), plus tab and carriage
# return, which some spreadsheets also treat as formula starts.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


# ---------------------------------------------------------------------------
# Errors and results
# ---------------------------------------------------------------------------


class ExportError(Exception):
    """Base exception for export creation."""


class ExportRequestConflictError(ExportError):
    """The client_request_id was already used for a different request."""


class NoEligibleOrdersError(ExportError):
    """No order matches the request."""


@dataclass(frozen=True)
class IneligibleOrder:
    order_id: UUID
    reason: str  # not_found, already_exported, status_<status>, confirmed_after_cutoff


class IneligibleOrdersError(ExportError):
    """Explicitly requested orders cannot be exported; nothing was exported."""

    def __init__(self, orders: tuple[IneligibleOrder, ...]) -> None:
        self.orders = orders
        super().__init__(f"{len(orders)} requested order(s) cannot be exported.")


@dataclass(frozen=True)
class ExportResult:
    batch: ExportBatch
    created: bool  # False when an earlier identical request was returned


@dataclass(frozen=True)
class EligibleSummary:
    order_count: int
    net_total: Decimal


@dataclass(frozen=True)
class BatchRow:
    batch: ExportBatch  # csv_content deferred
    created_by_name: str | None


@dataclass(frozen=True)
class BatchPage:
    rows: list[BatchRow]
    total: int


@dataclass(frozen=True)
class ExportedOrder:
    order: Order
    retailer_business_name: str


@dataclass(frozen=True)
class BatchView:
    batch: ExportBatch  # csv_content deferred
    created_by_name: str | None
    orders: list[ExportedOrder]


# ---------------------------------------------------------------------------
# Pure CSV helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportLine:
    line_number: int
    sku: str
    item_name: str
    quantity: int
    free_quantity: int
    unit: str
    rate: Decimal
    discount_amount: Decimal
    net_amount: Decimal
    scheme_name: str | None


@dataclass(frozen=True)
class ExportOrder:
    order_number: str
    created_at: datetime
    confirmed_at: datetime
    party_name: str
    party_address: str
    subtotal: Decimal
    discount_total: Decimal
    net_total: Decimal
    lines: tuple[ExportLine, ...]


def protect_text(value: str | None) -> str:
    """Text for a CSV cell: '' for None; a leading apostrophe stops a value
    that starts like a formula from being evaluated by a spreadsheet."""
    if value is None:
        return ""
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def format_money(value: Decimal) -> str:
    """Exactly two decimal places, no separators or symbols, never a float.

    Stored amounts already have two places; anything finer is an error
    rather than being rounded silently.
    """
    if value != value.quantize(CENT):
        raise ValueError("Monetary values must have at most two decimal places.")
    return format(value.quantize(CENT), "f")


def build_rows(batch_number: str, orders: Sequence[ExportOrder]) -> list[list[str]]:
    """One row per order line, in the given order order and line_number order."""
    rows = []

    for order in orders:
        order_date = order.created_at.astimezone(BUSINESS_TIMEZONE).strftime("%d-%m-%Y")
        confirmed_at = order.confirmed_at.astimezone(BUSINESS_TIMEZONE).isoformat(
            timespec="seconds"
        )

        for line in sorted(order.lines, key=lambda line: line.line_number):
            rows.append(
                [
                    batch_number,
                    order.order_number,
                    order_date,
                    confirmed_at,
                    protect_text(order.party_name),
                    protect_text(order.party_address),
                    str(line.line_number),
                    protect_text(line.sku),
                    protect_text(line.item_name),
                    str(line.quantity),
                    str(line.free_quantity),
                    protect_text(line.unit),
                    format_money(line.rate),
                    format_money(line.net_amount + line.discount_amount),
                    format_money(line.discount_amount),
                    format_money(line.net_amount),
                    protect_text(line.scheme_name),
                    format_money(order.subtotal),
                    format_money(order.discount_total),
                    format_money(order.net_total),
                    TAX_TREATMENT,
                ]
            )

    return rows


def render_csv(rows: Sequence[Sequence[str]]) -> str:
    """The CSV text: byte order mark, header, rows; CRLF line endings."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows(rows)
    return CSV_BOM + buffer.getvalue()


def csv_bytes(content: str) -> bytes:
    """The exact bytes served for a CSV (UTF-8, BOM included)."""
    return content.encode("utf-8")


def csv_checksum(content: str) -> str:
    return hashlib.sha256(csv_bytes(content)).hexdigest()


def export_request_fingerprint(
    confirmed_before: datetime | None, order_ids: Sequence[UUID] | None
) -> str:
    """Normalised: the cutoff in UTC, the order ids sorted."""
    return request_fingerprint(
        {
            "kind": "tally_export",
            "confirmed_before": (
                confirmed_before.astimezone(UTC).isoformat()
                if confirmed_before is not None
                else None
            ),
            "order_ids": (
                sorted(str(order_id) for order_id in order_ids)
                if order_ids is not None
                else None
            ),
        }
    )


# ---------------------------------------------------------------------------
# Export creation
# ---------------------------------------------------------------------------


def _request_lock_key(client_request_id: UUID) -> int:
    """A signed 64-bit advisory lock key derived from the request id."""
    return int.from_bytes(client_request_id.bytes[:8], "big", signed=True)


def _validate_request(
    confirmed_before: datetime | None, order_ids: Sequence[UUID] | None
) -> None:
    if confirmed_before is not None and confirmed_before.tzinfo is None:
        raise ValueError("confirmed_before must be timezone-aware.")
    if order_ids is not None:
        if not order_ids:
            raise ValueError("order_ids must not be empty.")
        if len(order_ids) > MAX_EXPLICIT_ORDERS:
            raise ValueError(f"At most {MAX_EXPLICIT_ORDERS} order_ids are allowed.")
        if len(set(order_ids)) != len(order_ids):
            raise ValueError("order_ids must not repeat.")


def _ineligibility(
    order: Order | None, confirmed_before: datetime | None
) -> str | None:
    if order is None:
        return "not_found"
    if order.export_batch_id is not None or order.status == OrderStatus.EXPORTED:
        return "already_exported"
    if order.status != OrderStatus.CONFIRMED:
        return f"status_{order.status}"
    if confirmed_before is not None and (
        order.confirmed_at is None or order.confirmed_at >= confirmed_before
    ):
        return "confirmed_after_cutoff"
    return None


def _lock_orders(
    session: Session,
    confirmed_before: datetime | None,
    order_ids: Sequence[UUID] | None,
) -> list[Order]:
    """Lock the orders to export (ascending id) and check every one of them."""
    statement = select(Order).order_by(Order.id)

    if order_ids is not None:
        # Lock every requested order, eligible or not, so each can be checked.
        statement = statement.where(Order.id.in_(order_ids))
    else:
        statement = statement.where(
            Order.status == OrderStatus.CONFIRMED, Order.export_batch_id.is_(None)
        )
        if confirmed_before is not None:
            statement = statement.where(Order.confirmed_at < confirmed_before)

    orders = list(
        session.scalars(
            statement.with_for_update().execution_options(populate_existing=True)
        )
    )

    if order_ids is not None:
        found = {order.id: order for order in orders}
        problems = []
        for order_id in sorted(order_ids):
            reason = _ineligibility(found.get(order_id), confirmed_before)
            if reason is not None:
                problems.append(IneligibleOrder(order_id=order_id, reason=reason))
        if problems:
            raise IneligibleOrdersError(tuple(problems))

    return orders


def _export_orders(session: Session, orders: list[Order]) -> list[ExportOrder]:
    """CSV inputs for the locked orders, in two queries (no N+1)."""
    order_ids = [order.id for order in orders]

    lines: dict[UUID, list[ExportLine]] = defaultdict(list)
    for item in session.scalars(
        select(OrderItem)
        .where(OrderItem.order_id.in_(order_ids))
        .order_by(OrderItem.order_id, OrderItem.line_number)
    ):
        lines[item.order_id].append(
            ExportLine(
                line_number=item.line_number,
                sku=item.sku,
                item_name=item.product_name,
                quantity=item.quantity,
                free_quantity=item.free_quantity,
                unit=item.unit_name,
                rate=item.unit_price,
                discount_amount=item.discount_amount,
                net_amount=item.line_total,
                scheme_name=item.scheme_name,
            )
        )

    parties = {
        retailer_id: (name, address)
        for retailer_id, name, address in session.execute(
            select(Retailer.id, Retailer.business_name, Retailer.address).where(
                Retailer.id.in_({order.retailer_id for order in orders})
            )
        )
    }

    export_orders = [
        ExportOrder(
            order_number=order.order_number,
            created_at=order.created_at,
            confirmed_at=order.confirmed_at,
            party_name=parties[order.retailer_id][0],
            party_address=parties[order.retailer_id][1],
            subtotal=order.subtotal,
            discount_total=order.discount_total,
            net_total=order.grand_total,
            lines=tuple(lines[order.id]),
        )
        for order in orders
    ]
    # CSV order: by confirmation time, then order number.
    return sorted(export_orders, key=lambda o: (o.confirmed_at, o.order_number))


def _next_batch_number(session: Session, now: datetime) -> str:
    """EX-YYYY-NNNNNN; YYYY is the Asia/Kolkata business year of `now`."""
    value = session.scalar(select(EXPORT_BATCH_NUMBER_SEQUENCE.next_value()))
    return f"EX-{now.astimezone(BUSINESS_TIMEZONE).year:04d}-{value:06d}"


def create_export(
    session: Session,
    *,
    exported_by_user_id: UUID,
    client_request_id: UUID,
    confirmed_before: datetime | None = None,
    order_ids: Sequence[UUID] | None = None,
    now: datetime | None = None,
) -> ExportResult:
    """Export eligible confirmed orders as one batch, or return the batch an
    identical earlier request created.

    The whole CSV is built before any order changes. Raises
    ExportRequestConflictError, IneligibleOrdersError (explicit order_ids;
    nothing is skipped), NoEligibleOrdersError or ValueError (malformed
    request). The session must hold no unsaved changes; the caller commits.
    """
    _validate_request(confirmed_before, order_ids)
    _ensure_clean_session(session)
    fingerprint = export_request_fingerprint(confirmed_before, order_ids)

    # Serialise requests that share an id before checking it.
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _request_lock_key(client_request_id)},
    )
    existing = session.scalar(
        select(ExportBatch).where(ExportBatch.client_request_id == client_request_id)
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise ExportRequestConflictError(
                "This client_request_id was already used for a different export."
            )
        return ExportResult(batch=existing, created=False)

    orders = _lock_orders(session, confirmed_before, order_ids)
    if not orders:
        raise NoEligibleOrdersError("No confirmed orders are waiting for export.")

    # Build the complete CSV before anything is modified.
    now = now or datetime.now(UTC)
    batch_number = _next_batch_number(session, now)
    content = render_csv(build_rows(batch_number, _export_orders(session, orders)))
    net_total = sum((order.grand_total for order in orders), Decimal("0.00"))

    batch = ExportBatch(
        id=uuid4(),
        batch_number=batch_number,
        created_by_user_id=exported_by_user_id,
        created_at=now,
        order_count=len(orders),
        net_total=net_total,
        csv_content=content,
        csv_sha256=csv_checksum(content),
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
    )
    session.add(batch)
    # Insert the batch before orders reference it (no ORM relationship
    # orders the two statements).
    session.flush()

    for order in orders:
        if order.export_batch_id is not None:
            raise RuntimeError("An exported order cannot be exported again.")

        previous_status = order.status
        order.status = OrderStatus.EXPORTED
        order.export_batch_id = batch.id
        record_order_event(
            session,
            order,
            event_type=OrderEventType.EXPORTED,
            from_status=previous_status,
            to_status=OrderStatus.EXPORTED,
            actor_user_id=exported_by_user_id,
            details={
                "batch_id": str(batch.id),
                "batch_number": batch_number,
                "order_net_total": money_detail(order.grand_total),
            },
            now=now,
        )

    session.flush()
    return ExportResult(batch=batch, created=True)


# ---------------------------------------------------------------------------
# Read-only queries
# ---------------------------------------------------------------------------


def eligible_summary(session: Session) -> EligibleSummary:
    """Count and net total of orders waiting for export. No locks."""
    count, total = session.execute(
        select(
            func.count(Order.id), func.coalesce(func.sum(Order.grand_total), 0)
        ).where(Order.status == OrderStatus.CONFIRMED, Order.export_batch_id.is_(None))
    ).one()
    return EligibleSummary(order_count=count, net_total=Decimal(total).quantize(CENT))


def list_batches(session: Session, *, limit: int = 20, offset: int = 0) -> BatchPage:
    """Export batches, newest first (created_at, then id); CSV not loaded."""
    total = session.scalar(select(func.count(ExportBatch.id)))
    rows = session.execute(
        select(ExportBatch, User.full_name)
        .outerjoin(User, User.id == ExportBatch.created_by_user_id)
        .options(defer(ExportBatch.csv_content))
        .order_by(ExportBatch.created_at.desc(), ExportBatch.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return BatchPage(
        rows=[BatchRow(batch=batch, created_by_name=name) for batch, name in rows],
        total=total or 0,
    )


def get_batch(session: Session, batch_id: UUID) -> BatchView | None:
    """A batch's metadata and its orders (by order number); CSV not loaded."""
    row = session.execute(
        select(ExportBatch, User.full_name)
        .outerjoin(User, User.id == ExportBatch.created_by_user_id)
        .options(defer(ExportBatch.csv_content))
        .where(ExportBatch.id == batch_id)
    ).one_or_none()

    if row is None:
        return None

    batch, created_by_name = row
    orders = session.execute(
        select(Order, Retailer.business_name)
        .join(Retailer, Retailer.id == Order.retailer_id)
        .where(Order.export_batch_id == batch_id)
        .order_by(Order.order_number)
    ).all()

    return BatchView(
        batch=batch,
        created_by_name=created_by_name,
        orders=[ExportedOrder(order=o, retailer_business_name=n) for o, n in orders],
    )


def get_batch_csv(session: Session, batch_id: UUID) -> tuple[str, str] | None:
    """(batch_number, exact CSV text) for download, or None. Read-only."""
    row = session.execute(
        select(ExportBatch.batch_number, ExportBatch.csv_content).where(
            ExportBatch.id == batch_id
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])
