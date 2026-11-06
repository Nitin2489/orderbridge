from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Product,
    RetailerProductPrice,
    Scheme,
    SchemeType,
)
from app.services.scheme_engine import (
    SchemeInput,
    SchemeResult,
    calculate_scheme,
)


class PricingError(Exception):
    """Base exception for pricing and stock validation errors."""


class ProductNotFoundError(PricingError):
    pass


class RetailerPriceNotFoundError(PricingError):
    pass


class InsufficientStockError(PricingError):
    pass


class ApplicableSchemeError(PricingError):
    pass


@dataclass(frozen=True)
class RetailerProductPricing:
    result: SchemeResult
    scheme_name: str | None


def applicable_scheme_conditions(now: datetime) -> tuple[ColumnElement[bool], ...]:
    """SQL conditions for a scheme that applies at `now`.

    Active, already started, and not yet ended (ends_at is inclusive). Shared
    by pricing and the catalogue so both use exactly the same rule.
    """
    return (
        Scheme.is_active.is_(True),
        Scheme.starts_at <= now,
        or_(Scheme.ends_at.is_(None), Scheme.ends_at >= now),
    )


def get_active_scheme(
    session: Session,
    product_id: UUID,
    now: datetime,
    *,
    for_update: bool = False,
) -> Scheme | None:
    statement = (
        select(Scheme)
        .where(Scheme.product_id == product_id, *applicable_scheme_conditions(now))
        .order_by(Scheme.minimum_quantity.desc())
    )

    if for_update:
        statement = statement.with_for_update()

    schemes = list(session.scalars(statement))

    if len(schemes) > 1:
        raise ApplicableSchemeError(
            "Multiple active schemes exist for this product. "
            "Resolve the promotion configuration before pricing."
        )

    return schemes[0] if schemes else None


def calculate_retailer_product_price(
    session: Session,
    *,
    retailer_id: UUID,
    product_id: UUID,
    quantity: int,
    product: Product | None = None,
    for_update: bool = False,
) -> RetailerProductPricing:
    if quantity <= 0:
        raise ValueError("Order quantity must be greater than zero.")

    if product is None:
        product_statement = select(Product).where(
            Product.id == product_id,
            Product.status == "active",
        )

        if for_update:
            product_statement = product_statement.with_for_update()

        product = session.scalar(product_statement)

    if product is None or product.id != product_id or product.status != "active":
        raise ProductNotFoundError("Product does not exist or is inactive.")

    price_statement = select(RetailerProductPrice).where(
        RetailerProductPrice.retailer_id == retailer_id,
        RetailerProductPrice.product_id == product_id,
        RetailerProductPrice.is_active.is_(True),
    )

    if for_update:
        price_statement = price_statement.with_for_update()

    price_record = session.scalar(price_statement)

    if price_record is None:
        raise RetailerPriceNotFoundError(
            "No active price is configured for this retailer and product."
        )

    scheme_record = get_active_scheme(
        session,
        product_id,
        datetime.now(UTC),
        for_update=for_update,
    )

    scheme_input = None

    if scheme_record is not None:
        scheme_input = SchemeInput(
            scheme_type=SchemeType(scheme_record.scheme_type),
            minimum_quantity=scheme_record.minimum_quantity,
            free_quantity=scheme_record.free_quantity,
            discount_percent=scheme_record.discount_percent,
        )

    result = calculate_scheme(
        quantity=quantity,
        unit_price=price_record.unit_price,
        scheme=scheme_input,
    )

    if result.stock_required > product.stock_quantity:
        raise InsufficientStockError(
            f"Insufficient stock for {product.name}. "
            f"Required: {result.stock_required} cases. "
            f"Available: {product.stock_quantity} cases."
        )

    return RetailerProductPricing(
        result=result,
        scheme_name=scheme_record.name if scheme_record else None,
    )
