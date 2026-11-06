"""Tests for the seeded product catalogue (backend/seed.py).

Every test uses the db_session fixture (tests/conftest.py): it runs against
the dedicated test database, and all changes are rolled back afterwards.

Assertions are split into two kinds:
- Client products: the three products from the client brief, pinned to
  hand-written expected values. Other products may exist alongside them.
- Seed catalogue: row counts derived from the seed definitions, so adding
  synthetic products, retailers or schemes to the seed does not break them.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
    Product,
    ProductStatus,
    RetailerProductPrice,
    Scheme,
    SchemeType,
)
from seed import LEGACY_SKUS, PRODUCTS, RETAILERS, SCHEMES, seed_all

# Written out from the client brief's sample data table, independently of
# seed.py, so an accidental change to a client product fails this test.
CLIENT_PRODUCTS = {
    "SKU-BS-075": {
        "name": "Glucose Biscuit 75g",
        "units_per_case": 96,
        "stock_quantity": 340,
        "case_price": Decimal("777.60"),
    },
    "SKU-DT-1KG": {
        "name": "Detergent Powder 1kg",
        "units_per_case": 20,
        "stock_quantity": 85,
        "case_price": Decimal("2030.00"),
    },
    "SKU-TE-250": {
        "name": "Tea 250g",
        "units_per_case": 40,
        "stock_quantity": 0,
        "case_price": Decimal("4720.00"),
    },
}

CLIENT_SCHEMES = {
    "SKU-BS-075": {
        "scheme_type": SchemeType.FREE_GOODS,
        "minimum_quantity": 10,
        "free_quantity": 1,
        "discount_percent": Decimal("0.00"),
    },
    "SKU-DT-1KG": {
        "scheme_type": SchemeType.SLAB_DISCOUNT,
        "minimum_quantity": 5,
        "free_quantity": 0,
        "discount_percent": Decimal("3.00"),
    },
}

SEED_SKUS = {data["sku"] for data in PRODUCTS}


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def products_by_sku(session) -> dict[str, Product]:
    return {p.sku: p for p in session.scalars(select(Product))}


def assert_seed_catalogue_counts(session) -> None:
    """The database holds exactly what the seed definitions describe."""
    assert set(products_by_sku(session)) == SEED_SKUS
    assert count(session, Product) == len(PRODUCTS)
    assert count(session, RetailerProductPrice) == len(PRODUCTS) * len(RETAILERS)
    assert count(session, Scheme) == len(SCHEMES)


def test_client_products_match_brief(db_session):
    seed_all(db_session)

    products = products_by_sku(db_session)
    assert set(CLIENT_PRODUCTS) <= set(products)

    for sku, expected in CLIENT_PRODUCTS.items():
        product = products[sku]
        assert product.name == expected["name"]
        assert product.unit_name == "case"
        assert product.units_per_case == expected["units_per_case"]
        assert product.stock_quantity == expected["stock_quantity"]
        assert product.status == ProductStatus.ACTIVE

        prices = db_session.scalars(
            select(RetailerProductPrice).where(
                RetailerProductPrice.product_id == product.id
            )
        ).all()
        assert len(prices) == len(RETAILERS)
        assert all(p.unit_price == expected["case_price"] for p in prices)
        assert all(p.is_active for p in prices)

        schemes = db_session.scalars(
            select(Scheme).where(Scheme.product_id == product.id)
        ).all()
        if sku in CLIENT_SCHEMES:
            assert len(schemes) == 1
            scheme = schemes[0]
            for field, value in CLIENT_SCHEMES[sku].items():
                assert getattr(scheme, field) == value
            assert scheme.is_active
        else:
            assert schemes == []


def test_seed_catalogue_counts_match_seed_definitions(db_session):
    seed_all(db_session)

    assert_seed_catalogue_counts(db_session)


def test_seed_rerun_is_idempotent(db_session):
    seed_all(db_session)
    first_ids = {sku: p.id for sku, p in products_by_sku(db_session).items()}

    seed_all(db_session)

    assert {sku: p.id for sku, p in products_by_sku(db_session).items()} == (first_ids)
    assert_seed_catalogue_counts(db_session)


def test_seed_renames_legacy_skus_in_place(db_session):
    seed_all(db_session)
    original_ids = {sku: p.id for sku, p in products_by_sku(db_session).items()}

    # Simulate a database seeded by the old script: only products that had
    # a legacy code are renamed back to it.
    for sku, product in products_by_sku(db_session).items():
        if sku in LEGACY_SKUS:
            product.sku = LEGACY_SKUS[sku]
    db_session.flush()
    assert not set(LEGACY_SKUS) & set(products_by_sku(db_session))

    seed_all(db_session)

    products = products_by_sku(db_session)
    assert {sku: products[sku].id for sku in LEGACY_SKUS} == {
        sku: original_ids[sku] for sku in LEGACY_SKUS
    }
    assert not set(LEGACY_SKUS.values()) & set(products)
    assert_seed_catalogue_counts(db_session)


def test_seed_fails_when_legacy_and_replacement_sku_both_exist(db_session):
    seed_all(db_session)

    db_session.add(
        Product(
            sku=LEGACY_SKUS["SKU-BS-075"],
            name="Glucose Biscuit 75g",
            brand="Sample Foods",
            category="Biscuits",
            units_per_case=96,
            stock_quantity=0,
        )
    )
    db_session.flush()

    with pytest.raises(RuntimeError, match="SKU-BS-075.*GB-075"):
        seed_all(db_session)
