"""HTTP tests for /api/v1/auth, the authorization dependencies, CORS and CSRF.

The app is built with create_app(test settings); get_db is overridden to
yield the rolled-back db_session from tests/conftest.py, so every request
uses the dedicated test database. Protected routes under /test-only exist
only in this test app, to exercise the reusable dependencies.
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.dependencies import (
    FORBIDDEN,
    NOT_AUTHENTICATED,
    UNTRUSTED_ORIGIN,
    require_roles,
    require_trusted_origin,
)
from app.core.config import Settings
from app.core.database import get_db
from app.core.security import hash_session_token
from app.main import create_app
from app.models import OtpChallenge, User, UserRole, UserSession
from app.schemas.auth import INVALID_CODE_MESSAGE, OTP_REQUESTED_MESSAGE
from app.services import auth_service
from app.services.otp_delivery import (
    DevelopmentConsoleOtpDelivery,
    InMemoryOtpDelivery,
    NoOtpDelivery,
)
from seed import seed_all

API = "/api/v1/auth"
COOKIE = "orderbridge_session"
SESSION_SECONDS = 7 * 24 * 60 * 60

TRUSTED_ORIGIN = "http://localhost:3000"
UNTRUSTED_ORIGIN_URL = "https://attacker.example"
PRODUCTION_ORIGIN = "https://app.orderbridge.example"

SHARMA_PHONE = "+919000000001"
GUPTA_PHONE = "+919000000002"
PATEL_PHONE = "+919000000003"
DESK_PHONE = "+919000000010"
UNKNOWN_PHONE = "+919999999999"


# ---------------------------------------------------------------------------
# Test-only routes using the reusable dependencies
# ---------------------------------------------------------------------------

auth_test_router = APIRouter(prefix="/test-only")

RetailerUser = Annotated[User, Depends(require_roles(UserRole.RETAILER))]
DeskUser = Annotated[User, Depends(require_roles(UserRole.ORDER_DESK))]
AnyStaffUser = Annotated[
    User, Depends(require_roles(UserRole.RETAILER, UserRole.ORDER_DESK))
]


@auth_test_router.get("/retailer-area")
def retailer_area(user: RetailerUser) -> dict[str, str]:
    return {"user_id": str(user.id), "retailer_id": str(user.retailer.id)}


@auth_test_router.get("/desk-area")
def desk_area(user: DeskUser) -> dict[str, str]:
    return {"user_id": str(user.id)}


@auth_test_router.get("/staff-area")
def staff_area(user: AnyStaffUser) -> dict[str, str]:
    return {"user_id": str(user.id)}


@auth_test_router.post(
    "/retailer-action", dependencies=[Depends(require_trusted_origin)]
)
def retailer_action(user: RetailerUser) -> dict[str, str]:
    return {"user_id": str(user.id)}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "testing",
        "database_url": "postgresql+psycopg://unused:unused@localhost:5432/unused",
        "auth_secret_key": "api-test-secret-0123456789abcdef0123456789",
        "auth_otp_ttl_seconds": 300,
        "auth_otp_max_attempts": 5,
        "auth_otp_resend_cooldown_seconds": 60,
        "auth_session_ttl_seconds": SESSION_SECONDS,
        "auth_session_cookie_name": COOKIE,
        "auth_session_cookie_secure": False,
        "auth_session_cookie_samesite": "lax",
        "auth_debug_otp": False,
        "frontend_origins": [TRUSTED_ORIGIN],
    }
    values.update(overrides)
    return Settings(**values)


def production_settings() -> Settings:
    return make_settings(
        app_env="production",
        auth_debug_otp=False,
        auth_session_cookie_secure=True,
        frontend_origins=[PRODUCTION_ORIGIN],
    )


def build_client(
    db_session,
    settings: Settings,
    *,
    base_url: str,
    origin: str,
    configured_delivery: bool = False,
) -> TestClient:
    """A client for an app built with `settings`.

    Codes are recorded by an InMemoryOtpDelivery, unless configured_delivery
    is set: then the app uses the delivery its settings select.
    """
    delivery = None if configured_delivery else InMemoryOtpDelivery()
    app = create_app(settings, otp_delivery=delivery)
    app.include_router(auth_test_router)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, base_url=base_url, headers={"Origin": origin})


@pytest.fixture
def seeded(db_session):
    seed_all(db_session)


@pytest.fixture
def client(db_session, seeded):
    with build_client(
        db_session,
        make_settings(),
        base_url="http://testserver",
        origin=TRUSTED_ORIGIN,
    ) as test_client:
        yield test_client


def user_by_phone(db_session, phone: str) -> User:
    user = db_session.scalar(select(User).where(User.phone_number == phone))
    assert user is not None
    return user


def delivered_code(client: TestClient, phone: str) -> str:
    """The latest code the test delivery channel received for `phone`."""
    return client.app.state.otp_delivery.latest_code(phone)


def request_code(client: TestClient, phone: str) -> str:
    response = client.post(f"{API}/request-otp", json={"phone_number": phone})
    assert response.status_code == 200, response.text
    assert response.json() == {"message": OTP_REQUESTED_MESSAGE}
    return delivered_code(client, phone)


def keys_in(value) -> set[str]:
    """Every key in a JSON value, at any depth."""
    if isinstance(value, dict):
        return set(value) | {k for item in value.values() for k in keys_in(item)}
    if isinstance(value, list):
        return {k for item in value for k in keys_in(item)}
    return set()


def login(client: TestClient, phone: str) -> dict:
    code = request_code(client, phone)
    response = client.post(
        f"{API}/verify-otp", json={"phone_number": phone, "code": code}
    )
    assert response.status_code == 200, response.text
    return response.json()


def session_cookie_attributes(response) -> dict[str, str]:
    """Attributes of the session Set-Cookie header, keys lower-cased."""
    (header,) = [
        h for h in response.headers.get_list("set-cookie") if h.startswith(f"{COOKIE}=")
    ]
    name_value, *attributes = header.split(";")
    parsed = {"value": name_value.split("=", 1)[1]}

    for attribute in attributes:
        key, _, value = attribute.strip().partition("=")
        parsed[key.lower()] = value

    return parsed


def wrong_code(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


# ---------------------------------------------------------------------------
# Requesting a code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phone", [SHARMA_PHONE, DESK_PHONE])
def test_request_otp_for_active_accounts(client, phone):
    response = client.post(f"{API}/request-otp", json={"phone_number": phone})

    assert response.status_code == 200
    # The response never carries the code; the delivery channel received it.
    assert response.json() == {"message": OTP_REQUESTED_MESSAGE}
    code = delivered_code(client, phone)
    assert len(code) == 6 and code.isdigit()
    assert code not in response.text


def test_unknown_and_inactive_numbers_get_identical_generic_response(
    client, db_session
):
    user_by_phone(db_session, PATEL_PHONE).is_active = False
    db_session.flush()

    unknown = client.post(f"{API}/request-otp", json={"phone_number": UNKNOWN_PHONE})
    inactive = client.post(f"{API}/request-otp", json={"phone_number": PATEL_PHONE})

    assert unknown.status_code == inactive.status_code == 200
    assert unknown.json() == inactive.json() == {"message": OTP_REQUESTED_MESSAGE}
    assert db_session.scalars(select(OtpChallenge)).all() == []


def test_no_auth_response_contains_the_code(client):
    requested = client.post(f"{API}/request-otp", json={"phone_number": SHARMA_PHONE})
    code = delivered_code(client, SHARMA_PHONE)
    responses = [
        requested,
        client.post(
            f"{API}/verify-otp",
            json={"phone_number": SHARMA_PHONE, "code": wrong_code(code)},
        ),
        client.post(
            f"{API}/verify-otp", json={"phone_number": SHARMA_PHONE, "code": code}
        ),
        client.get(f"{API}/me"),
        client.post(f"{API}/logout"),
    ]

    assert [r.status_code for r in responses] == [200, 401, 200, 200, 204]
    assert requested.json() == {"message": OTP_REQUESTED_MESSAGE}
    assert code not in requested.text
    for response in responses:
        assert all(code not in value for value in response.headers.values())
        if response.content:
            keys = {key.lower() for key in keys_in(response.json())}
            assert not {key for key in keys if "otp" in key or "code" in key}


def test_development_delivery_prints_the_code_to_the_backend_terminal_only(
    db_session, seeded, capsys
):
    """APP_ENV=development with AUTH_DEBUG_OTP: the code reaches the backend's
    stderr, never the response, and it signs the user in."""
    settings = make_settings(app_env="development", auth_debug_otp=True)

    with build_client(
        db_session,
        settings,
        base_url="http://testserver",
        origin=TRUSTED_ORIGIN,
        configured_delivery=True,
    ) as client:
        assert isinstance(client.app.state.otp_delivery, DevelopmentConsoleOtpDelivery)
        requested = client.post(
            f"{API}/request-otp", json={"phone_number": SHARMA_PHONE}
        )
        printed = capsys.readouterr()
        (code,) = re.findall(r"\b(\d{6})\b", printed.err)

        verified = client.post(
            f"{API}/verify-otp", json={"phone_number": SHARMA_PHONE, "code": code}
        )

    assert requested.json() == {"message": OTP_REQUESTED_MESSAGE}
    assert code not in requested.text
    assert code not in printed.out
    assert SHARMA_PHONE not in printed.err  # the number is masked
    assert verified.status_code == 200


def test_development_outbox_leaves_the_http_response_unchanged(
    db_session, seeded, capsys, tmp_path
):
    """With the outbox enabled the response is still only {message}; the
    code reaches the backend's terminal and the outbox file, nothing else."""
    outbox = tmp_path / "outbox.jsonl"
    settings = make_settings(
        app_env="development",
        auth_debug_otp=True,
        auth_dev_otp_outbox_path=str(outbox),
    )

    with build_client(
        db_session,
        settings,
        base_url="http://testserver",
        origin=TRUSTED_ORIGIN,
        configured_delivery=True,
    ) as client:
        requested = client.post(
            f"{API}/request-otp", json={"phone_number": SHARMA_PHONE}
        )
        unknown = client.post(
            f"{API}/request-otp", json={"phone_number": UNKNOWN_PHONE}
        )
        printed = capsys.readouterr()
        records = [
            json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()
        ]
        (record,) = records  # nothing is written for an unknown number
        code = record["code"]

        verified = client.post(
            f"{API}/verify-otp", json={"phone_number": SHARMA_PHONE, "code": code}
        )

    assert requested.json() == unknown.json() == {"message": OTP_REQUESTED_MESSAGE}
    # Only the request responses: the verify body legitimately holds digits
    # (phone number, ids) that a random code could match by chance.
    for response in (requested, unknown):
        assert code not in response.text
        assert all(code not in value for value in response.headers.values())
    assert record["phone_number"] == SHARMA_PHONE
    # Both development channels received the same code.
    assert re.findall(r"\b(\d{6})\b", printed.err) == [code]
    assert verified.status_code == 200


def test_outbox_failure_does_not_change_the_otp_request(
    db_session, seeded, capsys, caplog, tmp_path
):
    """The outbox is rejected at delivery time (a symbolic link planted
    after startup). The request still gets the normal response, identical to
    an unknown number's, and the console-delivered code still signs in."""
    target = tmp_path / "elsewhere.txt"
    target.write_text("untouched", encoding="utf-8")
    outbox = tmp_path / "outbox.jsonl"  # absent at startup: accepted
    settings = make_settings(
        app_env="development",
        auth_debug_otp=True,
        auth_dev_otp_outbox_path=str(outbox),
    )
    caplog.set_level(logging.DEBUG)

    with build_client(
        db_session,
        settings,
        base_url="http://testserver",
        origin=TRUSTED_ORIGIN,
        configured_delivery=True,
    ) as client:
        outbox.symlink_to(target)
        known = client.post(f"{API}/request-otp", json={"phone_number": SHARMA_PHONE})
        unknown = client.post(
            f"{API}/request-otp", json={"phone_number": UNKNOWN_PHONE}
        )
        (code,) = re.findall(r"\b(\d{6})\b", capsys.readouterr().err)

        verified = client.post(
            f"{API}/verify-otp", json={"phone_number": SHARMA_PHONE, "code": code}
        )

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"message": OTP_REQUESTED_MESSAGE}
    assert code not in known.text
    assert verified.status_code == 200
    assert target.read_text(encoding="utf-8") == "untouched"

    outbox_errors = [
        record
        for record in caplog.records
        if record.name == "app.services.otp_delivery"
        and record.levelno >= logging.ERROR
    ]
    assert len(outbox_errors) == 1
    # Application logs only: SQLAlchemy's statement logging (enabled by the
    # DEBUG level) prints bound parameters, which legitimately include the
    # phone number.
    for record in caplog.records:
        if record.name.startswith("sqlalchemy"):
            continue
        message = record.getMessage()
        for secret in (code, SHARMA_PHONE, str(outbox), str(target)):
            assert secret not in message
        assert record.exc_info is None


def test_testing_environment_delivers_nothing_by_default(db_session, seeded):
    with build_client(
        db_session,
        make_settings(),
        base_url="http://testserver",
        origin=TRUSTED_ORIGIN,
        configured_delivery=True,
    ) as client:
        assert isinstance(client.app.state.otp_delivery, NoOtpDelivery)
        response = client.post(
            f"{API}/request-otp", json={"phone_number": SHARMA_PHONE}
        )

    assert response.json() == {"message": OTP_REQUESTED_MESSAGE}
    # A code was issued and stored as a digest, but sent nowhere.
    assert len(db_session.scalars(select(OtpChallenge)).all()) == 1


def test_production_never_returns_the_code(db_session, seeded):
    with build_client(
        db_session,
        production_settings(),
        base_url="https://testserver",
        origin=PRODUCTION_ORIGIN,
        configured_delivery=True,
    ) as client:
        assert isinstance(client.app.state.otp_delivery, NoOtpDelivery)
        response = client.post(
            f"{API}/request-otp", json={"phone_number": SHARMA_PHONE}
        )

    assert response.status_code == 200
    assert response.json() == {"message": OTP_REQUESTED_MESSAGE}
    # A code was issued; it is just never exposed.
    assert len(db_session.scalars(select(OtpChallenge)).all()) == 1


@pytest.mark.parametrize("phone", ["12345", "+1 202 555 0100", "not-a-number"])
def test_invalid_phone_number_is_rejected(client, phone):
    response = client.post(f"{API}/request-otp", json={"phone_number": phone})

    assert response.status_code == 422


def test_client_cannot_supply_role_or_extra_fields(client):
    response = client.post(
        f"{API}/request-otp",
        json={"phone_number": SHARMA_PHONE, "role": "order_desk"},
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Verifying a code
# ---------------------------------------------------------------------------


def test_verify_otp_returns_retailer_profile_and_sets_cookie(client):
    code = request_code(client, SHARMA_PHONE)

    response = client.post(
        f"{API}/verify-otp", json={"phone_number": SHARMA_PHONE, "code": code}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Amit Sharma"
    assert body["phone_number"] == SHARMA_PHONE
    assert body["role"] == "retailer"
    assert body["retailer"]["business_name"] == "Sharma General Store"
    assert body["retailer"]["owner_name"] == "Amit Sharma"
    assert body["retailer"]["address"] == "Wright Town, Jabalpur"
    assert "credit_limit" not in response.text

    token = client.cookies.get(COOKIE)
    assert token
    assert token not in response.text


def test_verify_otp_for_order_desk_has_no_retailer(client):
    body = login(client, DESK_PHONE)

    assert body["role"] == "order_desk"
    assert body["full_name"] == "Order Desk Operator"
    assert body["retailer"] is None


def test_wrong_code_is_rejected_and_attempt_is_recorded(client, db_session):
    code = request_code(client, SHARMA_PHONE)

    response = client.post(
        f"{API}/verify-otp",
        json={"phone_number": SHARMA_PHONE, "code": wrong_code(code)},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_CODE_MESSAGE}
    assert "set-cookie" not in response.headers
    challenge = db_session.scalar(select(OtpChallenge))
    assert challenge.attempts == 1
    assert challenge.consumed_at is None


def test_code_cannot_be_replayed(client):
    code = request_code(client, SHARMA_PHONE)
    payload = {"phone_number": SHARMA_PHONE, "code": code}
    assert client.post(f"{API}/verify-otp", json=payload).status_code == 200

    replay = client.post(f"{API}/verify-otp", json=payload)

    assert replay.status_code == 401
    assert replay.json() == {"detail": INVALID_CODE_MESSAGE}


def test_unknown_number_verification_gets_same_error(client):
    response = client.post(
        f"{API}/verify-otp", json={"phone_number": UNKNOWN_PHONE, "code": "123456"}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_CODE_MESSAGE}


def test_locked_out_number_gets_the_same_responses(db_session, seeded):
    """Locked, wrong and right-but-locked codes are indistinguishable."""
    settings = make_settings(auth_otp_lockout_threshold=2)

    with build_client(
        db_session, settings, base_url="http://testserver", origin=TRUSTED_ORIGIN
    ) as client:
        code = request_code(client, SHARMA_PHONE)
        wrong = {"phone_number": SHARMA_PHONE, "code": wrong_code(code)}

        responses = [
            client.post(f"{API}/verify-otp", json=wrong),
            client.post(f"{API}/verify-otp", json=wrong),
            # Locked now: the correct code gets the same answer.
            client.post(
                f"{API}/verify-otp",
                json={"phone_number": SHARMA_PHONE, "code": code},
            ),
        ]
        requested = client.post(
            f"{API}/request-otp", json={"phone_number": SHARMA_PHONE}
        )

    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"detail": INVALID_CODE_MESSAGE}
        assert "set-cookie" not in response.headers

    assert requested.status_code == 200
    assert requested.json()["message"] == OTP_REQUESTED_MESSAGE

    sharma = user_by_phone(db_session, SHARMA_PHONE)
    sessions = db_session.scalars(
        select(UserSession).where(UserSession.user_id == sharma.id)
    ).all()
    assert sessions == []


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_me_returns_session_user(client):
    login(client, SHARMA_PHONE)

    response = client.get(f"{API}/me")

    assert response.status_code == 200
    assert response.json()["phone_number"] == SHARMA_PHONE


def test_raw_session_token_is_not_stored(client, db_session):
    login(client, SHARMA_PHONE)
    token = client.cookies.get(COOKIE)

    rows = db_session.scalars(select(UserSession)).all()

    assert [row.token_hash for row in rows] == [hash_session_token(token)]
    assert all(row.token_hash != token for row in rows)


def test_missing_cookie_is_unauthenticated(client):
    response = client.get(f"{API}/me")

    assert response.status_code == 401
    assert response.json() == {"detail": NOT_AUTHENTICATED}


def test_forged_cookie_is_unauthenticated(client):
    client.cookies.set(COOKIE, "forged-session-token")

    assert client.get(f"{API}/me").status_code == 401


def test_expired_session_is_rejected(client, db_session):
    login(client, SHARMA_PHONE)
    db_session.execute(
        update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    assert client.get(f"{API}/me").status_code == 401


def test_revoked_session_is_rejected(client, db_session):
    login(client, SHARMA_PHONE)
    db_session.execute(update(UserSession).values(revoked_at=datetime.now(UTC)))

    assert client.get(f"{API}/me").status_code == 401


def test_session_of_deactivated_user_is_rejected(client, db_session):
    login(client, SHARMA_PHONE)
    user_by_phone(db_session, SHARMA_PHONE).is_active = False
    db_session.flush()

    assert client.get(f"{API}/me").status_code == 401


def test_session_of_deactivated_retailer_is_rejected(client, db_session):
    login(client, SHARMA_PHONE)
    user_by_phone(db_session, SHARMA_PHONE).retailer.is_active = False
    db_session.flush()

    assert client.get(f"{API}/me").status_code == 401
    assert client.get("/test-only/retailer-area").status_code == 401


def test_logout_revokes_session_and_clears_cookie(client, db_session):
    login(client, SHARMA_PHONE)
    token = client.cookies.get(COOKIE)

    response = client.post(f"{API}/logout")

    assert response.status_code == 204
    cleared = session_cookie_attributes(response)
    assert cleared["value"] in ("", '""')
    assert cleared["max-age"] == "0"
    assert cleared["path"] == "/"

    stored = db_session.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    assert stored.revoked_at is not None
    assert client.cookies.get(COOKIE) is None
    assert client.get(f"{API}/me").status_code == 401


def test_repeated_logout_is_safe_and_always_clears_cookie(client):
    login(client, SHARMA_PHONE)
    token = client.cookies.get(COOKIE)
    assert client.post(f"{API}/logout").status_code == 204

    # No cookie at all.
    again = client.post(f"{API}/logout")
    assert again.status_code == 204
    assert session_cookie_attributes(again)["max-age"] == "0"

    # A cookie holding the already-revoked token.
    client.cookies.set(COOKIE, token)
    revoked = client.post(f"{API}/logout")
    assert revoked.status_code == 204
    assert session_cookie_attributes(revoked)["max-age"] == "0"
    assert client.get(f"{API}/me").status_code == 401


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phone", "path", "expected"),
    [
        (SHARMA_PHONE, "/test-only/retailer-area", 200),
        (SHARMA_PHONE, "/test-only/desk-area", 403),
        (DESK_PHONE, "/test-only/desk-area", 200),
        (DESK_PHONE, "/test-only/retailer-area", 403),
        (SHARMA_PHONE, "/test-only/staff-area", 200),
        (DESK_PHONE, "/test-only/staff-area", 200),
    ],
)
def test_role_access_matrix(client, phone, path, expected):
    login(client, phone)

    response = client.get(path)

    assert response.status_code == expected
    if expected == 403:
        assert response.json() == {"detail": FORBIDDEN}


@pytest.mark.parametrize("path", ["/test-only/retailer-area", "/test-only/desk-area"])
def test_protected_routes_require_authentication(client, path):
    assert client.get(path).status_code == 401

    client.cookies.set(COOKIE, "invalid-token")
    assert client.get(path).status_code == 401


def test_client_cannot_impersonate_another_retailer(client, db_session):
    login(client, SHARMA_PHONE)
    sharma = user_by_phone(db_session, SHARMA_PHONE)
    gupta = user_by_phone(db_session, GUPTA_PHONE)
    claims = {
        "user_id": str(gupta.id),
        "retailer_id": str(gupta.retailer.id),
        "role": "order_desk",
    }

    area = client.get(
        "/test-only/retailer-area",
        params=claims,
        headers={"X-User-Id": str(gupta.id), "X-Retailer-Id": claims["retailer_id"]},
    )
    me = client.get(f"{API}/me", params=claims)
    desk = client.get("/test-only/desk-area", params=claims, headers={"X-Role": "x"})

    assert area.json() == {
        "user_id": str(sharma.id),
        "retailer_id": str(sharma.retailer.id),
    }
    assert me.json()["id"] == str(sharma.id)
    assert desk.status_code == 403


def test_require_roles_needs_at_least_one_role():
    with pytest.raises(ValueError):
        require_roles()


# ---------------------------------------------------------------------------
# CSRF origin checks and CORS
# ---------------------------------------------------------------------------


def test_trusted_origin_allows_state_changing_request(client):
    login(client, SHARMA_PHONE)

    assert client.post("/test-only/retailer-action").status_code == 200


@pytest.mark.parametrize("origin", [UNTRUSTED_ORIGIN_URL, "null"])
def test_untrusted_origin_is_rejected(client, origin):
    login(client, SHARMA_PHONE)

    response = client.post("/test-only/retailer-action", headers={"Origin": origin})

    assert response.status_code == 403
    assert response.json() == {"detail": UNTRUSTED_ORIGIN}


def test_missing_origin_falls_back_to_referer(client):
    login(client, SHARMA_PHONE)
    client.headers.pop("Origin")

    no_headers = client.post("/test-only/retailer-action")
    trusted_referer = client.post(
        "/test-only/retailer-action",
        headers={"Referer": f"{TRUSTED_ORIGIN}/orders/new"},
    )
    untrusted_referer = client.post(
        "/test-only/retailer-action",
        headers={"Referer": f"{UNTRUSTED_ORIGIN_URL}/page"},
    )

    assert no_headers.status_code == 403
    assert trusted_referer.status_code == 200
    assert untrusted_referer.status_code == 403


@pytest.mark.parametrize(
    "origin",
    ["HTTP://LOCALHOST:3000", "http://localhost:3000/", "http://LocalHost:3000"],
)
def test_equivalent_spellings_of_trusted_origin_are_accepted(client, origin):
    login(client, SHARMA_PHONE)

    response = client.post("/test-only/retailer-action", headers={"Origin": origin})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "http://localhost:3000/orders",
        "http://localhost:3000?x=1",
        "http://localhost:99999",
        "http://localhost:3000@attacker.example",
        "http://localhost:3001",
        "https://localhost:3000",
        "http://[::1",
    ],
)
def test_malformed_or_mismatched_origin_is_rejected(client, origin):
    login(client, SHARMA_PHONE)

    response = client.post("/test-only/retailer-action", headers={"Origin": origin})

    assert response.status_code == 403
    assert response.json() == {"detail": UNTRUSTED_ORIGIN}


def test_malformed_origin_does_not_fall_back_to_referer(client):
    login(client, SHARMA_PHONE)

    response = client.post(
        "/test-only/retailer-action",
        headers={"Origin": "null", "Referer": f"{TRUSTED_ORIGIN}/orders/new"},
    )

    assert response.status_code == 403


def test_malformed_referer_is_rejected(client):
    login(client, SHARMA_PHONE)
    client.headers.pop("Origin")

    response = client.post(
        "/test-only/retailer-action",
        headers={"Referer": "http://localhost:3000@attacker.example/page"},
    )

    assert response.status_code == 403


def test_logout_from_untrusted_origin_is_rejected(client, db_session):
    login(client, SHARMA_PHONE)

    response = client.post(f"{API}/logout", headers={"Origin": UNTRUSTED_ORIGIN_URL})

    assert response.status_code == 403
    assert db_session.scalar(select(UserSession)).revoked_at is None
    assert client.get(f"{API}/me").status_code == 200


def test_verify_from_untrusted_origin_is_rejected(client, db_session):
    code = request_code(client, SHARMA_PHONE)
    payload = {"phone_number": SHARMA_PHONE, "code": code}

    response = client.post(
        f"{API}/verify-otp", json=payload, headers={"Origin": UNTRUSTED_ORIGIN_URL}
    )

    assert response.status_code == 403
    challenge = db_session.scalar(select(OtpChallenge))
    assert challenge.attempts == 0
    assert challenge.consumed_at is None
    assert client.post(f"{API}/verify-otp", json=payload).status_code == 200


def test_safe_methods_are_not_origin_checked(client):
    login(client, SHARMA_PHONE)

    response = client.get(f"{API}/me", headers={"Origin": UNTRUSTED_ORIGIN_URL})

    assert response.status_code == 200
    # The browser still may not read it: no CORS grant for that origin.
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_allows_only_trusted_origins(client):
    preflight = {"Access-Control-Request-Method": "POST"}

    trusted = client.options(
        f"{API}/logout", headers={"Origin": TRUSTED_ORIGIN, **preflight}
    )
    untrusted = client.options(
        f"{API}/logout", headers={"Origin": UNTRUSTED_ORIGIN_URL, **preflight}
    )

    assert trusted.status_code == 200
    assert trusted.headers["access-control-allow-origin"] == TRUSTED_ORIGIN
    assert trusted.headers["access-control-allow-credentials"] == "true"
    assert untrusted.status_code == 400
    assert "access-control-allow-origin" not in untrusted.headers


# ---------------------------------------------------------------------------
# Cookie flags, logging and health
# ---------------------------------------------------------------------------


def test_development_cookie_flags(client):
    code = request_code(client, SHARMA_PHONE)
    response = client.post(
        f"{API}/verify-otp", json={"phone_number": SHARMA_PHONE, "code": code}
    )

    attributes = session_cookie_attributes(response)
    assert "httponly" in attributes
    assert attributes["path"] == "/"
    assert attributes["samesite"].lower() == "lax"
    assert attributes["max-age"] == str(SESSION_SECONDS)
    assert "domain" not in attributes
    assert "secure" not in attributes


def test_production_cookie_is_secure(db_session, seeded):
    settings = production_settings()
    issued = auth_service.request_otp(db_session, SHARMA_PHONE, settings=settings)

    with build_client(
        db_session, settings, base_url="https://testserver", origin=PRODUCTION_ORIGIN
    ) as client:
        response = client.post(
            f"{API}/verify-otp",
            json={"phone_number": SHARMA_PHONE, "code": issued.code},
        )

    assert response.status_code == 200
    attributes = session_cookie_attributes(response)
    assert "secure" in attributes
    assert "httponly" in attributes
    assert attributes["samesite"].lower() == "lax"
    assert "domain" not in attributes


def test_raw_otp_and_session_token_are_not_logged(client, caplog):
    caplog.set_level(logging.DEBUG)

    code = request_code(client, SHARMA_PHONE)
    client.post(f"{API}/verify-otp", json={"phone_number": SHARMA_PHONE, "code": code})
    token = client.cookies.get(COOKIE)
    client.get(f"{API}/me")
    client.post(f"{API}/logout")

    assert token not in caplog.text
    # SQL statement logging (when enabled) prints timestamps and hashes whose
    # digits could match a six-digit code by chance; the raw code never
    # reaches SQL (see the hashing tests), so check application logs only.
    application_logs = [
        record.getMessage()
        for record in caplog.records
        if not record.name.startswith("sqlalchemy")
    ]
    assert all(code not in message for message in application_logs)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_production_does_not_serve_api_documentation(db_session, seeded, path):
    with build_client(
        db_session,
        production_settings(),
        base_url="https://testserver",
        origin=PRODUCTION_ORIGIN,
    ) as client:
        response = client.get(path)

    assert response.status_code == 404


@pytest.mark.parametrize("app_env", ["development", "testing"])
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_documentation_is_served_outside_production(
    db_session, seeded, app_env, path
):
    with build_client(
        db_session,
        make_settings(app_env=app_env),
        base_url="http://testserver",
        origin=TRUSTED_ORIGIN,
    ) as client:
        response = client.get(path)

    assert response.status_code == 200


def test_health_endpoint_still_works(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "application": "OrderBridge API"}
