"""PostgreSQL concurrency tests for the shared rate limiter.

Each participant uses its own connection and transaction, as separate
workers or instances would, and commits. Counting tests use a random
identifier, so they only ever touch their own rows, and delete them
afterwards. Cleanup tests commit their own expired rows; purging may also
remove expired counters left in the test database by earlier runs, which is
harmless, and the assertions allow for it.
"""

import threading
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import RateLimitBucket
from app.services import rate_limit
from app.services.rate_limit import PURGE_GRACE, RateLimitRule, rate_limit_key

SECRET = "rate-limit-concurrency-9f8e7d6c5b4a39281706f5e4d3c2b1a0"
RULE = RateLimitRule(name="test:concurrency", limit=5, window_seconds=3600)
PARTICIPANTS = 10
WAIT_SECONDS = 10.0


@pytest.fixture
def identifier(test_engine: Engine) -> Generator[str, None, None]:
    value = f"concurrency-{uuid.uuid4()}"
    key = rate_limit_key(SECRET, RULE.name, value)

    yield value

    with Session(test_engine) as session:
        session.execute(delete(RateLimitBucket).where(RateLimitBucket.key_hash == key))
        session.commit()


def _hit_and_commit(engine: Engine, identifier: str, now: datetime, barrier) -> bool:
    with Session(engine) as session:
        session.execute(text("SET LOCAL lock_timeout = '10s'"))
        barrier.wait()
        result = rate_limit.hit(session, RULE, identifier, secret=SECRET, now=now)
        session.commit()
        return result.allowed


def test_concurrent_requests_on_separate_connections_never_exceed_the_limit(
    test_engine, identifier
):
    now = datetime.now(UTC)
    barrier = threading.Barrier(PARTICIPANTS, timeout=WAIT_SECONDS)

    with ThreadPoolExecutor(max_workers=PARTICIPANTS) as pool:
        futures = [
            pool.submit(_hit_and_commit, test_engine, identifier, now, barrier)
            for _ in range(PARTICIPANTS)
        ]
        outcomes = [future.result(timeout=WAIT_SECONDS * 3) for future in futures]

    # Exactly `limit` requests were allowed, and no increment was lost.
    assert outcomes.count(True) == RULE.limit
    assert outcomes.count(False) == PARTICIPANTS - RULE.limit

    key = rate_limit_key(SECRET, RULE.name, identifier)
    with Session(test_engine) as session:
        hits = session.scalars(
            select(RateLimitBucket.hits).where(RateLimitBucket.key_hash == key)
        ).all()
    assert hits == [PARTICIPANTS]


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

EXPIRED_ROWS = 40


@pytest.fixture
def expired_keys(test_engine: Engine) -> Generator[list[str], None, None]:
    """EXPIRED_ROWS committed counters from a window three days ago."""
    long_ago = datetime.now(UTC) - timedelta(days=3)
    identifiers = [f"cleanup-{uuid.uuid4()}" for _ in range(EXPIRED_ROWS)]
    keys = [rate_limit_key(SECRET, RULE.name, value) for value in identifiers]

    with Session(test_engine) as session:
        for value in identifiers:
            rate_limit.hit(session, RULE, value, secret=SECRET, now=long_ago)
        session.commit()

    yield keys

    with Session(test_engine) as session:
        session.execute(
            delete(RateLimitBucket).where(RateLimitBucket.key_hash.in_(keys))
        )
        session.commit()


def _remaining(engine: Engine, keys: list[str]) -> set[str]:
    with Session(engine) as session:
        return set(
            session.scalars(
                select(RateLimitBucket.key_hash).where(
                    RateLimitBucket.key_hash.in_(keys)
                )
            )
        )


def _expired_count(engine: Engine, now: datetime) -> int:
    with Session(engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(RateLimitBucket)
            .where(RateLimitBucket.expires_at <= now - PURGE_GRACE)
        )


def test_cleanup_skips_rows_locked_by_another_transaction(test_engine, expired_keys):
    locked, unlocked = expired_keys[:10], expired_keys[10:]
    now = datetime.now(UTC)

    holder = Session(test_engine)
    try:
        holder.execute(
            select(RateLimitBucket)
            .where(RateLimitBucket.key_hash.in_(locked))
            .with_for_update()
        ).all()

        with Session(test_engine) as purger:
            # A purge that waited for the holder's locks would fail here
            # with a lock timeout instead of returning.
            purger.execute(text("SET LOCAL lock_timeout = '1s'"))
            deleted = rate_limit.purge_expired(purger, now=now)
            purger.commit()
    finally:
        holder.rollback()
        holder.close()

    assert deleted >= len(unlocked)
    # Every unlocked row was purged; every locked row was skipped.
    assert _remaining(test_engine, expired_keys) == set(locked)


def test_concurrent_cleanup_workers_neither_wait_nor_deadlock(
    test_engine, expired_keys
):
    now = datetime.now(UTC)
    expected = _expired_count(test_engine, now)
    workers = 4
    barrier = threading.Barrier(workers, timeout=WAIT_SECONDS)

    def worker() -> int:
        """Purge small batches, each in its own transaction, until none is left."""
        total = 0
        with Session(test_engine) as session:
            barrier.wait()
            while True:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                deleted = rate_limit.purge_expired(session, now=now, batch_size=5)
                session.commit()
                if deleted == 0:
                    return total
                total += deleted

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker) for _ in range(workers)]
        # Any deadlock or lock timeout would surface here as an exception.
        deleted = [future.result(timeout=WAIT_SECONDS * 3) for future in futures]

    # Each expired row was deleted exactly once, and all of ours are gone.
    assert sum(deleted) == expected
    assert _remaining(test_engine, expired_keys) == set()
