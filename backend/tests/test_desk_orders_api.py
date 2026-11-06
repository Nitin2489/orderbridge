"""HTTP tests for /api/v1/desk/orders (Unit C1: order desk workflow).

The app is built with create_app(test settings); get_db is overridden to
yield the rolled-back db_session (tests/conftest.py). seed_all() provides the
brief's catalogue and the demo accounts:

    Sharma (+919000000001)  credit 50000.00, outstanding 12000.00
    Gupta  (+919000000002)  credit 75000.00, outstanding 25000.00
    Patel  (+919000000003)  credit 30000.00, outstanding  5000.00
    Order desk (+919000000010)

Orders are placed with retailer_order_service.submit_order(), the real
submission path, at controlled times. Setup changes that must survive a
failed request (which rolls back) are committed first; in the fixture's
savepoint mode that commit still ends in the test's final rollback.
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.models import (
    Order,
    OrderEvent,
    OrderStatus,
    Product,
    Retailer,
    RetailerProductPrice,
    User,
)
from app.services.retailer_order_service import OrderLineInput, submit_order
from seed import seed_all

AUTH = "/api/v1/auth"
DESK = "/api/v1/desk/orders"
RETAILER_ORDERS = "/api/v1/retailer/orders"
TRUSTED_ORIGIN = "http://localhost:3000"

SHARMA_PHONE = "+919000000001"
GUPTA_PHONE = "+919000000002"
PATEL_PHONE = "+919000000003"
DESK_PHONE = "+919000000010"

BISCUIT = "SKU-BS-075"
DETERGENT = "SKU-DT-1KG"
TEA = "SKU-TE-250"

BASE_TIME = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
HEX64 = re.compile(r"[0-9a-f]{64}")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="testing",
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        auth_secret_key="desk-test-secret-0123456789abcdef012345",
        auth_session_cookie_secure=False,
        auth_debug_otp=True,
        frontend_origins=[TRUSTED_ORIGIN],
    )


@pytest.fixture
def client(db_session):
    seed_all(db_session)
    app = create_app(make_settings())

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(
        app, base_url="http://testserver", headers={"Origin": TRUSTED_ORIGIN}
    ) as test_client:
        yield test_client


def login(client: TestClient, phone: str) -> None:
    requested = client.post(f"{AUTH}/request-otp", json={"phone_number": phone})
    assert requested.status_code == 200, requested.text
    verified = client.post(
        f"{AUTH}/verify-otp",
        json={"phone_number": phone, "code": requested.json()["debug_otp"]},
    )
    assert verified.status_code == 200, verified.text


@pytest.fixture
def desk(client):
    """The client, signed in as the order desk."""
    login(client, DESK_PHONE)
    return client


def user_by_phone(db_session, phone: str) -> User:
    user = db_session.scalar(select(User).where(User.phone_number == phone))
    assert user is not None
    return user


def retailer_by_phone(db_session, phone: str) -> Retailer:
    retailer = db_session.scalar(
        select(Retailer).join(User).where(User.phone_number == phone)
    )
    assert retailer is not None
    return retailer


def product_by_sku(db_session, sku: str) -> Product:
    product = db_session.scalar(select(Product).where(Product.sku == sku))
    assert product is not None
    return product


def price_row(db_session, phone: str, sku: str) -> RetailerProductPrice:
    row = db_session.scalar(
        select(RetailerProductPrice).where(
            RetailerProductPrice.retailer_id == retailer_by_phone(db_session, phone).id,
            RetailerProductPrice.product_id == product_by_sku(db_session, sku).id,
        )
    )
    assert row is not None
    return row


def place_order(
    db_session, phone: str, lines: list[tuple[str, int]], *, minutes: int = 0
) -> Order:
    """A pending order submitted through the real retailer submission path.

    Committed (into the test's outer transaction) so that a later failed
    desk request, which rolls back, cannot remove it.
    """
    result = submit_order(
        db_session,
        retailer_by_phone(db_session, phone),
        [
            OrderLineInput(
                product_id=product_by_sku(db_session, sku).id, quantity=quantity
            )
            for sku, quantity in lines
        ],
        client_request_id=uuid4(),
        now=BASE_TIME + timedelta(minutes=minutes),
    )
    db_session.commit()
    return result.order


def events_of(db_session, order_id) -> list[OrderEvent]:
    return list(
        db_session.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == UUID(str(order_id)))
            .order_by(OrderEvent.created_at, OrderEvent.id)
        )
    )


def event_types(db_session, order_id) -> list[str]:
    return [event.event_type for event in events_of(db_session, order_id)]


def preview(client, order_id) -> dict:
    response = client.get(f"{DESK}/{order_id}/preview")
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, order_id, fingerprint: str):
    return client.post(
        f"{DESK}/{order_id}/confirm", json={"approved_fingerprint": fingerprint}
    )


def preview_and_confirm(client, order_id):
    return confirm(client, order_id, preview(client, order_id)["fingerprint"])


def reject(client, order_id, reason: str = "Retailer asked to cancel."):
    return client.post(f"{DESK}/{order_id}/reject", json={"reason": reason})


@pytest.fixture
def sharma_order(db_session):
    """Sharma: 5 detergent (line 1) and 10 biscuit (line 2); total 17621.50."""
    return place_order(db_session, SHARMA_PHONE, [(DETERGENT, 5), (BISCUIT, 10)])


# ---------------------------------------------------------------------------
# Authentication, roles and CSRF
# ---------------------------------------------------------------------------


def _desk_requests(order_id) -> list[tuple[str, str, dict]]:
    confirm_body = {"json": {"approved_fingerprint": "a" * 64}}
    reject_body = {"json": {"reason": "Not needed"}}
    return [
        ("get", DESK, {}),
        ("get", f"{DESK}/{order_id}", {}),
        ("get", f"{DESK}/{order_id}/preview", {}),
        ("post", f"{DESK}/{order_id}/confirm", confirm_body),
        ("post", f"{DESK}/{order_id}/reject", reject_body),
    ]


def test_desk_endpoints_require_authentication(client, sharma_order):
    for method, path, kwargs in _desk_requests(sharma_order.id):
        assert getattr(client, method)(path, **kwargs).status_code == 401, path


def test_retailers_cannot_use_desk_endpoints(client, sharma_order):
    login(client, SHARMA_PHONE)

    for method, path, kwargs in _desk_requests(sharma_order.id):
        assert getattr(client, method)(path, **kwargs).status_code == 403, path


def test_desk_post_endpoints_require_a_trusted_origin(desk, db_session, sharma_order):
    fingerprint = preview(desk, sharma_order.id)["fingerprint"]
    untrusted = {"Origin": "https://attacker.example"}

    confirmed = desk.post(
        f"{DESK}/{sharma_order.id}/confirm",
        json={"approved_fingerprint": fingerprint},
        headers=untrusted,
    )
    rejected = desk.post(
        f"{DESK}/{sharma_order.id}/reject",
        json={"reason": "Forged request"},
        headers=untrusted,
    )

    assert confirmed.status_code == rejected.status_code == 403
    assert desk.get(f"{DESK}/{sharma_order.id}").json()["status"] == "pending_review"
    assert event_types(db_session, sharma_order.id) == ["submitted"]


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


def test_queue_lists_orders_across_retailers(desk, db_session):
    sharma = place_order(db_session, SHARMA_PHONE, [(BISCUIT, 10)], minutes=1)
    gupta = place_order(db_session, GUPTA_PHONE, [(DETERGENT, 1), (BISCUIT, 1)])

    body = desk.get(DESK).json()

    assert body["total"] == 2
    rows = {row["id"]: row for row in body["items"]}
    assert rows[str(sharma.id)]["retailer_business_name"] == "Sharma General Store"
    assert rows[str(sharma.id)]["grand_total"] == "7776.00"
    assert rows[str(gupta.id)]["retailer_id"] == str(gupta.retailer_id)
    assert rows[str(gupta.id)]["item_count"] == 2
    assert rows[str(gupta.id)]["status"] == "pending_review"
    assert rows[str(gupta.id)]["source"] == "retailer_app"
    assert rows[str(gupta.id)]["tax_treatment"] == "exclusive"


def test_pending_queue_is_oldest_first_and_other_lists_newest_first(desk, db_session):
    orders = [
        place_order(db_session, SHARMA_PHONE, [(BISCUIT, 1)], minutes=minute)
        for minute in (0, 1, 2)
    ]
    ids = [str(order.id) for order in orders]

    pending = desk.get(DESK, params={"status": "pending_review"}).json()
    everything = desk.get(DESK).json()

    assert [row["id"] for row in pending["items"]] == ids
    assert [row["id"] for row in everything["items"]] == ids[::-1]


def test_queue_filters(desk, db_session):
    first = place_order(db_session, SHARMA_PHONE, [(BISCUIT, 1)], minutes=0)
    second = place_order(db_session, GUPTA_PHONE, [(BISCUIT, 1)], minutes=10)
    third = place_order(db_session, SHARMA_PHONE, [(BISCUIT, 1)], minutes=20)
    third.status = OrderStatus.CONFIRMED
    db_session.flush()

    def ids(**params) -> list[str]:
        response = desk.get(DESK, params=params)
        assert response.status_code == 200, response.text
        return sorted(row["id"] for row in response.json()["items"])

    assert ids(status="confirmed") == [str(third.id)]
    assert ids(retailer_id=str(second.retailer_id)) == [str(second.id)]
    assert ids(search="gupta") == [str(second.id)]
    assert ids(search=first.order_number.lower()) == [str(first.id)]
    assert ids(search="%") == []
    window = {
        "created_from": BASE_TIME.isoformat(),
        "created_to": (BASE_TIME + timedelta(minutes=20)).isoformat(),
    }
    assert ids(**window) == sorted([str(first.id), str(second.id)])


def test_queue_pagination(desk, db_session):
    for minute in (0, 1, 2):
        place_order(db_session, SHARMA_PHONE, [(BISCUIT, 1)], minutes=minute)

    page = desk.get(
        DESK, params={"status": "pending_review", "limit": 1, "offset": 1}
    ).json()

    assert page["total"] == 3
    assert page["limit"] == 1
    assert page["offset"] == 1
    assert len(page["items"]) == 1


@pytest.mark.parametrize(
    "params",
    [
        {
            "created_from": "2026-11-02T10:00:00+00:00",
            "created_to": "2026-11-02T09:00:00+00:00",
        },
        {"created_from": "2026-11-02T09:00:00"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"status": "shipped"},
        {"retailer_id": "not-a-uuid"},
    ],
)
def test_invalid_queue_filters_are_rejected(desk, params):
    assert desk.get(DESK, params=params).status_code == 422


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def test_detail_shows_credit_position_lines_and_events(desk, sharma_order):
    response = desk.get(f"{DESK}/{sharma_order.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["order_number"] == sharma_order.order_number
    assert body["retailer"] == {
        "retailer_id": str(sharma_order.retailer_id),
        "business_name": "Sharma General Store",
        "is_active": True,
        "credit_limit": "50000.00",
        "outstanding_balance": "12000.00",
        "available_credit": "38000.00",
    }
    assert [(line["line_number"], line["sku"]) for line in body["items"]] == [
        (1, DETERGENT),
        (2, BISCUIT),
    ]
    assert body["items"][1]["scheme_name"] == "Buy 10 cases, get 1 free"
    (event,) = body["events"]
    assert event["event_type"] == "submitted"
    assert event["to_status"] == "pending_review"
    assert event["actor_name"] == "Amit Sharma"
    assert SHARMA_PHONE not in response.text
    assert "phone" not in response.text


def test_detail_of_missing_order_is_404(desk):
    response = desk.get(f"{DESK}/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Order not found."}


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_maps_every_field_in_line_number_order(desk, sharma_order):
    body = preview(desk, sharma_order.id)

    assert [line["line_number"] for line in body["lines"]] == [1, 2]
    detergent, biscuit = body["lines"]
    assert detergent["sku"] == DETERGENT
    assert detergent["stock_required"] == 5
    assert detergent["stored_discount_amount"] == "304.50"
    assert detergent["current_discount_amount"] == "304.50"
    assert detergent["current_line_total"] == "9845.50"
    assert biscuit["stock_required"] == 11
    assert biscuit["stored_free_quantity"] == biscuit["current_free_quantity"] == 1
    assert biscuit["stored_unit_price"] == biscuit["current_unit_price"] == "777.60"
    assert biscuit["current_subtotal"] == "7776.00"
    assert biscuit["stored_scheme_name"] == biscuit["current_scheme_name"]
    assert biscuit["price_changed"] is False

    assert body["issues"] == []
    assert body["has_price_changes"] is False
    assert body["subtotal"] == "17926.00"
    assert body["discount_total"] == "304.50"
    assert body["grand_total"] == "17621.50"
    assert body["totals_cover_all_lines"] is True
    assert body["available_credit"] == "38000.00"
    assert body["exceeds_credit_limit"] is False
    assert body["can_confirm"] is True
    assert HEX64.fullmatch(body["fingerprint"])
    assert body["tax_treatment"] == "exclusive"


def test_preview_reports_price_changes_and_line_issues(desk, db_session, sharma_order):
    price_row(db_session, SHARMA_PHONE, BISCUIT).unit_price = Decimal("800.00")
    product_by_sku(db_session, DETERGENT).stock_quantity = 0
    db_session.flush()

    body = preview(desk, sharma_order.id)

    (biscuit,) = body["lines"]
    assert biscuit["line_number"] == 2
    assert biscuit["current_unit_price"] == "800.00"
    assert biscuit["price_changed"] is True
    assert body["has_price_changes"] is True
    (issue,) = body["issues"]
    assert issue["line_number"] == 1
    assert issue["sku"] == DETERGENT
    assert issue["code"] == "insufficient_stock"
    assert body["totals_cover_all_lines"] is False
    assert body["can_confirm"] is False
    assert body["fingerprint"] is None


def test_preview_errors(desk, db_session, sharma_order):
    assert desk.get(f"{DESK}/{uuid4()}/preview").status_code == 404

    retailer_by_phone(db_session, SHARMA_PHONE).is_active = False
    db_session.flush()
    inactive = desk.get(f"{DESK}/{sharma_order.id}/preview")
    assert inactive.status_code == 409

    retailer_by_phone(db_session, SHARMA_PHONE).is_active = True
    sharma_order.status = OrderStatus.CONFIRMED
    db_session.flush()
    assert desk.get(f"{DESK}/{sharma_order.id}/preview").status_code == 409


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def test_confirm_records_the_signed_in_desk_user(desk, db_session, sharma_order):
    desk_user = user_by_phone(db_session, DESK_PHONE)

    response = preview_and_confirm(desk, sharma_order.id)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "confirmed"
    assert body["required_credit"] is None
    order = body["order"]
    assert order["status"] == "confirmed"
    assert order["confirmed_by_user_id"] == str(desk_user.id)
    assert [e["event_type"] for e in order["events"]] == ["submitted", "confirmed"]
    confirmed = order["events"][1]
    assert confirmed["actor_user_id"] == str(desk_user.id)
    assert confirmed["actor_name"] == "Order Desk Operator"
    assert confirmed["details"]["grand_total"] == "17621.50"

    db_session.expire_all()
    assert product_by_sku(db_session, BISCUIT).stock_quantity == 329
    assert product_by_sku(db_session, DETERGENT).stock_quantity == 80
    assert retailer_by_phone(db_session, SHARMA_PHONE).outstanding_balance == (
        Decimal("29621.50")
    )


def test_credit_limit_places_order_on_hold_without_deduction(desk, db_session):
    # 13 detergent: 26390.00 - 3% = 25598.30 > Patel's 25000.00 available.
    order = place_order(db_session, PATEL_PHONE, [(DETERGENT, 13)])

    response = preview_and_confirm(desk, order.id)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "on_hold_credit_limit"
    assert body["required_credit"] == "25598.30"
    assert body["available_credit"] == "25000.00"
    assert body["order"]["status"] == "on_hold"
    assert body["order"]["confirmed_at"] is None

    hold = body["order"]["events"][-1]
    assert hold["event_type"] == "placed_on_hold"
    assert hold["from_status"] == "pending_review"
    assert hold["to_status"] == "on_hold"
    assert hold["actor_name"] == "Order Desk Operator"
    assert hold["details"]["required_credit"] == "25598.30"
    assert hold["details"]["available_credit"] == "25000.00"
    assert hold["details"]["shortfall"] == "598.30"
    assert "confirmed" not in event_types(db_session, order.id)

    db_session.expire_all()
    assert product_by_sku(db_session, DETERGENT).stock_quantity == 85
    assert retailer_by_phone(db_session, PATEL_PHONE).outstanding_balance == (
        Decimal("5000.00")
    )


def test_stale_fingerprint_returns_a_refreshed_preview(desk, db_session, sharma_order):
    approved = preview(desk, sharma_order.id)["fingerprint"]
    price_row(db_session, SHARMA_PHONE, BISCUIT).unit_price = Decimal("800.00")
    db_session.commit()  # must survive the rollback of the failed request

    stale = confirm(desk, sharma_order.id, approved)

    assert stale.status_code == 409
    body = stale.json()
    assert "Prices changed" in body["detail"]
    fresh = body["preview"]
    assert fresh["fingerprint"] != approved
    assert HEX64.fullmatch(fresh["fingerprint"])
    assert fresh["has_price_changes"] is True
    assert fresh["lines"][1]["current_unit_price"] == "800.00"
    assert event_types(db_session, sharma_order.id) == ["submitted"]

    again = confirm(desk, sharma_order.id, fresh["fingerprint"])

    assert again.status_code == 200
    assert again.json()["outcome"] == "confirmed"


def test_confirm_with_line_issues_returns_422(desk, db_session, sharma_order):
    approved = preview(desk, sharma_order.id)["fingerprint"]
    product_by_sku(db_session, DETERGENT).stock_quantity = 0
    db_session.flush()

    response = confirm(desk, sharma_order.id, approved)

    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "Some lines cannot be confirmed. Nothing was changed."
    assert [(i["line_number"], i["code"]) for i in body["issues"]] == [
        (1, "insufficient_stock")
    ]
    assert desk.get(f"{DESK}/{sharma_order.id}").json()["status"] == "pending_review"


def test_already_confirmed_records_no_second_event(desk, db_session, sharma_order):
    fingerprint = preview(desk, sharma_order.id)["fingerprint"]
    assert confirm(desk, sharma_order.id, fingerprint).status_code == 200

    again = confirm(desk, sharma_order.id, fingerprint)

    assert again.status_code == 200
    assert again.json()["outcome"] == "already_confirmed"
    assert event_types(db_session, sharma_order.id) == ["submitted", "confirmed"]


def test_confirm_error_mappings(desk, db_session, sharma_order):
    assert confirm(desk, uuid4(), "a" * 64).status_code == 404

    for fingerprint in ("a" * 63, "g" * 64, ""):
        assert confirm(desk, sharma_order.id, fingerprint).status_code == 422

    with_actor = desk.post(
        f"{DESK}/{sharma_order.id}/confirm",
        json={"approved_fingerprint": "a" * 64, "confirmed_by_user_id": str(uuid4())},
    )
    assert with_actor.status_code == 422

    assert reject(desk, sharma_order.id).status_code == 200
    rejected = confirm(desk, sharma_order.id, "a" * 64)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "This order is not awaiting review."}


def test_confirm_for_inactive_retailer_is_409(desk, db_session, sharma_order):
    fingerprint = preview(desk, sharma_order.id)["fingerprint"]
    retailer_by_phone(db_session, SHARMA_PHONE).is_active = False
    db_session.flush()

    response = confirm(desk, sharma_order.id, fingerprint)

    assert response.status_code == 409
    assert "inactive" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"reason": ""},
        {"reason": "   "},
        {"reason": "ab"},
        {"reason": "  ab  "},
        {"reason": "x" * 501},
        {},
        {"reason": "Valid reason", "actor_user_id": str(uuid4())},
    ],
)
def test_rejection_reason_is_validated(desk, db_session, sharma_order, body):
    response = desk.post(f"{DESK}/{sharma_order.id}/reject", json=body)

    assert response.status_code == 422
    assert event_types(db_session, sharma_order.id) == ["submitted"]


def test_reject_pending_order(desk, db_session, sharma_order):
    desk_user = user_by_phone(db_session, DESK_PHONE)

    response = reject(desk, sharma_order.id, "  Duplicate of another order.  ")

    assert response.status_code == 200
    body = response.json()
    assert body["already_rejected"] is False
    assert body["rejection"]["event_type"] == "rejected"
    assert body["rejection"]["reason"] == "Duplicate of another order."
    assert body["rejection"]["from_status"] == "pending_review"
    assert body["rejection"]["actor_user_id"] == str(desk_user.id)
    assert body["rejection"]["actor_name"] == "Order Desk Operator"
    assert body["order"]["status"] == "rejected"
    assert [e["event_type"] for e in body["order"]["events"]] == [
        "submitted",
        "rejected",
    ]

    db_session.expire_all()
    assert product_by_sku(db_session, BISCUIT).stock_quantity == 340
    assert retailer_by_phone(db_session, SHARMA_PHONE).outstanding_balance == (
        Decimal("12000.00")
    )


def test_reject_order_on_hold(desk, db_session):
    order = place_order(db_session, PATEL_PHONE, [(DETERGENT, 13)])
    assert preview_and_confirm(desk, order.id).json()["outcome"] == (
        "on_hold_credit_limit"
    )

    response = reject(desk, order.id, "Credit not cleared.")

    assert response.status_code == 200
    assert response.json()["rejection"]["from_status"] == "on_hold"
    assert event_types(db_session, order.id) == [
        "submitted",
        "placed_on_hold",
        "rejected",
    ]


def test_rejecting_twice_is_idempotent(desk, db_session, sharma_order):
    first = reject(desk, sharma_order.id, "First reason").json()

    again = reject(desk, sharma_order.id, "Second reason")

    assert again.status_code == 200
    body = again.json()
    assert body["already_rejected"] is True
    assert body["rejection"]["id"] == first["rejection"]["id"]
    assert body["rejection"]["reason"] == "First reason"
    assert event_types(db_session, sharma_order.id).count("rejected") == 1


@pytest.mark.parametrize("final_status", ["confirmed", "exported"])
def test_confirmed_or_exported_orders_cannot_be_rejected(
    desk, db_session, sharma_order, final_status
):
    assert preview_and_confirm(desk, sharma_order.id).status_code == 200
    if final_status == "exported":
        db_session.get(Order, sharma_order.id).status = OrderStatus.EXPORTED
        db_session.flush()

    response = reject(desk, sharma_order.id)

    assert response.status_code == 409
    assert response.json() == {"detail": "This order can no longer be rejected."}
    assert "rejected" not in event_types(db_session, sharma_order.id)


def test_reject_works_for_an_inactive_retailer(desk, db_session, sharma_order):
    retailer_by_phone(db_session, SHARMA_PHONE).is_active = False
    db_session.flush()

    response = reject(desk, sharma_order.id, "Account closed.")

    assert response.status_code == 200
    assert response.json()["order"]["status"] == "rejected"
    assert response.json()["order"]["retailer"]["is_active"] is False


def test_rejecting_a_missing_order_is_404(desk):
    assert reject(desk, uuid4()).status_code == 404


# ---------------------------------------------------------------------------
# Submission audit events
# ---------------------------------------------------------------------------


def test_submissions_and_repeats_write_one_event_each(client, db_session):
    login(client, SHARMA_PHONE)
    sharma_user = user_by_phone(db_session, SHARMA_PHONE)
    request_id = str(uuid4())
    biscuit_id = str(product_by_sku(db_session, BISCUIT).id)
    body = {
        "client_request_id": request_id,
        "items": [{"product_id": biscuit_id, "quantity": 2}],
    }

    created = client.post(RETAILER_ORDERS, json=body)
    retried = client.post(RETAILER_ORDERS, json=body)
    order_id = created.json()["id"]

    assert (created.status_code, retried.status_code) == (201, 200)
    (submitted,) = events_of(db_session, order_id)
    assert submitted.event_type == "submitted"
    assert submitted.from_status is None
    assert submitted.to_status == "pending_review"
    assert submitted.actor_user_id == sharma_user.id
    assert submitted.details == {"client_request_id": request_id}

    repeat_body = {"client_request_id": str(uuid4())}
    repeated = client.post(f"{RETAILER_ORDERS}/{order_id}/repeat", json=repeat_body)
    retried_repeat = client.post(
        f"{RETAILER_ORDERS}/{order_id}/repeat", json=repeat_body
    )
    new_id = repeated.json()["id"]

    assert (repeated.status_code, retried_repeat.status_code) == (201, 200)
    (repeat_event,) = events_of(db_session, new_id)
    assert repeat_event.event_type == "submitted"
    assert repeat_event.details["repeat_of_order_id"] == order_id
    assert len(events_of(db_session, order_id)) == 1


# ---------------------------------------------------------------------------
# Rollback failure
# ---------------------------------------------------------------------------


def test_failed_rollback_becomes_a_generic_server_error(
    client, db_session, sharma_order, monkeypatch
):
    with TestClient(
        client.app,
        base_url="http://testserver",
        headers={"Origin": TRUSTED_ORIGIN},
        raise_server_exceptions=False,
    ) as server_errors:
        login(server_errors, DESK_PHONE)

        def failing_rollback():
            raise SQLAlchemyError("simulated rollback failure: secret-db-detail")

        # A missing order makes reject_order raise; its rollback then fails.
        monkeypatch.setattr(db_session, "rollback", failing_rollback)
        response = server_errors.post(
            f"{DESK}/{uuid4()}/reject", json={"reason": "Missing order"}
        )

    assert response.status_code == 500
    assert "secret-db-detail" not in response.text
    assert "Order not found" not in response.text
