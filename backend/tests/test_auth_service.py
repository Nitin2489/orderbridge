"""Tests for app/services/auth_service.py (OTP challenges and sessions).

Runs against the dedicated test database through db_session; seed_all()
provides the demo accounts. Time is passed explicitly, so expiry and
cooldown are tested without waiting.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.security import hash_otp, hash_session_token
from app.models import OtpChallenge, Retailer, User, UserRole, UserSession
from app.services import auth_service
from app.services.auth_service import (
    OtpFailureReason,
    OtpVerificationError,
    authenticate_session,
    create_session,
    request_otp,
    revoke_session,
    verify_otp,
)
from seed import seed_all

SECRET = "service-test-secret-0123456789abcdef0123"

SHARMA_PHONE = "+919000000001"
PATEL_PHONE = "+919000000003"
DESK_PHONE = "+919000000010"
UNKNOWN_PHONE = "+919999999999"

NOW = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="testing",
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        auth_secret_key=SECRET,
        auth_otp_ttl_seconds=300,
        auth_otp_max_attempts=5,
        auth_otp_resend_cooldown_seconds=60,
        auth_session_ttl_seconds=7 * 24 * 60 * 60,
        auth_debug_otp=True,
        auth_session_cookie_secure=False,
        frontend_origins=["http://localhost:3000"],
    )


@pytest.fixture
def seeded(db_session):
    seed_all(db_session)


def user_by_phone(db_session, phone: str) -> User:
    user = db_session.scalar(select(User).where(User.phone_number == phone))
    assert user is not None
    return user


def challenges_for(db_session, phone: str) -> list[OtpChallenge]:
    return list(
        db_session.scalars(
            select(OtpChallenge)
            .where(OtpChallenge.phone_number == phone)
            .order_by(OtpChallenge.created_at)
        )
    )


def count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model))


def wrong_code(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def assert_fails(reason, db_session, phone, code, settings, now):
    with pytest.raises(OtpVerificationError) as raised:
        verify_otp(db_session, phone, code, settings=settings, now=now)
    assert raised.value.reason == reason
    if code:
        assert code not in str(raised.value)


# ---------------------------------------------------------------------------
# Requesting codes
# ---------------------------------------------------------------------------


def test_request_otp_for_active_retailer_stores_only_hmac(db_session, seeded, settings):
    issued = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)

    assert issued is not None
    assert len(issued.code) == 6 and issued.code.isdigit()
    assert issued.code not in repr(issued)

    (challenge,) = challenges_for(db_session, SHARMA_PHONE)
    assert challenge.id == issued.challenge_id
    assert challenge.code_hash != issued.code
    assert issued.code not in challenge.code_hash
    assert challenge.code_hash == hash_otp(
        SECRET,
        challenge_id=challenge.id,
        phone_number=SHARMA_PHONE,
        code=issued.code,
    )
    assert challenge.expires_at == NOW + timedelta(minutes=5)
    assert challenge.attempts == 0
    assert challenge.consumed_at is None


def test_request_otp_for_active_order_desk(db_session, seeded, settings):
    issued = request_otp(db_session, DESK_PHONE, settings=settings, now=NOW)

    assert issued is not None
    assert len(challenges_for(db_session, DESK_PHONE)) == 1


def test_request_otp_normalises_phone_number(db_session, seeded, settings):
    issued = request_otp(db_session, "90000 00001", settings=settings, now=NOW)

    assert issued is not None
    assert len(challenges_for(db_session, SHARMA_PHONE)) == 1


def test_unknown_number_gets_no_challenge(db_session, seeded, settings):
    assert request_otp(db_session, UNKNOWN_PHONE, settings=settings, now=NOW) is None
    assert count(db_session, OtpChallenge) == 0


def test_inactive_user_gets_no_challenge(db_session, seeded, settings):
    user_by_phone(db_session, PATEL_PHONE).is_active = False
    db_session.flush()

    assert request_otp(db_session, PATEL_PHONE, settings=settings, now=NOW) is None
    assert count(db_session, OtpChallenge) == 0


def test_retailer_without_active_retailer_record_gets_no_challenge(
    db_session, seeded, settings
):
    user_by_phone(db_session, PATEL_PHONE).retailer.is_active = False
    db_session.flush()

    assert request_otp(db_session, PATEL_PHONE, settings=settings, now=NOW) is None

    # A retailer-role user with no Retailer record at all.
    db_session.add(
        User(
            phone_number="+919000000020",
            full_name="Retailer Without Shop",
            role=UserRole.RETAILER,
            is_active=True,
        )
    )
    db_session.flush()

    assert request_otp(db_session, "+919000000020", settings=settings, now=NOW) is None
    assert count(db_session, OtpChallenge) == 0


def test_resend_cooldown_keeps_the_existing_challenge(db_session, seeded, settings):
    first = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)

    again = request_otp(
        db_session, SHARMA_PHONE, settings=settings, now=NOW + timedelta(seconds=59)
    )

    assert again is None
    assert len(challenges_for(db_session, SHARMA_PHONE)) == 1
    # The first code is still valid.
    verify_otp(
        db_session,
        SHARMA_PHONE,
        first.code,
        settings=settings,
        now=NOW + timedelta(seconds=59),
    )


def test_new_challenge_invalidates_previous_one(db_session, seeded, settings):
    first = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)
    later = NOW + timedelta(seconds=61)

    second = request_otp(db_session, SHARMA_PHONE, settings=settings, now=later)

    assert second is not None
    old, new = challenges_for(db_session, SHARMA_PHONE)
    assert old.id == first.challenge_id
    assert old.expires_at == later  # invalidated
    assert new.id == second.challenge_id

    if first.code != second.code:
        assert_fails(
            OtpFailureReason.INVALID_CODE,
            db_session,
            SHARMA_PHONE,
            first.code,
            settings,
            later,
        )

    issued = verify_otp(
        db_session, SHARMA_PHONE, second.code, settings=settings, now=later
    )
    assert issued.user.phone_number == SHARMA_PHONE


# ---------------------------------------------------------------------------
# Verifying codes
# ---------------------------------------------------------------------------


def test_correct_code_consumes_challenge_and_creates_session(
    db_session, seeded, settings
):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)
    at = NOW + timedelta(minutes=1)

    issued = verify_otp(
        db_session, SHARMA_PHONE, issued_otp.code, settings=settings, now=at
    )

    assert issued.user.phone_number == SHARMA_PHONE
    assert issued.expires_at == at + timedelta(days=7)
    assert issued.token not in repr(issued)

    (challenge,) = challenges_for(db_session, SHARMA_PHONE)
    assert challenge.consumed_at == at

    (user_session,) = db_session.scalars(select(UserSession)).all()
    assert user_session.user_id == issued.user.id
    assert user_session.token_hash == hash_session_token(issued.token)
    assert user_session.token_hash != issued.token
    assert user_session.expires_at == issued.expires_at
    assert user_session.revoked_at is None


def test_wrong_code_increments_attempts_without_consuming(db_session, seeded, settings):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)

    assert_fails(
        OtpFailureReason.INVALID_CODE,
        db_session,
        SHARMA_PHONE,
        wrong_code(issued_otp.code),
        settings,
        NOW,
    )

    (challenge,) = challenges_for(db_session, SHARMA_PHONE)
    assert challenge.attempts == 1
    assert challenge.consumed_at is None
    assert count(db_session, UserSession) == 0

    # The right code still works after a wrong one.
    verify_otp(db_session, SHARMA_PHONE, issued_otp.code, settings=settings, now=NOW)


def test_fifth_failure_exhausts_challenge(db_session, seeded, settings):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)

    for _ in range(5):
        assert_fails(
            OtpFailureReason.INVALID_CODE,
            db_session,
            SHARMA_PHONE,
            wrong_code(issued_otp.code),
            settings,
            NOW,
        )

    (challenge,) = challenges_for(db_session, SHARMA_PHONE)
    assert challenge.attempts == 5

    # Even the correct code is refused, and attempts stop increasing.
    assert_fails(
        OtpFailureReason.EXHAUSTED,
        db_session,
        SHARMA_PHONE,
        issued_otp.code,
        settings,
        NOW,
    )
    assert challenge.attempts == 5
    assert challenge.consumed_at is None
    assert count(db_session, UserSession) == 0


def test_expired_code_is_rejected(db_session, seeded, settings):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)

    assert_fails(
        OtpFailureReason.EXPIRED,
        db_session,
        SHARMA_PHONE,
        issued_otp.code,
        settings,
        NOW + timedelta(minutes=5),
    )
    assert count(db_session, UserSession) == 0


def test_consumed_code_cannot_be_replayed(db_session, seeded, settings):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)
    verify_otp(db_session, SHARMA_PHONE, issued_otp.code, settings=settings, now=NOW)

    assert_fails(
        OtpFailureReason.CONSUMED,
        db_session,
        SHARMA_PHONE,
        issued_otp.code,
        settings,
        NOW + timedelta(seconds=1),
    )
    assert count(db_session, UserSession) == 1


def test_number_without_challenge_is_rejected(db_session, seeded, settings):
    assert_fails(
        OtpFailureReason.NO_CHALLENGE,
        db_session,
        UNKNOWN_PHONE,
        "123456",
        settings,
        NOW,
    )


@pytest.mark.parametrize("code", ["12345", "1234567", "12a456", "", "١٢٣٤٥٦"])
def test_malformed_code_is_rejected_without_counting(
    db_session, seeded, settings, code
):
    request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)

    assert_fails(
        OtpFailureReason.INVALID_REQUEST,
        db_session,
        SHARMA_PHONE,
        code,
        settings,
        NOW,
    )
    (challenge,) = challenges_for(db_session, SHARMA_PHONE)
    assert challenge.attempts == 0


def test_account_deactivated_before_verification_is_rejected(
    db_session, seeded, settings
):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)
    user_by_phone(db_session, SHARMA_PHONE).is_active = False
    db_session.flush()

    assert_fails(
        OtpFailureReason.ACCOUNT_UNAVAILABLE,
        db_session,
        SHARMA_PHONE,
        issued_otp.code,
        settings,
        NOW,
    )

    # The correct code was used up; it cannot be retried later.
    (challenge,) = challenges_for(db_session, SHARMA_PHONE)
    assert challenge.consumed_at == NOW
    assert count(db_session, UserSession) == 0


def test_retailer_deactivated_before_verification_is_rejected(
    db_session, seeded, settings
):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)
    user_by_phone(db_session, SHARMA_PHONE).retailer.is_active = False
    db_session.flush()

    assert_fails(
        OtpFailureReason.ACCOUNT_UNAVAILABLE,
        db_session,
        SHARMA_PHONE,
        issued_otp.code,
        settings,
        NOW,
    )
    assert count(db_session, UserSession) == 0


# ---------------------------------------------------------------------------
# Timestamps: read after the account locks are held
# ---------------------------------------------------------------------------


@pytest.fixture
def clock_after_lock(monkeypatch):
    """Record the order of account locking and clock reads.

    The clock returns the time set in state["time"], standing in for the
    moment a request proceeds after possibly waiting for the locks.
    """
    state = {"events": [], "time": NOW}
    real_lock_account = auth_service._lock_account

    def recording_lock_account(session, phone):
        state["events"].append("lock")
        return real_lock_account(session, phone)

    def recording_clock():
        state["events"].append("clock")
        return state["time"]

    monkeypatch.setattr(auth_service, "_lock_account", recording_lock_account)
    monkeypatch.setattr(auth_service, "_utcnow", recording_clock)
    return state


@pytest.fixture
def clock_forbidden(monkeypatch):
    """Fail if the service reads the clock although now was supplied."""

    def forbidden_clock():
        raise AssertionError("_utcnow() called although now was supplied")

    monkeypatch.setattr(auth_service, "_utcnow", forbidden_clock)


def test_request_otp_reads_clock_after_locking(
    db_session, seeded, settings, clock_after_lock
):
    after_wait = NOW + timedelta(seconds=30)
    clock_after_lock["time"] = after_wait

    issued = request_otp(db_session, SHARMA_PHONE, settings=settings)

    assert clock_after_lock["events"] == ["lock", "clock"]
    (challenge,) = challenges_for(db_session, SHARMA_PHONE)
    assert challenge.created_at == after_wait
    assert challenge.expires_at == after_wait + timedelta(minutes=5)
    assert issued.expires_at == challenge.expires_at


def test_request_otp_cooldown_and_invalidation_use_post_lock_time(
    db_session, seeded, settings, clock_after_lock
):
    first = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)

    # The request started inside the cooldown but proceeded after it ended.
    clock_after_lock["time"] = NOW + timedelta(seconds=61)
    clock_after_lock["events"].clear()

    second = request_otp(db_session, SHARMA_PHONE, settings=settings)

    assert clock_after_lock["events"] == ["lock", "clock"]
    assert second is not None
    old, new = challenges_for(db_session, SHARMA_PHONE)
    assert old.id == first.challenge_id
    assert old.expires_at == NOW + timedelta(seconds=61)
    assert new.created_at == NOW + timedelta(seconds=61)


def test_verify_otp_reads_clock_after_locking(
    db_session, seeded, settings, clock_after_lock
):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)
    after_wait = NOW + timedelta(minutes=1)
    clock_after_lock["time"] = after_wait
    clock_after_lock["events"].clear()

    issued = verify_otp(db_session, SHARMA_PHONE, issued_otp.code, settings=settings)

    assert clock_after_lock["events"] == ["lock", "clock"]
    (challenge,) = challenges_for(db_session, SHARMA_PHONE)
    assert challenge.consumed_at == after_wait
    assert issued.expires_at == after_wait + timedelta(days=7)
    (user_session,) = db_session.scalars(select(UserSession)).all()
    assert user_session.created_at == after_wait


def test_verify_otp_expiry_uses_post_lock_time(
    db_session, seeded, settings, clock_after_lock
):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)

    # Valid when the request arrived, expired by the time the locks were held.
    clock_after_lock["time"] = NOW + timedelta(minutes=5)

    assert_fails(
        OtpFailureReason.EXPIRED,
        db_session,
        SHARMA_PHONE,
        issued_otp.code,
        settings,
        None,
    )
    assert count(db_session, UserSession) == 0


def test_explicit_now_is_used_as_given(db_session, seeded, settings, clock_forbidden):
    issued_otp = request_otp(db_session, SHARMA_PHONE, settings=settings, now=NOW)
    at = NOW + timedelta(minutes=2)

    issued = verify_otp(
        db_session, SHARMA_PHONE, issued_otp.code, settings=settings, now=at
    )

    (challenge,) = challenges_for(db_session, SHARMA_PHONE)
    assert challenge.created_at == NOW
    assert challenge.expires_at == NOW + timedelta(minutes=5)
    assert challenge.consumed_at == at
    assert issued.expires_at == at + timedelta(days=7)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@pytest.fixture
def sharma_session(db_session, seeded, settings):
    return create_session(
        db_session, user_by_phone(db_session, SHARMA_PHONE), settings=settings, now=NOW
    )


def test_valid_session_authenticates(db_session, sharma_session):
    user = authenticate_session(db_session, sharma_session.token, now=NOW)

    assert user is not None
    assert user.phone_number == SHARMA_PHONE


def test_raw_session_token_is_not_stored(db_session, sharma_session):
    rows = db_session.scalars(select(UserSession)).all()

    assert [row.token_hash for row in rows] == [
        hash_session_token(sharma_session.token)
    ]
    assert all(sharma_session.token not in row.token_hash for row in rows)


@pytest.mark.parametrize("token", [None, "", "forged-token", "x" * 43])
def test_invalid_token_is_rejected(db_session, sharma_session, token):
    assert authenticate_session(db_session, token, now=NOW) is None


def test_expired_session_is_rejected(db_session, sharma_session):
    assert (
        authenticate_session(
            db_session, sharma_session.token, now=sharma_session.expires_at
        )
        is None
    )


def test_revoked_session_is_rejected(db_session, sharma_session):
    assert revoke_session(db_session, sharma_session.token, now=NOW) is True

    assert authenticate_session(db_session, sharma_session.token, now=NOW) is None
    # Revoking again is a safe no-op.
    assert revoke_session(db_session, sharma_session.token, now=NOW) is False
    assert revoke_session(db_session, "unknown-token", now=NOW) is False
    assert revoke_session(db_session, None, now=NOW) is False


def test_session_of_inactive_user_is_rejected(db_session, sharma_session):
    sharma_session.user.is_active = False
    db_session.flush()

    assert authenticate_session(db_session, sharma_session.token, now=NOW) is None


def test_session_of_inactive_retailer_is_rejected(db_session, sharma_session):
    retailer = db_session.scalar(
        select(Retailer).where(Retailer.user_id == sharma_session.user.id)
    )
    retailer.is_active = False
    db_session.flush()

    assert authenticate_session(db_session, sharma_session.token, now=NOW) is None


def test_order_desk_session_needs_no_retailer(db_session, seeded, settings):
    desk = user_by_phone(db_session, DESK_PHONE)
    issued = create_session(db_session, desk, settings=settings, now=NOW)

    assert authenticate_session(db_session, issued.token, now=NOW) is desk
