"""Retailer catalogue: what one retailer can see and buy, for display only.

A product is in a retailer's catalogue when the product is active and the
retailer has an active price for it. Stock does not affect membership:
out-of-stock products stay visible and are marked unavailable. Prices are
only ever read for the given retailer.

This module never validates stock or calculates order prices. Order
submission recalculates prices and schemes through pricing_service.

Queries per catalogue page: one count, one page of products with prices
(a join), and one batch query for the schemes of those products. There are
no per-product queries.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Product,
    ProductStatus,
    Retailer,
    RetailerProductPrice,
    Scheme,
    SchemeType,
)
from app.services.pricing_service import applicable_scheme_conditions
from app.services.scheme_engine import SchemeInput, SchemeResult, calculate_scheme


class SchemeStatus(StrEnum):
    """Whether a product has a usable scheme right now."""

    NONE = "none"  # no applicable scheme
    ACTIVE = "active"  # exactly one applicable scheme
    CONFIGURATION_ERROR = "configuration_error"  # more than one applicable


@dataclass(frozen=True)
class CatalogueEntry:
    """A product as one retailer sees it.

    scheme is set only when scheme_status is ACTIVE; with several applicable
    schemes none is chosen. reference is the scheme applied at the scheme's
    minimum quantity, as an illustration of the benefit. It is not a quote:
    stock is not checked and order pricing is recalculated at submission.
    """

    product: Product
    unit_price: Decimal
    scheme_status: SchemeStatus
    scheme: Scheme | None
    reference: SchemeResult | None


@dataclass(frozen=True)
class CataloguePage:
    entries: list[CatalogueEntry]
    total: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _in_catalogue(statement: Select, retailer_id: UUID) -> Select:
    """Restrict a products query to active products with this retailer's
    active price (inner join, so unpriced products drop out)."""
    return statement.join(
        RetailerProductPrice,
        and_(
            RetailerProductPrice.product_id == Product.id,
            RetailerProductPrice.retailer_id == retailer_id,
            RetailerProductPrice.is_active.is_(True),
        ),
    ).where(Product.status == ProductStatus.ACTIVE)


def _catalogue_rows(retailer_id: UUID) -> Select:
    """(product, this retailer's unit price) rows of the catalogue."""
    return _in_catalogue(select(Product, RetailerProductPrice.unit_price), retailer_id)


def _apply_filters(
    statement: Select, *, search: str | None, category: str | None
) -> Select:
    if search:
        # icontains with autoescape treats % and _ in the input literally.
        statement = statement.where(
            or_(
                Product.name.icontains(search, autoescape=True),
                Product.sku.icontains(search, autoescape=True),
                Product.brand.icontains(search, autoescape=True),
            )
        )

    if category:
        statement = statement.where(func.lower(Product.category) == category.lower())

    return statement


def _applicable_schemes(
    session: Session, product_ids: list[UUID], now: datetime
) -> dict[UUID, list[Scheme]]:
    """All applicable schemes per product, in one query."""
    by_product: dict[UUID, list[Scheme]] = defaultdict(list)

    if not product_ids:
        return by_product

    for scheme in session.scalars(
        select(Scheme).where(
            Scheme.product_id.in_(product_ids), *applicable_scheme_conditions(now)
        )
    ):
        by_product[scheme.product_id].append(scheme)

    return by_product


def _resolve_scheme(schemes: list[Scheme]) -> tuple[SchemeStatus, Scheme | None]:
    """Status and the single usable scheme, never choosing among several.

    More than one applicable scheme is a configuration error, as in
    pricing_service.get_active_scheme(), which raises ApplicableSchemeError;
    such a product cannot be ordered until the schemes are corrected.
    """
    if not schemes:
        return SchemeStatus.NONE, None

    if len(schemes) > 1:
        return SchemeStatus.CONFIGURATION_ERROR, None

    return SchemeStatus.ACTIVE, schemes[0]


def _reference(unit_price: Decimal, scheme: Scheme | None) -> SchemeResult | None:
    """The scheme's effect at its minimum quantity, or None without a scheme."""
    if scheme is None:
        return None

    try:
        return calculate_scheme(
            quantity=scheme.minimum_quantity,
            unit_price=unit_price,
            scheme=SchemeInput(
                scheme_type=SchemeType(scheme.scheme_type),
                minimum_quantity=scheme.minimum_quantity,
                free_quantity=scheme.free_quantity,
                discount_percent=scheme.discount_percent,
            ),
        )
    except ValueError:
        # A misconfigured scheme still shows its terms, just no illustration.
        return None


def _entries(
    session: Session, rows: list[tuple[Product, Decimal]], now: datetime
) -> list[CatalogueEntry]:
    schemes = _applicable_schemes(session, [product.id for product, _ in rows], now)
    entries = []

    for product, unit_price in rows:
        status, scheme = _resolve_scheme(schemes[product.id])
        entries.append(
            CatalogueEntry(
                product=product,
                unit_price=unit_price,
                scheme_status=status,
                scheme=scheme,
                reference=_reference(unit_price, scheme),
            )
        )

    return entries


def list_catalogue(
    session: Session,
    retailer: Retailer,
    *,
    search: str | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> CataloguePage:
    """One page of the retailer's catalogue, ordered by name, then id."""
    now = now or _utcnow()
    filtered = _apply_filters(
        _catalogue_rows(retailer.id), search=search, category=category
    )

    total = session.scalar(select(func.count()).select_from(filtered.subquery()))
    page = filtered.order_by(Product.name, Product.id).limit(limit).offset(offset)
    rows = [(product, unit_price) for product, unit_price in session.execute(page)]

    return CataloguePage(entries=_entries(session, rows, now), total=total or 0)


def list_categories(session: Session, retailer: Retailer) -> list[str]:
    """Distinct categories of the retailer's catalogue, alphabetical."""
    statement = _in_catalogue(select(Product.category).distinct(), retailer.id)
    return list(session.scalars(statement.order_by(Product.category)))


def get_catalogue_product(
    session: Session,
    retailer: Retailer,
    product_id: UUID,
    *,
    now: datetime | None = None,
) -> CatalogueEntry | None:
    """One product of the retailer's catalogue, or None if not in it.

    None covers missing, inactive and unpriced products alike, so callers
    cannot learn whether a product exists for another retailer.
    """
    now = now or _utcnow()
    statement = _catalogue_rows(retailer.id).where(Product.id == product_id)
    rows = [(product, unit_price) for product, unit_price in session.execute(statement)]

    if not rows:
        return None

    (entry,) = _entries(session, rows, now)
    return entry
