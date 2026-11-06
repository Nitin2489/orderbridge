from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Product, Retailer
from app.services.pricing_service import (
    InsufficientStockError,
    calculate_retailer_product_price,
)
from seed import seed_all


def get_test_retailer_and_product(session):
    retailer = session.scalar(
        select(Retailer).where(Retailer.business_name == "Sharma General Store")
    )
    product = session.scalar(select(Product).where(Product.sku == "SKU-BS-075"))

    assert retailer is not None
    assert product is not None

    return retailer.id, product.id


def test_retailer_price_and_free_goods_scheme(db_session):
    seed_all(db_session)
    retailer_id, product_id = get_test_retailer_and_product(db_session)

    result = calculate_retailer_product_price(
        db_session,
        retailer_id=retailer_id,
        product_id=product_id,
        quantity=10,
    )

    assert result.result.unit_price == Decimal("777.60")
    assert result.result.ordered_quantity == 10
    assert result.result.free_quantity == 1
    assert result.result.stock_required == 11
    assert result.result.subtotal == Decimal("7776.00")
    assert result.result.grand_total == Decimal("7776.00")
    assert result.scheme_name is not None


def test_insufficient_stock_rejects_pricing(db_session):
    seed_all(db_session)

    retailer = db_session.scalar(
        select(Retailer).where(Retailer.business_name == "Sharma General Store")
    )
    product = db_session.scalar(select(Product).where(Product.sku == "SKU-TE-250"))

    assert retailer is not None
    assert product is not None

    with pytest.raises(InsufficientStockError):
        calculate_retailer_product_price(
            db_session,
            retailer_id=retailer.id,
            product_id=product.id,
            quantity=1,
        )
