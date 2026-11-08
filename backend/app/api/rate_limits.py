"""Rate limits for the unauthenticated OTP endpoints.

API layer: it decides the client IP, applies the per-IP and then the
per-number rule, and commits the counts at once, so they persist whatever
the endpoint does next (a refused or failed request still counts). The
commit happens before the endpoint takes any account or challenge locks.

The limits run before the phone number is looked up and the 429 response is
identical for every number, so it reveals nothing about whether a number is
registered or locked out.
"""

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.client_ip import client_ip
from app.core.config import Settings
from app.services import rate_limit
from app.services.rate_limit import RateLimitRule

TOO_MANY_REQUESTS = "Too many requests. Please wait and try again."

logger = logging.getLogger(__name__)

OtpAction = Literal["request", "verify"]


def otp_rate_limit_rules(
    settings: Settings, action: OtpAction
) -> tuple[RateLimitRule, RateLimitRule]:
    """(per-IP rule, per-phone rule) for an OTP endpoint."""
    if action == "request":
        return (
            RateLimitRule(
                name="otp_request:ip",
                limit=settings.rate_limit_otp_request_ip_limit,
                window_seconds=settings.rate_limit_otp_request_ip_window_seconds,
            ),
            RateLimitRule(
                name="otp_request:phone",
                limit=settings.rate_limit_otp_request_phone_limit,
                window_seconds=settings.rate_limit_otp_request_phone_window_seconds,
            ),
        )
    return (
        RateLimitRule(
            name="otp_verify:ip",
            limit=settings.rate_limit_otp_verify_ip_limit,
            window_seconds=settings.rate_limit_otp_verify_ip_window_seconds,
        ),
        RateLimitRule(
            name="otp_verify:phone",
            limit=settings.rate_limit_otp_verify_phone_limit,
            window_seconds=settings.rate_limit_otp_verify_phone_window_seconds,
        ),
    )


def enforce_otp_rate_limits(
    db: Session,
    request: Request,
    settings: Settings,
    *,
    action: OtpAction,
    phone_number: str,
    now: datetime | None = None,
) -> None:
    """Count this request; raise 429 (with Retry-After) if a limit is exceeded.

    phone_number must already be normalised. A request refused by the IP
    rule is not counted against the number, so one abusive client cannot
    use up a number's allowance on its own.

    Transaction contract: this function owns the commit of its counter
    transaction and must be the endpoint's first database operation.

    - It calls db.commit() once the counters are written, which commits
      everything pending in the request session, not only the counters.
    - The opportunistic cleanup that follows runs in its own transaction;
      if it fails, db.rollback() discards it, which also expires every
      object loaded in the session.

    Called first, neither has anything else to act on, and the session is
    clean and usable for the endpoint's own work afterwards. Calling it
    after loading or changing data would commit that data early, or leave
    loaded objects expired.
    """
    now = now or datetime.now(UTC)
    ip_rule, phone_rule = otp_rate_limit_rules(settings, action)
    secret = settings.auth_secret

    result = rate_limit.hit(
        db,
        ip_rule,
        client_ip(request, settings.trusted_proxy_networks),
        secret=secret,
        now=now,
    )
    if result.allowed:
        result = rate_limit.hit(db, phone_rule, phone_number, secret=secret, now=now)

    # The decision is final once the counts are committed.
    db.commit()

    _clean_up_expired_counters(db, settings, now=now)

    if not result.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=TOO_MANY_REQUESTS,
            headers={"Retry-After": str(result.retry_after_seconds)},
        )


def _clean_up_expired_counters(
    db: Session, settings: Settings, *, now: datetime
) -> None:
    """Opportunistic cleanup in its own short transaction, never fatal.

    Runs only after the counters have been committed, so a failure here can
    neither lose them nor change the decision. Any error rolls back the
    cleanup transaction alone and is logged by type only (no identifiers,
    keys or SQL parameters); the request then continues normally.
    """
    try:
        rate_limit.maybe_purge_expired(
            db, now=now, probability=settings.rate_limit_cleanup_probability
        )
        db.commit()
    # Deliberately broad: cleanup is housekeeping and must never fail a request.
    except Exception as error:  # noqa: BLE001
        logger.warning(
            "Rate-limit cleanup failed (%s); counters are unaffected.",
            type(error).__name__,
        )
        try:
            db.rollback()
        # Deliberately broad: a failed rollback must not fail the request either.
        except Exception as rollback_error:  # noqa: BLE001
            # The connection itself is unusable; the request's next database
            # call reports that as it would have anyway.
            logger.warning(
                "Rollback after rate-limit cleanup failed (%s).",
                type(rollback_error).__name__,
            )
