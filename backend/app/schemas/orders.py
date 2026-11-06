"""Request and response schemas for /api/v1/retailer/orders.

Requests carry only product ids, quantities, optional notes and a
client_request_id; unknown fields are rejected, so a client cannot send
prices, totals, discounts, free quantities, a status or a retailer id.
Money is Decimal, serialised as a JSON string.

GST (prototype assumption): amounts exclude GST, which is not calculated
because tax rates are not configured; it is determined at invoicing.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Order, OrderItem, OrderSource, OrderStatus
from app.schemas.catalogue import TAX_TREATMENT_DESCRIPTION
from app.services.order_service import LineIssueCode
from app.services.retailer_order_service import MAX_ORDER_LINES

MAX_LINE_QUANTITY = 10_000
MAX_NOTES_LENGTH = 500

CLIENT_REQUEST_ID_DESCRIPTION = (
    "Client-generated UUID for this submission. Resending the same request "
    "with the same id returns the original order instead of creating a "
    "duplicate; reusing the id for a different request is rejected (409)."
)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class OrderLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    quantity: int = Field(ge=1, le=MAX_LINE_QUANTITY, description="Units to order.")


class SubmitOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID = Field(description=CLIENT_REQUEST_ID_DESCRIPTION)
    items: list[OrderLineRequest] = Field(
        min_length=1,
        max_length=MAX_ORDER_LINES,
        description="One line per product; lines are kept in this order.",
    )
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)

    @field_validator("items")
    @classmethod
    def _reject_duplicate_products(
        cls, items: list[OrderLineRequest]
    ) -> list[OrderLineRequest]:
        product_ids = [item.product_id for item in items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("Each product may appear on only one line.")
        return items


class RepeatOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID = Field(description=CLIENT_REQUEST_ID_DESCRIPTION)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class OrderLineResponse(BaseModel):
    """A line as stored on the order (the pricing snapshot)."""

    line_number: int
    product_id: UUID
    sku: str
    product_name: str
    quantity: int
    free_quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    line_total: Decimal
    scheme_name: str | None

    @classmethod
    def from_item(cls, item: OrderItem) -> "OrderLineResponse":
        return cls(
            line_number=item.line_number,
            product_id=item.product_id,
            sku=item.sku,
            product_name=item.product_name,
            quantity=item.quantity,
            free_quantity=item.free_quantity,
            unit_price=item.unit_price,
            discount_amount=item.discount_amount,
            line_total=item.line_total,
            scheme_name=item.scheme_name,
        )


class RetailerOrderSummary(BaseModel):
    id: UUID
    order_number: str
    status: OrderStatus
    source: OrderSource
    item_count: int
    subtotal: Decimal
    discount_total: Decimal
    grand_total: Decimal = Field(
        description=(
            "Excludes GST. For orders not yet confirmed this is the price at "
            "submission; the order desk re-prices the order at confirmation."
        )
    )
    tax_treatment: Literal["exclusive"] = Field(
        default="exclusive", description=TAX_TREATMENT_DESCRIPTION
    )
    created_at: datetime
    confirmed_at: datetime | None

    @classmethod
    def from_order(cls, order: Order, item_count: int) -> "RetailerOrderSummary":
        return cls(
            id=order.id,
            order_number=order.order_number,
            status=OrderStatus(order.status),
            source=OrderSource(order.source),
            item_count=item_count,
            subtotal=order.subtotal,
            discount_total=order.discount_total,
            grand_total=order.grand_total,
            tax_treatment="exclusive",
            created_at=order.created_at,
            confirmed_at=order.confirmed_at,
        )


class RetailerOrderDetail(RetailerOrderSummary):
    notes: str | None
    items: list[OrderLineResponse]

    @classmethod
    def from_order_with_items(cls, order: Order) -> "RetailerOrderDetail":
        summary = RetailerOrderSummary.from_order(order, len(order.items))
        return cls(
            **summary.model_dump(),
            notes=order.notes,
            items=[OrderLineResponse.from_item(item) for item in order.items],
        )


class RetailerOrderPage(BaseModel):
    items: list[RetailerOrderSummary]
    total: int = Field(description="Matching orders across all pages.")
    limit: int
    offset: int


class OrderLineIssueResponse(BaseModel):
    line_number: int = Field(description="1-based position of the line.")
    product_id: UUID
    code: LineIssueCode
    message: str


class OrderLinesErrorResponse(BaseModel):
    """422: lines that cannot be ordered. Nothing was created."""

    detail: str
    issues: list[OrderLineIssueResponse]
