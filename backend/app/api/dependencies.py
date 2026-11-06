"""Shared FastAPI dependencies: database, settings, identity and CSRF origin.

Identity always comes from the server-side session behind the session
cookie. No dependency reads a user, role or retailer id supplied by the
client.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db
from app.core.origins import (
    InvalidOriginError,
    canonicalize_origin,
    origin_from_referer,
)
from app.models import Retailer, User, UserRole
from app.services import auth_service

NOT_AUTHENTICATED = "Not authenticated."
FORBIDDEN = "You do not have permission to perform this action."
UNTRUSTED_ORIGIN = "Request origin is not allowed."

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_app_settings(request: Request) -> Settings:
    """The settings the running application was created with (see create_app)."""
    return request.app.state.settings


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


def get_current_user(request: Request, db: DbSession, settings: AppSettings) -> User:
    """The user behind the session cookie; HTTP 401 if there is none.

    The token is hashed and looked up; expired or revoked sessions, inactive
    users and retailers without an active Retailer record are rejected.
    """
    token = request.cookies.get(settings.auth_session_cookie_name)
    user = auth_service.authenticate_session(db, token)

    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=NOT_AUTHENTICATED)

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """Dependency factory allowing only users with one of the given roles.

    Usage: Depends(require_roles(UserRole.ORDER_DESK)). Unauthenticated
    requests get 401; authenticated users with another role get 403.
    """
    if not roles:
        raise ValueError("require_roles() needs at least one role.")

    allowed = tuple(UserRole(role) for role in roles)

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
        return user

    return dependency


def get_current_retailer(
    user: Annotated[User, Depends(require_roles(UserRole.RETAILER))],
) -> Retailer:
    """The signed-in retailer user's own Retailer record.

    Ownership comes only from the session's user, never from a request
    value. Session authentication already rejects inactive retailers; the
    check here keeps this dependency safe on its own.
    """
    retailer = user.retailer

    if retailer is None or not retailer.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)

    return retailer


CurrentRetailer = Annotated[Retailer, Depends(get_current_retailer)]

# The signed-in order desk user (401 if not signed in, 403 for other roles).
# Its id is the actor for every desk action; no request value is trusted.
DeskUser = Annotated[User, Depends(require_roles(UserRole.ORDER_DESK))]


def _request_origin(request: Request) -> str | None:
    """The request's canonical origin, or None if it cannot be trusted.

    Uses the Origin header when present. A present but malformed Origin
    (including "null" or an empty value) is rejected outright, never
    replaced by the Referer. Only when Origin is absent is the Referer's
    origin used. Both go through the same canonicalisation as
    FRONTEND_ORIGINS (app.core.origins).
    """
    origin = request.headers.get("origin")

    try:
        if origin is not None:
            return canonicalize_origin(origin)

        referer = request.headers.get("referer")

        if referer:
            return origin_from_referer(referer)
    except InvalidOriginError:
        return None

    return None


def require_trusted_origin(request: Request, settings: AppSettings) -> None:
    """CSRF protection for state-changing, cookie-authenticated routes.

    Rejects (403) unsafe-method requests whose Origin (or, failing that,
    Referer) is not one of FRONTEND_ORIGINS, including requests with
    neither header. Browsers always send Origin on cross-origin POSTs, so a
    forged cross-site request cannot pass. CORS does not provide this: it
    only controls whether a browser may read the response.

    Attach it to every state-changing route that relies on the session
    cookie: dependencies=[Depends(require_trusted_origin)].

    Behind the planned same-origin Next.js proxy, the proxy must forward the
    browser's Origin (or Referer) header; a request arriving with neither
    is rejected.
    """
    if request.method in SAFE_METHODS:
        return

    origin = _request_origin(request)

    if origin is None or origin not in settings.frontend_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=UNTRUSTED_ORIGIN)
