from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import ProductStatus, UserRole


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    phone_number: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(
        String(20), default=UserRole.RETAILER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    retailer: Mapped["Retailer | None"] = relationship(
        back_populates="user", uselist=False
    )


class Retailer(Base):
    __tablename__ = "retailers"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        unique=True,
    )
    business_name: Mapped[str] = mapped_column(String(160))
    owner_name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(300))
    credit_limit: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00")
    )
    outstanding_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User | None"] = relationship(back_populates="retailer")
    product_prices: Mapped[list["RetailerProductPrice"]] = relationship(
        back_populates="retailer"
    )

    __table_args__ = (
        CheckConstraint("credit_limit >= 0", name="credit_limit_non_negative"),
        CheckConstraint(
            "outstanding_balance >= 0",
            name="outstanding_balance_non_negative",
        ),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    sku: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str | None] = mapped_column(String(500))
    brand: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(100))
    unit_name: Mapped[str] = mapped_column(String(30), default="case")
    units_per_case: Mapped[int] = mapped_column(default=1)
    stock_quantity: Mapped[int] = mapped_column(default=0)
    status: Mapped[ProductStatus] = mapped_column(
        String(20), default=ProductStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    retailer_prices: Mapped[list["RetailerProductPrice"]] = relationship(
        back_populates="product"
    )

    __table_args__ = (
        CheckConstraint("units_per_case > 0", name="units_per_case_positive"),
        CheckConstraint("stock_quantity >= 0", name="stock_non_negative"),
    )


class RetailerProductPrice(Base):
    __tablename__ = "retailer_product_prices"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    retailer_id: Mapped[UUID] = mapped_column(
        ForeignKey("retailers.id", ondelete="RESTRICT")
    )
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT")
    )
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    retailer: Mapped["Retailer"] = relationship(back_populates="product_prices")
    product: Mapped["Product"] = relationship(back_populates="retailer_prices")

    __table_args__ = (
        UniqueConstraint(
            "retailer_id", "product_id", name="retailer_product_price_unique"
        ),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        Index("ix_retailer_price_lookup", "retailer_id", "is_active"),
    )
