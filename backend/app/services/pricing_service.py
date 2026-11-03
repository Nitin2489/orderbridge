from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
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


def get_active_scheme(
    session: Session,
    product_id: UUID,
    now: datetime,
) -> Scheme | None:
    statement = (
        select(Scheme)
        .where(
            Scheme.product_id == product_id,
            Scheme.is_active.is_(True),
            Scheme.starts_at <= now,
            (Scheme.ends_at.is_(None)) | (Scheme.ends_at >= now),
        )
        .order_by(Scheme.minimum_quantity.desc())
    )

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
) -> SchemeResult:
    if quantity <= 0:
        raise ValueError("Order quantity must be greater than zero.")

    product = session.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.status == "active",
        )
    )

    if product is None:
        raise ProductNotFoundError("Product does not exist or is inactive.")

    price_record = session.scalar(
        select(RetailerProductPrice).where(
            RetailerProductPrice.retailer_id == retailer_id,
            RetailerProductPrice.product_id == product_id,
            RetailerProductPrice.is_active.is_(True),
        )
    )

    if price_record is None:
        raise RetailerPriceNotFoundError(
            "No active price is configured for this retailer and product."
        )

    now = datetime.now(UTC)

    scheme_record = get_active_scheme(
        session,
        product_id,
        now,
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

    return result
