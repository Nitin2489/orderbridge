from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.catalog import Product, Retailer
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import (
    CaptureStatus,
    OrderEventType,
    OrderSource,
    OrderStatus,
    SchemeType,
)


def _values_in(column: str, enum: type[OrderStatus] | type[OrderEventType]) -> str:
    """SQL check: column IN ('a', 'b', ...) for every value of the enum."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


class Scheme(Base):
    __tablename__ = "schemes"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(160))
    scheme_type: Mapped[SchemeType] = mapped_column(String(30))
    minimum_quantity: Mapped[int] = mapped_column(Integer)
    free_quantity: Mapped[int] = mapped_column(Integer, default=0)
    discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("0.00")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    product: Mapped["Product"] = relationship()

    __table_args__ = (
        CheckConstraint(
            "minimum_quantity > 0",
            name="minimum_quantity_positive",
        ),
        CheckConstraint(
            "free_quantity >= 0",
            name="free_quantity_non_negative",
        ),
        CheckConstraint(
            "discount_percent >= 0 AND discount_percent <= 100",
            name="discount_percent_valid",
        ),
        CheckConstraint(
            "ends_at IS NULL OR ends_at >= starts_at",
            name="valid_scheme_dates",
        ),
        CheckConstraint(
            """
        (scheme_type = 'free_goods'
            AND free_quantity > 0
            AND discount_percent = 0)
        OR
        (scheme_type = 'slab_discount'
            AND free_quantity = 0
            AND discount_percent > 0)
        """,
            name="valid_scheme_configuration",
        ),
        Index("ix_scheme_product_active", "product_id", "is_active"),
    )


#: Source of readable order numbers (see retailer_order_service).
ORDER_NUMBER_SEQUENCE = Sequence("order_number_seq", metadata=Base.metadata)

#: Source of export batch numbers (see tally_export_service).
EXPORT_BATCH_NUMBER_SEQUENCE = Sequence(
    "export_batch_number_seq", metadata=Base.metadata
)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    retailer_id: Mapped[UUID] = mapped_column(
        ForeignKey("retailers.id", ondelete="RESTRICT")
    )
    source: Mapped[OrderSource] = mapped_column(String(30))
    status: Mapped[OrderStatus] = mapped_column(
        String(30), default=OrderStatus.PENDING_REVIEW
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    discount_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00")
    )
    grand_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who confirmed the order. Nullable until authentication supplies the
    # acting order-desk user.
    confirmed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    # Idempotent retailer submission: a client-generated id, unique per
    # retailer, and a hash of the normalised request it was first used for.
    client_request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # Set once, when the order is exported; never overwritten.
    export_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("export_batches.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    retailer: Mapped["Retailer"] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.line_number",
    )
    captures: Mapped[list["OrderCapture"]] = relationship(back_populates="order")
    # Read-only: events are only ever added through OrderEvent.order, and the
    # ORM never updates or deletes them through the order.
    events: Mapped[list["OrderEvent"]] = relationship(
        viewonly=True, order_by="OrderEvent.created_at"
    )

    __table_args__ = (
        CheckConstraint(_values_in("status", OrderStatus), name="status_valid"),
        CheckConstraint("subtotal >= 0", name="subtotal_non_negative"),
        CheckConstraint("discount_total >= 0", name="discount_non_negative"),
        CheckConstraint("grand_total >= 0", name="grand_total_non_negative"),
        CheckConstraint(
            "(client_request_id IS NULL) = (request_fingerprint IS NULL)",
            name="client_request_has_fingerprint",
        ),
        UniqueConstraint(
            "retailer_id",
            "client_request_id",
            name="order_retailer_client_request_unique",
        ),
        Index("ix_order_retailer_created", "retailer_id", "created_at"),
        Index("ix_order_status_created", "status", "created_at"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT")
    )
    # 1-based position within the order; fixes the display order of lines.
    line_number: Mapped[int] = mapped_column(Integer)
    product_name: Mapped[str] = mapped_column(String(160))
    # The product's unit (e.g. "case") when the line was created.
    unit_name: Mapped[str] = mapped_column(String(30))
    sku: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer)
    free_quantity: Mapped[int] = mapped_column(Integer, default=0)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00")
    )
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    scheme_name: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("free_quantity >= 0", name="free_quantity_non_negative"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("discount_amount >= 0", name="discount_amount_non_negative"),
        CheckConstraint("line_total >= 0", name="line_total_non_negative"),
        CheckConstraint(
            "free_quantity <= quantity",
            name="free_quantity_not_exceeding_quantity",
        ),
        CheckConstraint("line_number > 0", name="line_number_positive"),
        UniqueConstraint(
            "order_id", "line_number", name="order_item_line_number_unique"
        ),
    )


class OrderCapture(Base):
    __tablename__ = "order_captures"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL")
    )
    retailer_id: Mapped[UUID] = mapped_column(
        ForeignKey("retailers.id", ondelete="RESTRICT")
    )
    original_message: Mapped[str] = mapped_column(Text)
    extracted_data: Mapped[dict] = mapped_column(JSON)
    status: Mapped[CaptureStatus] = mapped_column(
        String(30), default=CaptureStatus.PROCESSING
    )
    review_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped["Order | None"] = relationship(back_populates="captures")
    retailer: Mapped["Retailer"] = relationship()

    __table_args__ = (
        Index("ix_capture_retailer_created", "retailer_id", "created_at"),
        Index("ix_capture_status_created", "status", "created_at"),
    )


class OrderEvent(Base):
    """Append-only audit record of an order's lifecycle.

    Written by the services in the same transaction as the change it records
    (see app.services.order_events). Nothing updates or deletes events; the
    RESTRICT foreign keys keep orders and actors from being deleted under
    them.
    """

    __tablename__ = "order_events"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    event_type: Mapped[OrderEventType] = mapped_column(String(30))
    from_status: Mapped[OrderStatus | None] = mapped_column(String(30))
    to_status: Mapped[OrderStatus | None] = mapped_column(String(30))
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    order: Mapped["Order"] = relationship()

    __table_args__ = (
        CheckConstraint(
            _values_in("event_type", OrderEventType), name="event_type_valid"
        ),
        Index("ix_order_event_order_created", "order_id", "created_at"),
    )


class ExportBatch(Base):
    """One Tally CSV export: the exact file produced and the orders in it.

    Immutable once created: csv_content is the generated file (including its
    byte order mark) and csv_sha256 the checksum of its UTF-8 bytes, so a
    download always returns exactly what was exported. Orders point to their
    batch through orders.export_batch_id.
    """

    __tablename__ = "export_batches"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    batch_number: Mapped[str] = mapped_column(String(30), unique=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    order_count: Mapped[int] = mapped_column(Integer)
    net_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    csv_content: Mapped[str] = mapped_column(Text)
    csv_sha256: Mapped[str] = mapped_column(String(64))
    client_request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("order_count > 0", name="order_count_positive"),
        CheckConstraint("net_total >= 0", name="net_total_non_negative"),
        Index("ix_export_batch_created", "created_at"),
    )
