"""HTTP tests for /api/v1/retailer/orders (Unit B: retailer ordering).

The app is built with create_app(test settings); get_db is overridden to
yield the rolled-back db_session (tests/conftest.py), so every request uses
the dedicated test database. seed_all() provides the brief's catalogue,
priced for all three demo retailers:

    Glucose Biscuit 75g   SKU-BS-075   777.60  stock 340  buy 10 get 1 free
    Detergent Powder 1kg  SKU-DT-1KG  2030.00  stock 85   3% off 5+
    Tea 250g              SKU-TE-250  4720.00  stock 0    no scheme
"""

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.models import (
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    Product,
    ProductStatus,
    Retailer,
    RetailerProductPrice,
    Scheme,
    User,
)
from app.services.order_service import (
    ConfirmationOutcome,
    confirm_order,
    preview_order_confirmation,
)
from app.services.otp_delivery import InMemoryOtpDelivery
from app.services.retailer_order_service import OrderLineInput, submit_order
from seed import seed_all

AUTH = "/api/v1/auth"
ORDERS = "/api/v1/retailer/orders"
TRUSTED_ORIGIN = "http://localhost:3000"
UNTRUSTED_ORIGIN = "https://attacker.example"

SHARMA_PHONE = "+919000000001"
GUPTA_PHONE = "+919000000002"
DESK_PHONE = "+919000000010"

BISCUIT = "SKU-BS-075"
DETERGENT = "SKU-DT-1KG"
TEA = "SKU-TE-250"

ORDER_NUMBER = re.compile(r"OB-\d{4}-\d{6,}")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="testing",
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        auth_secret_key="orders-test-secret-0123456789abcdef0123",
        auth_session_cookie_secure=False,
        auth_debug_otp=False,
        frontend_origins=[TRUSTED_ORIGIN],
    )


@pytest.fixture
def client(db_session):
    seed_all(db_session)
    app = create_app(make_settings(), otp_delivery=InMemoryOtpDelivery())

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
    # Codes are never in responses; the test delivery channel records them.
    code = client.app.state.otp_delivery.latest_code(phone)
    verified = client.post(
        f"{AUTH}/verify-otp", json={"phone_number": phone, "code": code}
    )
    assert verified.status_code == 200, verified.text


@pytest.fixture
def sharma(client):
    """The client, signed in as Sharma General Store."""
    login(client, SHARMA_PHONE)
    return client


def product_by_sku(db_session, sku: str) -> Product:
    product = db_session.scalar(select(Product).where(Product.sku == sku))
    assert product is not None
    return product


def retailer_by_phone(db_session, phone: str) -> Retailer:
    retailer = db_session.scalar(
        select(Retailer).join(User).where(User.phone_number == phone)
    )
    assert retailer is not None
    return retailer


def price_row(db_session, phone: str, sku: str) -> RetailerProductPrice:
    row = db_session.scalar(
        select(RetailerProductPrice).where(
            RetailerProductPrice.retailer_id == retailer_by_phone(db_session, phone).id,
            RetailerProductPrice.product_id == product_by_sku(db_session, sku).id,
        )
    )
    assert row is not None
    return row


def order_body(
    db_session, lines: list[tuple[str, int]], *, request_id=None, notes=None
) -> dict:
    body = {
        "client_request_id": str(request_id or uuid4()),
        "items": [
            {"product_id": str(product_by_sku(db_session, sku).id), "quantity": qty}
            for sku, qty in lines
        ],
    }
    if notes is not None:
        body["notes"] = notes
    return body


def submit(client, db_session, lines, *, request_id=None, notes=None):
    return client.post(
        ORDERS, json=order_body(db_session, lines, request_id=request_id, notes=notes)
    )


def submitted(client, db_session, lines, **kwargs) -> dict:
    response = submit(client, db_session, lines, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model))


def lines_by_sku(order: dict) -> dict[str, dict]:
    return {line["sku"]: line for line in order["items"]}


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def test_submit_creates_pending_order_priced_by_the_server(sharma, db_session):
    response = submit(
        sharma, db_session, [(BISCUIT, 10), (DETERGENT, 5)], notes="  Deliver AM  "
    )

    assert response.status_code == 201
    order = response.json()
    assert ORDER_NUMBER.fullmatch(order["order_number"])
    assert order["status"] == "pending_review"
    assert order["source"] == "retailer_app"
    assert order["notes"] == "Deliver AM"
    assert order["item_count"] == 2
    assert order["confirmed_at"] is None
    assert order["tax_treatment"] == "exclusive"

    lines = lines_by_sku(order)
    assert lines[BISCUIT] == {
        "line_number": 1,
        "product_id": str(product_by_sku(db_session, BISCUIT).id),
        "sku": BISCUIT,
        "product_name": "Glucose Biscuit 75g",
        "quantity": 10,
        "free_quantity": 1,
        "unit_price": "777.60",
        "discount_amount": "0.00",
        "line_total": "7776.00",
        "scheme_name": "Buy 10 cases, get 1 free",
    }
    assert lines[DETERGENT]["line_number"] == 2
    assert lines[DETERGENT]["free_quantity"] == 0
    assert lines[DETERGENT]["discount_amount"] == "304.50"
    assert lines[DETERGENT]["line_total"] == "9845.50"
    assert lines[DETERGENT]["scheme_name"] == "3% off on 5 or more cases"

    assert order["subtotal"] == "17926.00"
    assert order["discount_total"] == "304.50"
    assert order["grand_total"] == "17621.50"

    stored = db_session.get(Order, UUID(order["id"]))
    assert stored.retailer_id == retailer_by_phone(db_session, SHARMA_PHONE).id
    assert stored.grand_total == Decimal("17621.50")


@pytest.mark.parametrize(
    ("quantity", "free", "line_total", "scheme_name"),
    [
        (9, 0, "6998.40", "Buy 10 cases, get 1 free"),  # below the threshold
        (20, 2, "15552.00", "Buy 10 cases, get 1 free"),  # free goods repeat
    ],
)
def test_free_goods_follow_the_scheme(
    sharma, db_session, quantity, free, line_total, scheme_name
):
    (line,) = submitted(sharma, db_session, [(BISCUIT, quantity)])["items"]

    assert line["free_quantity"] == free
    assert line["line_total"] == line_total
    assert line["scheme_name"] == scheme_name


def test_submission_does_not_deduct_stock_or_credit(sharma, db_session):
    retailer = retailer_by_phone(db_session, SHARMA_PHONE)
    balance = retailer.outstanding_balance

    submitted(sharma, db_session, [(BISCUIT, 10), (DETERGENT, 5)])
    db_session.expire_all()

    assert product_by_sku(db_session, BISCUIT).stock_quantity == 340
    assert product_by_sku(db_session, DETERGENT).stock_quantity == 85
    assert retailer_by_phone(db_session, SHARMA_PHONE).outstanding_balance == balance


@pytest.mark.parametrize(
    ("where", "field", "value"),
    [
        ("order", "status", "confirmed"),
        ("order", "retailer_id", str(uuid4())),
        ("order", "grand_total", "1.00"),
        ("order", "order_number", "OB-2026-999999"),
        ("line", "unit_price", "0.01"),
        ("line", "free_quantity", 100),
        ("line", "discount_amount", "500.00"),
        ("line", "line_total", "1.00"),
    ],
)
def test_client_cannot_supply_prices_status_or_identity(
    sharma, db_session, where, field, value
):
    body = order_body(db_session, [(BISCUIT, 10)])
    target = body if where == "order" else body["items"][0]
    target[field] = value

    response = sharma.post(ORDERS, json=body)

    assert response.status_code == 422
    assert count(db_session, Order) == 0


def test_lines_keep_the_submitted_order(sharma, db_session):
    order = submitted(sharma, db_session, [(DETERGENT, 1), (BISCUIT, 1)])

    assert [(line["line_number"], line["sku"]) for line in order["items"]] == [
        (1, DETERGENT),
        (2, BISCUIT),
    ]
    detail = sharma.get(f"{ORDERS}/{order['id']}").json()
    assert [line["sku"] for line in detail["items"]] == [DETERGENT, BISCUIT]


def test_order_numbers_are_unique_and_increasing(sharma, db_session):
    numbers = [
        submitted(sharma, db_session, [(BISCUIT, 1)])["order_number"] for _ in range(3)
    ]

    assert len(set(numbers)) == 3
    suffixes = [int(number.rsplit("-", 1)[1]) for number in numbers]
    assert suffixes == sorted(suffixes)


@pytest.mark.parametrize(
    ("now", "year"),
    [
        # 18:30 UTC on 31 December is midnight on 1 January in India.
        (datetime(2026, 12, 31, 18, 30, tzinfo=UTC), 2027),
        # One second earlier it is still 31 December in India.
        (datetime(2026, 12, 31, 18, 29, 59, tzinfo=UTC), 2026),
        (datetime(2027, 1, 1, 0, 0, tzinfo=UTC), 2027),
    ],
)
def test_order_number_year_is_the_indian_business_year(client, db_session, now, year):
    retailer = retailer_by_phone(db_session, SHARMA_PHONE)
    biscuit = product_by_sku(db_session, BISCUIT)

    result = submit_order(
        db_session,
        retailer,
        [OrderLineInput(product_id=biscuit.id, quantity=1)],
        client_request_id=uuid4(),
        now=now,
    )

    assert result.order.order_number.startswith(f"OB-{year}-")
    assert ORDER_NUMBER.fullmatch(result.order.order_number)


def test_order_number_requires_an_aware_timestamp(client, db_session):
    retailer = retailer_by_phone(db_session, SHARMA_PHONE)
    biscuit = product_by_sku(db_session, BISCUIT)

    with pytest.raises(ValueError, match="timezone-aware"):
        submit_order(
            db_session,
            retailer,
            [OrderLineInput(product_id=biscuit.id, quantity=1)],
            client_request_id=uuid4(),
            now=datetime(2026, 12, 31, 23, 0),
        )


def test_failed_submissions_roll_back_and_leave_the_session_usable(sharma, db_session):
    request_id = uuid4()
    submitted(sharma, db_session, [(BISCUIT, 1)], request_id=request_id)

    failures = [
        submit(sharma, db_session, [(TEA, 1)]),  # line issue: 422
        submit(sharma, db_session, [(BISCUIT, 2)], request_id=request_id),  # 409
        sharma.post(
            f"{ORDERS}/{uuid4()}/repeat", json={"client_request_id": str(uuid4())}
        ),  # 404
    ]

    assert [response.status_code for response in failures] == [422, 409, 404]
    # The endpoint rolled back explicitly: no transaction is left open.
    assert db_session.in_transaction() is False

    after = submit(sharma, db_session, [(DETERGENT, 1)])
    assert after.status_code == 201
    assert count(db_session, Order) == 2


def test_failed_rollback_becomes_a_server_error(
    client, db_session, monkeypatch, caplog
):
    def failing_rollback():
        raise SQLAlchemyError("simulated rollback failure: secret-db-detail")

    # A second client on the same app returns 500 responses instead of
    # re-raising them, so the HTTP response can be inspected.
    with TestClient(
        client.app,
        base_url="http://testserver",
        headers={"Origin": TRUSTED_ORIGIN},
        raise_server_exceptions=False,
    ) as server_errors:
        login(server_errors, SHARMA_PHONE)
        body = order_body(db_session, [(TEA, 1)])  # out of stock: service error

        # Only the request's rollback is replaced; the fixture's teardown
        # closes the session and rolls back its own outer transaction.
        monkeypatch.setattr(db_session, "rollback", failing_rollback)
        caplog.set_level(logging.ERROR, logger="app.api.v1.retailer_orders")

        response = server_errors.post(ORDERS, json=body)

    assert response.status_code == 500
    assert "secret-db-detail" not in response.text
    assert "issues" not in response.text
    (record,) = [r for r in caplog.records if r.name == "app.api.v1.retailer_orders"]
    assert "OrderLinesInvalidError" in record.getMessage()
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], SQLAlchemyError)


# ---------------------------------------------------------------------------
# Invalid lines and requests (nothing is created)
# ---------------------------------------------------------------------------


def test_every_invalid_line_is_reported_and_nothing_is_created(sharma, db_session):
    detergent = product_by_sku(db_session, DETERGENT)
    detergent.status = ProductStatus.INACTIVE
    price_row(db_session, SHARMA_PHONE, BISCUIT).is_active = False
    db_session.flush()
    missing_id = uuid4()

    body = order_body(db_session, [(DETERGENT, 1), (BISCUIT, 1), (TEA, 1)])
    body["items"].append({"product_id": str(missing_id), "quantity": 1})
    response = sharma.post(ORDERS, json=body)

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"] == (
        "Some order lines cannot be ordered. No order was created."
    )
    assert [(i["line_number"], i["code"]) for i in payload["issues"]] == [
        (1, "product_unavailable"),
        (2, "price_not_configured"),
        (3, "insufficient_stock"),
        (4, "product_unavailable"),
    ]
    assert payload["issues"][3]["product_id"] == str(missing_id)
    assert count(db_session, Order) == 0
    assert count(db_session, OrderItem) == 0


def test_one_invalid_line_rejects_the_whole_order(sharma, db_session):
    response = submit(sharma, db_session, [(BISCUIT, 10), (TEA, 1)])

    assert response.status_code == 422
    assert [i["code"] for i in response.json()["issues"]] == ["insufficient_stock"]
    assert count(db_session, Order) == 0
    assert count(db_session, OrderItem) == 0


def test_product_priced_only_for_another_retailer_is_rejected(sharma, db_session):
    price_row(db_session, SHARMA_PHONE, DETERGENT).is_active = False
    db_session.flush()

    response = submit(sharma, db_session, [(DETERGENT, 1)])

    assert response.status_code == 422
    assert response.json()["issues"][0]["code"] == "price_not_configured"


def test_free_goods_count_against_stock(sharma, db_session):
    # 340 ordered + 34 free = 374 cases, but only 340 in stock.
    response = submit(sharma, db_session, [(BISCUIT, 340)])

    assert response.status_code == 422
    assert response.json()["issues"][0]["code"] == "insufficient_stock"


def test_duplicate_product_lines_are_rejected(sharma, db_session):
    response = submit(sharma, db_session, [(BISCUIT, 1), (BISCUIT, 2)])

    assert response.status_code == 422
    assert count(db_session, Order) == 0


@pytest.mark.parametrize("quantity", [0, -1, 10_001, "two", 1.5])
def test_invalid_quantities_are_rejected(sharma, db_session, quantity):
    body = order_body(db_session, [(BISCUIT, 1)])
    body["items"][0]["quantity"] = quantity

    assert sharma.post(ORDERS, json=body).status_code == 422


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update(items=[]),
        lambda body: body.update(items=body["items"] * 51),
        lambda body: body.update(notes="x" * 501),
        lambda body: body.pop("client_request_id"),
        lambda body: body.update(client_request_id="not-a-uuid"),
        lambda body: body["items"][0].update(product_id="not-a-uuid"),
    ],
    ids=["empty", "too-many", "long-notes", "no-id", "bad-id", "bad-product"],
)
def test_malformed_requests_are_rejected(sharma, db_session, mutate):
    body = order_body(db_session, [(BISCUIT, 1)])
    mutate(body)

    assert sharma.post(ORDERS, json=body).status_code == 422
    assert count(db_session, Order) == 0


# ---------------------------------------------------------------------------
# Authentication, CSRF and isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", ORDERS),
        ("get", ORDERS),
        ("get", f"{ORDERS}/{uuid4()}"),
        ("post", f"{ORDERS}/{uuid4()}/repeat"),
    ],
)
def test_requires_a_signed_in_retailer(client, db_session, method, path):
    body = {"client_request_id": str(uuid4())}
    if path == ORDERS:
        body = order_body(db_session, [(BISCUIT, 1)])
    kwargs = {"json": body} if method == "post" else {}

    assert getattr(client, method)(path, **kwargs).status_code == 401

    login(client, DESK_PHONE)
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_inactive_retailer_cannot_order(sharma, db_session):
    retailer_by_phone(db_session, SHARMA_PHONE).is_active = False
    db_session.flush()

    assert submit(sharma, db_session, [(BISCUIT, 1)]).status_code == 401


def test_submit_and_repeat_require_a_trusted_origin(sharma, db_session):
    order = submitted(sharma, db_session, [(BISCUIT, 1)])
    untrusted = {"Origin": UNTRUSTED_ORIGIN}

    created = sharma.post(
        ORDERS, json=order_body(db_session, [(BISCUIT, 1)]), headers=untrusted
    )
    repeated = sharma.post(
        f"{ORDERS}/{order['id']}/repeat",
        json={"client_request_id": str(uuid4())},
        headers=untrusted,
    )

    assert created.status_code == 403
    assert repeated.status_code == 403
    assert count(db_session, Order) == 1


def test_retailers_only_see_their_own_orders(client, db_session):
    login(client, GUPTA_PHONE)
    gupta_order = submitted(client, db_session, [(DETERGENT, 1)])

    login(client, SHARMA_PHONE)
    sharma_order = submitted(client, db_session, [(BISCUIT, 1)])

    listing = client.get(ORDERS).json()
    assert [order["id"] for order in listing["items"]] == [sharma_order["id"]]
    assert listing["total"] == 1

    other = client.get(f"{ORDERS}/{gupta_order['id']}")
    missing = client.get(f"{ORDERS}/{uuid4()}")
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json() == {"detail": "Order not found."}


def test_retailer_id_in_query_is_ignored(client, db_session):
    login(client, GUPTA_PHONE)
    submitted(client, db_session, [(DETERGENT, 1)])
    gupta = retailer_by_phone(db_session, GUPTA_PHONE)

    login(client, SHARMA_PHONE)
    listing = client.get(ORDERS, params={"retailer_id": str(gupta.id)}).json()

    assert listing["total"] == 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_resending_the_same_request_returns_the_original_order(sharma, db_session):
    request_id = uuid4()
    lines = [(BISCUIT, 10), (DETERGENT, 5)]

    first = submit(sharma, db_session, lines, request_id=request_id, notes="AM")
    again = submit(sharma, db_session, lines, request_id=request_id, notes="AM")

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json() == first.json()
    assert count(db_session, Order) == 1


def test_request_fingerprint_is_normalised(sharma, db_session):
    request_id = uuid4()

    first = submit(
        sharma, db_session, [(BISCUIT, 10), (DETERGENT, 5)], request_id=request_id
    )
    reordered = submit(
        sharma,
        db_session,
        [(DETERGENT, 5), (BISCUIT, 10)],
        request_id=request_id,
        notes="   ",
    )

    assert reordered.status_code == 200
    assert reordered.json()["id"] == first.json()["id"]


@pytest.mark.parametrize(
    "changed",
    [
        {"lines": [(BISCUIT, 11)]},
        {"lines": [(BISCUIT, 10), (DETERGENT, 1)]},
        {"lines": [(BISCUIT, 10)], "notes": "different"},
    ],
)
def test_reusing_an_id_for_a_different_request_conflicts(sharma, db_session, changed):
    request_id = uuid4()
    submitted(sharma, db_session, [(BISCUIT, 10)], request_id=request_id)

    response = submit(
        sharma,
        db_session,
        changed["lines"],
        request_id=request_id,
        notes=changed.get("notes"),
    )

    assert response.status_code == 409
    assert "client_request_id" in response.json()["detail"]
    assert count(db_session, Order) == 1


def test_a_new_id_creates_a_new_order(sharma, db_session):
    first = submitted(sharma, db_session, [(BISCUIT, 10)])
    second = submitted(sharma, db_session, [(BISCUIT, 10)])

    assert first["id"] != second["id"]
    assert count(db_session, Order) == 2


def test_request_ids_are_scoped_per_retailer(client, db_session):
    request_id = uuid4()

    login(client, SHARMA_PHONE)
    sharma_order = submitted(client, db_session, [(BISCUIT, 1)], request_id=request_id)
    login(client, GUPTA_PHONE)
    gupta_order = submitted(client, db_session, [(BISCUIT, 1)], request_id=request_id)

    assert sharma_order["id"] != gupta_order["id"]


def test_an_id_used_for_a_submission_cannot_be_used_to_repeat(sharma, db_session):
    request_id = uuid4()
    order = submitted(sharma, db_session, [(BISCUIT, 1)], request_id=request_id)

    response = sharma.post(
        f"{ORDERS}/{order['id']}/repeat", json={"client_request_id": str(request_id)}
    )

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# History: list and detail
# ---------------------------------------------------------------------------


def test_orders_are_listed_newest_first_with_pagination(sharma, db_session):
    ids = [submitted(sharma, db_session, [(BISCUIT, n)])["id"] for n in (1, 2, 3)]

    everything = sharma.get(ORDERS).json()
    second_page = sharma.get(ORDERS, params={"limit": 1, "offset": 1}).json()

    assert [order["id"] for order in everything["items"]] == ids[::-1]
    assert everything["total"] == 3
    assert everything["limit"] == 20
    assert everything["offset"] == 0
    assert everything["items"][0]["item_count"] == 1
    assert everything["items"][0]["tax_treatment"] == "exclusive"
    assert "items" not in everything["items"][0]
    assert [order["id"] for order in second_page["items"]] == [ids[1]]
    assert second_page["total"] == 3


def test_orders_can_be_filtered_by_status(sharma, db_session):
    first = submitted(sharma, db_session, [(BISCUIT, 1)])
    submitted(sharma, db_session, [(BISCUIT, 2)])
    db_session.get(Order, UUID(first["id"])).status = OrderStatus.CONFIRMED
    db_session.flush()

    confirmed = sharma.get(ORDERS, params={"status": "confirmed"}).json()
    pending = sharma.get(ORDERS, params={"status": "pending_review"}).json()

    assert [order["id"] for order in confirmed["items"]] == [first["id"]]
    assert pending["total"] == 1


@pytest.mark.parametrize(
    "params",
    [{"status": "shipped"}, {"limit": 0}, {"limit": 101}, {"offset": -1}],
)
def test_invalid_list_parameters_are_rejected(sharma, params):
    assert sharma.get(ORDERS, params=params).status_code == 422


def test_empty_history(sharma):
    assert sharma.get(ORDERS).json() == {
        "items": [],
        "total": 0,
        "limit": 20,
        "offset": 0,
    }


def test_order_detail(sharma, db_session):
    order = submitted(sharma, db_session, [(BISCUIT, 10)], notes="Gate 2")

    detail = sharma.get(f"{ORDERS}/{order['id']}")

    assert detail.status_code == 200
    assert detail.json() == order
    assert detail.json()["tax_treatment"] == "exclusive"


def test_invalid_order_id_is_rejected(sharma):
    assert sharma.get(f"{ORDERS}/not-a-uuid").status_code == 422


# ---------------------------------------------------------------------------
# Repeat ordering
# ---------------------------------------------------------------------------


def repeat(client, order_id: str, request_id=None):
    return client.post(
        f"{ORDERS}/{order_id}/repeat",
        json={"client_request_id": str(request_id or uuid4())},
    )


def test_repeat_uses_current_prices_and_keeps_history(sharma, db_session):
    original = submitted(sharma, db_session, [(BISCUIT, 10), (DETERGENT, 5)])
    price_row(db_session, SHARMA_PHONE, BISCUIT).unit_price = Decimal("800.00")
    db_session.flush()

    response = repeat(sharma, original["id"])

    assert response.status_code == 201
    new = response.json()
    assert new["id"] != original["id"]
    assert new["order_number"] != original["order_number"]
    assert new["status"] == "pending_review"
    assert [line["sku"] for line in new["items"]] == [BISCUIT, DETERGENT]
    assert lines_by_sku(new)[BISCUIT]["unit_price"] == "800.00"
    assert lines_by_sku(new)[BISCUIT]["line_total"] == "8000.00"

    # The original order's snapshot is unchanged.
    history = sharma.get(f"{ORDERS}/{original['id']}").json()
    assert lines_by_sku(history)[BISCUIT]["unit_price"] == "777.60"
    assert history == original


def test_repeat_uses_current_schemes(sharma, db_session):
    original = submitted(sharma, db_session, [(BISCUIT, 10)])
    db_session.scalar(
        select(Scheme).where(
            Scheme.product_id == product_by_sku(db_session, BISCUIT).id
        )
    ).is_active = False
    db_session.flush()

    (line,) = repeat(sharma, original["id"]).json()["items"]

    assert line["free_quantity"] == 0
    assert line["scheme_name"] is None
    assert line["line_total"] == "7776.00"


@pytest.mark.parametrize(
    ("change", "code"),
    [("out_of_stock", "insufficient_stock"), ("inactive", "product_unavailable")],
)
def test_repeat_reports_lines_that_can_no_longer_be_ordered(
    sharma, db_session, change, code
):
    original = submitted(sharma, db_session, [(BISCUIT, 10), (DETERGENT, 5)])
    detergent = product_by_sku(db_session, DETERGENT)
    if change == "out_of_stock":
        detergent.stock_quantity = 0
    else:
        detergent.status = ProductStatus.INACTIVE
    db_session.flush()

    response = repeat(sharma, original["id"])

    assert response.status_code == 422
    assert [(i["line_number"], i["code"]) for i in response.json()["issues"]] == [
        (2, code)
    ]
    assert count(db_session, Order) == 1


def test_any_status_can_be_repeated(sharma, db_session):
    original = submitted(sharma, db_session, [(BISCUIT, 1)])
    db_session.get(Order, UUID(original["id"])).status = OrderStatus.CONFIRMED
    db_session.flush()

    assert repeat(sharma, original["id"]).status_code == 201


def test_repeat_is_idempotent(sharma, db_session):
    original = submitted(sharma, db_session, [(BISCUIT, 1)])
    other = submitted(sharma, db_session, [(DETERGENT, 1)])
    request_id = uuid4()

    first = repeat(sharma, original["id"], request_id)
    again = repeat(sharma, original["id"], request_id)
    different_source = repeat(sharma, other["id"], request_id)

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert different_source.status_code == 409
    assert count(db_session, Order) == 3


def test_repeat_retry_returns_the_original_after_the_source_is_removed(
    sharma, db_session
):
    source = submitted(sharma, db_session, [(BISCUIT, 1)])
    request_id = uuid4()
    first = repeat(sharma, source["id"], request_id)
    assert first.status_code == 201

    # Removing an order is allowed by the schema (its lines cascade); nothing
    # links the repeat to its source by foreign key. Its audit events block
    # deletion (ON DELETE RESTRICT), so this test removes them first.
    db_session.execute(
        delete(OrderEvent).where(OrderEvent.order_id == UUID(source["id"]))
    )
    db_session.delete(db_session.get(Order, UUID(source["id"])))
    db_session.flush()
    assert sharma.get(f"{ORDERS}/{source['id']}").status_code == 404

    retry = repeat(sharma, source["id"], request_id)

    assert retry.status_code == 200
    assert retry.json() == first.json()
    # A new key for the removed source still finds nothing.
    assert repeat(sharma, source["id"]).status_code == 404


def test_cannot_repeat_another_retailers_order(client, db_session):
    login(client, GUPTA_PHONE)
    gupta_order = submitted(client, db_session, [(DETERGENT, 1)])

    login(client, SHARMA_PHONE)
    response = repeat(client, gupta_order["id"])
    missing = repeat(client, str(uuid4()))

    assert response.status_code == missing.status_code == 404
    assert response.json() == missing.json()
    assert count(db_session, Order) == 1


# ---------------------------------------------------------------------------
# Compatibility with the order desk's confirmation service
# ---------------------------------------------------------------------------


def test_submitted_order_confirms_without_price_changes(sharma, db_session):
    order = submitted(sharma, db_session, [(BISCUIT, 10), (DETERGENT, 5)])
    order_id = UUID(order["id"])
    desk = db_session.scalar(select(User).where(User.phone_number == DESK_PHONE))

    preview = preview_order_confirmation(db_session, order_id)

    assert preview.can_confirm is True
    assert preview.price_changes == ()
    assert preview.grand_total == Decimal("17621.50")

    result = confirm_order(
        db_session,
        order_id,
        approved_fingerprint=preview.fingerprint,
        confirmed_by_user_id=desk.id,
    )

    assert result.outcome == ConfirmationOutcome.CONFIRMED
    db_session.expire_all()
    assert product_by_sku(db_session, BISCUIT).stock_quantity == 329
    assert product_by_sku(db_session, DETERGENT).stock_quantity == 80
    assert sharma.get(f"{ORDERS}/{order['id']}").json()["status"] == "confirmed"
