"""PostgreSQL concurrency tests for OTP issuance and verification.

Why these exist: the guarantees in auth_service's lock order (one session per
code, one challenge per cooldown window) depend on row locks held across
separate transactions. The ordinary db_session tests run on one connection
and one transaction, where a lock can never block anything, so they cannot
observe these guarantees.

How they work:
- Each participant uses its own Session on the safety-checked test_engine
  (tests/conftest.py), so each has its own connection and transaction.
- Concurrent transactions only see committed data, so these tests cannot use
  the rolled-back db_session fixture. Instead they commit one retailer
  account under PHONE, which is reserved for this module, and delete
  everything for that number before and after each test. Rows of the seeded
  demo accounts are never touched.
- "Pinned" tests make the ordering deterministic: the first transaction
  takes its locks, the second is started, and the test waits until
  PostgreSQL reports that second backend as waiting on a lock
  (pg_stat_activity.wait_event_type = 'Lock') before committing the first.
  No assertion depends on sleep timing.
- "Race" tests release both participants together with a threading.Barrier
  and assert only order-independent outcomes.
- Every transaction sets lock_timeout, so a regression fails the test
  instead of hanging it.
"""

import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from queue import Queue

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import OtpChallenge, Retailer, User, UserRole, UserSession
from app.services.auth_service import (
    IssuedOtp,
    OtpFailureReason,
    OtpVerificationError,
    request_otp,
    verify_otp,
)

# Reserved for this module; not used by the seed data or any other test.
PHONE = "+919000000901"

WAIT_SECONDS = 10.0
SESSION_CREATED = "session_created"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="testing",
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        auth_secret_key="concurrency-test-secret-0123456789abcdef",
        auth_otp_ttl_seconds=300,
        auth_otp_max_attempts=5,
        auth_otp_resend_cooldown_seconds=60,
        auth_session_ttl_seconds=7 * 24 * 60 * 60,
        auth_debug_otp=True,
        auth_session_cookie_secure=False,
        frontend_origins=["http://localhost:3000"],
    )


def _delete_account_rows(engine: Engine) -> None:
    """Remove every row this module may have committed for PHONE."""
    user_ids = select(User.id).where(User.phone_number == PHONE)

    with Session(engine) as session:
        for statement in (
            delete(UserSession).where(UserSession.user_id.in_(user_ids)),
            delete(OtpChallenge).where(OtpChallenge.phone_number == PHONE),
            delete(Retailer).where(Retailer.user_id.in_(user_ids)),
            delete(User).where(User.phone_number == PHONE),
        ):
            session.execute(statement.execution_options(synchronize_session=False))
        session.commit()


@pytest.fixture
def committed_account(test_engine: Engine) -> Generator[None, None, None]:
    """A committed, active retailer account visible to every connection."""
    _delete_account_rows(test_engine)

    with Session(test_engine) as session:
        user = User(
            phone_number=PHONE,
            full_name="Concurrency Test Owner",
            role=UserRole.RETAILER,
            is_active=True,
        )
        session.add(user)
        session.flush()
        session.add(
            Retailer(
                user_id=user.id,
                business_name="Concurrency Test Store",
                owner_name="Concurrency Test Owner",
                address="Test Address, Jabalpur",
                credit_limit=Decimal("0.00"),
                outstanding_balance=Decimal("0.00"),
                is_active=True,
            )
        )
        session.commit()

    try:
        yield
    finally:
        _delete_account_rows(test_engine)


def _new_session(engine: Engine) -> Session:
    """A session on its own connection whose transaction gives up on locks."""
    session = Session(engine, autoflush=False, expire_on_commit=False)
    session.execute(text("SET LOCAL lock_timeout = '10s'"))
    return session


def _backend_pid(session: Session) -> int:
    return session.scalar(text("SELECT pg_backend_pid()"))


def _wait_until_blocked_on_lock(engine: Engine, pid: int) -> None:
    """Wait until PostgreSQL reports backend pid as waiting for a lock."""
    deadline = time.monotonic() + WAIT_SECONDS

    with engine.connect() as connection:
        while time.monotonic() < deadline:
            wait_type = connection.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            # End the transaction so the next read sees fresh statistics.
            connection.rollback()

            if wait_type == "Lock":
                return

            time.sleep(0.02)

    pytest.fail(f"Backend {pid} never waited on a row lock; it was not serialised.")


def _issue_code(engine: Engine, settings: Settings) -> str:
    with _new_session(engine) as session:
        issued = request_otp(session, PHONE, settings=settings)
        assert issued is not None
        session.commit()
        return issued.code


def _verify(session: Session, code: str, settings: Settings) -> str:
    """Verify as the API does: commit on success and on failure."""
    try:
        verify_otp(session, PHONE, code, settings=settings)
    except OtpVerificationError as error:
        session.commit()
        return error.reason

    session.commit()
    return SESSION_CREATED


def _request(session: Session, settings: Settings) -> IssuedOtp | None:
    issued = request_otp(session, PHONE, settings=settings)
    session.commit()
    return issued


def _run_while_holding_locks(
    engine: Engine,
    hold: Callable[[Session], object],
    contend: Callable[[Session], object],
) -> tuple[object, object]:
    """Run hold() in an open transaction, then contend() in another.

    contend() is started only after hold() has taken its locks, and the
    holder commits only once PostgreSQL shows the contender blocked on a
    lock. Returns (hold result, contend result).
    """
    pids: Queue[int] = Queue()

    def contender() -> object:
        with _new_session(engine) as session:
            pids.put(_backend_pid(session))
            return contend(session)

    holder = _new_session(engine)

    try:
        held = hold(holder)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(contender)
            _wait_until_blocked_on_lock(engine, pids.get(timeout=WAIT_SECONDS))
            holder.commit()
            contended = future.result(timeout=WAIT_SECONDS * 3)
    finally:
        holder.close()

    return held, contended


def _race(engine: Engine, action: Callable[[Session], object]) -> list[object]:
    """Run action twice in parallel transactions released by a barrier."""
    barrier = threading.Barrier(2, timeout=WAIT_SECONDS)

    def participant() -> object:
        with _new_session(engine) as session:
            barrier.wait()
            return action(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(participant) for _ in range(2)]
        return [future.result(timeout=WAIT_SECONDS * 3) for future in futures]


def _account_state(engine: Engine) -> tuple[int, list[OtpChallenge]]:
    """(number of sessions, challenges) committed for PHONE."""
    with Session(engine) as session:
        sessions = session.scalar(
            select(func.count(UserSession.id))
            .join(User, UserSession.user_id == User.id)
            .where(User.phone_number == PHONE)
        )
        challenges = list(
            session.scalars(
                select(OtpChallenge).where(OtpChallenge.phone_number == PHONE)
            )
        )

    return sessions, challenges


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_second_verification_waits_and_cannot_reuse_the_code(
    test_engine, committed_account, settings
):
    code = _issue_code(test_engine, settings)

    first, second = _run_while_holding_locks(
        test_engine,
        hold=lambda session: verify_otp(session, PHONE, code, settings=settings),
        contend=lambda session: _verify(session, code, settings),
    )

    assert first.user.phone_number == PHONE
    assert second == OtpFailureReason.CONSUMED

    sessions, challenges = _account_state(test_engine)
    assert sessions == 1
    (challenge,) = challenges
    assert challenge.consumed_at is not None
    assert challenge.attempts == 0


def test_racing_verifications_create_exactly_one_session(
    test_engine, committed_account, settings
):
    code = _issue_code(test_engine, settings)

    outcomes = _race(test_engine, lambda session: _verify(session, code, settings))

    assert sorted(outcomes) == sorted([SESSION_CREATED, OtpFailureReason.CONSUMED])
    sessions, challenges = _account_state(test_engine)
    assert sessions == 1
    (challenge,) = challenges
    assert challenge.consumed_at is not None


# ---------------------------------------------------------------------------
# Issuance
# ---------------------------------------------------------------------------


def _outstanding(challenges: list[OtpChallenge]) -> list[OtpChallenge]:
    now = datetime.now(UTC)
    return [c for c in challenges if c.consumed_at is None and c.expires_at > now]


def test_second_issuance_waits_and_respects_the_cooldown(
    test_engine, committed_account, settings
):
    first, second = _run_while_holding_locks(
        test_engine,
        hold=lambda session: request_otp(session, PHONE, settings=settings),
        contend=lambda session: _request(session, settings),
    )

    assert first is not None
    assert second is None

    _, challenges = _account_state(test_engine)
    assert [c.id for c in challenges] == [first.challenge_id]
    assert [c.id for c in _outstanding(challenges)] == [first.challenge_id]


def test_racing_issuance_creates_exactly_one_challenge(
    test_engine, committed_account, settings
):
    outcomes = _race(test_engine, lambda session: _request(session, settings))

    issued = [outcome for outcome in outcomes if outcome is not None]
    assert len(issued) == 1
    assert outcomes.count(None) == 1

    _, challenges = _account_state(test_engine)
    assert [c.id for c in challenges] == [issued[0].challenge_id]
    assert len(_outstanding(challenges)) == 1
