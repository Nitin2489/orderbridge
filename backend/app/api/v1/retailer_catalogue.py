"""Retailer catalogue endpoints (read-only).

The retailer is always the signed-in user's own Retailer record
(CurrentRetailer); no endpoint accepts a retailer id.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import CurrentRetailer, DbSession
from app.schemas.auth import ErrorResponse
from app.schemas.catalogue import (
    CatalogueCategoriesResponse,
    CataloguePageResponse,
    CatalogueProduct,
)
from app.services import catalogue_service

router = APIRouter(prefix="/retailer/catalogue", tags=["retailer catalogue"])

PRODUCT_NOT_FOUND = "Product not found."

_AUTH_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not signed in."},
    403: {"model": ErrorResponse, "description": "Not a retailer account."},
}


def _clean(value: str | None) -> str | None:
    """Strip whitespace; treat a blank value as not supplied."""
    if value is None:
        return None
    return value.strip() or None


@router.get(
    "",
    response_model=CataloguePageResponse,
    summary="The signed-in retailer's catalogue",
    description=(
        "Active products this retailer has an active price for, including "
        "out-of-stock products (is_available=false). Ordered by name, then "
        "id. Prices and schemes are for display; orders are re-priced on "
        "submission. Prices exclude GST (tax_treatment='exclusive'); GST is "
        "not calculated here and is determined at invoicing. scheme_status "
        "distinguishes no scheme from a scheme configuration error."
    ),
    responses=_AUTH_ERRORS,
)
def list_catalogue(
    retailer: CurrentRetailer,
    db: DbSession,
    search: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=100,
            description="Case-insensitive match on product name, SKU or brand.",
        ),
    ] = None,
    category: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=100,
            description="Exact category name, case-insensitive.",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CataloguePageResponse:
    page = catalogue_service.list_catalogue(
        db,
        retailer,
        search=_clean(search),
        category=_clean(category),
        limit=limit,
        offset=offset,
    )
    return CataloguePageResponse(
        items=[CatalogueProduct.from_entry(entry) for entry in page.entries],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/categories",
    response_model=CatalogueCategoriesResponse,
    summary="Categories in the signed-in retailer's catalogue",
    responses=_AUTH_ERRORS,
)
def list_categories(
    retailer: CurrentRetailer, db: DbSession
) -> CatalogueCategoriesResponse:
    return CatalogueCategoriesResponse(
        categories=catalogue_service.list_categories(db, retailer)
    )


@router.get(
    "/{product_id}",
    response_model=CatalogueProduct,
    summary="One product in the signed-in retailer's catalogue",
    description=(
        "404 when the product is missing, inactive, or not priced for this "
        "retailer; the response does not say which. The price excludes GST "
        "(tax_treatment='exclusive'), which is determined at invoicing."
    ),
    responses={
        **_AUTH_ERRORS,
        404: {"model": ErrorResponse, "description": PRODUCT_NOT_FOUND},
    },
)
def get_catalogue_product(
    product_id: UUID, retailer: CurrentRetailer, db: DbSession
) -> CatalogueProduct:
    entry = catalogue_service.get_catalogue_product(db, retailer, product_id)

    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=PRODUCT_NOT_FOUND)

    return CatalogueProduct.from_entry(entry)
