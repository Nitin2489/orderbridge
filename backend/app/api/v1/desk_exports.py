"""Order desk Tally CSV exports.

Every endpoint requires the order desk role (DeskUser); the POST also needs
a trusted Origin. The CSV is a generic, Tally-oriented file: ledger
mappings, GST configuration and voucher setup must be done in Tally.

Transactions: the POST commits exactly once after the service succeeds. On
any error it rolls back explicitly (a failed rollback is logged and ends as
a generic 500), so no order is ever left exported without its batch, CSV
and audit event. Downloads only read the stored CSV.

The TallyPrime XML download (tally_xml_service) is built from the stored
CSV on request: sales vouchers in Tally's documented import structure,
excluding GST. Its masters endpoint lists the Tally names it needs.
"""

import hashlib
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    AppSettings,
    DbSession,
    DeskUser,
    require_trusted_origin,
)
from app.api.transactions import rollback_after_error
from app.schemas.auth import ErrorResponse
from app.schemas.exports import (
    CreateExportRequest,
    EligibleExportResponse,
    ExportBatchDetail,
    ExportBatchPage,
    ExportBatchSummary,
    IneligibleOrderResponse,
    IneligibleOrdersResponse,
    TallyMastersResponse,
    TallyStockItem,
    xml_url,
)
from app.services import tally_export_service
from app.services.tally_export_service import (
    ExportRequestConflictError,
    IneligibleOrdersError,
    NoEligibleOrdersError,
    csv_bytes,
)
from app.services.tally_xml_service import (
    BatchDataError,
    TallyConfigurationError,
    TallySettings,
    build_tally_xml,
    missing_settings,
    read_batch_csv,
    required_masters,
)

router = APIRouter(prefix="/desk/exports", tags=["order desk exports"])
logger = logging.getLogger(__name__)

# Plain status code: the named 422 constant differs between Starlette versions.
UNPROCESSABLE = 422

BATCH_NOT_FOUND = "Export batch not found."
REQUEST_CONFLICT = (
    "This client_request_id was already used for a different export. "
    "Generate a new id for a new export."
)
INELIGIBLE = "Some requested orders cannot be exported. Nothing was exported."
NOTHING_ELIGIBLE = "No confirmed orders are waiting for export."

_AUTH = {
    401: {"model": ErrorResponse, "description": "Not signed in."},
    403: {
        "model": ErrorResponse,
        "description": "Not an order desk account, or untrusted request origin.",
    },
}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": BATCH_NOT_FOUND}}


@router.get(
    "/eligible",
    response_model=EligibleExportResponse,
    summary="Orders waiting for export",
    description="Confirmed orders not yet exported. Read-only; takes no locks.",
    responses=_AUTH,
)
def eligible(desk_user: DeskUser, db: DbSession) -> EligibleExportResponse:
    return EligibleExportResponse.from_summary(
        tally_export_service.eligible_summary(db)
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ExportBatchSummary,
    dependencies=[Depends(require_trusted_origin)],
    summary="Export confirmed orders to a Tally-oriented CSV",
    description=(
        "Exports every eligible confirmed order, optionally limited to those "
        "confirmed before confirmed_before and/or to the given order_ids "
        "(each of which must be eligible). The orders become exported, the "
        "CSV is stored with the batch, and one exported event is recorded "
        "per order, all in one transaction. 201 creates a batch; 200 returns "
        "the batch an identical earlier request created. GST is not "
        "calculated; ledgers, GST and vouchers are configured in Tally."
    ),
    responses={
        200: {"model": ExportBatchSummary},
        **_AUTH,
        409: {
            "model": IneligibleOrdersResponse,
            "description": (
                "Requested orders are not eligible (listed), or the "
                "client_request_id was used for a different request."
            ),
        },
        422: {"model": ErrorResponse, "description": NOTHING_ELIGIBLE},
    },
)
def create_export(
    payload: CreateExportRequest,
    response: Response,
    desk_user: DeskUser,
    db: DbSession,
) -> ExportBatchSummary | JSONResponse:
    try:
        result = tally_export_service.create_export(
            db,
            exported_by_user_id=desk_user.id,
            client_request_id=payload.client_request_id,
            confirmed_before=payload.confirmed_before,
            order_ids=payload.order_ids,
        )
    except Exception as error:
        rollback_after_error(db, error, logger)

        if isinstance(error, IneligibleOrdersError):
            body = IneligibleOrdersResponse(
                detail=INELIGIBLE,
                ineligible=[
                    IneligibleOrderResponse.from_entry(entry) for entry in error.orders
                ],
            )
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=body.model_dump(mode="json"),
            )
        if isinstance(error, ExportRequestConflictError):
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=REQUEST_CONFLICT
            ) from None
        if isinstance(error, NoEligibleOrdersError):
            raise HTTPException(UNPROCESSABLE, detail=NOTHING_ELIGIBLE) from None
        raise

    db.commit()

    if not result.created:
        response.status_code = status.HTTP_200_OK

    view = tally_export_service.get_batch(db, result.batch.id)
    return ExportBatchSummary.from_row(view)


@router.get(
    "",
    response_model=ExportBatchPage,
    summary="Export batch history",
    description="Newest first (created_at, then id).",
    responses=_AUTH,
)
def list_exports(
    desk_user: DeskUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExportBatchPage:
    page = tally_export_service.list_batches(db, limit=limit, offset=offset)
    return ExportBatchPage(
        items=[ExportBatchSummary.from_row(row) for row in page.rows],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{batch_id}",
    response_model=ExportBatchDetail,
    summary="Export batch metadata and its orders",
    description="Does not include the CSV; download it from csv_url.",
    responses={**_AUTH, **_NOT_FOUND},
)
def get_export(batch_id: UUID, desk_user: DeskUser, db: DbSession) -> ExportBatchDetail:
    view = tally_export_service.get_batch(db, batch_id)

    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=BATCH_NOT_FOUND)

    return ExportBatchDetail.from_view(view)


@router.get(
    "/{batch_id}/csv",
    summary="Download the exported CSV",
    description=(
        "The exact stored bytes (UTF-8 with BOM, CRLF). Downloading again "
        "changes nothing."
    ),
    responses={
        200: {"content": {"text/csv": {}}, "description": "The CSV file."},
        **_AUTH,
        **_NOT_FOUND,
    },
    response_class=Response,
)
def download_export_csv(batch_id: UUID, desk_user: DeskUser, db: DbSession) -> Response:
    stored = tally_export_service.get_batch_csv(db, batch_id)

    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=BATCH_NOT_FOUND)

    batch_number, content = stored
    return Response(
        content=csv_bytes(content),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="orderbridge-tally-{batch_number}.csv"'
            ),
            "Cache-Control": "no-store",
        },
    )


def _tally_settings(settings: AppSettings) -> TallySettings:
    return TallySettings(
        sales_ledger=settings.tally_sales_ledger_name,
        discount_ledger=settings.tally_discount_ledger_name,
        voucher_type=settings.tally_voucher_type_name.strip(),
    )


def _stored_vouchers(db: DbSession, batch_id: UUID):
    stored = tally_export_service.get_batch_csv(db, batch_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=BATCH_NOT_FOUND)
    batch_number, content = stored
    try:
        return batch_number, read_batch_csv(content)
    except BatchDataError as error:
        # The stored export is inconsistent: never adjusted silently.
        logger.error("Stored export cannot be turned into Tally vouchers: %s", error)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"This export cannot be turned into Tally vouchers: {error}",
        ) from None


@router.get(
    "/{batch_id}/tally-masters",
    response_model=TallyMastersResponse,
    summary="Tally masters the XML vouchers need",
    description=(
        "Party ledgers, stock items, units, ledgers and voucher type that must "
        "exist in the Tally company with exactly these names."
    ),
    responses={**_AUTH, **_NOT_FOUND},
)
def tally_masters(
    batch_id: UUID, desk_user: DeskUser, db: DbSession, settings: AppSettings
) -> TallyMastersResponse:
    batch_number, vouchers = _stored_vouchers(db, batch_id)
    masters = required_masters(vouchers, _tally_settings(settings))
    return TallyMastersResponse(
        batch_number=batch_number,
        voucher_type=masters.voucher_type,
        sales_ledger=masters.sales_ledger,
        discount_ledger=masters.discount_ledger,
        discount_needed=masters.discount_needed,
        party_ledgers=list(masters.party_ledgers),
        stock_items=[
            TallyStockItem(name=item.name, unit=item.unit)
            for item in masters.stock_items
        ],
        units=list(masters.units),
        missing_settings=missing_settings(masters),
        xml_url=xml_url(batch_id),
    )


@router.get(
    "/{batch_id}/tally.xml",
    summary="Download TallyPrime XML sales vouchers",
    description=(
        "One Sales voucher per order, built from the stored CSV. Follows "
        "Tally's documented XML voucher format; not yet tested in TallyPrime. "
        "Amounts exclude GST. X-Content-SHA256 is the SHA-256 of the exact "
        "bytes. Import each batch once."
    ),
    responses={
        200: {"content": {"application/xml": {}}, "description": "The XML file."},
        **_AUTH,
        **_NOT_FOUND,
        409: {
            "model": ErrorResponse,
            "description": "Tally ledger names not configured, or inconsistent data.",
        },
    },
    response_class=Response,
)
def download_tally_xml(
    batch_id: UUID, desk_user: DeskUser, db: DbSession, settings: AppSettings
) -> Response:
    batch_number, vouchers = _stored_vouchers(db, batch_id)
    try:
        content = build_tally_xml(batch_number, vouchers, _tally_settings(settings))
    except TallyConfigurationError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from None
    except BatchDataError as error:
        logger.error("Tally vouchers do not balance: %s", error)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"This export cannot be turned into Tally vouchers: {error}",
        ) from None
    return Response(
        content=content,
        media_type="application/xml; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="orderbridge-tally-{batch_number}.xml"'
            ),
            "Cache-Control": "no-store",
            "X-Content-SHA256": hashlib.sha256(content).hexdigest(),
        },
    )
