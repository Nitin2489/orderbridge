"""Fixed-window rate limiting shared through PostgreSQL.

Why fixed windows: one row per key and window, updated with a single
INSERT ... ON CONFLICT DO UPDATE ... RETURNING. PostgreSQL makes that
statement atomic across connections, workers and instances, so concurrent
requests can never lose an increment or both see a count below the limit;
no explicit locks or read-modify-write cycles are needed. The known cost is
that a client can send up to twice the limit around a window boundary. For
the OTP endpoints that is acceptable: the per-number lockout and resend
cooldown bound guessing and SMS volume independently.

Keys are HMAC-SHA256(AUTH_SECRET_KEY, "rate-limit:v1:<rule>:<identifier>"),
so client IPs and phone numbers are not stored readably, and a database
reader cannot test guesses without the key.

Windows are aligned to the Unix epoch, so every worker agrees on the window
for a given time. A row is only read while its window is current; afterwards
it is dead weight and purge_expired() removes it.

The service never commits; the caller owns the transaction.
"""

import hashlib
import hmac
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import RateLimitBucket

# Rows are deleted only once they have been expired for this long, so a
# purge never races a request still counting in a just-ended window.
PURGE_GRACE = timedelta(minutes=5)
PURGE_BATCH_SIZE = 1000


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    hits: int
    retry_after_seconds: int  # 0 when allowed


def rate_limit_key(secret: str, rule_name: str, identifier: str) -> str:
    message = f"rate-limit:v1:{rule_name}:{identifier}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def window_bounds(now: datetime, window_seconds: int) -> tuple[datetime, datetime]:
    """(start, end) of the epoch-aligned window containing `now`."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware.")
    epoch_seconds = math.floor(now.timestamp())
    start_seconds = epoch_seconds - epoch_seconds % window_seconds
    start = datetime.fromtimestamp(start_seconds, UTC)
    return start, start + timedelta(seconds=window_seconds)


def hit(
    session: Session,
    rule: RateLimitRule,
    identifier: str,
    *,
    secret: str,
    now: datetime,
) -> RateLimitResult:
    """Count one request for `identifier` under `rule` and decide it.

    Every call counts, including refused ones, so a client that keeps
    sending stays limited until the window ends.
    """
    start, end = window_bounds(now, rule.window_seconds)
    statement = (
        insert(RateLimitBucket)
        .values(
            key_hash=rate_limit_key(secret, rule.name, identifier),
            window_start=start,
            expires_at=end,
            hits=1,
        )
        .on_conflict_do_update(
            index_elements=[RateLimitBucket.key_hash, RateLimitBucket.window_start],
            set_={"hits": RateLimitBucket.hits + 1},
        )
        .returning(RateLimitBucket.hits)
    )
    hits = session.execute(statement).scalar_one()

    if hits <= rule.limit:
        return RateLimitResult(allowed=True, hits=hits, retry_after_seconds=0)

    retry_after = max(1, math.ceil((end - now).total_seconds()))
    return RateLimitResult(allowed=False, hits=hits, retry_after_seconds=retry_after)


def purge_expired(
    session: Session, *, now: datetime, batch_size: int = PURGE_BATCH_SIZE
) -> int:
    """Delete up to batch_size counters whose window ended before now - grace.

    Bounded, so it can run inside a request. Returns the number deleted.

    Safe to run concurrently: the batch is chosen oldest first with FOR
    UPDATE SKIP LOCKED, so each purge locks only rows no other transaction
    holds and never waits for a lock. Concurrent purges take disjoint
    batches, and none can deadlock with another (or with a request counting
    in a current window, whose rows are never expired).
    """
    expired = (
        select(RateLimitBucket.key_hash, RateLimitBucket.window_start)
        .where(RateLimitBucket.expires_at <= now - PURGE_GRACE)
        .order_by(RateLimitBucket.expires_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    result = session.execute(
        delete(RateLimitBucket)
        .where(
            tuple_(RateLimitBucket.key_hash, RateLimitBucket.window_start).in_(expired)
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


def maybe_purge_expired(
    session: Session,
    *,
    now: datetime,
    probability: float,
    chance: Callable[[], float] = random.random,
) -> int:
    """Opportunistic cleanup: purge a batch with the given probability.

    With the default 1% of rate-limited requests each deleting up to 1,000
    rows, cleanup keeps pace with the counters those requests create (at
    most four rows per request) without a scheduler.
    """
    if probability <= 0 or chance() >= probability:
        return 0
    return purge_expired(session, now=now)
