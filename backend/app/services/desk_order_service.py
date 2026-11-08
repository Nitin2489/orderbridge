"""Order desk: review queue, order detail, preview mapping and rejection.

Confirmation itself (pricing, stock, schemes, credit, locking, the hold and
its audit event) is order_service.confirm_order(); this module never
recalculates any of it. Rejection is implemented here and follows the same
rules as confirmation: clean session, the order row locked first, the audit
event written in the same transaction, and no commit (the caller owns the
transaction).

Lock order: reject_order() locks only the orders row, which is the first
lock confirm_order() takes, so a rejection and a confirmation of the same
order are serialised and cannot deadlock.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    Order,
    OrderEvent,
    OrderEventType,
    OrderItem,
    OrderStatus,
    Retailer,
    User,
)
from app.services.order_events import record_order_event
from app.services.order_service import (
    InvalidOrderStateError,
    OrderConfirmationPreview,
    OrderNotFoundError,
    _ensure_clean_session,
    preview_order_confirmation,
)

REJECTION_REASON_MIN_LENGTH = 3
REJECTION_REASON_MAX_LENGTH = 500

REJECTABLE_STATUSES = frozenset({OrderStatus.PENDING_REVIEW, OrderStatus.ON_HOLD})

# Queues of work awaiting the desk are served oldest first; everything else
# (including the unfiltered list) newest first. Ties break on id.
OLDEST_FIRST_STATUSES = frozenset({OrderStatus.PENDING_REVIEW, OrderStatus.ON_HOLD})


class InvalidRejectionReasonError(ValueError):
    """The rejection reason is empty, too short or too long after trimming."""


class InvalidQueueFilterError(ValueError):
    """The queue filters are inconsistent (e.g. an inverted date range)."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueRow:
    order: Order
    retailer_business_name: str
    item_count: int


@dataclass(frozen=True)
class QueuePage:
    rows: list[QueueRow]
    total: int


@dataclass(frozen=True)
class EventRow:
    event: OrderEvent
    actor_name: str | None


@dataclass(frozen=True)
class DeskOrderView:
    order: Order  # items loaded, sorted by line_number
    retailer: Retailer
    events: list[EventRow]  # chronological


@dataclass(frozen=True)
class DeskPreview:
    preview: OrderConfirmationPreview
    line_numbers: dict[UUID, int]  # order_item_id -> line_number


@dataclass(frozen=True)
class RejectionResult:
    order: Order
    # The recorded rejection; None only for an order rejected before audit
    # events existed.
    rejection: OrderEvent | None
    already_rejected: bool


# ---------------------------------------------------------------------------
# Queue and detail
# ---------------------------------------------------------------------------


def list_queue(
    session: Session,
    *,
    status: OrderStatus | None = None,
    retailer_id: UUID | None = None,
    search: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> QueuePage:
    """Orders across all retailers for the desk.

    created_from is inclusive and created_to exclusive; both must be
    timezone-aware. search matches order number or retailer business name,
    case-insensitively, with % and _ taken literally. Ordering: oldest first
    for pending_review and on_hold, newest first otherwise; ties by id.
    """
    for value in (created_from, created_to):
        if value is not None and value.tzinfo is None:
            raise InvalidQueueFilterError("Date filters must include a timezone.")
    if created_from and created_to and created_from >= created_to:
        raise InvalidQueueFilterError("created_from must be before created_to.")

    filters = []
    if status is not None:
        filters.append(Order.status == status)
    if retailer_id is not None:
        filters.append(Order.retailer_id == retailer_id)
    if search:
        filters.append(
            or_(
                Order.order_number.icontains(search, autoescape=True),
                Retailer.business_name.icontains(search, autoescape=True),
            )
        )
    if created_from is not None:
        filters.append(Order.created_at >= created_from)
    if created_to is not None:
        filters.append(Order.created_at < created_to)

    total = session.scalar(
        select(func.count(Order.id))
        .join(Retailer, Retailer.id == Order.retailer_id)
        .where(*filters)
    )

    if status in OLDEST_FIRST_STATUSES:
        ordering = (Order.created_at.asc(), Order.id.asc())
    else:
        ordering = (Order.created_at.desc(), Order.id.desc())

    rows = session.execute(
        select(Order, Retailer.business_name)
        .join(Retailer, Retailer.id == Order.retailer_id)
        .where(*filters)
        .order_by(*ordering)
        .limit(limit)
        .offset(offset)
    ).all()

    counts: dict[UUID, int] = {}
    if rows:
        counts = dict(
            session.execute(
                select(OrderItem.order_id, func.count(OrderItem.id))
                .where(OrderItem.order_id.in_([order.id for order, _ in rows]))
                .group_by(OrderItem.order_id)
            ).all()
        )

    return QueuePage(
        rows=[
            QueueRow(
                order=order,
                retailer_business_name=business_name,
                item_count=counts.get(order.id, 0),
            )
            for order, business_name in rows
        ],
        total=total or 0,
    )


def _events(session: Session, order_id: UUID) -> list[EventRow]:
    """The order's events, oldest first, with each actor's name."""
    rows = session.execute(
        select(OrderEvent, User.full_name)
        .outerjoin(User, User.id == OrderEvent.actor_user_id)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.created_at, OrderEvent.id)
    ).all()
    return [EventRow(event=event, actor_name=name) for event, name in rows]


def get_desk_order(session: Session, order_id: UUID) -> DeskOrderView | None:
    """An order with its retailer, lines and events, or None if missing."""
    order = session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), joinedload(Order.retailer))
    )

    if order is None:
        return None

    return DeskOrderView(
        order=order, retailer=order.retailer, events=_events(session, order.id)
    )


def line_numbers(session: Session, order_id: UUID) -> dict[UUID, int]:
    """order_item_id -> line_number, for mapping preview lines and issues."""
    return dict(
        session.execute(
            select(OrderItem.id, OrderItem.line_number).where(
                OrderItem.order_id == order_id
            )
        ).all()
    )


def preview_order(session: Session, order_id: UUID) -> DeskPreview:
    """order_service's preview plus each line's line_number.

    The preview itself is unchanged (its lines follow item id, the order its
    locking uses); callers sort by line_number for display.
    """
    preview = preview_order_confirmation(session, order_id)
    return DeskPreview(preview=preview, line_numbers=line_numbers(session, order_id))


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


def normalise_rejection_reason(reason: str) -> str:
    """Trimmed reason, or InvalidRejectionReasonError."""
    trimmed = reason.strip()

    if not (REJECTION_REASON_MIN_LENGTH <= len(trimmed) <= REJECTION_REASON_MAX_LENGTH):
        raise InvalidRejectionReasonError(
            f"The reason must be {REJECTION_REASON_MIN_LENGTH} to "
            f"{REJECTION_REASON_MAX_LENGTH} characters."
        )

    return trimmed


def _latest_rejection(session: Session, order_id: UUID) -> OrderEvent | None:
    return session.scalar(
        select(OrderEvent)
        .where(
            OrderEvent.order_id == order_id,
            OrderEvent.event_type == OrderEventType.REJECTED,
        )
        .order_by(OrderEvent.created_at.desc(), OrderEvent.id.desc())
        .limit(1)
    )


def reject_order(
    session: Session,
    order_id: UUID,
    *,
    reason: str,
    rejected_by_user_id: UUID,
    now: datetime | None = None,
) -> RejectionResult:
    """Reject a pending_review or on_hold order.

    Already rejected: a no-op returning the current order and its recorded
    rejection (no new event). Confirmed, exported or draft orders raise
    InvalidOrderStateError. Stock and retailer balances are never touched
    (nothing was deducted for these statuses), and the retailer does not
    need to be active. The session must hold no unsaved changes; the
    caller commits.
    """
    reason = normalise_rejection_reason(reason)
    _ensure_clean_session(session)

    order = session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if order is None:
        raise OrderNotFoundError("Order was not found.")

    if order.status == OrderStatus.REJECTED:
        return RejectionResult(
            order=order,
            rejection=_latest_rejection(session, order.id),
            already_rejected=True,
        )

    if order.status not in REJECTABLE_STATUSES:
        raise InvalidOrderStateError(
            f"Cannot reject an order with status: {order.status}"
        )

    previous_status = order.status
    order.status = OrderStatus.REJECTED
    rejection = record_order_event(
        session,
        order,
        event_type=OrderEventType.REJECTED,
        from_status=previous_status,
        to_status=OrderStatus.REJECTED,
        actor_user_id=rejected_by_user_id,
        reason=reason,
        now=now or datetime.now(UTC),
    )
    session.flush()

    return RejectionResult(order=order, rejection=rejection, already_rejected=False)


def actor_name(session: Session, user_id: UUID | None) -> str | None:
    """The display name of an event's actor, if any."""
    if user_id is None:
        return None
    return session.scalar(select(User.full_name).where(User.id == user_id))


RETAILER_DIRECTORY_LIMIT = 50


def list_retailers(
    session: Session, *, search: str | None = None, limit: int = 20
) -> list[Retailer]:
    """Retailers for the order desk's pickers, by business name.

    Read-only. `search` matches anywhere in the business name, ignoring case;
    LIKE wildcards in it are treated as plain characters.
    """
    statement = select(Retailer).order_by(Retailer.business_name, Retailer.id)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(
            Retailer.business_name.ilike(f"%{escaped}%", escape="\\")
        )
    return list(session.scalars(statement.limit(min(limit, RETAILER_DIRECTORY_LIMIT))))
