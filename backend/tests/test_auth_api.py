"""HTTP tests for /api/v1/auth, the authorization dependencies, CORS and CSRF.

The app is built with create_app(test settings); get_db is overridden to
yield the rolled-back db_session from tests/conftest.py, so every request
uses the dedicated test database. Protected routes under /test-only exist
only in this test app, to exercise the reusable dependencies.
"""

import logging
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
        "auth_debug_otp": True,
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
    db_session, settings: Settings, *, base_url: str, origin: str
) -> TestClient:
    app = create_app(settings)
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


def request_code(client: TestClient, phone: str) -> str:
    response = client.post(f"{API}/request-otp", json={"phone_number": phone})
    assert response.status_code == 200, response.text
    return response.json()["debug_otp"]


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
    body = response.json()
    assert body["message"] == OTP_REQUESTED_MESSAGE
    assert len(body["debug_otp"]) == 6 and body["debug_otp"].isdigit()


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


def test_debug_otp_absent_when_mock_delivery_disabled(db_session, seeded):
    settings = make_settings(auth_debug_otp=False)

    with build_client(
        db_session, settings, base_url="http://testserver", origin=TRUSTED_ORIGIN
    ) as client:
        response = client.post(
            f"{API}/request-otp", json={"phone_number": SHARMA_PHONE}
        )

    assert response.json() == {"message": OTP_REQUESTED_MESSAGE}


def test_production_never_returns_debug_otp(db_session, seeded):
    with build_client(
        db_session,
        production_settings(),
        base_url="https://testserver",
        origin=PRODUCTION_ORIGIN,
    ) as client:
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


def test_health_endpoint_still_works(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "application": "OrderBridge API"}
