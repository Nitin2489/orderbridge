"""Retailer ordering endpoints.

The retailer is always the signed-in user's own Retailer record
(CurrentRetailer); orders are looked up only within it, so another
retailer's order is indistinguishable from a missing one (404). POST
endpoints are cookie-authenticated and state-changing, so they require a
trusted Origin (CSRF protection).

Transactions: the service never commits; each endpoint commits once the
order has been created. On a service error the endpoint rolls back
explicitly before responding, so nothing is committed and the retailer row
lock is released immediately. If that rollback fails, the request ends as a
generic 500 instead of the 4xx.
"""

import logging
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import (
    FORBIDDEN,
    CurrentRetailer,
    DbSession,
    require_trusted_origin,
)
from app.models import OrderStatus
from app.schemas.auth import ErrorResponse
from app.schemas.orders import (
    OrderLineIssueResponse,
    OrderLinesErrorResponse,
    RepeatOrderRequest,
    RetailerOrderDetail,
    RetailerOrderPage,
    RetailerOrderSummary,
    SubmitOrderRequest,
)
from app.services import retailer_order_service
from app.services.retailer_order_service import (
    IdempotencyConflictError,
    InvalidOrderRequestError,
    OrderLineInput,
    OrderLinesInvalidError,
    RetailerNotOrderableError,
    SourceOrderNotFoundError,
    SubmissionResult,
)

router = APIRouter(prefix="/retailer/orders", tags=["retailer orders"])
logger = logging.getLogger(__name__)

# Plain status code: the named 422 constant differs between Starlette versions.
UNPROCESSABLE = 422

ORDER_NOT_FOUND = "Order not found."
LINES_INVALID = "Some order lines cannot be ordered. No order was created."
IDEMPOTENCY_CONFLICT = (
    "This client_request_id was already used for a different order. "
    "Generate a new id for a new order."
)

_AUTH_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not signed in."},
    403: {
        "model": ErrorResponse,
        "description": "Not a retailer account, or untrusted request origin.",
    },
}
_SUBMIT_ERRORS = {
    **_AUTH_ERRORS,
    409: {"model": ErrorResponse, "description": IDEMPOTENCY_CONFLICT},
    422: {
        "model": OrderLinesErrorResponse,
        "description": (
            "Invalid request, or lines that cannot be ordered (all listed; "
            "nothing created)."
        ),
    },
}
_SUBMIT_DESCRIPTION = (
    "Prices, schemes, free quantities, discounts and totals are calculated "
    "by the server at current prices; amounts exclude GST "
    "(tax_treatment='exclusive'). The order is created as pending_review for "
    "the order desk; stock is not deducted and credit is not reserved. "
    "201 creates an order; 200 returns the order already created for this "
    "client_request_id and request."
)
_REPEAT_DESCRIPTION = (
    "Creates a new order with the same products and quantities as one of the "
    "signed-in retailer's orders (any status). Lines that can no longer be "
    "ordered are reported (422), never dropped. " + _SUBMIT_DESCRIPTION
)


def _lines_invalid(error: OrderLinesInvalidError) -> JSONResponse:
    body = OrderLinesErrorResponse(
        detail=LINES_INVALID,
        issues=[
            OrderLineIssueResponse(
                line_number=issue.line_number,
                product_id=issue.product_id,
                code=issue.code,
                message=issue.message,
            )
            for issue in error.issues
        ],
    )
    return JSONResponse(status_code=UNPROCESSABLE, content=body.model_dump(mode="json"))


def _submission_response(
    result: SubmissionResult, response: Response
) -> RetailerOrderDetail:
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return RetailerOrderDetail.from_order_with_items(result.order)


def _run_submission(
    db: DbSession, submit: Callable[[], SubmissionResult]
) -> SubmissionResult | JSONResponse:
    """Run a submission, commit it, and turn service errors into HTTP errors.

    On a service error the transaction is rolled back explicitly before the
    error response is produced: it releases the retailer row lock and any
    pending work at once and leaves the session usable. If the rollback
    itself fails, that failure propagates as a 500 (see
    _rollback_after_error) instead of the 4xx.
    """
    try:
        result = submit()
    except (
        OrderLinesInvalidError,
        InvalidOrderRequestError,
        IdempotencyConflictError,
        SourceOrderNotFoundError,
        RetailerNotOrderableError,
    ) as error:
        _rollback_after_error(db, error)
        return _error_response(error)

    db.commit()
    return result


def _rollback_after_error(db: DbSession, error: Exception) -> None:
    """Roll back after a service error; a failed rollback is a server error.

    If the rollback itself fails (for example, the connection dropped), the
    transaction state is unknown, so the original 4xx response is not
    returned. The failure is logged here, naming the service error that
    triggered it, and re-raised so the request ends as a generic 500; the
    response carries no database details. The re-raised exception keeps the
    service error as its __context__, so the log traceback shows both.
    """
    try:
        db.rollback()
    except SQLAlchemyError:
        logger.exception(
            "Rollback failed after order submission error %s; "
            "responding with a server error.",
            type(error).__name__,
        )
        raise


def _error_response(error: Exception) -> JSONResponse:
    """The HTTP response for a submission service error."""
    if isinstance(error, OrderLinesInvalidError):
        return _lines_invalid(error)
    if isinstance(error, InvalidOrderRequestError):
        raise HTTPException(UNPROCESSABLE, detail=str(error)) from None
    if isinstance(error, IdempotencyConflictError):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=IDEMPOTENCY_CONFLICT
        ) from None
    if isinstance(error, SourceOrderNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ORDER_NOT_FOUND) from None
    if isinstance(error, RetailerNotOrderableError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=FORBIDDEN) from None
    raise error


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=RetailerOrderDetail,
    dependencies=[Depends(require_trusted_origin)],
    summary="Submit an order",
    description=_SUBMIT_DESCRIPTION,
    responses={200: {"model": RetailerOrderDetail}, **_SUBMIT_ERRORS},
)
def submit_order(
    payload: SubmitOrderRequest,
    response: Response,
    retailer: CurrentRetailer,
    db: DbSession,
) -> RetailerOrderDetail | JSONResponse:
    outcome = _run_submission(
        db,
        lambda: retailer_order_service.submit_order(
            db,
            retailer,
            [
                OrderLineInput(product_id=line.product_id, quantity=line.quantity)
                for line in payload.items
            ],
            client_request_id=payload.client_request_id,
            notes=payload.notes,
        ),
    )
    if isinstance(outcome, JSONResponse):
        return outcome
    return _submission_response(outcome, response)


@router.get(
    "",
    response_model=RetailerOrderPage,
    summary="The signed-in retailer's orders",
    description="Newest first (created_at, then id). Amounts exclude GST.",
    responses=_AUTH_ERRORS,
)
def list_orders(
    retailer: CurrentRetailer,
    db: DbSession,
    status_filter: Annotated[
        OrderStatus | None, Query(alias="status", description="Only this status.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RetailerOrderPage:
    page = retailer_order_service.list_orders(
        db, retailer, status=status_filter, limit=limit, offset=offset
    )
    return RetailerOrderPage(
        items=[
            RetailerOrderSummary.from_order(order, item_count)
            for order, item_count in page.orders
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{order_id}",
    response_model=RetailerOrderDetail,
    summary="One of the signed-in retailer's orders",
    description=(
        "Lines show the stored pricing snapshot, in line order. 404 for "
        "missing orders and other retailers' orders alike."
    ),
    responses={
        **_AUTH_ERRORS,
        404: {"model": ErrorResponse, "description": ORDER_NOT_FOUND},
    },
)
def get_order(
    order_id: UUID, retailer: CurrentRetailer, db: DbSession
) -> RetailerOrderDetail:
    order = retailer_order_service.get_order(db, retailer, order_id)

    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ORDER_NOT_FOUND)

    return RetailerOrderDetail.from_order_with_items(order)


@router.post(
    "/{order_id}/repeat",
    status_code=status.HTTP_201_CREATED,
    response_model=RetailerOrderDetail,
    dependencies=[Depends(require_trusted_origin)],
    summary="Repeat an order as a new order",
    description=_REPEAT_DESCRIPTION,
    responses={
        200: {"model": RetailerOrderDetail},
        **_SUBMIT_ERRORS,
        404: {"model": ErrorResponse, "description": ORDER_NOT_FOUND},
    },
)
def repeat_order(
    order_id: UUID,
    payload: RepeatOrderRequest,
    response: Response,
    retailer: CurrentRetailer,
    db: DbSession,
) -> RetailerOrderDetail | JSONResponse:
    outcome = _run_submission(
        db,
        lambda: retailer_order_service.repeat_order(
            db, retailer, order_id, client_request_id=payload.client_request_id
        ),
    )
    if isinstance(outcome, JSONResponse):
        return outcome
    return _submission_response(outcome, response)
