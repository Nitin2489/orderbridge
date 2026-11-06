"""Phone OTP sign-in and server-side sessions.

Business rules only: no HTTP, no cookies and no commits. The caller owns the
transaction and must commit after every call, including after an
OtpVerificationError, because a failed attempt is recorded on the challenge.

Eligibility: only an existing, active user may sign in, and a retailer user
also needs an active Retailer record. Ineligible and unknown numbers are
treated identically, so callers cannot tell them apart.

Lock order
----------
request_otp() and verify_otp() take row locks (SELECT ... FOR UPDATE) in
the same order, so they cannot deadlock with each other:

    1. users row for the phone number
    2. retailers row of that user (retailer accounts only)
    3. otp_challenges rows for the phone number
    (4. user_sessions: insert only)

Locking the user row first serialises all OTP issuance and verification for
one account: concurrent requests cannot both pass the resend cooldown, and
concurrent verifications of one code cannot both create a session.
Eligibility is decided on the locked, freshly read user and retailer rows,
so a concurrent deactivation either completes first (and is seen) or waits
until sign-in finishes. Any other code that locks more than one of these
rows (for example, deactivating an account) must use the same order. Locks
are per account; there is no table or global lock.

PostgreSQL concurrency tests (tests/test_auth_concurrency.py) exercise
these guarantees with separate connections, because sequential tests on one
connection can never observe lock waits.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.phone import InvalidPhoneNumberError, normalize_phone_number
from app.core.security import (
    OTP_DIGITS,
    generate_otp,
    generate_session_token,
    hash_otp,
    hash_session_token,
    otp_matches,
)
from app.models import OtpChallenge, Retailer, User, UserRole, UserSession

_OTP_FORMAT = re.compile(rf"[0-9]{{{OTP_DIGITS}}}")


class OtpFailureReason(StrEnum):
    INVALID_REQUEST = "invalid_request"
    NO_CHALLENGE = "no_challenge"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"
    INVALID_CODE = "invalid_code"
    ACCOUNT_UNAVAILABLE = "account_unavailable"


class OtpVerificationError(Exception):
    """The code could not be verified.

    The reason is for tests and internal diagnostics only; API responses must
    stay generic. The message never contains the submitted or expected code.
    """

    def __init__(self, reason: OtpFailureReason) -> None:
        self.reason = reason
        super().__init__(f"OTP verification failed: {reason}")


@dataclass(frozen=True)
class IssuedOtp:
    """A newly issued challenge.

    code is the raw OTP: hand it to a delivery channel (or, in development,
    the debug response) and never persist or log it.
    """

    challenge_id: UUID
    expires_at: datetime
    code: str = field(repr=False)


@dataclass(frozen=True)
class IssuedSession:
    """A newly created session. token is the raw cookie value; never log it."""

    user: User
    expires_at: datetime
    token: str = field(repr=False)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def is_user_eligible(user: User | None) -> bool:
    """Active user; retailers additionally need an active Retailer record."""
    if user is None:
        return False

    return _is_account_eligible(user, user.retailer)


def _is_account_eligible(user: User | None, retailer: Retailer | None) -> bool:
    if user is None or not user.is_active:
        return False

    if user.role == UserRole.ORDER_DESK:
        return True

    if user.role == UserRole.RETAILER:
        return retailer is not None and retailer.is_active

    return False


def _lock_account(session: Session, phone: str) -> tuple[User | None, Retailer | None]:
    """Lock and freshly read the user and (for retailers) the retailer row.

    Steps 1 and 2 of the module's lock order. populate_existing replaces any
    copy already in the session with the locked database values.
    """
    user = session.scalar(
        select(User)
        .where(User.phone_number == phone)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if user is None or user.role != UserRole.RETAILER:
        return user, None

    retailer = session.scalar(
        select(Retailer)
        .where(Retailer.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return user, retailer


def request_otp(
    session: Session,
    phone_number: str,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> IssuedOtp | None:
    """Issue a new OTP challenge for an eligible account.

    Returns None, without creating anything, when the number is unknown or
    ineligible, or when an outstanding challenge was issued within the resend
    cooldown (that challenge stays valid). Otherwise every outstanding
    challenge for the number is invalidated (expired now) and a new one is
    created. Only the HMAC digest of the code is stored.
    """
    phone = normalize_phone_number(phone_number)

    # Lock order steps 1-2: concurrent requests for one account queue here,
    # so only one of them can pass the cooldown check below.
    user, retailer = _lock_account(session, phone)

    # Read the clock only once the locks are held: waiting for them can take
    # a while, and the cooldown, invalidation and expiry must use the time at
    # which this request actually proceeds. An explicit now is kept as given.
    if now is None:
        now = _utcnow()

    if not _is_account_eligible(user, retailer):
        return None

    outstanding = (
        OtpChallenge.phone_number == phone,
        OtpChallenge.consumed_at.is_(None),
        OtpChallenge.expires_at > now,
    )
    latest = session.scalar(
        select(OtpChallenge)
        .where(*outstanding)
        .order_by(OtpChallenge.created_at.desc())
        .limit(1)
    )
    cooldown = timedelta(seconds=settings.auth_otp_resend_cooldown_seconds)

    if latest is not None and latest.created_at > now - cooldown:
        return None

    session.execute(update(OtpChallenge).where(*outstanding).values(expires_at=now))

    code = generate_otp()
    challenge_id = uuid4()
    expires_at = now + timedelta(seconds=settings.auth_otp_ttl_seconds)

    session.add(
        OtpChallenge(
            id=challenge_id,
            phone_number=phone,
            code_hash=hash_otp(
                settings.auth_secret,
                challenge_id=challenge_id,
                phone_number=phone,
                code=code,
            ),
            expires_at=expires_at,
            attempts=0,
            created_at=now,
        )
    )
    session.flush()

    return IssuedOtp(challenge_id=challenge_id, expires_at=expires_at, code=code)


def verify_otp(
    session: Session,
    phone_number: str,
    code: str,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> IssuedSession:
    """Verify a code against the latest challenge and start a session.

    Locks the user, the retailer (if any) and then the latest challenge,
    following the module's lock order, so concurrent attempts for one
    account are serialised: once one request consumes the challenge, every
    other one sees it as consumed. A wrong code increments attempts; after
    auth_otp_max_attempts failures the challenge is exhausted. A correct
    code consumes the challenge, then the locked account is re-checked.

    Raises OtpVerificationError. The caller must still commit, so recorded
    attempts and consumption persist.
    """
    try:
        phone = normalize_phone_number(phone_number)
    except InvalidPhoneNumberError:
        raise OtpVerificationError(OtpFailureReason.INVALID_REQUEST) from None

    if not _OTP_FORMAT.fullmatch(code):
        raise OtpVerificationError(OtpFailureReason.INVALID_REQUEST)

    # Lock order steps 1-2, before the challenge (step 3). Unknown numbers
    # lock nothing here and fail below with no challenge.
    user, retailer = _lock_account(session, phone)

    # As in request_otp: the expiry check, consumption time and session
    # timestamps use the time after the locks were acquired.
    if now is None:
        now = _utcnow()

    challenge = session.scalar(
        select(OtpChallenge)
        .where(OtpChallenge.phone_number == phone)
        .order_by(OtpChallenge.created_at.desc(), OtpChallenge.id.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if challenge is None:
        raise OtpVerificationError(OtpFailureReason.NO_CHALLENGE)
    if challenge.consumed_at is not None:
        raise OtpVerificationError(OtpFailureReason.CONSUMED)
    if challenge.expires_at <= now:
        raise OtpVerificationError(OtpFailureReason.EXPIRED)
    if challenge.attempts >= settings.auth_otp_max_attempts:
        raise OtpVerificationError(OtpFailureReason.EXHAUSTED)

    if not otp_matches(
        settings.auth_secret,
        challenge_id=challenge.id,
        phone_number=phone,
        code=code,
        expected_hash=challenge.code_hash,
    ):
        challenge.attempts += 1
        session.flush()
        raise OtpVerificationError(OtpFailureReason.INVALID_CODE)

    # The code was right: it can never be used again, whatever happens next.
    challenge.consumed_at = now

    if user is None or not _is_account_eligible(user, retailer):
        session.flush()
        raise OtpVerificationError(OtpFailureReason.ACCOUNT_UNAVAILABLE)

    return create_session(session, user, settings=settings, now=now)


def create_session(
    session: Session,
    user: User,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> IssuedSession:
    """Create a server-side session; only the token's SHA-256 is stored."""
    now = now or _utcnow()
    token = generate_session_token()
    expires_at = now + timedelta(seconds=settings.auth_session_ttl_seconds)

    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=expires_at,
            created_at=now,
        )
    )
    session.flush()

    return IssuedSession(user=user, expires_at=expires_at, token=token)


def authenticate_session(
    session: Session, token: str | None, *, now: datetime | None = None
) -> User | None:
    """Return the user for a valid session token, or None.

    Valid means: the token's hash matches a session that is neither expired
    nor revoked, and its user is still eligible.
    """
    if not token:
        return None

    now = now or _utcnow()
    user_session = session.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_session_token(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
    )

    if user_session is None or not is_user_eligible(user_session.user):
        return None

    return user_session.user


def revoke_session(
    session: Session, token: str | None, *, now: datetime | None = None
) -> bool:
    """Revoke the session for a token. Returns False if nothing was revoked.

    Safe to call repeatedly and with expired, revoked or unknown tokens.
    """
    if not token:
        return False

    now = now or _utcnow()
    user_session = session.scalar(
        select(UserSession)
        .where(UserSession.token_hash == hash_session_token(token))
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if user_session is None or user_session.revoked_at is not None:
        return False

    user_session.revoked_at = now
    session.flush()
    return True
