"""/api/v1/desk/retailers: the order desk's retailer picker."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.services.otp_delivery import InMemoryOtpDelivery
from seed import seed_all

AUTH = "/api/v1/auth"
RETAILERS = "/api/v1/desk/retailers"
TRUSTED_ORIGIN = "http://localhost:3000"


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="testing",
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        auth_secret_key="retailer-directory-secret-0123456789abcd",
        auth_session_cookie_secure=False,
        auth_debug_otp=False,
        frontend_origins=[TRUSTED_ORIGIN],
    )


@pytest.fixture
def client(db_session):
    seed_all(db_session)
    db_session.commit()
    app = create_app(make_settings(), otp_delivery=InMemoryOtpDelivery())

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(
        app, base_url="http://testserver", headers={"Origin": TRUSTED_ORIGIN}
    ) as test_client:
        yield test_client


def login(client: TestClient, phone: str) -> None:
    assert (
        client.post(f"{AUTH}/request-otp", json={"phone_number": phone}).status_code
        == 200
    )
    code = client.app.state.otp_delivery.latest_code(phone)
    verified = client.post(
        f"{AUTH}/verify-otp", json={"phone_number": phone, "code": code}
    )
    assert verified.status_code == 200, verified.text


def test_requires_the_order_desk(client):
    assert client.get(RETAILERS).status_code == 401
    login(client, "+919000000001")
    assert client.get(RETAILERS).status_code == 403


def test_lists_the_seeded_retailers_by_name_without_private_data(client):
    login(client, "+919000000010")

    body = client.get(RETAILERS).json()

    assert [item["business_name"] for item in body["items"]] == [
        "Gupta Kirana Store",
        "Patel Provision Store",
        "Sharma General Store",
    ]
    assert set(body["items"][0]) == {"id", "business_name", "is_active"}


def test_search_matches_part_of_the_name_and_escapes_wildcards(client):
    login(client, "+919000000010")

    assert [
        i["business_name"]
        for i in client.get(RETAILERS, params={"search": "kirana"}).json()["items"]
    ] == ["Gupta Kirana Store"]
    assert client.get(RETAILERS, params={"search": "%"}).json()["items"] == []
    assert client.get(RETAILERS, params={"search": "_"}).json()["items"] == []
    assert (
        client.get(RETAILERS, params={"limit": 1}).json()["items"][0]["business_name"]
        == "Gupta Kirana Store"
    )
    assert client.get(RETAILERS, params={"limit": 0}).status_code == 422
