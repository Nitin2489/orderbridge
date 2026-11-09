"""Reviewer access to the demonstration accounts' one-time codes.

The demo numbers are public, so their codes must reach only a browser that
has unlocked reviewer access with the private AUTH_DEMO_ACCESS_KEY. These
tests cover configuration, the unlock endpoint, the signed cookie, and
request-otp / verify-otp with and without access.

The access keys below are test fixtures only, never deployment values.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.database import get_db
from app.core.demo_accounts import (
    DEMO_ACCOUNT_NUMBERS,
    DEMO_ORDER_DESK_NUMBER,
    DEMO_RETAILER_NUMBERS,
)
from app.main import create_app
from app.models import OtpChallenge, User, UserRole
from app.services import demo_access
from app.services.otp_delivery import InMemoryOtpDelivery
from seed import seed_all

AUTH = "/api/v1/auth"
ORIGIN = "https://orderbridge.example"
SECRET = "demo-otp-test-secret-0123456789abcdefgh"
ACCESS_KEY = "Qm7vT2xLp9RkW4nZc8HsJ3dYf6GbN1aE5uVo0iKt"
OTHER_ACCESS_KEY = "Zr4Pq8Wn2Lx6Tc0Vb5Hm9Jd3Gs7Fk1Ya4Ue8Ni2"
ALL_DEMO_NUMBERS = ["9000000001", "9000000002", "9000000003", "9000000010"]


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "production",
        "database_url": "postgresql+psycopg://unused:unused@localhost:5432/unused",
        "auth_secret_key": SECRET,
        "auth_session_cookie_secure": True,
        "auth_debug_otp": False,
        "auth_dev_otp_outbox_path": None,
        "frontend_origins": [ORIGIN],
    }
    values.update(overrides)
    return Settings(**values)


def demo_settings(**overrides) -> Settings:
    values = {
        "auth_demo_otp_numbers": ["9000000001"],
        "auth_demo_access_key": ACCESS_KEY,
    }
    values.update(overrides)
    return make_settings(**values)


def make_app(db_session, settings: Settings):
    seed_all(db_session)
    db_session.commit()
    app = create_app(settings, otp_delivery=InMemoryOtpDelivery())

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return app


def browser(app) -> TestClient:
    """A separate browser: its own cookie jar, same trusted origin."""
    return TestClient(app, base_url="https://testserver", headers={"Origin": ORIGIN})


def request_code(client: TestClient, phone: str):
    return client.post(f"{AUTH}/request-otp", json={"phone_number": phone})


def verify(client: TestClient, phone: str, code: str):
    return client.post(f"{AUTH}/verify-otp", json={"phone_number": phone, "code": code})


def unlock(client: TestClient, key: str = ACCESS_KEY):
    return client.post(f"{AUTH}/demo-access", json={"access_key": key})


def challenge_count(db_session, phone: str) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(OtpChallenge)
        .where(OtpChallenge.phone_number == phone)
    )


# --- The pre-provisioned accounts ------------------------------------------


def test_demo_numbers_match_the_seeded_synthetic_accounts(db_session):
    seed_all(db_session)
    db_session.flush()

    users = {
        user.phone_number: user
        for user in db_session.scalars(
            select(User).where(User.phone_number.in_(sorted(DEMO_ACCOUNT_NUMBERS)))
        )
    }
    assert set(users) == DEMO_ACCOUNT_NUMBERS
    for phone in DEMO_RETAILER_NUMBERS:
        assert users[phone].role == UserRole.RETAILER
    assert users[DEMO_ORDER_DESK_NUMBER].role == UserRole.ORDER_DESK


# --- Configuration ----------------------------------------------------------


def test_demo_access_is_off_by_default():
    settings = make_settings()
    assert settings.auth_demo_otp_numbers == []
    assert settings.auth_demo_access_key is None
    assert not demo_access.demo_access_enabled(settings)


def test_demo_numbers_are_normalised_and_deduplicated():
    settings = demo_settings(
        auth_demo_otp_numbers=["9000000001", "+91 90000 00001", "09000000010"]
    )
    assert settings.auth_demo_otp_numbers == ["+919000000001", "+919000000010"]


@pytest.mark.parametrize(
    "number",
    [
        "12345",  # Not a phone number.
        "9876543210",  # A valid number, but not a demo account.
        "9000000004",  # Next to the demo numbers, but not one of them.
    ],
)
def test_only_the_pre_provisioned_accounts_can_be_listed(number):
    with pytest.raises(ValidationError, match="AUTH_DEMO_OTP_NUMBERS"):
        demo_settings(auth_demo_otp_numbers=[number])


def test_all_four_demo_accounts_can_be_listed():
    settings = demo_settings(auth_demo_otp_numbers=ALL_DEMO_NUMBERS)
    assert set(settings.auth_demo_otp_numbers) == DEMO_ACCOUNT_NUMBERS


@pytest.mark.parametrize("app_env", ["development", "testing", "staging", "production"])
@pytest.mark.parametrize("key", [None, "", "   "])
def test_demo_numbers_require_an_access_key_in_every_environment(app_env, key):
    overrides = {"app_env": app_env, "auth_demo_access_key": key}
    if app_env == "development":
        overrides["auth_session_cookie_secure"] = False
    with pytest.raises(ValidationError, match="AUTH_DEMO_ACCESS_KEY must be set"):
        demo_settings(**overrides)


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("Qm7vT2xLp9RkW4nZc8HsJ3dY", "at least 32"),
        ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "placeholder"),
        ("change-me-Qm7vT2xLp9RkW4nZc8HsJ3dYf6Gb", "placeholder"),
        (SECRET, "different from AUTH_SECRET_KEY"),
    ],
)
def test_weak_or_reused_access_keys_are_refused(key, message):
    with pytest.raises(ValidationError, match=message) as error:
        demo_settings(auth_demo_access_key=key)
    # The rejected value never appears in the error.
    assert key not in str(error.value)


def test_a_key_without_demo_numbers_enables_nothing():
    settings = make_settings(auth_demo_access_key=ACCESS_KEY)
    assert not demo_access.demo_access_enabled(settings)


def test_the_key_is_hidden_in_the_settings_repr():
    assert ACCESS_KEY not in repr(demo_settings())


# --- Signed access tokens ---------------------------------------------------


def test_a_fresh_token_is_valid_and_contains_neither_secret():
    settings = demo_settings()
    token = demo_access.issue_access_token(settings)

    assert demo_access.access_token_is_valid(token, settings)
    assert ACCESS_KEY not in token
    assert SECRET not in token


def test_tokens_expire_after_eight_hours():
    settings = demo_settings()
    issued_at = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
    token = demo_access.issue_access_token(settings, now=issued_at)

    almost = issued_at + timedelta(hours=8) - timedelta(seconds=1)
    assert demo_access.access_token_is_valid(token, settings, now=almost)
    expired = issued_at + timedelta(hours=8)
    assert not demo_access.access_token_is_valid(token, settings, now=expired)


def test_rotating_either_key_invalidates_existing_tokens():
    token = demo_access.issue_access_token(demo_settings())

    assert not demo_access.access_token_is_valid(
        token, demo_settings(auth_demo_access_key=OTHER_ACCESS_KEY)
    )
    assert not demo_access.access_token_is_valid(
        token,
        demo_settings(auth_secret_key="another-test-secret-9876543210zyxwvuts"),
    )


def test_tokens_are_refused_once_demo_access_is_switched_off():
    token = demo_access.issue_access_token(demo_settings())
    assert not demo_access.access_token_is_valid(token, make_settings())


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "garbage",
        "v2.9999999999." + "0" * 64,
        "v1.notanumber." + "0" * 64,
        "v1.1." + "0" * 64,  # Expired long ago.
    ],
)
def test_missing_and_malformed_tokens_are_refused(token):
    assert not demo_access.access_token_is_valid(token, demo_settings())


def test_forged_and_far_future_tokens_are_refused():
    settings = demo_settings()
    now = datetime.now(UTC)
    token = demo_access.issue_access_token(settings, now=now)
    version, expires, signature = token.split(".")

    # A later expiry with the old signature.
    later = str(int(expires) + 60)
    assert not demo_access.access_token_is_valid(
        f"{version}.{later}.{signature}", settings
    )
    # A tampered signature.
    flipped = ("1" if signature[0] == "0" else "0") + signature[1:]
    assert not demo_access.access_token_is_valid(
        f"{version}.{expires}.{flipped}", settings
    )
    # Correctly signed, but beyond anything the server issues.
    far_future = demo_access.issue_access_token(settings, now=now + timedelta(days=30))
    assert not demo_access.access_token_is_valid(far_future, settings, now=now)


def test_key_comparison_handles_bad_input():
    settings = demo_settings()
    assert demo_access.access_key_matches(ACCESS_KEY, settings)
    assert not demo_access.access_key_matches(OTHER_ACCESS_KEY, settings)
    assert not demo_access.access_key_matches("", settings)
    assert not demo_access.access_key_matches(ACCESS_KEY + "x", settings)
    assert not demo_access.access_key_matches("x" * 10_000, settings)
    # Without demo numbers there is nothing to unlock.
    key_only = make_settings(auth_demo_access_key=ACCESS_KEY)
    assert not demo_access.access_key_matches(ACCESS_KEY, key_only)


# --- Demo access switched off: ordinary sign-in is unchanged ----------------


def test_without_demo_access_codes_are_never_returned_and_sign_in_works(db_session):
    app = make_app(db_session, make_settings())
    with browser(app) as client:
        response = request_code(client, "9000000001")
        assert response.status_code == 200
        assert "demo_code" not in response.json()

        # The code went to the delivery channel as usual and signs in.
        code = app.state.otp_delivery.latest_code("+919000000001")
        assert verify(client, "9000000001", code).status_code == 200

        status = client.get(f"{AUTH}/demo-access")
        assert status.json() == {"enabled": False, "active": False}
        assert unlock(client).status_code == 404


# --- Unlocking reviewer access ----------------------------------------------


def test_unlock_sets_a_restricted_cookie_and_returns_nothing_secret(db_session):
    app = make_app(db_session, demo_settings())
    with browser(app) as client:
        assert client.get(f"{AUTH}/demo-access").json() == {
            "enabled": True,
            "active": False,
        }

        response = unlock(client)
        assert response.status_code == 204
        assert response.content == b""
        assert response.headers["cache-control"] == "no-store"

        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{demo_access.ACCESS_COOKIE_NAME}=")
        lowered = cookie.lower()
        assert "httponly" in lowered
        assert "secure" in lowered
        assert "samesite=lax" in lowered
        assert "path=/api/v1/auth" in lowered
        assert "max-age=28800" in lowered
        assert ACCESS_KEY not in cookie

        status = client.get(f"{AUTH}/demo-access")
        assert status.json() == {"enabled": True, "active": True}
        assert status.headers["cache-control"] == "no-store"
        assert ACCESS_KEY not in status.text


def test_the_cookie_is_not_secure_only_for_local_http(db_session):
    settings = demo_settings(
        app_env="development",
        auth_session_cookie_secure=False,
        frontend_origins=[ORIGIN],
    )
    app = make_app(db_session, settings)
    with browser(app) as client:
        response = unlock(client)
    assert response.status_code == 204
    assert "secure" not in response.headers["set-cookie"].lower()


@pytest.mark.parametrize(
    "body",
    [
        {"access_key": OTHER_ACCESS_KEY},
        {"access_key": ACCESS_KEY + "x"},
        {"access_key": ""},
        {"access_key": ACCESS_KEY.upper()},
    ],
)
def test_a_wrong_key_is_refused_without_a_cookie(db_session, body):
    app = make_app(db_session, demo_settings())
    with browser(app) as client:
        response = client.post(f"{AUTH}/demo-access", json=body)

        assert response.status_code == 401
        assert response.json() == {"detail": "The reviewer access key is not valid."}
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        if body["access_key"]:
            assert body["access_key"] not in response.text
        assert client.get(f"{AUTH}/demo-access").json()["active"] is False


@pytest.mark.parametrize(
    "content",
    [
        f'{{"key": "{ACCESS_KEY}"}}',  # Wrong field name.
        f'{{"access_key": "{ACCESS_KEY}", "extra": 1}}',  # Unknown field.
        f'"{ACCESS_KEY}"',  # Not an object.
        f'{{"access_key": ["{ACCESS_KEY}"]}}',  # Not a string.
        f'{{"access_key": "{ACCESS_KEY}"',  # Invalid JSON.
        '{"access_key": "' + "k" * 300 + '"}',  # Too long.
        "",
    ],
)
def test_malformed_bodies_are_refused_without_echoing_input(db_session, content):
    app = make_app(db_session, demo_settings())
    with browser(app) as client:
        response = client.post(
            f"{AUTH}/demo-access",
            content=content,
            headers={"Content-Type": "application/json"},
        )

    # The same 401 as a wrong key, never a 422 that would echo the input.
    assert response.status_code == 401
    assert ACCESS_KEY not in response.text
    assert "k" * 300 not in response.text
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "headers", [{"Origin": "https://evil.example"}, {"Origin": "null"}]
)
def test_unlock_requires_a_trusted_origin(db_session, headers):
    app = make_app(db_session, demo_settings())
    with TestClient(app, base_url="https://testserver", headers=headers) as client:
        response = unlock(client)

    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_unlock_attempts_are_rate_limited_per_ip(db_session):
    app = make_app(db_session, demo_settings())
    with browser(app) as client:
        for _ in range(10):
            assert unlock(client, OTHER_ACCESS_KEY).status_code == 401

        # Over the limit, even the right key is refused.
        limited = unlock(client)
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0
        assert "set-cookie" not in limited.headers


def test_the_key_is_never_logged(db_session, caplog):
    app = make_app(db_session, demo_settings())
    with caplog.at_level(logging.DEBUG), browser(app) as client:
        unlock(client, OTHER_ACCESS_KEY)
        unlock(client)
        request_code(client, "9000000001")

    assert ACCESS_KEY not in caplog.text
    assert OTHER_ACCESS_KEY not in caplog.text


# --- Codes for demo numbers -------------------------------------------------


def test_a_reviewer_gets_a_working_single_use_code(db_session):
    app = make_app(db_session, demo_settings())
    with browser(app) as client:
        assert unlock(client).status_code == 204

        response = request_code(client, "9000000001")
        code = response.json()["demo_code"]
        assert response.status_code == 200
        assert len(code) == 6 and code.isdigit()
        assert response.headers["cache-control"] == "no-store"
        assert code == app.state.otp_delivery.latest_code("+919000000001")

        signed_in = verify(client, "9000000001", code)
        assert signed_in.status_code == 200
        # The role comes from the account, never from reviewer access.
        assert signed_in.json()["role"] == "retailer"

        client.post(f"{AUTH}/logout")
        assert verify(client, "9000000001", code).status_code == 401


def test_the_order_desk_demo_account_keeps_its_own_role(db_session):
    app = make_app(db_session, demo_settings(auth_demo_otp_numbers=ALL_DEMO_NUMBERS))
    with browser(app) as client:
        unlock(client)
        code = request_code(client, "9000000010").json()["demo_code"]
        response = verify(client, "9000000010", code)

    assert response.status_code == 200
    assert response.json()["role"] == "order_desk"
    assert response.json()["retailer"] is None


def test_reviewer_access_alone_is_not_a_session(db_session):
    app = make_app(db_session, demo_settings())
    with browser(app) as client:
        unlock(client)
        assert client.get(f"{AUTH}/me").status_code == 401


def test_an_outsider_cannot_obtain_or_issue_a_demo_code(db_session):
    app = make_app(db_session, demo_settings())
    with browser(app) as outsider:
        response = request_code(outsider, "9000000001")

    assert response.status_code == 200
    assert "demo_code" not in response.json()
    # Every API response is no-store, so this one looks like any other.
    assert response.headers["cache-control"] == "no-store"
    # Nothing was issued, stored or delivered.
    assert challenge_count(db_session, "+919000000001") == 0
    assert app.state.otp_delivery.delivered_count("+919000000001") == 0


def test_an_outsider_cannot_invalidate_or_use_the_reviewers_code(db_session):
    app = make_app(db_session, demo_settings())
    with browser(app) as reviewer, browser(app) as outsider:
        unlock(reviewer)
        code = request_code(reviewer, "9000000001").json()["demo_code"]

        # Requests and guesses from a browser without access, including the
        # right code, are refused and change nothing.
        assert request_code(outsider, "9000000001").status_code == 200
        for guess in ("000000", "111111", "222222", "333333", "444444", "555555"):
            assert verify(outsider, "9000000001", guess).status_code == 401
        assert verify(outsider, "9000000001", code).status_code == 401

        challenge = db_session.scalar(
            select(OtpChallenge).where(OtpChallenge.phone_number == "+919000000001")
        )
        db_session.refresh(challenge)
        assert challenge.attempts == 0

        assert verify(reviewer, "9000000001", code).status_code == 200


def test_outsiders_use_a_separate_rate_limit_bucket(db_session):
    app = make_app(db_session, demo_settings())
    with browser(app) as reviewer, browser(app) as outsider:
        # The per-number request limit is 5 an hour.
        for _ in range(5):
            assert request_code(outsider, "9000000001").status_code == 200
        assert request_code(outsider, "9000000001").status_code == 429

        unlock(reviewer)
        response = request_code(reviewer, "9000000001")
        assert response.status_code == 200
        assert response.json()["demo_code"] is not None


def test_an_expired_cookie_means_no_access(db_session):
    settings = demo_settings()
    app = make_app(db_session, settings)
    stale = demo_access.issue_access_token(
        settings, now=datetime.now(UTC) - timedelta(hours=9)
    )
    cookie = {"Cookie": f"{demo_access.ACCESS_COOKIE_NAME}={stale}"}
    with browser(app) as client:
        status = client.get(f"{AUTH}/demo-access", headers=cookie)
        assert status.json()["active"] is False
        response = client.post(
            f"{AUTH}/request-otp", json={"phone_number": "9000000001"}, headers=cookie
        )

    assert "demo_code" not in response.json()
    assert challenge_count(db_session, "+919000000001") == 0


def test_a_cookie_from_a_rotated_key_means_no_access(db_session):
    old = demo_access.issue_access_token(demo_settings())
    app = make_app(db_session, demo_settings(auth_demo_access_key=OTHER_ACCESS_KEY))
    cookie = {"Cookie": f"{demo_access.ACCESS_COOKIE_NAME}={old}"}
    with browser(app) as client:
        response = client.post(
            f"{AUTH}/request-otp", json={"phone_number": "9000000001"}, headers=cookie
        )

    assert "demo_code" not in response.json()
    assert challenge_count(db_session, "+919000000001") == 0


def test_numbers_that_are_not_listed_are_unaffected(db_session):
    app = make_app(db_session, demo_settings())
    with browser(app) as reviewer, browser(app) as other:
        unlock(reviewer)
        # A seeded account that is not listed: never a demo code, even for a
        # reviewer, and ordinary sign-in works without reviewer access.
        assert "demo_code" not in request_code(reviewer, "9000000003").json()

        assert request_code(other, "9000000002").status_code == 200
        code = app.state.otp_delivery.latest_code("+919000000002")
        assert verify(other, "9000000002", code).status_code == 200

        # An unknown number gets the same generic answer.
        unknown = request_code(other, "9876543210")
        assert unknown.status_code == 200
        assert "demo_code" not in unknown.json()


def test_no_code_during_the_resend_cooldown(db_session):
    app = make_app(db_session, demo_settings())
    with browser(app) as client:
        unlock(client)
        first = request_code(client, "9000000001").json()["demo_code"]
        again = request_code(client, "9000000001").json().get("demo_code")

    assert first is not None
    # No new code is issued within the cooldown, so none is shown; the
    # first one stays valid.
    assert again is None
