"""Demo OTP display: codes returned only for listed demonstration accounts."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.services.otp_delivery import InMemoryOtpDelivery
from seed import seed_all

AUTH = "/api/v1/auth"
ORIGIN = "https://orderbridge.example"


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "production",
        "database_url": "postgresql+psycopg://unused:unused@localhost:5432/unused",
        "auth_secret_key": "demo-otp-test-secret-0123456789abcdefgh",
        "auth_session_cookie_secure": True,
        "auth_debug_otp": False,
        "auth_dev_otp_outbox_path": None,
        "frontend_origins": [ORIGIN],
    }
    values.update(overrides)
    return Settings(**values)


def client_for(db_session, settings: Settings) -> TestClient:
    seed_all(db_session)
    db_session.commit()
    app = create_app(settings, otp_delivery=InMemoryOtpDelivery())

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, base_url="https://testserver", headers={"Origin": ORIGIN})


def request(client: TestClient, phone: str):
    return client.post(f"{AUTH}/request-otp", json={"phone_number": phone})


def test_no_code_is_returned_by_default(db_session):
    with client_for(db_session, make_settings()) as client:
        response = request(client, "9000000001")

    assert response.status_code == 200
    assert "demo_code" not in response.json()


def test_listed_demo_accounts_get_a_working_single_use_code(db_session):
    settings = make_settings(auth_demo_otp_numbers=["9000000001"])
    with client_for(db_session, settings) as client:
        response = request(client, "9000000001")
        code = response.json()["demo_code"]

        assert response.status_code == 200
        assert code is not None and len(code) == 6 and code.isdigit()
        assert response.headers["cache-control"] == "no-store"
        # The same code as the delivery channel received.
        assert code == client.app.state.otp_delivery.latest_code("+919000000001")

        verify = {"phone_number": "9000000001", "code": code}
        assert client.post(f"{AUTH}/verify-otp", json=verify).status_code == 200
        # Single use, like any code.
        client.post(f"{AUTH}/logout")
        assert client.post(f"{AUTH}/verify-otp", json=verify).status_code == 401


def test_other_numbers_never_get_a_code(db_session):
    settings = make_settings(auth_demo_otp_numbers=["9000000001"])
    with client_for(db_session, settings) as client:
        # An existing account that is not listed, and an unknown number.
        for phone in ("9000000003", "9876543210"):
            response = request(client, phone)
            assert response.status_code == 200
            assert "demo_code" not in response.json()
            assert "cache-control" not in response.headers


def test_no_code_during_the_resend_cooldown(db_session):
    settings = make_settings(auth_demo_otp_numbers=["9000000001"])
    with client_for(db_session, settings) as client:
        first = request(client, "9000000001").json()["demo_code"]
        again = request(client, "9000000001").json().get("demo_code")

    assert first is not None
    # No new code is issued within the cooldown, so none is shown; the
    # first one stays valid.
    assert again is None


def test_demo_numbers_are_normalised_and_validated():
    settings = make_settings(
        auth_demo_otp_numbers=["9000000001", "+91 90000 00001", "09000000002"]
    )
    assert settings.auth_demo_otp_numbers == ["+919000000001", "+919000000002"]

    with pytest.raises(ValidationError, match="AUTH_DEMO_OTP_NUMBERS"):
        make_settings(auth_demo_otp_numbers=["12345"])
