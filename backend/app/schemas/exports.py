"""Request and response schemas for /api/v1/desk/exports.

Money is Decimal, serialised as a JSON string. Batch metadata never includes
the CSV itself (download it from csv_url) or internal user ids.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.models import OrderStatus
from app.services.tally_export_service import (
    MAX_EXPLICIT_ORDERS,
    BatchRow,
    BatchView,
    EligibleSummary,
    IneligibleOrder,
)

CSV_PATH = "/api/v1/desk/exports/{batch_id}/csv"


def csv_url(batch_id: UUID) -> str:
    return CSV_PATH.format(batch_id=batch_id)


def xml_url(batch_id: UUID) -> str:
    return CSV_PATH.format(batch_id=batch_id).removesuffix("/csv") + "/tally.xml"


class CreateExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID = Field(
        description=(
            "Client-generated UUID. Retrying with the same id and request "
            "returns the original batch; reusing it for a different request "
            "is rejected (409)."
        )
    )
    confirmed_before: AwareDatetime | None = Field(
        default=None,
        description="Only orders confirmed before this instant (exclusive).",
    )
    order_ids: list[UUID] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_EXPLICIT_ORDERS,
        description=(
            "Export exactly these orders. Every one must be eligible, or "
            "nothing is exported (409)."
        ),
    )

    @field_validator("order_ids")
    @classmethod
    def _reject_duplicates(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("order_ids must not repeat.")
        return value


class EligibleExportResponse(BaseModel):
    order_count: int = Field(description="Confirmed orders not yet exported.")
    net_total: Decimal = Field(description="Their total, excluding GST.")

    @classmethod
    def from_summary(cls, summary: EligibleSummary) -> "EligibleExportResponse":
        return cls(order_count=summary.order_count, net_total=summary.net_total)


class ExportBatchSummary(BaseModel):
    id: UUID
    batch_number: str
    created_at: datetime
    created_by_name: str | None
    order_count: int
    net_total: Decimal = Field(description="Sum of order net totals, excl. GST.")
    csv_sha256: str = Field(description="SHA-256 of the exact CSV bytes.")
    csv_url: str = Field(description="Download path for the stored CSV.")

    @classmethod
    def from_row(cls, row: BatchRow | BatchView) -> "ExportBatchSummary":
        batch = row.batch
        return cls(
            id=batch.id,
            batch_number=batch.batch_number,
            created_at=batch.created_at,
            created_by_name=row.created_by_name,
            order_count=batch.order_count,
            net_total=batch.net_total,
            csv_sha256=batch.csv_sha256,
            csv_url=csv_url(batch.id),
        )


class ExportBatchPage(BaseModel):
    items: list[ExportBatchSummary]
    total: int
    limit: int
    offset: int


class ExportedOrderSummary(BaseModel):
    id: UUID
    order_number: str
    status: OrderStatus
    retailer_business_name: str
    confirmed_at: datetime | None
    net_total: Decimal


class ExportBatchDetail(ExportBatchSummary):
    orders: list[ExportedOrderSummary] = Field(description="By order number.")

    @classmethod
    def from_view(cls, view: BatchView) -> "ExportBatchDetail":
        return cls(
            **ExportBatchSummary.from_row(view).model_dump(),
            orders=[
                ExportedOrderSummary(
                    id=entry.order.id,
                    order_number=entry.order.order_number,
                    status=OrderStatus(entry.order.status),
                    retailer_business_name=entry.retailer_business_name,
                    confirmed_at=entry.order.confirmed_at,
                    net_total=entry.order.grand_total,
                )
                for entry in view.orders
            ],
        )


class TallyStockItem(BaseModel):
    name: str
    unit: str


class TallyMastersResponse(BaseModel):
    """The Tally masters this batch's XML vouchers refer to. OrderBridge
    cannot see the Tally company: each must already exist with exactly this
    name, or Tally's import reports it."""

    batch_number: str
    voucher_type: str
    sales_ledger: str | None = Field(description="Null when not configured.")
    discount_ledger: str | None = Field(description="Null when not configured.")
    discount_needed: bool
    party_ledgers: list[str]
    stock_items: list[TallyStockItem]
    units: list[str]
    missing_settings: list[str] = Field(
        description="Settings to configure before the XML can be downloaded."
    )
    xml_url: str


class IneligibleOrderResponse(BaseModel):
    order_id: UUID
    reason: str = Field(
        description=(
            "not_found, already_exported, status_<status> or confirmed_after_cutoff."
        )
    )

    @classmethod
    def from_entry(cls, entry: IneligibleOrder) -> "IneligibleOrderResponse":
        return cls(order_id=entry.order_id, reason=entry.reason)


class IneligibleOrdersResponse(BaseModel):
    """409: requested orders cannot be exported. Nothing was exported."""

    detail: str
    ineligible: list[IneligibleOrderResponse]
