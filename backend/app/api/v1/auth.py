"""Authentication endpoints: phone OTP sign-in and cookie sessions.

Transactions: the service functions never commit; each endpoint commits once
its work is complete. A failed verification is committed too, so the failed
attempt is recorded and cannot be retried without limit.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import (
    AppSettings,
    CurrentUser,
    DbSession,
    OtpDeliveryChannel,
    require_trusted_origin,
)
from app.api.rate_limits import TOO_MANY_REQUESTS, enforce_otp_rate_limits
from app.core.config import Settings
from app.schemas.auth import (
    INVALID_CODE_MESSAGE,
    OTP_REQUESTED_MESSAGE,
    AuthenticatedUser,
    ErrorResponse,
    OtpRequest,
    OtpRequestAccepted,
    OtpVerifyRequest,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_UNAUTHENTICATED = {401: {"model": ErrorResponse, "description": "Not signed in."}}
_UNTRUSTED_ORIGIN = {
    403: {"model": ErrorResponse, "description": "Untrusted request origin."}
}
_RATE_LIMITED = {
    429: {
        "model": ErrorResponse,
        "description": (
            f"{TOO_MANY_REQUESTS} Per client IP and per phone number; the "
            "Retry-After header gives the seconds until the window resets."
        ),
    }
}


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=token,
        max_age=settings.auth_session_ttl_seconds,
        path="/",
        secure=settings.auth_session_cookie_secure,
        httponly=True,
        samesite=settings.auth_session_cookie_samesite,
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_session_cookie_name,
        path="/",
        secure=settings.auth_session_cookie_secure,
        httponly=True,
        samesite=settings.auth_session_cookie_samesite,
    )


@router.post(
    "/request-otp",
    response_model=OtpRequestAccepted,
    responses=_RATE_LIMITED,
    summary="Request a one-time sign-in code",
    description=(
        "Issues a six-digit code for an active account and hands it to the "
        "configured delivery channel. The response is the same whether or "
        "not the number belongs to an account, and never contains the code. "
        "No SMS provider is integrated yet; in local development "
        "(APP_ENV=development, AUTH_DEBUG_OTP=true) the code is printed to "
        "the backend's terminal. A new code is not issued within the resend "
        "cooldown or while the number is locked out; the previous code stays "
        "valid during the cooldown."
    ),
)
def request_otp(
    payload: OtpRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    delivery: OtpDeliveryChannel,
) -> OtpRequestAccepted:
    enforce_otp_rate_limits(
        db, request, settings, action="request", phone_number=payload.phone_number
    )
    issued = auth_service.request_otp(db, payload.phone_number, settings=settings)
    db.commit()

    # Deliver only a code that has been committed, so it can be verified.
    if issued is not None:
        delivery.deliver(payload.phone_number, issued.code)

    return OtpRequestAccepted(message=OTP_REQUESTED_MESSAGE)


@router.post(
    "/verify-otp",
    response_model=AuthenticatedUser,
    dependencies=[Depends(require_trusted_origin)],
    summary="Verify a code and start a session",
    description=(
        "Verifies the code, sets the HTTP-only session cookie and returns the "
        "signed-in user. Every failure returns the same 401 response."
    ),
    responses={
        401: {"model": ErrorResponse, "description": INVALID_CODE_MESSAGE},
        **_UNTRUSTED_ORIGIN,
        **_RATE_LIMITED,
    },
)
def verify_otp(
    payload: OtpVerifyRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> AuthenticatedUser:
    enforce_otp_rate_limits(
        db, request, settings, action="verify", phone_number=payload.phone_number
    )
    try:
        issued = auth_service.verify_otp(
            db, payload.phone_number, payload.code, settings=settings
        )
    except auth_service.OtpVerificationError:
        # Persist the recorded attempt or consumption before failing.
        db.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail=INVALID_CODE_MESSAGE
        ) from None

    db.commit()
    _set_session_cookie(response, issued.token, settings)
    return AuthenticatedUser.from_user(issued.user)


@router.get(
    "/me",
    response_model=AuthenticatedUser,
    summary="The signed-in user",
    description="Identity comes only from the session cookie.",
    responses=_UNAUTHENTICATED,
)
def me(user: CurrentUser) -> AuthenticatedUser:
    return AuthenticatedUser.from_user(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_trusted_origin)],
    summary="End the current session",
    description=(
        "Revokes the server-side session, if any, and always clears the "
        "cookie. Safe to repeat, and works with expired or revoked sessions."
    ),
    responses=_UNTRUSTED_ORIGIN,
)
def logout(request: Request, db: DbSession, settings: AppSettings) -> Response:
    token = request.cookies.get(settings.auth_session_cookie_name)

    if auth_service.revoke_session(db, token):
        db.commit()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response, settings)
    return response
