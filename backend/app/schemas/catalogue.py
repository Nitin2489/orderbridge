"""Response schemas for /api/v1/retailer/catalogue.

Money is Decimal and serialised as a JSON string (e.g. "777.60"), never as
a float. Responses contain no retailer, user or session identifiers.

GST (prototype assumption): displayed prices exclude GST. Tax rates are not
configured, so GST is not calculated here; it is determined at invoicing.
Every product reports this as tax_treatment="exclusive".
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import SchemeType
from app.services.catalogue_service import CatalogueEntry, SchemeStatus

TAX_TREATMENT_DESCRIPTION = (
    "GST treatment of the displayed prices. Always 'exclusive' in this "
    "prototype: prices exclude GST, which is not calculated here because tax "
    "rates are not configured, and is determined at invoicing."
)


class SchemeReference(BaseModel):
    """The scheme applied at its minimum quantity, as an illustration.

    Not a quote: stock is not checked, GST is excluded, and the backend
    recalculates the order price when the order is submitted.
    """

    quantity: int = Field(description="Ordered quantity (the scheme minimum).")
    free_quantity: int
    subtotal: Decimal
    discount_amount: Decimal
    illustrative_total: Decimal = Field(
        description=(
            "Illustrates the scheme at its minimum quantity. Not an order "
            "quote and not a payable amount: it excludes GST (not "
            "configured), and final order pricing is recalculated by the "
            "backend when the order is submitted."
        )
    )


class CatalogueScheme(BaseModel):
    id: UUID
    name: str
    scheme_type: SchemeType
    minimum_quantity: int
    free_quantity: int = Field(
        description="Free units per minimum_quantity ordered (free goods)."
    )
    discount_percent: Decimal = Field(
        description="Percentage off the line at or above minimum_quantity (slab)."
    )
    starts_at: datetime
    ends_at: datetime | None
    reference: SchemeReference | None = Field(
        description="Illustration at minimum_quantity; not an order price."
    )


class CatalogueProduct(BaseModel):
    id: UUID
    sku: str
    name: str
    description: str | None
    brand: str
    category: str
    unit_name: str
    units_per_case: int
    stock_quantity: int
    is_available: bool = Field(description="True when stock_quantity > 0.")
    unit_price: Decimal = Field(
        description=(
            "This retailer's price per unit_name (e.g. per case), excluding GST."
        )
    )
    tax_treatment: Literal["exclusive"] = Field(
        default="exclusive", description=TAX_TREATMENT_DESCRIPTION
    )
    scheme_status: SchemeStatus = Field(
        description=(
            "'none': no scheme applies now. 'active': exactly one applies and "
            "is given in scheme. 'configuration_error': more than one applies; "
            "none is chosen and the product cannot be ordered until the "
            "schemes are corrected."
        )
    )
    scheme: CatalogueScheme | None = Field(
        description="The scheme when scheme_status is 'active', otherwise null."
    )

    @classmethod
    def from_entry(cls, entry: CatalogueEntry) -> "CatalogueProduct":
        product = entry.product
        scheme = None

        if entry.scheme is not None:
            reference = None

            if entry.reference is not None:
                reference = SchemeReference(
                    quantity=entry.reference.ordered_quantity,
                    free_quantity=entry.reference.free_quantity,
                    subtotal=entry.reference.subtotal,
                    discount_amount=entry.reference.discount_amount,
                    illustrative_total=entry.reference.grand_total,
                )

            scheme = CatalogueScheme(
                id=entry.scheme.id,
                name=entry.scheme.name,
                scheme_type=SchemeType(entry.scheme.scheme_type),
                minimum_quantity=entry.scheme.minimum_quantity,
                free_quantity=entry.scheme.free_quantity,
                discount_percent=entry.scheme.discount_percent,
                starts_at=entry.scheme.starts_at,
                ends_at=entry.scheme.ends_at,
                reference=reference,
            )

        return cls(
            id=product.id,
            sku=product.sku,
            name=product.name,
            description=product.description,
            brand=product.brand,
            category=product.category,
            unit_name=product.unit_name,
            units_per_case=product.units_per_case,
            stock_quantity=product.stock_quantity,
            is_available=product.stock_quantity > 0,
            unit_price=entry.unit_price,
            tax_treatment="exclusive",
            scheme_status=entry.scheme_status,
            scheme=scheme,
        )


class CataloguePageResponse(BaseModel):
    items: list[CatalogueProduct]
    total: int = Field(description="Matching products across all pages.")
    limit: int
    offset: int


class CatalogueCategoriesResponse(BaseModel):
    categories: list[str] = Field(description="Distinct, alphabetical.")
