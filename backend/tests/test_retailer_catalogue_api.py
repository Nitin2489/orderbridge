"""HTTP tests for /api/v1/retailer/catalogue.

The app is built with create_app(test settings) and get_db is overridden to
yield the rolled-back db_session (tests/conftest.py), so every request uses
the dedicated test database. seed_all() provides the brief's catalogue:

    Detergent Powder 1kg  SKU-DT-1KG  Home Care  2030.00  stock 85   3% off 5+
    Glucose Biscuit 75g   SKU-BS-075  Biscuits    777.60  stock 340  buy 10 get 1
    Tea 250g              SKU-TE-250  Beverages  4720.00  stock 0    no scheme

All three are priced for all three demo retailers.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import get_db
from app.main import create_app
from app.models import (
    Product,
    ProductStatus,
    Retailer,
    RetailerProductPrice,
    Scheme,
    SchemeType,
    User,
)
from app.services.otp_delivery import InMemoryOtpDelivery
from seed import seed_all

AUTH = "/api/v1/auth"
CATALOGUE = "/api/v1/retailer/catalogue"
TRUSTED_ORIGIN = "http://localhost:3000"

SHARMA_PHONE = "+919000000001"
GUPTA_PHONE = "+919000000002"
DESK_PHONE = "+919000000010"

BISCUIT = "SKU-BS-075"
DETERGENT = "SKU-DT-1KG"
TEA = "SKU-TE-250"

SEEDED_ORDER = [DETERGENT, BISCUIT, TEA]  # by product name


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="testing",
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        auth_secret_key="catalogue-test-secret-0123456789abcdef01",
        auth_otp_resend_cooldown_seconds=60,
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


def scheme_for(db_session, sku: str) -> Scheme:
    scheme = db_session.scalar(
        select(Scheme).where(Scheme.product_id == product_by_sku(db_session, sku).id)
    )
    assert scheme is not None
    return scheme


def add_product(
    db_session,
    *,
    sku: str,
    name: str,
    category: str = "Snacks",
    brand: str = "Test Brand",
    stock: int = 10,
    priced_for: tuple[str, ...] = (SHARMA_PHONE,),
    unit_price: Decimal = Decimal("100.00"),
    price_active: bool = True,
    status: ProductStatus = ProductStatus.ACTIVE,
) -> Product:
    product = Product(
        sku=sku,
        name=name,
        description=None,
        brand=brand,
        category=category,
        unit_name="case",
        units_per_case=12,
        stock_quantity=stock,
        status=status,
    )
    db_session.add(product)
    db_session.flush()

    for phone in priced_for:
        db_session.add(
            RetailerProductPrice(
                retailer_id=retailer_by_phone(db_session, phone).id,
                product_id=product.id,
                unit_price=unit_price,
                is_active=price_active,
            )
        )

    db_session.flush()
    return product


def catalogue_skus(client: TestClient, **params) -> list[str]:
    response = client.get(CATALOGUE, params=params)
    assert response.status_code == 200, response.text
    return [item["sku"] for item in response.json()["items"]]


def item_by_sku(client: TestClient, sku: str) -> dict:
    items = client.get(CATALOGUE).json()["items"]
    return next(item for item in items if item["sku"] == sku)


# ---------------------------------------------------------------------------
# Authentication and authorization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", [CATALOGUE, f"{CATALOGUE}/categories", f"{CATALOGUE}/{uuid4()}"]
)
def test_unauthenticated_requests_are_rejected(client, path):
    assert client.get(path).status_code == 401


def test_retailer_can_read_catalogue(sharma):
    response = sharma.get(CATALOGUE)

    assert response.status_code == 200
    assert response.json()["total"] == 3


@pytest.mark.parametrize(
    "path", [CATALOGUE, f"{CATALOGUE}/categories", f"{CATALOGUE}/{uuid4()}"]
)
def test_order_desk_cannot_use_retailer_endpoints(client, path):
    login(client, DESK_PHONE)

    assert client.get(path).status_code == 403


def test_inactive_retailer_cannot_read_catalogue(sharma, db_session):
    retailer_by_phone(db_session, SHARMA_PHONE).is_active = False
    db_session.flush()

    # The session itself is no longer valid for a deactivated retailer.
    assert sharma.get(CATALOGUE).status_code == 401
    assert sharma.get(f"{CATALOGUE}/categories").status_code == 401


def test_retailer_sees_only_their_own_prices(sharma, db_session):
    price_row(db_session, GUPTA_PHONE, BISCUIT).unit_price = Decimal("700.00")
    db_session.flush()

    item = item_by_sku(sharma, BISCUIT)
    detail = sharma.get(f"{CATALOGUE}/{item['id']}").json()

    assert item["unit_price"] == "777.60"
    assert detail["unit_price"] == "777.60"
    assert "700.00" not in sharma.get(CATALOGUE).text


def test_retailer_id_in_request_is_ignored(sharma, db_session):
    gupta = retailer_by_phone(db_session, GUPTA_PHONE)
    price_row(db_session, GUPTA_PHONE, BISCUIT).unit_price = Decimal("700.00")
    db_session.flush()

    response = sharma.get(
        CATALOGUE,
        params={"retailer_id": str(gupta.id)},
        headers={"X-Retailer-Id": str(gupta.id)},
    )

    biscuit = next(i for i in response.json()["items"] if i["sku"] == BISCUIT)
    assert biscuit["unit_price"] == "777.60"


def test_responses_do_not_expose_retailer_or_user_ids(sharma, db_session):
    sharma_retailer = retailer_by_phone(db_session, SHARMA_PHONE)

    body = sharma.get(CATALOGUE).text

    assert str(sharma_retailer.id) not in body
    assert str(sharma_retailer.user_id) not in body
    assert "retailer_id" not in body


# ---------------------------------------------------------------------------
# Catalogue contents
# ---------------------------------------------------------------------------


def test_active_priced_products_are_returned_with_prices(sharma):
    response = sharma.get(CATALOGUE)
    items = {item["sku"]: item for item in response.json()["items"]}

    assert list(items) == SEEDED_ORDER
    assert items[BISCUIT]["unit_price"] == "777.60"
    assert items[DETERGENT]["unit_price"] == "2030.00"
    assert items[TEA]["unit_price"] == "4720.00"
    assert items[BISCUIT]["name"] == "Glucose Biscuit 75g"
    assert items[BISCUIT]["brand"] == "Sample Foods"
    assert items[BISCUIT]["category"] == "Biscuits"
    assert items[BISCUIT]["unit_name"] == "case"
    assert items[BISCUIT]["units_per_case"] == 96


def test_inactive_products_are_excluded(sharma, db_session):
    product_by_sku(db_session, TEA).status = ProductStatus.INACTIVE
    db_session.flush()

    assert catalogue_skus(sharma) == [DETERGENT, BISCUIT]


def test_products_without_a_price_for_this_retailer_are_excluded(sharma, db_session):
    add_product(db_session, sku="SKU-NP-001", name="Unpriced Item", priced_for=())
    add_product(
        db_session,
        sku="SKU-GU-001",
        name="Gupta Only Item",
        priced_for=(GUPTA_PHONE,),
    )

    assert catalogue_skus(sharma) == SEEDED_ORDER


def test_inactive_retailer_prices_are_excluded(sharma, db_session):
    price_row(db_session, SHARMA_PHONE, DETERGENT).is_active = False
    db_session.flush()

    assert catalogue_skus(sharma) == [BISCUIT, TEA]


def test_out_of_stock_products_remain_visible(sharma):
    tea = item_by_sku(sharma, TEA)

    assert tea["stock_quantity"] == 0
    assert tea["is_available"] is False


def test_in_stock_products_are_available(sharma):
    biscuit = item_by_sku(sharma, BISCUIT)

    assert biscuit["stock_quantity"] == 340
    assert biscuit["is_available"] is True


def test_current_schemes_are_returned_with_terms_and_reference(sharma, db_session):
    items = {item["sku"]: item for item in sharma.get(CATALOGUE).json()["items"]}
    biscuit_scheme = scheme_for(db_session, BISCUIT)

    free_goods = items[BISCUIT]["scheme"]
    assert free_goods["id"] == str(biscuit_scheme.id)
    assert free_goods["name"] == "Buy 10 cases, get 1 free"
    assert free_goods["scheme_type"] == SchemeType.FREE_GOODS
    assert free_goods["minimum_quantity"] == 10
    assert free_goods["free_quantity"] == 1
    assert free_goods["discount_percent"] == "0.00"
    assert free_goods["starts_at"] is not None
    assert free_goods["reference"] == {
        "quantity": 10,
        "free_quantity": 1,
        "subtotal": "7776.00",
        "discount_amount": "0.00",
        "illustrative_total": "7776.00",
    }

    slab = items[DETERGENT]["scheme"]
    assert slab["scheme_type"] == SchemeType.SLAB_DISCOUNT
    assert slab["minimum_quantity"] == 5
    assert slab["free_quantity"] == 0
    assert slab["discount_percent"] == "3.00"
    assert slab["reference"] == {
        "quantity": 5,
        "free_quantity": 0,
        "subtotal": "10150.00",
        "discount_amount": "304.50",
        "illustrative_total": "9845.50",
    }
    assert "total" not in slab["reference"]


def test_products_without_a_scheme_return_null(sharma):
    assert item_by_sku(sharma, TEA)["scheme"] is None


@pytest.mark.parametrize("state", ["expired", "future", "inactive"])
def test_non_applicable_schemes_are_excluded(sharma, db_session, state):
    scheme = scheme_for(db_session, BISCUIT)
    now = datetime.now(UTC)

    if state == "expired":
        scheme.starts_at = now - timedelta(days=10)
        scheme.ends_at = now - timedelta(days=1)
    elif state == "future":
        scheme.starts_at = now + timedelta(days=1)
        scheme.ends_at = now + timedelta(days=10)
    else:
        scheme.is_active = False
    db_session.flush()

    listed = item_by_sku(sharma, BISCUIT)
    detail = sharma.get(f"{CATALOGUE}/{listed['id']}").json()

    for body in (listed, detail):
        assert body["scheme"] is None
        assert body["scheme_status"] == "none"


def add_second_active_scheme(db_session, sku: str) -> None:
    """A second scheme applicable now, making the product misconfigured."""
    db_session.add(
        Scheme(
            product_id=product_by_sku(db_session, sku).id,
            name="Buy 20 cases, get 3 free",
            scheme_type=SchemeType.FREE_GOODS,
            minimum_quantity=20,
            free_quantity=3,
            discount_percent=Decimal("0.00"),
            starts_at=datetime.now(UTC) - timedelta(days=1),
            ends_at=None,
            is_active=True,
        )
    )
    db_session.flush()


@pytest.mark.parametrize(
    ("sku", "status"),
    [(BISCUIT, "active"), (DETERGENT, "active"), (TEA, "none")],
)
def test_scheme_status_in_list_and_detail(sharma, sku, status):
    listed = item_by_sku(sharma, sku)
    detail = sharma.get(f"{CATALOGUE}/{listed['id']}").json()

    for body in (listed, detail):
        assert body["scheme_status"] == status
        assert (body["scheme"] is not None) == (status == "active")


def test_conflicting_schemes_report_configuration_error(sharma, db_session):
    # Never chooses one: pricing_service.get_active_scheme() raises for this.
    add_second_active_scheme(db_session, BISCUIT)

    listed = item_by_sku(sharma, BISCUIT)
    detail = sharma.get(f"{CATALOGUE}/{listed['id']}").json()

    for body in (listed, detail):
        assert body["scheme_status"] == "configuration_error"
        assert body["scheme"] is None
        assert body["unit_price"] == "777.60"

    # Other products are unaffected.
    assert item_by_sku(sharma, DETERGENT)["scheme_status"] == "active"


def test_conflicting_schemes_do_not_hide_the_product(sharma, db_session):
    add_second_active_scheme(db_session, BISCUIT)

    assert catalogue_skus(sharma) == SEEDED_ORDER


def test_prices_are_marked_gst_exclusive(sharma, db_session):
    add_second_active_scheme(db_session, BISCUIT)
    items = sharma.get(CATALOGUE).json()["items"]
    details = [sharma.get(f"{CATALOGUE}/{item['id']}").json() for item in items]

    assert len(items) == 3
    assert all(item["tax_treatment"] == "exclusive" for item in items + details)
    # Prices are unchanged: no GST is added.
    assert {item["sku"]: item["unit_price"] for item in items} == {
        BISCUIT: "777.60",
        DETERGENT: "2030.00",
        TEA: "4720.00",
    }


def test_gst_exclusion_is_documented_in_openapi(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]

    tax = schema["CatalogueProduct"]["properties"]["tax_treatment"]
    reference_total = schema["SchemeReference"]["properties"]["illustrative_total"]

    assert tax["const"] == "exclusive" or tax.get("enum") == ["exclusive"]
    assert "GST" in tax["description"]
    assert "invoicing" in tax["description"]
    assert "not an order quote" in reference_total["description"].lower()
    assert "GST" in reference_total["description"]
    assert "total" not in schema["SchemeReference"]["properties"]


# ---------------------------------------------------------------------------
# Search, categories and empty results
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("search", "expected"),
    [
        ("glucose", [BISCUIT]),
        ("BISCUIT", [BISCUIT]),
        ("sku-dt", [DETERGENT]),
        ("TE-250", [TEA]),
        ("sample beverages", [TEA]),
        ("Sample", SEEDED_ORDER),
    ],
)
def test_search_matches_name_sku_or_brand(sharma, search, expected):
    assert catalogue_skus(sharma, search=search) == expected


def test_search_wildcards_are_literal(sharma):
    assert catalogue_skus(sharma, search="%") == []
    assert catalogue_skus(sharma, search="_") == []


def test_category_filter(sharma):
    assert catalogue_skus(sharma, category="Home Care") == [DETERGENT]
    assert catalogue_skus(sharma, category="home care") == [DETERGENT]


def test_search_and_category_together(sharma, db_session):
    add_product(
        db_session, sku="SKU-SN-001", name="Glucose Snack Bar", category="Snacks"
    )

    assert catalogue_skus(sharma, search="glucose", category="Biscuits") == [BISCUIT]
    assert catalogue_skus(sharma, search="glucose", category="Snacks") == ["SKU-SN-001"]


def test_empty_results(sharma):
    response = sharma.get(CATALOGUE, params={"search": "no-such-product"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert catalogue_skus(sharma, category="No Such Category") == []


def test_categories_are_distinct_and_sorted(sharma, db_session):
    add_product(db_session, sku="SKU-BS-200", name="Cream Biscuit", category="Biscuits")

    response = sharma.get(f"{CATALOGUE}/categories")

    assert response.status_code == 200
    assert response.json() == {"categories": ["Beverages", "Biscuits", "Home Care"]}


def test_categories_exclude_products_the_retailer_cannot_buy(sharma, db_session):
    product_by_sku(db_session, TEA).status = ProductStatus.INACTIVE
    add_product(
        db_session, sku="SKU-NP-002", name="Unpriced", category="Dairy", priced_for=()
    )
    add_product(
        db_session,
        sku="SKU-IP-002",
        name="Inactive Price",
        category="Frozen",
        price_active=False,
    )
    add_product(
        db_session,
        sku="SKU-GU-002",
        name="Gupta Only",
        category="Personal Care",
        priced_for=(GUPTA_PHONE,),
    )

    categories = sharma.get(f"{CATALOGUE}/categories").json()["categories"]

    assert categories == ["Biscuits", "Home Care"]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_default_pagination(sharma):
    body = sharma.get(CATALOGUE).json()

    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_custom_limit_and_offset(sharma):
    body = sharma.get(CATALOGUE, params={"limit": 1, "offset": 1}).json()

    assert body["limit"] == 1
    assert body["offset"] == 1
    assert body["total"] == 3
    assert [item["sku"] for item in body["items"]] == [BISCUIT]


def test_offset_beyond_results_returns_empty_page(sharma):
    body = sharma.get(CATALOGUE, params={"offset": 10}).json()

    assert body["items"] == []
    assert body["total"] == 3


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"limit": "abc"},
        {"offset": -1},
        {"offset": "abc"},
        {"search": "x" * 101},
        {"category": "x" * 101},
    ],
)
def test_invalid_query_parameters_are_rejected(sharma, params):
    assert sharma.get(CATALOGUE, params=params).status_code == 422


def test_ordering_is_stable_by_name_then_id(sharma, db_session):
    twins = [
        add_product(db_session, sku=f"SKU-TW-00{n}", name="Twin Product")
        for n in range(1, 4)
    ]
    expected = [str(p.id) for p in sorted(twins, key=lambda p: p.id)]

    one_per_page = []
    for offset in range(3):
        params = {"search": "Twin", "limit": 1, "offset": offset}
        (item,) = sharma.get(CATALOGUE, params=params).json()["items"]
        one_per_page.append(item["id"])

    all_at_once = sharma.get(CATALOGUE, params={"search": "Twin"}).json()["items"]

    assert one_per_page == expected
    assert [item["id"] for item in all_at_once] == expected


# ---------------------------------------------------------------------------
# Product detail
# ---------------------------------------------------------------------------


def test_product_detail(sharma, db_session):
    biscuit = product_by_sku(db_session, BISCUIT)

    response = sharma.get(f"{CATALOGUE}/{biscuit.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(biscuit.id)
    assert body["sku"] == BISCUIT
    assert body["unit_price"] == "777.60"
    assert body["unit_name"] == "case"
    assert body["units_per_case"] == 96
    assert body["stock_quantity"] == 340
    assert body["is_available"] is True
    assert body["tax_treatment"] == "exclusive"
    assert body["scheme_status"] == "active"
    assert body["scheme"]["name"] == "Buy 10 cases, get 1 free"
    assert body["scheme"]["reference"]["illustrative_total"] == "7776.00"


def test_out_of_stock_product_detail(sharma, db_session):
    tea = product_by_sku(db_session, TEA)

    body = sharma.get(f"{CATALOGUE}/{tea.id}").json()

    assert body["stock_quantity"] == 0
    assert body["is_available"] is False
    assert body["scheme"] is None
    assert body["scheme_status"] == "none"
    assert body["tax_treatment"] == "exclusive"


def test_missing_product_returns_404(sharma):
    response = sharma.get(f"{CATALOGUE}/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Product not found."}


def test_invalid_product_id_returns_422(sharma):
    assert sharma.get(f"{CATALOGUE}/not-a-uuid").status_code == 422


def test_inactive_product_returns_404(sharma, db_session):
    tea = product_by_sku(db_session, TEA)
    tea.status = ProductStatus.INACTIVE
    db_session.flush()

    assert sharma.get(f"{CATALOGUE}/{tea.id}").status_code == 404


@pytest.mark.parametrize("price_state", ["missing", "inactive"])
def test_product_without_active_price_returns_404(sharma, db_session, price_state):
    product = add_product(
        db_session,
        sku="SKU-NP-003",
        name="No Price",
        priced_for=() if price_state == "missing" else (SHARMA_PHONE,),
        price_active=False,
    )

    assert sharma.get(f"{CATALOGUE}/{product.id}").status_code == 404


def test_other_retailers_product_looks_missing(sharma, db_session):
    gupta_only = add_product(
        db_session,
        sku="SKU-GU-003",
        name="Gupta Only",
        priced_for=(GUPTA_PHONE,),
        unit_price=Decimal("555.55"),
    )

    other = sharma.get(f"{CATALOGUE}/{gupta_only.id}")
    missing = sharma.get(f"{CATALOGUE}/{UUID(int=1)}")

    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()
    assert "555.55" not in other.text
