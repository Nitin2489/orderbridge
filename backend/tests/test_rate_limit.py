"""Tests for OTP rate limiting (app/services/rate_limit.py,
app/api/rate_limits.py and their use in /api/v1/auth).

Runs against the test database through db_session, so counters are rolled
back after every test. Cross-connection behaviour is in
test_rate_limit_concurrency.py.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.rate_limits import (
    TOO_MANY_REQUESTS,
    enforce_otp_rate_limits,
    otp_rate_limit_rules,
)
from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.models import OtpChallenge, RateLimitBucket, Retailer, User, UserRole
from app.services import auth_service, rate_limit
from app.services.otp_delivery import InMemoryOtpDelivery
from app.services.rate_limit import (
    PURGE_BATCH_SIZE,
    PURGE_GRACE,
    RateLimitRule,
    maybe_purge_expired,
    purge_expired,
    rate_limit_key,
    window_bounds,
)
from seed import seed_all

SECRET = "rate-limit-test-9f8e7d6c5b4a39281706f5e4d3c2b1a0"
AUTH = "/api/v1/auth"
TRUSTED_ORIGIN = "http://localhost:3000"

SHARMA_PHONE = "+919000000001"
GUPTA_PHONE = "+919000000002"
UNKNOWN_PHONE = "+919999999999"

CLIENT_A = "203.0.113.10"
CLIENT_B = "203.0.113.20"
RENDER_PROXY = "10.0.0.5"

# 10:07:30 UTC: inside the 10:00-10:15 window of a 15-minute rule.
NOW = datetime(2026, 11, 7, 10, 7, 30, tzinfo=UTC)
RULE = RateLimitRule(name="test:rule", limit=3, window_seconds=15 * 60)


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "testing",
        "database_url": "postgresql+psycopg://unused:unused@localhost:5432/unused",
        "auth_secret_key": SECRET,
        "auth_debug_otp": False,
        "auth_session_cookie_secure": False,
        "frontend_origins": [TRUSTED_ORIGIN],
        "rate_limit_cleanup_probability": 0,
    }
    values.update(overrides)
    return Settings(**values)


def bucket_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(RateLimitBucket))


def hit(db_session, identifier="client", *, rule=RULE, now=NOW):
    return rate_limit.hit(db_session, rule, identifier, secret=SECRET, now=now)


# ---------------------------------------------------------------------------
# Service: counting, windows, keys
# ---------------------------------------------------------------------------


def test_requests_up_to_the_limit_are_allowed_then_refused(db_session):
    results = [hit(db_session) for _ in range(5)]

    assert [r.allowed for r in results] == [True, True, True, False, False]
    assert [r.hits for r in results] == [1, 2, 3, 4, 5]
    assert results[0].retry_after_seconds == 0
    # Refused at 10:07:30; the window ends at 10:15:00.
    assert results[3].retry_after_seconds == 450


def test_identifiers_and_rules_are_counted_separately(db_session):
    for _ in range(3):
        hit(db_session, "a")

    assert hit(db_session, "a").allowed is False
    assert hit(db_session, "b").allowed is True
    other_rule = RateLimitRule(name="test:other", limit=3, window_seconds=900)
    assert hit(db_session, "a", rule=other_rule).allowed is True


def test_a_new_window_starts_a_new_count(db_session):
    for _ in range(4):
        hit(db_session)
    assert hit(db_session).allowed is False

    _, window_end = window_bounds(NOW, RULE.window_seconds)
    recovered = hit(db_session, now=window_end)

    assert recovered.allowed is True
    assert recovered.hits == 1


def test_windows_are_aligned_to_the_epoch():
    start, end = window_bounds(NOW, 15 * 60)

    assert start == datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 7, 10, 15, tzinfo=UTC)
    with pytest.raises(ValueError):
        window_bounds(NOW.replace(tzinfo=None), 900)


def test_keys_are_hmacs_not_raw_identifiers(db_session):
    hit(db_session, CLIENT_A)
    hit(db_session, SHARMA_PHONE)

    stored = set(db_session.scalars(select(RateLimitBucket.key_hash)))
    assert stored == {
        rate_limit_key(SECRET, RULE.name, CLIENT_A),
        rate_limit_key(SECRET, RULE.name, SHARMA_PHONE),
    }
    for key in stored:
        assert len(key) == 64
        assert CLIENT_A not in key
        assert SHARMA_PHONE.lstrip("+") not in key
    # A different secret gives different keys.
    assert rate_limit_key("another-secret", RULE.name, CLIENT_A) not in stored


def test_purge_removes_only_counters_expired_beyond_the_grace(db_session):
    _, end = window_bounds(NOW, RULE.window_seconds)
    hit(db_session, "old", now=NOW)
    hit(db_session, "current", now=end)  # next window

    assert purge_expired(db_session, now=end) == 0  # inside the grace period
    assert purge_expired(db_session, now=end + PURGE_GRACE) == 1
    assert bucket_count(db_session) == 1
    assert hit(db_session, "current", now=end).hits == 2


def test_opportunistic_purge_follows_its_probability(db_session):
    hit(db_session, "old", now=NOW)
    later = NOW + timedelta(days=1)

    assert maybe_purge_expired(db_session, now=later, probability=0) == 0
    assert (
        maybe_purge_expired(db_session, now=later, probability=0.5, chance=lambda: 0.9)
        == 0
    )
    assert (
        maybe_purge_expired(db_session, now=later, probability=0.5, chance=lambda: 0.1)
        == 1
    )


def test_purge_deletes_the_oldest_expired_counters_in_bounded_batches(db_session):
    hourly = RateLimitRule(name="test:hourly", limit=10, window_seconds=3600)
    # Five expired counters, one per hourly window ending 06:00 ... 10:00.
    for hours_ago in range(5, 0, -1):
        hit(
            db_session,
            f"id-{hours_ago}",
            rule=hourly,
            now=NOW - timedelta(hours=hours_ago),
        )
    later = NOW + timedelta(days=1)

    def remaining_ends() -> list[int]:
        ends = db_session.scalars(
            select(RateLimitBucket.expires_at).order_by(RateLimitBucket.expires_at)
        )
        return [end.astimezone(UTC).hour for end in ends]

    assert purge_expired(db_session, now=later, batch_size=2) == 2
    assert remaining_ends() == [8, 9, 10]  # the two oldest went first
    assert purge_expired(db_session, now=later, batch_size=2) == 2
    assert remaining_ends() == [10]
    assert purge_expired(db_session, now=later, batch_size=2) == 1
    assert purge_expired(db_session, now=later, batch_size=2) == 0


def test_cleanup_batch_size_and_grace_are_unchanged():
    assert PURGE_BATCH_SIZE == 1000
    assert PURGE_GRACE == timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Enforcement helper (fixed clock)
# ---------------------------------------------------------------------------


def fake_request(peer: str, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_enforcement_recovers_when_the_window_ends(db_session):
    settings = make_settings(
        rate_limit_otp_request_phone_limit=1,
        rate_limit_otp_request_phone_window_seconds=3600,
    )
    request = fake_request(CLIENT_A)

    def enforce(now):
        enforce_otp_rate_limits(
            db_session,
            request,
            settings,
            action="request",
            phone_number=SHARMA_PHONE,
            now=now,
        )

    enforce(NOW)
    with pytest.raises(HTTPException) as refused:
        enforce(NOW + timedelta(minutes=1))

    assert refused.value.status_code == 429
    assert refused.value.detail == TOO_MANY_REQUESTS
    # 10:08:30 -> the hour window ends at 11:00:00.
    assert refused.value.headers == {"Retry-After": str(51 * 60 + 30)}

    enforce(datetime(2026, 11, 7, 11, 0, tzinfo=UTC))


def test_ip_refusal_does_not_use_up_the_number(db_session):
    settings = make_settings(
        rate_limit_otp_request_ip_limit=1, rate_limit_otp_request_phone_limit=2
    )

    def enforce(peer):
        enforce_otp_rate_limits(
            db_session,
            fake_request(peer),
            settings,
            action="request",
            phone_number=SHARMA_PHONE,
            now=NOW,
        )

    enforce(CLIENT_A)
    for _ in range(5):
        with pytest.raises(HTTPException):
            enforce(CLIENT_A)  # refused by the IP rule only

    # The number has been counted once, so another client may still use it.
    enforce(CLIENT_B)


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(db_session):
    seed_all(db_session)


@pytest.fixture
def make_client(db_session, seeded):
    clients = []

    def build(settings: Settings, *, peer: str = CLIENT_A) -> TestClient:
        app = create_app(settings, otp_delivery=InMemoryOtpDelivery())

        def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(
            app,
            base_url="http://testserver",
            headers={"Origin": TRUSTED_ORIGIN},
            client=(peer, 50000),
        )
        clients.append(client)
        return client

    yield build

    for client in clients:
        client.close()


def request_otp(client: TestClient, phone: str, **kwargs):
    return client.post(f"{AUTH}/request-otp", json={"phone_number": phone}, **kwargs)


def verify_otp(client: TestClient, phone: str, code: str = "000000", **kwargs):
    return client.post(
        f"{AUTH}/verify-otp", json={"phone_number": phone, "code": code}, **kwargs
    )


def assert_rate_limited(response) -> None:
    assert response.status_code == 429
    assert response.json() == {"detail": TOO_MANY_REQUESTS}
    retry_after = int(response.headers["Retry-After"])
    assert 1 <= retry_after <= 3600


def test_request_otp_is_limited_per_client_ip(make_client):
    settings = make_settings(rate_limit_otp_request_ip_limit=2)
    client_a = make_client(settings, peer=CLIENT_A)
    client_b = make_client(settings, peer=CLIENT_B)

    # Different numbers, one IP.
    assert request_otp(client_a, SHARMA_PHONE).status_code == 200
    assert request_otp(client_a, GUPTA_PHONE).status_code == 200
    assert_rate_limited(request_otp(client_a, UNKNOWN_PHONE))

    # Another IP is unaffected.
    assert request_otp(client_b, UNKNOWN_PHONE).status_code == 200


def test_request_otp_is_limited_per_number_across_ips(make_client):
    settings = make_settings(rate_limit_otp_request_phone_limit=2)
    client_a = make_client(settings, peer=CLIENT_A)
    client_b = make_client(settings, peer=CLIENT_B)

    assert request_otp(client_a, SHARMA_PHONE).status_code == 200
    assert request_otp(client_b, SHARMA_PHONE).status_code == 200
    assert_rate_limited(request_otp(client_a, SHARMA_PHONE))
    assert_rate_limited(request_otp(client_b, SHARMA_PHONE))

    # Another number is unaffected.
    assert request_otp(client_a, GUPTA_PHONE).status_code == 200


def test_number_limit_uses_the_normalised_number(make_client):
    settings = make_settings(rate_limit_otp_request_phone_limit=1)
    client = make_client(settings)

    assert request_otp(client, "90000 00001").status_code == 200
    assert_rate_limited(request_otp(client, "+91 90000-00001"))


def test_unknown_and_known_numbers_are_limited_identically(make_client):
    settings = make_settings(rate_limit_otp_request_phone_limit=1)
    client = make_client(settings)

    request_otp(client, SHARMA_PHONE)
    request_otp(client, UNKNOWN_PHONE)
    known = request_otp(client, SHARMA_PHONE)
    unknown = request_otp(client, UNKNOWN_PHONE)

    assert known.status_code == unknown.status_code == 429
    assert known.json() == unknown.json()
    # Same window: Retry-After differs at most by the second that passed
    # between the two requests.
    retry_known = int(known.headers["Retry-After"])
    retry_unknown = int(unknown.headers["Retry-After"])
    assert abs(retry_known - retry_unknown) <= 1


def test_refused_request_issues_no_code(make_client, db_session):
    settings = make_settings(rate_limit_otp_request_phone_limit=1)
    client = make_client(settings)

    request_otp(client, SHARMA_PHONE)
    assert_rate_limited(request_otp(client, SHARMA_PHONE))

    assert client.app.state.otp_delivery.delivered_count(SHARMA_PHONE) == 1
    challenges = db_session.scalars(
        select(OtpChallenge).where(OtpChallenge.phone_number == SHARMA_PHONE)
    ).all()
    assert len(challenges) == 1


def test_verify_otp_is_limited_per_client_ip(make_client):
    settings = make_settings(rate_limit_otp_verify_ip_limit=2)
    client_a = make_client(settings, peer=CLIENT_A)
    client_b = make_client(settings, peer=CLIENT_B)

    assert verify_otp(client_a, SHARMA_PHONE).status_code == 401
    assert verify_otp(client_a, GUPTA_PHONE).status_code == 401
    assert_rate_limited(verify_otp(client_a, UNKNOWN_PHONE))
    assert verify_otp(client_b, SHARMA_PHONE).status_code == 401


def test_verify_otp_is_limited_per_number_without_counting_attempts(
    make_client, db_session
):
    settings = make_settings(rate_limit_otp_verify_phone_limit=2)
    client_a = make_client(settings, peer=CLIENT_A)
    client_b = make_client(settings, peer=CLIENT_B)
    request_otp(client_a, SHARMA_PHONE)
    code = client_a.app.state.otp_delivery.latest_code(SHARMA_PHONE)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    assert verify_otp(client_a, SHARMA_PHONE, wrong).status_code == 401
    assert verify_otp(client_b, SHARMA_PHONE, wrong).status_code == 401
    # Refused before verification: even the right code is not tried.
    assert_rate_limited(verify_otp(client_a, SHARMA_PHONE, code))

    challenge = db_session.scalar(
        select(OtpChallenge).where(OtpChallenge.phone_number == SHARMA_PHONE)
    )
    assert challenge.attempts == 2
    assert challenge.consumed_at is None

    # Other numbers are unaffected.
    assert verify_otp(client_a, GUPTA_PHONE).status_code == 401


def test_rate_limit_runs_after_the_origin_check(make_client):
    settings = make_settings(rate_limit_otp_verify_ip_limit=1)
    client = make_client(settings)

    for _ in range(3):
        response = verify_otp(
            client, SHARMA_PHONE, headers={"Origin": "https://attacker.example"}
        )
        assert response.status_code == 403

    # Cross-site attempts did not use up the allowance.
    assert verify_otp(client, SHARMA_PHONE).status_code == 401


# ---------------------------------------------------------------------------
# Cleanup runs after the decision and can never fail the request
# ---------------------------------------------------------------------------


def test_cleanup_runs_in_its_own_transaction_after_the_counts(make_client, db_session):
    # An old expired counter from some earlier window.
    hit(db_session, "stale", now=datetime(2026, 1, 1, tzinfo=UTC))
    settings = make_settings(rate_limit_cleanup_probability=1)
    client = make_client(settings)

    assert request_otp(client, SHARMA_PHONE).status_code == 200

    # The stale counter is gone; this request's two counters remain.
    hits = db_session.scalars(select(RateLimitBucket.hits)).all()
    assert hits == [1, 1]


def _failing_cleanup(session, **kwargs):
    raise RuntimeError("cleanup exploded")


def _database_error_cleanup(session, **kwargs):
    # A real database error: it aborts the cleanup transaction.
    session.execute(text("SELECT 1 / 0"))


@pytest.mark.parametrize("cleanup", [_failing_cleanup, _database_error_cleanup])
def test_cleanup_failure_does_not_break_the_otp_request(
    make_client, db_session, monkeypatch, caplog, cleanup
):
    monkeypatch.setattr(rate_limit, "maybe_purge_expired", cleanup)
    caplog.set_level(logging.WARNING, logger="app.api.rate_limits")
    settings = make_settings(rate_limit_otp_request_phone_limit=1)
    client = make_client(settings, peer=CLIENT_A)

    response = request_otp(client, SHARMA_PHONE)

    # The request completed normally: code issued and delivered.
    assert response.status_code == 200
    assert client.app.state.otp_delivery.delivered_count(SHARMA_PHONE) == 1
    challenges = db_session.scalars(
        select(OtpChallenge).where(OtpChallenge.phone_number == SHARMA_PHONE)
    ).all()
    assert len(challenges) == 1

    # The counters were committed before the cleanup and survived it.
    assert sorted(db_session.scalars(select(RateLimitBucket.hits)).all()) == [1, 1]
    assert_rate_limited(request_otp(client, SHARMA_PHONE))

    # Logged by exception type only: no number, IP, key or code.
    messages = [record.getMessage() for record in caplog.records]
    assert any("Rate-limit cleanup failed" in message for message in messages)
    code = client.app.state.otp_delivery.latest_code(SHARMA_PHONE)
    for message in messages:
        assert SHARMA_PHONE not in message
        assert CLIENT_A not in message
        assert code not in message
        assert "exploded" not in message


def test_cleanup_failure_does_not_break_verification(make_client, monkeypatch):
    monkeypatch.setattr(rate_limit, "maybe_purge_expired", _database_error_cleanup)
    client = make_client(make_settings())

    request_otp(client, SHARMA_PHONE)
    code = client.app.state.otp_delivery.latest_code(SHARMA_PHONE)

    assert verify_otp(client, SHARMA_PHONE, code).status_code == 200


# Reserved for the plain-session test; not used by the seed data or any other
# test. Its rows are committed, so they are deleted before and after.
PLAIN_PHONE = "+919000000902"
PLAIN_CLIENT = "203.0.113.77"


def _delete_plain_session_rows(engine: Engine, settings: Settings) -> None:
    ip_rule, phone_rule = otp_rate_limit_rules(settings, "request")
    keys = [
        rate_limit_key(settings.auth_secret, ip_rule.name, PLAIN_CLIENT),
        rate_limit_key(settings.auth_secret, phone_rule.name, PLAIN_PHONE),
    ]
    user_ids = select(User.id).where(User.phone_number == PLAIN_PHONE)

    with Session(engine) as session:
        for statement in (
            delete(RateLimitBucket).where(RateLimitBucket.key_hash.in_(keys)),
            delete(OtpChallenge).where(OtpChallenge.phone_number == PLAIN_PHONE),
            delete(Retailer).where(Retailer.user_id.in_(user_ids)),
            delete(User).where(User.phone_number == PLAIN_PHONE),
        ):
            session.execute(statement.execution_options(synchronize_session=False))
        session.commit()


@pytest.fixture
def plain_account(test_engine: Engine):
    """A committed, active retailer account under PLAIN_PHONE."""
    settings = make_settings()
    _delete_plain_session_rows(test_engine, settings)

    with Session(test_engine) as session:
        user = User(
            phone_number=PLAIN_PHONE,
            full_name="Plain Session Test Owner",
            role=UserRole.RETAILER,
            is_active=True,
        )
        session.add(user)
        session.flush()
        session.add(
            Retailer(
                user_id=user.id,
                business_name="Plain Session Test Store",
                owner_name="Plain Session Test Owner",
                address="Test Address, Jabalpur",
                credit_limit=Decimal("0.00"),
                outstanding_balance=Decimal("0.00"),
                is_active=True,
            )
        )
        session.commit()

    try:
        yield settings
    finally:
        _delete_plain_session_rows(test_engine, settings)


def test_cleanup_failure_in_a_plain_session_leaves_it_usable(
    test_engine, plain_account, monkeypatch, caplog
):
    """Same scenario as above, without the db_session fixture's SAVEPOINTs.

    Here the counter commit, the failed cleanup and its rollback are all
    top-level transactions, as in production.
    """
    settings = plain_account
    monkeypatch.setattr(rate_limit, "maybe_purge_expired", _database_error_cleanup)
    caplog.set_level(logging.WARNING, logger="app.api.rate_limits")

    with Session(test_engine) as session:
        enforce_otp_rate_limits(
            session,
            fake_request(PLAIN_CLIENT),
            settings,
            action="request",
            phone_number=PLAIN_PHONE,
        )
        # The cleanup really failed, and its rollback ended the transaction.
        assert any(
            "Rate-limit cleanup failed" in record.getMessage()
            for record in caplog.records
        )
        assert not session.in_transaction()

        # The endpoint's authentication work on the same session still works.
        issued = auth_service.request_otp(session, PLAIN_PHONE, settings=settings)
        session.commit()

    assert issued is not None

    # Seen from a separate connection: the counters and the challenge were
    # both committed.
    ip_rule, phone_rule = otp_rate_limit_rules(settings, "request")
    keys = [
        rate_limit_key(settings.auth_secret, ip_rule.name, PLAIN_CLIENT),
        rate_limit_key(settings.auth_secret, phone_rule.name, PLAIN_PHONE),
    ]
    with Session(test_engine) as reader:
        hits = reader.scalars(
            select(RateLimitBucket.hits).where(RateLimitBucket.key_hash.in_(keys))
        ).all()
        challenge_ids = reader.scalars(
            select(OtpChallenge.id).where(OtpChallenge.phone_number == PLAIN_PHONE)
        ).all()

    assert sorted(hits) == [1, 1]
    assert challenge_ids == [issued.challenge_id]


# ---------------------------------------------------------------------------
# Forwarded headers
# ---------------------------------------------------------------------------


def test_spoofed_forwarded_headers_cannot_bypass_the_ip_limit(make_client):
    # No trusted proxy is configured: X-Forwarded-For is ignored.
    settings = make_settings(rate_limit_otp_request_ip_limit=2)
    client = make_client(settings, peer=CLIENT_A)

    responses = [
        request_otp(
            client,
            UNKNOWN_PHONE,
            headers={
                "X-Forwarded-For": f"198.51.100.{n}",
                "X-Real-IP": f"198.51.100.{n}",
            },
        )
        for n in range(1, 5)
    ]

    assert [r.status_code for r in responses] == [200, 200, 429, 429]


def test_spoofed_prefix_behind_a_trusted_proxy_cannot_bypass_the_ip_limit(
    make_client,
):
    settings = make_settings(
        rate_limit_otp_request_ip_limit=2, trusted_proxy_cidrs=["10.0.0.0/8"]
    )
    proxy = make_client(settings, peer=RENDER_PROXY)

    def via_proxy(spoofed: str, real: str):
        return request_otp(
            proxy, UNKNOWN_PHONE, headers={"X-Forwarded-For": f"{spoofed}, {real}"}
        )

    statuses = [via_proxy(f"198.51.100.{n}", CLIENT_A).status_code for n in range(4)]
    assert statuses == [200, 200, 429, 429]

    # A different real client behind the same proxy has its own allowance.
    assert via_proxy("198.51.100.99", CLIENT_B).status_code == 200


def test_untrusted_peer_cannot_claim_to_be_a_proxy(make_client):
    settings = make_settings(
        rate_limit_otp_request_ip_limit=1, trusted_proxy_cidrs=["10.0.0.0/8"]
    )
    client = make_client(settings, peer=CLIENT_A)

    first = request_otp(client, UNKNOWN_PHONE, headers={"X-Forwarded-For": "10.0.0.9"})
    second = request_otp(
        client, UNKNOWN_PHONE, headers={"X-Forwarded-For": "198.51.100.7"}
    )

    assert first.status_code == 200
    assert second.status_code == 429


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_default_limits():
    settings = make_settings()

    assert (
        settings.rate_limit_otp_request_ip_limit,
        settings.rate_limit_otp_request_ip_window_seconds,
        settings.rate_limit_otp_request_phone_limit,
        settings.rate_limit_otp_request_phone_window_seconds,
        settings.rate_limit_otp_verify_ip_limit,
        settings.rate_limit_otp_verify_ip_window_seconds,
        settings.rate_limit_otp_verify_phone_limit,
        settings.rate_limit_otp_verify_phone_window_seconds,
    ) == (30, 900, 5, 3600, 60, 900, 15, 3600)


@pytest.mark.parametrize(
    "overrides",
    [
        {"rate_limit_otp_request_ip_limit": 0},
        {"rate_limit_otp_verify_phone_limit": -1},
        {"rate_limit_otp_request_ip_window_seconds": 0},
        {"rate_limit_otp_verify_ip_window_seconds": 24 * 60 * 60 + 1},
        {"rate_limit_cleanup_probability": 1.5},
        {"rate_limit_cleanup_probability": -0.1},
    ],
)
def test_invalid_rate_limit_settings_are_rejected(overrides):
    with pytest.raises(ValueError):
        make_settings(**overrides)
