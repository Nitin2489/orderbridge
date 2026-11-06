"""Tests for the Tally-oriented CSV export (Unit C2).

The first part tests the pure CSV helpers with hand-built inputs. The second
part tests the service and /api/v1/desk/exports against the dedicated test
database through db_session (tests/conftest.py), with orders placed and
confirmed through the real retailer and confirmation services. Setup is
committed so a failed request (which rolls back) cannot remove it; in the
fixture's savepoint mode that commit still ends in the test's rollback.
"""

import csv
import hashlib
import io
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.models import (
    ExportBatch,
    Order,
    OrderEvent,
    OrderStatus,
    Product,
    Retailer,
    User,
)
from app.services import tally_export_service
from app.services.order_service import confirm_order, preview_order_confirmation
from app.services.retailer_order_service import OrderLineInput, submit_order
from app.services.tally_export_service import (
    CSV_COLUMNS,
    TAX_TREATMENT,
    ExportLine,
    ExportOrder,
    build_rows,
    create_export,
    format_money,
    protect_text,
    render_csv,
)
from seed import seed_all

# ---------------------------------------------------------------------------
# Pure CSV helpers
# ---------------------------------------------------------------------------

BOM = "﻿"


def parse(content: str) -> list[list[str]]:
    assert content.startswith(BOM)
    return list(csv.reader(io.StringIO(content[len(BOM) :], newline="")))


def export_line(**overrides) -> ExportLine:
    values = {
        "line_number": 1,
        "sku": "SKU-BS-075",
        "item_name": "Glucose Biscuit 75g",
        "quantity": 10,
        "free_quantity": 1,
        "unit": "case",
        "rate": Decimal("777.60"),
        "discount_amount": Decimal("0.00"),
        "net_amount": Decimal("7776.00"),
        "scheme_name": "Buy 10 cases, get 1 free",
    }
    values.update(overrides)
    return ExportLine(**values)


def export_order(**overrides) -> ExportOrder:
    values = {
        "order_number": "OB-2026-000001",
        "created_at": datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
        "confirmed_at": datetime(2026, 11, 2, 9, 5, 30, tzinfo=UTC),
        "party_name": "Sharma General Store",
        "party_address": "Wright Town, Jabalpur",
        "subtotal": Decimal("17926.00"),
        "discount_total": Decimal("304.50"),
        "net_total": Decimal("17621.50"),
        "lines": (
            export_line(
                line_number=2,
                sku="SKU-DT-1KG",
                item_name="Detergent Powder 1kg",
                quantity=5,
                free_quantity=0,
                rate=Decimal("2030.00"),
                discount_amount=Decimal("304.50"),
                net_amount=Decimal("9845.50"),
                scheme_name="3% off on 5 or more cases",
            ),
            export_line(),
        ),
    }
    values.update(overrides)
    return ExportOrder(**values)


def test_header_is_exact_and_first():
    content = render_csv([])

    assert content == BOM + ",".join(CSV_COLUMNS) + "\r\n"
    assert list(CSV_COLUMNS) == [
        "export_batch_number",
        "order_number",
        "order_date",
        "confirmed_at",
        "party_name",
        "party_address",
        "line_number",
        "sku",
        "item_name",
        "quantity",
        "free_quantity",
        "unit",
        "rate",
        "gross_amount",
        "discount_amount",
        "net_amount",
        "scheme_name",
        "order_subtotal",
        "order_discount_total",
        "order_net_total",
        "tax_treatment",
    ]


def test_one_row_per_line_with_exact_values():
    rows = build_rows("EX-2026-000001", [export_order()])

    assert rows == [
        [
            "EX-2026-000001",
            "OB-2026-000001",
            "02-11-2026",
            "2026-11-02T14:35:30+05:30",
            "Sharma General Store",
            "Wright Town, Jabalpur",
            "1",
            "SKU-BS-075",
            "Glucose Biscuit 75g",
            "10",
            "1",
            "case",
            "777.60",
            "7776.00",
            "0.00",
            "7776.00",
            "Buy 10 cases, get 1 free",
            "17926.00",
            "304.50",
            "17621.50",
            TAX_TREATMENT,
        ],
        [
            "EX-2026-000001",
            "OB-2026-000001",
            "02-11-2026",
            "2026-11-02T14:35:30+05:30",
            "Sharma General Store",
            "Wright Town, Jabalpur",
            "2",
            "SKU-DT-1KG",
            "Detergent Powder 1kg",
            "5",
            "0",
            "case",
            "2030.00",
            "10150.00",  # gross = net 9845.50 + discount 304.50
            "304.50",
            "9845.50",
            "3% off on 5 or more cases",
            "17926.00",
            "304.50",
            "17621.50",
            TAX_TREATMENT,
        ],
    ]
    assert TAX_TREATMENT == "exclusive (GST not calculated)"


def test_dates_use_the_indian_business_date():
    # 19:00 UTC on 31 December is 00:30 on 1 January in India.
    order = export_order(
        created_at=datetime(2026, 12, 31, 19, 0, tzinfo=UTC),
        confirmed_at=datetime(2026, 12, 31, 19, 0, tzinfo=UTC),
    )

    (row, _) = build_rows("EX-2027-000001", [order])

    assert row[2] == "01-01-2027"
    assert row[3] == "2027-01-01T00:30:00+05:30"


def test_money_is_fixed_point_and_never_rounded_silently():
    assert format_money(Decimal(5)) == "5.00"
    assert format_money(Decimal("1234567.8")) == "1234567.80"
    assert format_money(Decimal("0.00")) == "0.00"

    with pytest.raises(ValueError, match="two decimal places"):
        format_money(Decimal("1.005"))


def test_bom_crlf_and_quoting():
    order = export_order(
        party_name='Sharma "Best" Store, Jabalpur',
        party_address="12 MG Road\nNapier Town",
        lines=(export_line(),),
    )

    content = render_csv(build_rows("EX-2026-000001", [order]))
    data = content.encode("utf-8")

    assert data.startswith(b"\xef\xbb\xbf")
    assert content.endswith("\r\n")
    assert content.count("\r\n") == 2  # header + one row
    assert '"Sharma ""Best"" Store, Jabalpur"' in content
    (_, row) = parse(content)
    assert row[4] == 'Sharma "Best" Store, Jabalpur'
    assert row[5] == "12 MG Road\nNapier Town"


@pytest.mark.parametrize("value", ["=SUM(A1:A9)", "+91 900", "-2+3", "@cmd", "\tx"])
def test_formula_like_text_is_neutralised(value):
    order = export_order(
        party_name=value,
        party_address=value,
        lines=(export_line(item_name=value, scheme_name=value),),
    )

    (_, row) = parse(render_csv(build_rows("EX-2026-000001", [order])))

    for column in ("party_name", "party_address", "item_name", "scheme_name"):
        assert row[CSV_COLUMNS.index(column)] == "'" + value
    # Numbers are untouched.
    assert row[CSV_COLUMNS.index("rate")] == "777.60"
    assert row[CSV_COLUMNS.index("quantity")] == "10"


def test_protect_text_leaves_ordinary_text_and_maps_none():
    assert protect_text("Sharma General Store") == "Sharma General Store"
    assert protect_text("SKU-BS-075") == "SKU-BS-075"
    assert protect_text(None) == ""


def test_missing_scheme_is_an_empty_cell():
    order = export_order(lines=(export_line(scheme_name=None),))

    (row,) = build_rows("EX-2026-000001", [order])

    assert row[CSV_COLUMNS.index("scheme_name")] == ""


def test_multiple_orders_and_lines_and_unit_snapshot():
    first = export_order()
    second = export_order(
        order_number="OB-2026-000002",
        lines=(export_line(unit="pack", quantity=3),),
    )

    rows = build_rows("EX-2026-000001", [first, second])

    assert [(row[1], row[6]) for row in rows] == [
        ("OB-2026-000001", "1"),
        ("OB-2026-000001", "2"),
        ("OB-2026-000002", "1"),
    ]
    assert rows[2][CSV_COLUMNS.index("unit")] == "pack"


# ---------------------------------------------------------------------------
# Service and API: fixtures and helpers
# ---------------------------------------------------------------------------

AUTH = "/api/v1/auth"
EXPORTS = "/api/v1/desk/exports"
TRUSTED_ORIGIN = "http://localhost:3000"

SHARMA_PHONE = "+919000000001"
GUPTA_PHONE = "+919000000002"
DESK_PHONE = "+919000000010"
BISCUIT = "SKU-BS-075"
DETERGENT = "SKU-DT-1KG"

BASE_TIME = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
BATCH_NUMBER = re.compile(r"EX-\d{4}-\d{6,}")


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="testing",
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        auth_secret_key="export-test-secret-0123456789abcdef0123",
        auth_session_cookie_secure=False,
        auth_debug_otp=True,
        frontend_origins=[TRUSTED_ORIGIN],
    )


@pytest.fixture
def client(db_session):
    seed_all(db_session)
    db_session.commit()
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


def place(
    db_session,
    phone: str,
    lines: list[tuple[str, int]],
    *,
    minutes: int = 0,
    notes=None,
) -> Order:
    order = submit_order(
        db_session,
        retailer_by_phone(db_session, phone),
        [
            OrderLineInput(
                product_id=product_by_sku(db_session, sku).id, quantity=quantity
            )
            for sku, quantity in lines
        ],
        client_request_id=uuid4(),
        notes=notes,
        now=BASE_TIME + timedelta(minutes=minutes),
    ).order
    db_session.commit()
    return order


def confirmed(
    db_session,
    phone: str,
    lines: list[tuple[str, int]],
    *,
    minutes: int = 0,
    confirmed_at: datetime | None = None,
    notes=None,
) -> Order:
    """A confirmed order, via the real submission and confirmation services."""
    order = place(db_session, phone, lines, minutes=minutes, notes=notes)
    preview = preview_order_confirmation(db_session, order.id)
    confirm_order(
        db_session,
        order.id,
        approved_fingerprint=preview.fingerprint,
        confirmed_by_user_id=user_by_phone(db_session, DESK_PHONE).id,
    )
    if confirmed_at is not None:
        order.confirmed_at = confirmed_at
    db_session.commit()
    return order


def export(client, **body):
    payload = {"client_request_id": str(uuid4()), **body}
    return client.post(EXPORTS, json=payload)


def exported_events(db_session, order_id) -> list[OrderEvent]:
    return list(
        db_session.scalars(
            select(OrderEvent).where(
                OrderEvent.order_id == order_id, OrderEvent.event_type == "exported"
            )
        )
    )


def batch_count(db_session) -> int:
    return db_session.scalar(select(func.count(ExportBatch.id)))


def status_of(db_session, order_id) -> str:
    db_session.expire_all()
    return db_session.get(Order, order_id).status


@pytest.fixture
def two_confirmed(db_session, client):
    """Sharma 10 biscuit (7776.00) and Gupta 5 detergent (9845.50)."""
    return (
        confirmed(db_session, SHARMA_PHONE, [(BISCUIT, 10)], minutes=0),
        confirmed(db_session, GUPTA_PHONE, [(DETERGENT, 5)], minutes=1),
    )


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def _requests():
    batch_id = uuid4()
    return [
        ("get", f"{EXPORTS}/eligible", {}),
        ("post", EXPORTS, {"json": {"client_request_id": str(uuid4())}}),
        ("get", EXPORTS, {}),
        ("get", f"{EXPORTS}/{batch_id}", {}),
        ("get", f"{EXPORTS}/{batch_id}/csv", {}),
    ]


def test_export_endpoints_require_the_order_desk(client):
    for method, path, kwargs in _requests():
        assert getattr(client, method)(path, **kwargs).status_code == 401, path

    login(client, SHARMA_PHONE)
    for method, path, kwargs in _requests():
        assert getattr(client, method)(path, **kwargs).status_code == 403, path


def test_export_post_requires_a_trusted_origin(desk, db_session, two_confirmed):
    response = desk.post(
        EXPORTS,
        json={"client_request_id": str(uuid4())},
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert batch_count(db_session) == 0


# ---------------------------------------------------------------------------
# Eligibility and creation
# ---------------------------------------------------------------------------


def test_eligible_summary_counts_only_confirmed_unexported(
    desk, db_session, two_confirmed
):
    place(db_session, SHARMA_PHONE, [(BISCUIT, 1)], minutes=5)  # pending

    body = desk.get(f"{EXPORTS}/eligible").json()

    assert body == {"order_count": 2, "net_total": "17621.50"}


def test_export_all_eligible_orders(desk, db_session, two_confirmed):
    pending = place(db_session, SHARMA_PHONE, [(BISCUIT, 1)], minutes=5)
    desk_user = user_by_phone(db_session, DESK_PHONE)

    response = export(desk)

    assert response.status_code == 201
    body = response.json()
    assert BATCH_NUMBER.fullmatch(body["batch_number"])
    assert body["order_count"] == 2
    assert body["net_total"] == "17621.50"
    assert body["created_by_name"] == "Order Desk Operator"
    assert body["csv_url"] == f"{EXPORTS}/{body['id']}/csv"

    for order in two_confirmed:
        assert status_of(db_session, order.id) == OrderStatus.EXPORTED
        assert db_session.get(Order, order.id).export_batch_id == UUID(body["id"])
        (event,) = exported_events(db_session, order.id)
        assert event.from_status == "confirmed"
        assert event.to_status == "exported"
        assert event.actor_user_id == desk_user.id
        assert event.details == {
            "batch_id": body["id"],
            "batch_number": body["batch_number"],
            "order_net_total": format(order.grand_total, "f"),
        }
    assert status_of(db_session, pending.id) == OrderStatus.PENDING_REVIEW
    assert desk.get(f"{EXPORTS}/eligible").json()["order_count"] == 0


def test_persisted_csv_matches_download_and_checksum(desk, db_session, two_confirmed):
    body = export(desk).json()
    batch = db_session.get(ExportBatch, UUID(body["id"]))

    download = desk.get(body["csv_url"])

    assert download.status_code == 200
    assert download.content == batch.csv_content.encode("utf-8")
    assert hashlib.sha256(download.content).hexdigest() == body["csv_sha256"]
    assert download.headers["content-type"] == "text/csv; charset=utf-8"
    assert download.headers["content-disposition"] == (
        f'attachment; filename="orderbridge-tally-{body["batch_number"]}.csv"'
    )
    assert download.headers["cache-control"] == "no-store"

    header, *rows = parse(download.content.decode("utf-8"))
    assert header == list(CSV_COLUMNS)
    assert [(row[4], row[8], row[11], row[15]) for row in rows] == [
        ("Sharma General Store", "Glucose Biscuit 75g", "case", "7776.00"),
        ("Gupta Kirana Store", "Detergent Powder 1kg", "case", "9845.50"),
    ]


def test_csv_contains_no_private_or_credit_information(desk, db_session):
    confirmed(db_session, SHARMA_PHONE, [(BISCUIT, 10)], notes="Gate code 4321")
    body = export(desk).json()

    text = desk.get(body["csv_url"]).content.decode("utf-8")

    for private in (
        "+919000000001",
        "Amit Sharma",  # owner / user name
        "50000",  # credit limit
        "12000",  # outstanding balance before confirmation
        "Gate code 4321",  # order notes
        "Order Desk Operator",
        str(user_by_phone(db_session, DESK_PHONE).id),
    ):
        assert private not in text


def test_export_filtered_by_confirmed_before(desk, db_session):
    early = confirmed(db_session, SHARMA_PHONE, [(BISCUIT, 1)], confirmed_at=BASE_TIME)
    late = confirmed(
        db_session,
        GUPTA_PHONE,
        [(BISCUIT, 1)],
        minutes=1,
        confirmed_at=BASE_TIME + timedelta(hours=2),
    )

    body = export(
        desk, confirmed_before=(BASE_TIME + timedelta(hours=1)).isoformat()
    ).json()

    assert body["order_count"] == 1
    assert status_of(db_session, early.id) == OrderStatus.EXPORTED
    assert status_of(db_session, late.id) == OrderStatus.CONFIRMED


def test_export_explicit_order_ids(desk, db_session, two_confirmed):
    sharma_order, gupta_order = two_confirmed

    body = export(desk, order_ids=[str(sharma_order.id)]).json()

    assert body["order_count"] == 1
    assert status_of(db_session, sharma_order.id) == OrderStatus.EXPORTED
    assert status_of(db_session, gupta_order.id) == OrderStatus.CONFIRMED
    detail = desk.get(f"{EXPORTS}/{body['id']}").json()
    assert [order["order_number"] for order in detail["orders"]] == [
        sharma_order.order_number
    ]
    assert "csv_content" not in detail


def test_ineligible_explicit_ids_roll_back_everything(desk, db_session, two_confirmed):
    sharma_order, gupta_order = two_confirmed
    pending = place(db_session, SHARMA_PHONE, [(BISCUIT, 1)], minutes=5)
    missing = uuid4()
    first = export(desk, order_ids=[str(gupta_order.id)]).json()  # now exported

    response = export(
        desk,
        order_ids=[
            str(sharma_order.id),
            str(pending.id),
            str(missing),
            str(gupta_order.id),
        ],
    )

    assert response.status_code == 409
    reasons = {
        entry["order_id"]: entry["reason"] for entry in response.json()["ineligible"]
    }
    assert reasons == {
        str(pending.id): "status_pending_review",
        str(missing): "not_found",
        str(gupta_order.id): "already_exported",
    }
    # The eligible order was not exported either: nothing is skipped.
    assert status_of(db_session, sharma_order.id) == OrderStatus.CONFIRMED
    assert exported_events(db_session, sharma_order.id) == []
    assert batch_count(db_session) == 1
    assert db_session.get(Order, gupta_order.id).export_batch_id == UUID(first["id"])


def test_cutoff_applies_to_explicit_ids(desk, db_session):
    late = confirmed(
        db_session,
        SHARMA_PHONE,
        [(BISCUIT, 1)],
        confirmed_at=BASE_TIME + timedelta(hours=2),
    )

    response = export(
        desk,
        order_ids=[str(late.id)],
        confirmed_before=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )

    assert response.status_code == 409
    assert response.json()["ineligible"][0]["reason"] == "confirmed_after_cutoff"


@pytest.mark.parametrize(
    "body",
    [
        {"order_ids": [str(uuid4()) for _ in range(501)]},
        {"order_ids": []},
        {"order_ids": ["not-a-uuid"]},
        {"confirmed_before": "2026-11-02T09:00:00"},  # no timezone
        {"status": "confirmed"},  # unknown field
    ],
)
def test_malformed_export_requests_are_rejected(desk, db_session, two_confirmed, body):
    assert export(desk, **body).status_code == 422
    assert batch_count(db_session) == 0


def test_duplicate_order_ids_are_rejected(desk, db_session, two_confirmed):
    order_id = str(two_confirmed[0].id)

    assert export(desk, order_ids=[order_id, order_id]).status_code == 422


def test_nothing_eligible_returns_422(desk, db_session):
    place(db_session, SHARMA_PHONE, [(BISCUIT, 1)])  # pending only

    response = export(desk)

    assert response.status_code == 422
    assert response.json() == {"detail": "No confirmed orders are waiting for export."}
    assert batch_count(db_session) == 0


# ---------------------------------------------------------------------------
# Idempotency, history and downloads
# ---------------------------------------------------------------------------


def test_retry_returns_the_original_batch(desk, db_session, two_confirmed):
    request_id = str(uuid4())

    first = desk.post(EXPORTS, json={"client_request_id": request_id})
    again = desk.post(EXPORTS, json={"client_request_id": request_id})

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert batch_count(db_session) == 1


def test_reused_id_with_a_different_request_conflicts(desk, db_session, two_confirmed):
    request_id = str(uuid4())
    desk.post(
        EXPORTS,
        json={"client_request_id": request_id, "order_ids": [str(two_confirmed[0].id)]},
    )

    response = desk.post(
        EXPORTS,
        json={"client_request_id": request_id, "order_ids": [str(two_confirmed[1].id)]},
    )

    assert response.status_code == 409
    assert "client_request_id" in response.json()["detail"]
    assert batch_count(db_session) == 1
    assert status_of(db_session, two_confirmed[1].id) == OrderStatus.CONFIRMED


def test_redownload_is_identical_and_changes_nothing(desk, db_session, two_confirmed):
    body = export(desk).json()
    events_before = db_session.scalar(select(func.count(OrderEvent.id)))

    first = desk.get(body["csv_url"])
    second = desk.get(body["csv_url"])

    assert first.content == second.content
    assert db_session.scalar(select(func.count(OrderEvent.id))) == events_before
    assert batch_count(db_session) == 1


def test_history_is_newest_first(desk, db_session, two_confirmed):
    first = export(desk, order_ids=[str(two_confirmed[0].id)]).json()
    second = export(desk, order_ids=[str(two_confirmed[1].id)]).json()

    page = desk.get(EXPORTS).json()

    assert [item["id"] for item in page["items"]] == [second["id"], first["id"]]
    assert page["total"] == 2
    assert page["items"][0]["created_by_name"] == "Order Desk Operator"
    assert desk.get(f"{EXPORTS}/{uuid4()}").status_code == 404
    assert desk.get(f"{EXPORTS}/{uuid4()}/csv").status_code == 404


def test_batch_number_uses_the_indian_business_year(client, db_session):
    order = confirmed(db_session, SHARMA_PHONE, [(BISCUIT, 1)])

    result = create_export(
        db_session,
        exported_by_user_id=user_by_phone(db_session, DESK_PHONE).id,
        client_request_id=uuid4(),
        order_ids=[order.id],
        now=datetime(2026, 12, 31, 19, 0, tzinfo=UTC),  # 1 January in India
    )

    assert result.batch.batch_number.startswith("EX-2027-")


def test_failed_export_rolls_back_every_change(
    client, db_session, two_confirmed, monkeypatch
):
    calls = []
    real_record = tally_export_service.record_order_event

    def fail_on_second_event(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("simulated failure while exporting")
        return real_record(*args, **kwargs)

    monkeypatch.setattr(
        tally_export_service, "record_order_event", fail_on_second_event
    )

    with TestClient(
        client.app,
        base_url="http://testserver",
        headers={"Origin": TRUSTED_ORIGIN},
        raise_server_exceptions=False,
    ) as server_errors:
        login(server_errors, DESK_PHONE)
        response = server_errors.post(EXPORTS, json={"client_request_id": str(uuid4())})

    assert response.status_code == 500
    assert "simulated" not in response.text
    assert batch_count(db_session) == 0
    for order in two_confirmed:
        assert status_of(db_session, order.id) == OrderStatus.CONFIRMED
        assert db_session.get(Order, order.id).export_batch_id is None
        assert exported_events(db_session, order.id) == []
