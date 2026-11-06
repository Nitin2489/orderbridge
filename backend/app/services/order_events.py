"""Order audit events.

record_order_event() is the only way the application writes OrderEvent rows.
It adds the event to the caller's session, so the event is committed or
rolled back together with the state change it records. It never flushes or
commits, and nothing in the application updates or deletes events.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Order, OrderEvent, OrderEventType, OrderStatus


def money_detail(value: Decimal) -> str:
    """Decimal as a fixed-point string for JSON event details (never float)."""
    return format(value, "f")


def record_order_event(
    session: Session,
    order: Order,
    *,
    event_type: OrderEventType,
    from_status: OrderStatus | str | None,
    to_status: OrderStatus | str | None,
    actor_user_id: UUID | None,
    reason: str | None = None,
    details: dict | None = None,
    now: datetime | None = None,
) -> OrderEvent:
    """Add an audit event for `order` to the session (not flushed).

    The event is linked through the relationship, so it can be recorded for
    an order that has not been flushed yet.
    """
    event = OrderEvent(
        order=order,
        event_type=OrderEventType(event_type),
        from_status=OrderStatus(from_status) if from_status is not None else None,
        to_status=OrderStatus(to_status) if to_status is not None else None,
        actor_user_id=actor_user_id,
        reason=reason,
        details=details,
        created_at=now or datetime.now(UTC),
    )
    session.add(event)
    return event
