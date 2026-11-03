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
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import (
    CaptureStatus,
    OrderSource,
    OrderStatus,
    SchemeType,
)


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
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00")
    )
    discount_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00")
    )
    grand_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        order_by="OrderItem.created_at",
    )
    captures: Mapped[list["OrderCapture"]] = relationship(
        back_populates="order"
    )

    __table_args__ = (
        CheckConstraint("subtotal >= 0", name="subtotal_non_negative"),
        CheckConstraint("discount_total >= 0", name="discount_non_negative"),
        CheckConstraint("grand_total >= 0", name="grand_total_non_negative"),
        Index("ix_order_retailer_created", "retailer_id", "created_at"),
        Index("ix_order_status_created", "status", "created_at"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE")
    )
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT")
    )
    product_name: Mapped[str] = mapped_column(String(160))
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
        CheckConstraint(
            "discount_amount >= 0", name="discount_amount_non_negative"
        ),
        CheckConstraint("line_total >= 0", name="line_total_non_negative"),
	CheckConstraint(
    "free_quantity <= quantity",
    name="free_quantity_not_exceeding_quantity",
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
