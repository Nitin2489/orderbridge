"""Captured order messages: create, list, read, review, discard and submit.

Both roles use these routes. Identity comes only from the session: a
retailer always acts for its own retailer record and sees only its own
captures; the order desk sees all captures and names the retailer when it
creates one. A capture the caller may not see is reported as missing (404).
POST routes are state-changing and cookie-authenticated, so they also
require a trusted Origin (CSRF protection).

Interpretation in Phase A is rule-based (not an AI model); responses say so
in `interpretation`. Submitting creates a PENDING_REVIEW order through the
existing order service; the order desk then confirms it through the
existing confirmation workflow, which alone deducts stock and uses credit.

Transactions: one commit per successful change. On any error the endpoint
rolls back explicitly before responding (a failed rollback ends as a
generic 500), so no partial order, link, alias or status change persists.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    FORBIDDEN,
    CurrentUser,
    DbSession,
    require_trusted_origin,
)
from app.api.transactions import rollback_after_error
from app.models import CaptureStatus, UserRole
from app.schemas.auth import ErrorResponse
from app.schemas.captures import (
    AliasConflictResponse,
    CaptureDetail,
    CaptureLineIssueResponse,
    CaptureLinesErrorResponse,
    CaptureNotReadyResponse,
    CaptureOrderLink,
    CapturePage,
    CaptureSummary,
    CreateCaptureRequest,
    DiscardCaptureRequest,
    ReviewLineRequest,
    StaleCaptureResponse,
    SubmitCaptureRequest,
    SubmitCaptureResponse,
)
from app.services import capture_service
from app.services.capture_interpreter import InvalidMessageError, TooManyLinesError
from app.services.capture_service import (
    AliasConflictError,
    AliasNotPermittedError,
    CaptureActor,
    CaptureLineNotFoundError,
    CaptureLinesInvalidError,
    CaptureNotFoundError,
    CaptureNotReadyError,
    CaptureRequestConflictError,
    CaptureStateError,
    InvalidCaptureRequestError,
    InvalidReviewError,
    LineDecision,
    NoOrderLinesError,
    RetailerInactiveError,
    RetailerNotFoundError,
    StaleCaptureVersionError,
)
from app.services.retailer_order_service import (
    IdempotencyConflictError,
    RetailerNotOrderableError,
)

router = APIRouter(prefix="/captures", tags=["order capture"])
logger = logging.getLogger(__name__)

# Plain status code: the named 422 constant differs between Starlette versions.
UNPROCESSABLE = 422

CAPTURE_NOT_FOUND = "Capture not found."
LINES_INVALID = "Some included lines cannot be ordered. No order was created."

_AUTH = {
    401: {"model": ErrorResponse, "description": "Not signed in."},
    403: {
        "model": ErrorResponse,
        "description": "Not allowed, or untrusted request origin.",
    },
}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": CAPTURE_NOT_FOUND}}
_CHANGE = {
    **_AUTH,
    **_NOT_FOUND,
    409: {
        "model": StaleCaptureResponse,
        "description": "Stale version, or the capture can no longer be changed.",
    },
    422: {"model": ErrorResponse, "description": "Invalid decision."},
}


def get_capture_actor(user: CurrentUser) -> CaptureActor:
    """The acting user, from the session only."""
    if user.role == UserRole.ORDER_DESK:
        return CaptureActor(user_id=user.id, role=UserRole.ORDER_DESK)
    retailer = user.retailer
    if user.role == UserRole.RETAILER and retailer is not None and retailer.is_active:
        return CaptureActor(
            user_id=user.id, role=UserRole.RETAILER, retailer_id=retailer.id
        )
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)


Actor = Annotated[CaptureActor, Depends(get_capture_actor)]


def _error_response(error: Exception) -> JSONResponse | None:
    """The HTTP response for a known capture error, or None."""

    def plain(code: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=code, content={"detail": detail})

    if isinstance(error, (CaptureNotFoundError, CaptureLineNotFoundError)):
        return plain(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, RetailerNotFoundError):
        return plain(status.HTTP_404_NOT_FOUND, "Retailer not found.")
    if isinstance(error, StaleCaptureVersionError):
        body = StaleCaptureResponse(
            detail=str(error), current_version=error.current_version
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=body.model_dump()
        )
    if isinstance(error, CaptureNotReadyError):
        body = CaptureNotReadyResponse(
            detail=str(error), unresolved_lines=list(error.unresolved_lines)
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=body.model_dump()
        )
    if isinstance(error, AliasConflictError):
        body = AliasConflictResponse(
            detail=str(error),
            line_number=error.line_number,
            alias_text=error.phrase,
            current_product_id=error.product_id,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=body.model_dump(mode="json")
        )
    if isinstance(
        error,
        (
            CaptureStateError,
            CaptureRequestConflictError,
            RetailerInactiveError,
            RetailerNotOrderableError,
            IdempotencyConflictError,
        ),
    ):
        return plain(status.HTTP_409_CONFLICT, str(error))
    if isinstance(error, CaptureLinesInvalidError):
        body = CaptureLinesErrorResponse(
            detail=LINES_INVALID,
            issues=[
                CaptureLineIssueResponse(
                    line_number=issue.line_number,
                    product_id=issue.product_id,
                    code=issue.code,
                    message=issue.message,
                )
                for issue in error.issues
            ],
        )
        return JSONResponse(
            status_code=UNPROCESSABLE, content=body.model_dump(mode="json")
        )
    if isinstance(
        error,
        (
            InvalidMessageError,
            TooManyLinesError,
            NoOrderLinesError,
            InvalidCaptureRequestError,
            InvalidReviewError,
        ),
    ):
        return plain(UNPROCESSABLE, str(error))
    if isinstance(error, AliasNotPermittedError):
        return plain(status.HTTP_403_FORBIDDEN, str(error))
    return None


def _rolled_back(db: DbSession, error: Exception) -> JSONResponse | None:
    """Roll back, then return the response for a known error (or None, in
    which case the caller re-raises and the request ends as a 500)."""
    rollback_after_error(db, error, logger)
    return _error_response(error)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CaptureDetail,
    dependencies=[Depends(require_trusted_origin)],
    summary="Capture a text order message",
    description=(
        "Stores the message exactly as received and proposes lines with the "
        "rule-based interpreter and matcher (not an AI model). Nothing is "
        "ordered. 201 creates a capture; 200 returns the capture an identical "
        "earlier request created. A message with no order lines is refused "
        "(422) and nothing is stored."
    ),
    responses={
        200: {"model": CaptureDetail},
        **_AUTH,
        404: {"model": ErrorResponse, "description": "Retailer not found."},
        409: {
            "model": ErrorResponse,
            "description": "Retailer inactive, or client_request_id reused.",
        },
        422: {"model": ErrorResponse, "description": "Invalid or empty message."},
    },
)
def create_capture(
    payload: CreateCaptureRequest,
    response: Response,
    actor: Actor,
    db: DbSession,
) -> CaptureDetail | JSONResponse:
    try:
        result = capture_service.create_capture(
            db,
            actor,
            message=payload.message,
            client_request_id=payload.client_request_id,
            retailer_id=payload.retailer_id,
        )
    except Exception as error:
        failure = _rolled_back(db, error)
        if failure is None:
            raise
        return failure
    db.commit()
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return CaptureDetail.from_capture(result.capture)


@router.get(
    "",
    response_model=CapturePage,
    summary="Captures you may see",
    description=(
        "Newest first. Retailers see only their own; the order desk sees all "
        "and may filter by retailer."
    ),
    responses={**_AUTH, 422: {"model": ErrorResponse}},
)
def list_captures(
    actor: Actor,
    db: DbSession,
    capture_status: Annotated[CaptureStatus | None, Query(alias="status")] = None,
    retailer_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CapturePage:
    if retailer_id is not None and not actor.is_desk:
        raise HTTPException(
            UNPROCESSABLE, detail="The retailer filter is for the order desk."
        )
    page = capture_service.list_captures(
        db,
        actor,
        status=capture_status,
        retailer_id=retailer_id,
        limit=limit,
        offset=offset,
    )
    return CapturePage(
        items=[CaptureSummary.from_capture(capture) for capture in page.captures],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{capture_id}",
    response_model=CaptureDetail,
    summary="A capture and its review state",
    responses={**_AUTH, **_NOT_FOUND},
)
def get_capture(
    capture_id: UUID, actor: Actor, db: DbSession
) -> CaptureDetail | JSONResponse:
    try:
        capture = capture_service.get_capture(db, actor, capture_id)
    except CaptureNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=CAPTURE_NOT_FOUND
        ) from None
    return CaptureDetail.from_capture(capture)


@router.post(
    "/{capture_id}/lines/{line_number}",
    response_model=CaptureDetail,
    dependencies=[Depends(require_trusted_origin)],
    summary="Record a review decision for one line",
    description=(
        "confirm_unit (cases or pieces, with the quantity), clear_unit, "
        "include (a product; needs a confirmed unit), exclude (with a reason) "
        "or reset. Confirming a product never confirms its unit. Only the "
        "order desk may opt in to saving an alias, and only for a corrected "
        "line; an existing alias for another product is never replaced "
        "unless replace_existing_alias is set (409 otherwise)."
    ),
    responses={**_CHANGE, 409: {"model": AliasConflictResponse}},
)
def review_line(
    capture_id: UUID,
    line_number: int,
    payload: ReviewLineRequest,
    actor: Actor,
    db: DbSession,
) -> CaptureDetail | JSONResponse:
    decision = LineDecision(
        action=payload.action,
        unit=payload.unit,
        quantity=payload.quantity,
        product_id=payload.product_id,
        reason=payload.reason,
        remember_alias=payload.remember_alias,
        replace_existing_alias=payload.replace_existing_alias,
    )
    try:
        capture = capture_service.review_line(
            db,
            actor,
            capture_id,
            line_number,
            decision,
            expected_version=payload.expected_version,
        )
    except Exception as error:
        failure = _rolled_back(db, error)
        if failure is None:
            raise
        return failure
    db.commit()
    return CaptureDetail.from_capture(capture)


@router.post(
    "/{capture_id}/discard",
    response_model=CaptureDetail,
    dependencies=[Depends(require_trusted_origin)],
    summary="Discard a capture, with a reason",
    responses=_CHANGE,
)
def discard_capture(
    capture_id: UUID,
    payload: DiscardCaptureRequest,
    actor: Actor,
    db: DbSession,
) -> CaptureDetail | JSONResponse:
    try:
        capture = capture_service.discard_capture(
            db,
            actor,
            capture_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
        )
    except Exception as error:
        failure = _rolled_back(db, error)
        if failure is None:
            raise
        return failure
    db.commit()
    return CaptureDetail.from_capture(capture)


@router.post(
    "/{capture_id}/submit",
    response_model=SubmitCaptureResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_trusted_origin)],
    summary="Create the order for a ready capture",
    description=(
        "Creates a pending_review order (source ai_text) with the shared "
        "pricing, scheme, stock and retailer validation; nothing is deducted. "
        "The order desk confirms it through the existing workflow. 201 creates "
        "the order; 200 returns the order of an already submitted capture."
    ),
    responses={
        200: {"model": SubmitCaptureResponse},
        **_CHANGE,
        409: {"model": CaptureNotReadyResponse},
        422: {"model": CaptureLinesErrorResponse},
    },
)
def submit_capture(
    capture_id: UUID,
    payload: SubmitCaptureRequest,
    response: Response,
    actor: Actor,
    db: DbSession,
) -> SubmitCaptureResponse | JSONResponse:
    try:
        outcome = capture_service.submit_capture(
            db,
            actor,
            capture_id,
            expected_version=payload.expected_version,
            client_request_id=payload.client_request_id,
        )
    except Exception as error:
        failure = _rolled_back(db, error)
        if failure is None:
            raise
        return failure
    db.commit()
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    order = outcome.order
    return SubmitCaptureResponse(
        created=outcome.created,
        order=CaptureOrderLink(
            id=order.id, order_number=order.order_number, status=order.status
        ),
        capture=CaptureDetail.from_capture(outcome.capture),
    )
