"""Request and response schemas for /api/v1/desk/orders.

Money is Decimal, serialised as a JSON string. Amounts exclude GST (see
tax_treatment). Responses expose the retailer's business identity and credit
position, which the order desk may see, but never users' phone numbers,
sessions or credentials.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import OrderEventType, OrderSource, OrderStatus
from app.schemas.catalogue import TAX_TREATMENT_DESCRIPTION
from app.schemas.orders import OrderLineResponse
from app.services.desk_order_service import (
    REJECTION_REASON_MAX_LENGTH,
    REJECTION_REASON_MIN_LENGTH,
    DeskOrderView,
    DeskPreview,
    EventRow,
    QueueRow,
    normalise_rejection_reason,
)
from app.services.order_service import ConfirmationOutcome, LineIssue, LineIssueCode

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class ConfirmOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_fingerprint: str = Field(
        pattern=r"^[0-9a-fA-F]{64}$",
        description=(
            "The fingerprint of the preview the desk approved. Confirmation "
            "proceeds only if current pricing still matches it."
        ),
    )

    @field_validator("approved_fingerprint")
    @classmethod
    def _lower_case(cls, value: str) -> str:
        return value.lower()


class RejectOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        max_length=REJECTION_REASON_MAX_LENGTH * 2,
        description=(
            f"Why the order is rejected; {REJECTION_REASON_MIN_LENGTH} to "
            f"{REJECTION_REASON_MAX_LENGTH} characters after trimming."
        ),
    )

    @field_validator("reason")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return normalise_rejection_reason(value)


# ---------------------------------------------------------------------------
# Queue, detail and events
# ---------------------------------------------------------------------------


class DeskOrderSummary(BaseModel):
    id: UUID
    order_number: str
    status: OrderStatus
    source: OrderSource
    retailer_id: UUID
    retailer_business_name: str
    item_count: int
    subtotal: Decimal
    discount_total: Decimal
    grand_total: Decimal = Field(
        description=(
            "Excludes GST. Before confirmation this is the price at "
            "submission; confirmation re-prices the order."
        )
    )
    tax_treatment: Literal["exclusive"] = Field(
        default="exclusive", description=TAX_TREATMENT_DESCRIPTION
    )
    created_at: datetime
    confirmed_at: datetime | None

    @classmethod
    def from_row(cls, row: QueueRow) -> "DeskOrderSummary":
        order = row.order
        return cls(
            id=order.id,
            order_number=order.order_number,
            status=OrderStatus(order.status),
            source=OrderSource(order.source),
            retailer_id=order.retailer_id,
            retailer_business_name=row.retailer_business_name,
            item_count=row.item_count,
            subtotal=order.subtotal,
            discount_total=order.discount_total,
            grand_total=order.grand_total,
            tax_treatment="exclusive",
            created_at=order.created_at,
            confirmed_at=order.confirmed_at,
        )


class DeskOrderPage(BaseModel):
    items: list[DeskOrderSummary]
    total: int = Field(description="Matching orders across all pages.")
    limit: int
    offset: int


class RetailerCreditPosition(BaseModel):
    retailer_id: UUID
    business_name: str
    is_active: bool
    credit_limit: Decimal
    outstanding_balance: Decimal
    available_credit: Decimal = Field(
        description="credit_limit minus outstanding_balance, now."
    )


class OrderEventResponse(BaseModel):
    id: UUID
    event_type: OrderEventType
    from_status: OrderStatus | None
    to_status: OrderStatus | None
    actor_user_id: UUID | None
    actor_name: str | None
    reason: str | None
    details: dict | None = Field(
        description=(
            "Event-specific figures as strings, e.g. required_credit and "
            "available_credit for placed_on_hold."
        )
    )
    created_at: datetime

    @classmethod
    def from_row(cls, row: EventRow) -> "OrderEventResponse":
        event = row.event
        return cls(
            id=event.id,
            event_type=OrderEventType(event.event_type),
            from_status=event.from_status,
            to_status=event.to_status,
            actor_user_id=event.actor_user_id,
            actor_name=row.actor_name,
            reason=event.reason,
            details=event.details,
            created_at=event.created_at,
        )


class DeskOrderDetail(BaseModel):
    id: UUID
    order_number: str
    status: OrderStatus
    source: OrderSource
    subtotal: Decimal
    discount_total: Decimal
    grand_total: Decimal
    tax_treatment: Literal["exclusive"] = Field(
        default="exclusive", description=TAX_TREATMENT_DESCRIPTION
    )
    notes: str | None
    created_at: datetime
    confirmed_at: datetime | None
    confirmed_by_user_id: UUID | None
    retailer: RetailerCreditPosition
    items: list[OrderLineResponse] = Field(description="Sorted by line_number.")
    events: list[OrderEventResponse] = Field(description="Oldest first.")

    @classmethod
    def from_view(cls, view: DeskOrderView) -> "DeskOrderDetail":
        order, retailer = view.order, view.retailer
        return cls(
            id=order.id,
            order_number=order.order_number,
            status=OrderStatus(order.status),
            source=OrderSource(order.source),
            subtotal=order.subtotal,
            discount_total=order.discount_total,
            grand_total=order.grand_total,
            tax_treatment="exclusive",
            notes=order.notes,
            created_at=order.created_at,
            confirmed_at=order.confirmed_at,
            confirmed_by_user_id=order.confirmed_by_user_id,
            retailer=RetailerCreditPosition(
                retailer_id=retailer.id,
                business_name=retailer.business_name,
                is_active=retailer.is_active,
                credit_limit=retailer.credit_limit,
                outstanding_balance=retailer.outstanding_balance,
                available_credit=retailer.credit_limit - retailer.outstanding_balance,
            ),
            items=[
                OrderLineResponse.from_item(item)
                for item in sorted(order.items, key=lambda item: item.line_number)
            ],
            events=[OrderEventResponse.from_row(row) for row in view.events],
        )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class PreviewLineResponse(BaseModel):
    """A line priced now (current_*) beside its stored snapshot (stored_*)."""

    line_number: int
    order_item_id: UUID
    product_id: UUID
    sku: str
    product_name: str
    quantity: int
    stock_required: int = Field(description="Ordered plus free units.")
    stored_unit_price: Decimal
    current_unit_price: Decimal
    stored_free_quantity: int
    current_free_quantity: int
    stored_discount_amount: Decimal
    current_discount_amount: Decimal
    stored_line_total: Decimal
    current_line_total: Decimal
    stored_scheme_name: str | None
    current_scheme_name: str | None
    current_subtotal: Decimal
    price_changed: bool = Field(
        description="True if any current value differs from the stored one."
    )


class LineIssueResponse(BaseModel):
    line_number: int
    order_item_id: UUID
    product_id: UUID
    sku: str
    code: LineIssueCode
    message: str

    @classmethod
    def from_issue(
        cls, issue: LineIssue, line_numbers: dict[UUID, int]
    ) -> "LineIssueResponse":
        return cls(
            line_number=line_numbers.get(issue.order_item_id, 0),
            order_item_id=issue.order_item_id,
            product_id=issue.product_id,
            sku=issue.sku,
            code=issue.code,
            message=issue.message,
        )


def issues_by_line(
    issues: tuple[LineIssue, ...], line_numbers: dict[UUID, int]
) -> list[LineIssueResponse]:
    mapped = [LineIssueResponse.from_issue(issue, line_numbers) for issue in issues]
    return sorted(mapped, key=lambda issue: issue.line_number)


class DeskOrderPreview(BaseModel):
    order_id: UUID
    lines: list[PreviewLineResponse] = Field(
        description="Lines that can be priced, sorted by line_number."
    )
    issues: list[LineIssueResponse] = Field(
        description="Lines that cannot be confirmed, sorted by line_number."
    )
    has_price_changes: bool
    subtotal: Decimal
    discount_total: Decimal
    grand_total: Decimal
    totals_cover_all_lines: bool = Field(
        description=(
            "False when some lines have issues: the totals then cover only "
            "the priced lines and are not the order total."
        )
    )
    available_credit: Decimal
    exceeds_credit_limit: bool = Field(
        description=(
            "Conclusive only when totals_cover_all_lines is true. Confirming "
            "an order over the limit places it on hold instead."
        )
    )
    can_confirm: bool
    fingerprint: str | None = Field(
        description="Send as approved_fingerprint to confirm these figures."
    )
    tax_treatment: Literal["exclusive"] = Field(
        default="exclusive", description=TAX_TREATMENT_DESCRIPTION
    )

    @classmethod
    def from_preview(cls, desk_preview: DeskPreview) -> "DeskOrderPreview":
        preview, numbers = desk_preview.preview, desk_preview.line_numbers
        lines = [
            PreviewLineResponse(
                line_number=numbers.get(line.order_item_id, 0),
                order_item_id=line.order_item_id,
                product_id=line.product_id,
                sku=line.sku,
                product_name=line.product_name,
                quantity=line.quantity,
                stock_required=line.stock_required,
                stored_unit_price=line.stored_unit_price,
                current_unit_price=line.unit_price,
                stored_free_quantity=line.stored_free_quantity,
                current_free_quantity=line.free_quantity,
                stored_discount_amount=line.stored_discount_amount,
                current_discount_amount=line.discount_amount,
                stored_line_total=line.stored_line_total,
                current_line_total=line.line_total,
                stored_scheme_name=line.stored_scheme_name,
                current_scheme_name=line.scheme_name,
                current_subtotal=line.subtotal,
                price_changed=line.price_changed,
            )
            for line in preview.lines
        ]

        return cls(
            order_id=preview.order_id,
            lines=sorted(lines, key=lambda line: line.line_number),
            issues=issues_by_line(preview.issues, numbers),
            has_price_changes=bool(preview.price_changes),
            subtotal=preview.subtotal,
            discount_total=preview.discount_total,
            grand_total=preview.grand_total,
            totals_cover_all_lines=preview.totals_cover_all_lines,
            available_credit=preview.available_credit,
            exceeds_credit_limit=preview.exceeds_credit_limit,
            can_confirm=preview.can_confirm,
            fingerprint=preview.fingerprint,
            tax_treatment="exclusive",
        )


# ---------------------------------------------------------------------------
# Action results and errors
# ---------------------------------------------------------------------------


class ConfirmOrderResponse(BaseModel):
    outcome: ConfirmationOutcome = Field(
        description=(
            "confirmed; on_hold_credit_limit (no stock or balance change); "
            "already_confirmed (nothing changed)."
        )
    )
    required_credit: Decimal | None = Field(default=None, description="If on hold.")
    available_credit: Decimal | None = Field(default=None, description="If on hold.")
    order: DeskOrderDetail


class RejectOrderResponse(BaseModel):
    already_rejected: bool = Field(
        description="True if the order was already rejected; nothing changed."
    )
    rejection: OrderEventResponse | None = Field(
        description="The recorded rejection (reason, actor, time)."
    )
    order: DeskOrderDetail


class PreviewOutdatedResponse(BaseModel):
    """409: prices changed since the approved preview. Nothing changed."""

    detail: str
    preview: DeskOrderPreview


class OrderIssuesResponse(BaseModel):
    """422: lines cannot be confirmed. Nothing changed."""

    detail: str
    issues: list[LineIssueResponse]
