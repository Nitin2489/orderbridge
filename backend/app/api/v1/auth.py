"""Authentication endpoints: phone OTP sign-in and cookie sessions.

Transactions: the service functions never commit; each endpoint commits once
its work is complete. A failed verification is committed too, so the failed
attempt is recorded and cannot be retried without limit.

Reviewer access (app.services.demo_access): the numbers in
AUTH_DEMO_OTP_NUMBERS are public demo accounts, so request-otp and
verify-otp act on them only for a browser holding a valid reviewer access
cookie. For any other browser, a request about a demo number is counted in
a separate rate-limit bucket and gets the usual generic answer without
issuing, invalidating or checking a code. Numbers that are not listed
behave exactly as before.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import (
    AppSettings,
    CurrentUser,
    DbSession,
    OtpDeliveryChannel,
    require_trusted_origin,
)
from app.api.rate_limits import (
    TOO_MANY_REQUESTS,
    enforce_demo_access_rate_limit,
    enforce_otp_rate_limits,
)
from app.core.config import Settings
from app.schemas.auth import (
    DEMO_ACCESS_DENIED_MESSAGE,
    INVALID_CODE_MESSAGE,
    OTP_REQUESTED_MESSAGE,
    AuthenticatedUser,
    DemoAccessStatus,
    ErrorResponse,
    OtpRequest,
    OtpRequestAccepted,
    OtpVerifyRequest,
)
from app.services import auth_service, demo_access

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


def _has_reviewer_access(request: Request, settings: Settings) -> bool:
    token = request.cookies.get(demo_access.ACCESS_COOKIE_NAME)
    return demo_access.access_token_is_valid(token, settings)


def _demo_number_without_access(
    phone_number: str, request: Request, settings: Settings
) -> bool:
    """A listed demo number, asked about by a browser without reviewer access."""
    if phone_number not in settings.auth_demo_otp_numbers:
        return False
    return not _has_reviewer_access(request, settings)


@router.post(
    "/request-otp",
    response_model=OtpRequestAccepted,
    dependencies=[Depends(require_trusted_origin)],
    # demo_code is omitted unless set, so every other response is unchanged.
    response_model_exclude_none=True,
    responses=_RATE_LIMITED,
    summary="Request a one-time sign-in code",
    description=(
        "Issues a six-digit code for an active account and hands it to the "
        "configured delivery channel. The response is the same whether or "
        "not the number belongs to an account, and never contains the code. "
        "No SMS provider is integrated yet; in local development "
        "(APP_ENV=development, AUTH_DEBUG_OTP=true) the code is printed to "
        "the backend's terminal. For the demonstration accounts listed in "
        "AUTH_DEMO_OTP_NUMBERS (empty by default), a code is issued only to a "
        "browser that has unlocked reviewer access (POST /auth/demo-access), "
        "and is then returned as demo_code, so a reviewer can sign in without "
        "SMS; any other browser gets the generic response and nothing is "
        "issued or invalidated. A new code is not issued within the resend "
        "cooldown or while the number is locked out; the previous code stays "
        "valid during the cooldown."
    ),
)
def request_otp(
    payload: OtpRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    delivery: OtpDeliveryChannel,
) -> OtpRequestAccepted:
    locked_demo = _demo_number_without_access(payload.phone_number, request, settings)
    enforce_otp_rate_limits(
        db,
        request,
        settings,
        action="request",
        phone_number=payload.phone_number,
        demo_unauthorized=locked_demo,
    )
    if locked_demo:
        # Without an SMS provider a demo code could reach no one but this
        # browser, and issuing one would expire the reviewer's current code.
        return OtpRequestAccepted(message=OTP_REQUESTED_MESSAGE)

    issued = auth_service.request_otp(db, payload.phone_number, settings=settings)
    db.commit()

    # Deliver only a code that has been committed, so it can be verified.
    if issued is not None:
        delivery.deliver(payload.phone_number, issued.code)

    demo_code = None
    # Reaching here with a listed number means reviewer access was checked.
    if issued is not None and payload.phone_number in settings.auth_demo_otp_numbers:
        demo_code = issued.code
        # The code must not be kept by any cache between here and the browser.
        response.headers["Cache-Control"] = "no-store"
    return OtpRequestAccepted(message=OTP_REQUESTED_MESSAGE, demo_code=demo_code)


@router.post(
    "/verify-otp",
    response_model=AuthenticatedUser,
    dependencies=[Depends(require_trusted_origin)],
    summary="Verify a code and start a session",
    description=(
        "Verifies the code, sets the HTTP-only session cookie and returns the "
        "signed-in user. Every failure returns the same 401 response. Codes "
        "for the demonstration accounts in AUTH_DEMO_OTP_NUMBERS are checked "
        "only for a browser with reviewer access; any other browser gets the "
        "401 without the attempt counting against the account."
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
    locked_demo = _demo_number_without_access(payload.phone_number, request, settings)
    enforce_otp_rate_limits(
        db,
        request,
        settings,
        action="verify",
        phone_number=payload.phone_number,
        demo_unauthorized=locked_demo,
    )
    if locked_demo:
        # Not checked and not recorded: an outsider can neither use the
        # reviewer's code nor lock the demo account with wrong guesses.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_CODE_MESSAGE)

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


async def _submitted_access_key(request: Request) -> str | None:
    """access_key from a JSON body {"access_key": "..."}; None if malformed.

    Parsed here instead of by a Pydantic body model on purpose: FastAPI's
    422 response echoes the submitted input, and this input is a secret.
    Every malformed body gets the same 401 as a wrong key.
    """
    try:
        payload = json.loads(await request.body())
    except ValueError:  # Invalid JSON or encoding.
        return None
    if not isinstance(payload, dict) or set(payload) != {"access_key"}:
        return None
    key = payload["access_key"]
    if not isinstance(key, str) or len(key) > demo_access.MAX_ACCESS_KEY_LENGTH:
        return None
    return key


_ACCESS_KEY_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["access_key"],
                    "additionalProperties": False,
                    "properties": {
                        "access_key": {
                            "type": "string",
                            "format": "password",
                            "maxLength": demo_access.MAX_ACCESS_KEY_LENGTH,
                        }
                    },
                }
            }
        },
    }
}


@router.get(
    "/demo-access",
    response_model=DemoAccessStatus,
    summary="Whether reviewer access is offered and active",
    description=(
        "enabled: this deployment has demonstration accounts. active: this "
        "browser has unlocked reviewer access. Contains no key or token."
    ),
)
def demo_access_status(
    request: Request, response: Response, settings: AppSettings
) -> DemoAccessStatus:
    response.headers["Cache-Control"] = "no-store"
    enabled = demo_access.demo_access_enabled(settings)
    return DemoAccessStatus(
        enabled=enabled,
        active=enabled and _has_reviewer_access(request, settings),
    )


@router.post(
    "/demo-access",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_trusted_origin)],
    openapi_extra=_ACCESS_KEY_BODY,
    summary="Unlock reviewer access to the demonstration accounts",
    description=(
        "Checks the private reviewer access key (AUTH_DEMO_ACCESS_KEY) in "
        "constant time and, if it matches, sets an HttpOnly cookie, valid "
        "for 8 hours, that lets this browser receive the codes of the "
        "demonstration accounts. It grants no session or role. Every "
        "attempt counts against a per-IP limit. 404 when this deployment has "
        "no demonstration accounts."
    ),
    responses={
        401: {"model": ErrorResponse, "description": DEMO_ACCESS_DENIED_MESSAGE},
        404: {"model": ErrorResponse, "description": "Not offered here."},
        **_UNTRUSTED_ORIGIN,
        **_RATE_LIMITED,
    },
)
def unlock_demo_access(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    submitted: Annotated[str | None, Depends(_submitted_access_key)],
) -> Response:
    if not demo_access.demo_access_enabled(settings):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not Found")

    enforce_demo_access_rate_limit(db, request, settings)

    if submitted is None or not demo_access.access_key_matches(submitted, settings):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=DEMO_ACCESS_DENIED_MESSAGE,
            headers={"Cache-Control": "no-store"},
        )

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=demo_access.ACCESS_COOKIE_NAME,
        value=demo_access.issue_access_token(settings),
        max_age=int(demo_access.ACCESS_TTL.total_seconds()),
        path=demo_access.ACCESS_COOKIE_PATH,
        # Secure wherever session cookies are (always in production).
        secure=settings.auth_session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


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
