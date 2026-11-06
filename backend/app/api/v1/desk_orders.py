"""Order desk endpoints: review queue, detail, preview, confirm and reject.

Every endpoint requires the order desk role (DeskUser). The acting user is
always the signed-in desk user; no actor, role or retailer identity is read
from a request. POST endpoints are cookie-authenticated and state-changing,
so they also require a trusted Origin (CSRF protection).

Confirmation is order_service.confirm_order() and rejection is
desk_order_service.reject_order(); both record their audit events
themselves. Transactions: each POST commits exactly once on success. On a
service error it rolls back explicitly before responding; if that rollback
fails the request ends as a generic 500. Error responses use fixed messages,
never exception text.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime

from app.api.dependencies import DbSession, DeskUser, require_trusted_origin
from app.api.transactions import rollback_after_error
from app.models import OrderStatus
from app.schemas.auth import ErrorResponse
from app.schemas.desk import (
    ConfirmOrderRequest,
    ConfirmOrderResponse,
    DeskOrderDetail,
    DeskOrderPage,
    DeskOrderPreview,
    DeskOrderSummary,
    OrderEventResponse,
    OrderIssuesResponse,
    PreviewOutdatedResponse,
    RejectOrderRequest,
    RejectOrderResponse,
    issues_by_line,
)
from app.services import desk_order_service
from app.services.desk_order_service import (
    DeskPreview,
    EventRow,
    InvalidQueueFilterError,
)
from app.services.order_service import (
    InvalidOrderStateError,
    OrderConfirmationError,
    OrderNotFoundError,
    OrderValidationError,
    PreviewOutdatedError,
    RetailerUnavailableError,
    confirm_order,
)

router = APIRouter(prefix="/desk/orders", tags=["order desk"])
logger = logging.getLogger(__name__)

# Plain status code: the named 422 constant differs between Starlette versions.
UNPROCESSABLE = 422

ORDER_NOT_FOUND = "Order not found."
NOT_AWAITING_REVIEW = "This order is not awaiting review."
NOT_REJECTABLE = "This order can no longer be rejected."
RETAILER_UNAVAILABLE = (
    "The retailer account is inactive, so the order cannot be confirmed. It can "
    "still be rejected."
)
PREVIEW_OUTDATED = (
    "Prices changed since the preview was approved. Nothing was changed. "
    "Review the new preview and confirm its fingerprint."
)
LINES_NOT_CONFIRMABLE = "Some lines cannot be confirmed. Nothing was changed."

_AUTH = {
    401: {"model": ErrorResponse, "description": "Not signed in."},
    403: {
        "model": ErrorResponse,
        "description": "Not an order desk account, or untrusted request origin.",
    },
}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": ORDER_NOT_FOUND}}


def _detail(db: DbSession, order_id: UUID) -> DeskOrderDetail:
    view = desk_order_service.get_desk_order(db, order_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ORDER_NOT_FOUND)
    return DeskOrderDetail.from_view(view)


@router.get(
    "",
    response_model=DeskOrderPage,
    summary="Order queue across retailers",
    description=(
        "Filters combine with AND. search matches the order number or retailer "
        "business name (case-insensitive). created_from is inclusive and "
        "created_to exclusive; both need a timezone. Ordering: oldest first "
        "for status=pending_review or on_hold (work queues), newest first "
        "otherwise; ties by id. Amounts exclude GST."
    ),
    responses={
        **_AUTH,
        422: {"model": ErrorResponse, "description": "Invalid filters."},
    },
)
def list_queue(
    desk_user: DeskUser,
    db: DbSession,
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    retailer_id: Annotated[UUID | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    created_from: Annotated[AwareDatetime | None, Query()] = None,
    created_to: Annotated[AwareDatetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DeskOrderPage:
    try:
        page = desk_order_service.list_queue(
            db,
            status=status_filter,
            retailer_id=retailer_id,
            search=(search.strip() or None) if search else None,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=offset,
        )
    except InvalidQueueFilterError as error:
        raise HTTPException(UNPROCESSABLE, detail=str(error)) from None

    return DeskOrderPage(
        items=[DeskOrderSummary.from_row(row) for row in page.rows],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{order_id}",
    response_model=DeskOrderDetail,
    summary="Order detail for review",
    description=(
        "The order, the retailer's current credit position, the lines in "
        "line_number order with their stored pricing snapshot, and the audit "
        "events oldest first."
    ),
    responses={**_AUTH, **_NOT_FOUND},
)
def get_order(order_id: UUID, desk_user: DeskUser, db: DbSession) -> DeskOrderDetail:
    return _detail(db, order_id)


@router.get(
    "/{order_id}/preview",
    response_model=DeskOrderPreview,
    summary="Price a pending order at current prices",
    description=(
        "Read-only. Shows each line's stored and current pricing, issues "
        "(e.g. insufficient stock), totals, the credit assessment and the "
        "fingerprint to approve. Only pending_review orders can be previewed."
    ),
    responses={
        **_AUTH,
        **_NOT_FOUND,
        409: {
            "model": ErrorResponse,
            "description": "Not awaiting review, or the retailer is inactive.",
        },
    },
)
def preview_order(
    order_id: UUID, desk_user: DeskUser, db: DbSession
) -> DeskOrderPreview:
    try:
        desk_preview = desk_order_service.preview_order(db, order_id)
    except OrderNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ORDER_NOT_FOUND) from None
    except InvalidOrderStateError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=NOT_AWAITING_REVIEW
        ) from None
    except RetailerUnavailableError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=RETAILER_UNAVAILABLE
        ) from None

    return DeskOrderPreview.from_preview(desk_preview)


def _confirm_error_response(
    db: DbSession, order_id: UUID, error: OrderConfirmationError
) -> JSONResponse:
    """The response for a confirmation error, after the rollback."""
    if isinstance(error, PreviewOutdatedError):
        fresh = DeskPreview(
            preview=error.preview,
            line_numbers=desk_order_service.line_numbers(db, order_id),
        )
        body = PreviewOutdatedResponse(
            detail=PREVIEW_OUTDATED, preview=DeskOrderPreview.from_preview(fresh)
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=body.model_dump(mode="json")
        )
    if isinstance(error, OrderValidationError):
        body = OrderIssuesResponse(
            detail=LINES_NOT_CONFIRMABLE,
            issues=issues_by_line(
                error.issues, desk_order_service.line_numbers(db, order_id)
            ),
        )
        return JSONResponse(
            status_code=UNPROCESSABLE, content=body.model_dump(mode="json")
        )
    if isinstance(error, OrderNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ORDER_NOT_FOUND)
    if isinstance(error, InvalidOrderStateError):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=NOT_AWAITING_REVIEW)
    if isinstance(error, RetailerUnavailableError):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=RETAILER_UNAVAILABLE)
    # UnsavedChangesError and anything unexpected: a programming error.
    raise error


@router.post(
    "/{order_id}/confirm",
    response_model=ConfirmOrderResponse,
    dependencies=[Depends(require_trusted_origin)],
    summary="Confirm an order at the approved preview's figures",
    description=(
        "Re-validates everything under row locks. If current pricing no "
        "longer matches approved_fingerprint, nothing changes and 409 returns "
        "the fresh preview to approve. If the total exceeds the retailer's "
        "available credit, the order is placed on hold (outcome "
        "on_hold_credit_limit) without deducting stock or increasing the "
        "balance. Confirming an already confirmed order changes nothing. "
        "The signed-in desk user is recorded as the actor."
    ),
    responses={
        **_AUTH,
        **_NOT_FOUND,
        409: {
            "model": PreviewOutdatedResponse,
            "description": (
                "Prices changed (body includes the fresh preview), the order "
                "is not awaiting review, or the retailer is inactive."
            ),
        },
        422: {"model": OrderIssuesResponse, "description": LINES_NOT_CONFIRMABLE},
    },
)
def confirm(
    order_id: UUID,
    payload: ConfirmOrderRequest,
    desk_user: DeskUser,
    db: DbSession,
) -> ConfirmOrderResponse | JSONResponse:
    try:
        result = confirm_order(
            db,
            order_id,
            approved_fingerprint=payload.approved_fingerprint,
            confirmed_by_user_id=desk_user.id,
        )
    except OrderConfirmationError as error:
        rollback_after_error(db, error, logger)
        return _confirm_error_response(db, order_id, error)

    db.commit()
    return ConfirmOrderResponse(
        outcome=result.outcome,
        required_credit=result.required_credit,
        available_credit=result.available_credit,
        order=_detail(db, order_id),
    )


@router.post(
    "/{order_id}/reject",
    response_model=RejectOrderResponse,
    dependencies=[Depends(require_trusted_origin)],
    summary="Reject an order",
    description=(
        "Rejects a pending_review or on_hold order with a reason. Stock and "
        "balances are unchanged. Rejecting an already rejected order changes "
        "nothing. Confirmed or exported orders cannot be rejected. Works even "
        "if the retailer is inactive. The signed-in desk user is recorded."
    ),
    responses={
        **_AUTH,
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": NOT_REJECTABLE},
    },
)
def reject(
    order_id: UUID,
    payload: RejectOrderRequest,
    desk_user: DeskUser,
    db: DbSession,
) -> RejectOrderResponse:
    try:
        result = desk_order_service.reject_order(
            db, order_id, reason=payload.reason, rejected_by_user_id=desk_user.id
        )
    except (OrderConfirmationError, ValueError) as error:
        rollback_after_error(db, error, logger)
        if isinstance(error, OrderNotFoundError):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=ORDER_NOT_FOUND
            ) from None
        if isinstance(error, InvalidOrderStateError):
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=NOT_REJECTABLE
            ) from None
        if isinstance(error, ValueError):
            raise HTTPException(
                UNPROCESSABLE, detail="The rejection reason is invalid."
            ) from None
        raise

    db.commit()

    rejection = None
    if result.rejection is not None:
        rejection = OrderEventResponse.from_row(
            EventRow(
                event=result.rejection,
                actor_name=desk_order_service.actor_name(
                    db, result.rejection.actor_user_id
                ),
            )
        )

    return RejectOrderResponse(
        already_rejected=result.already_rejected,
        rejection=rejection,
        order=_detail(db, order_id),
    )
