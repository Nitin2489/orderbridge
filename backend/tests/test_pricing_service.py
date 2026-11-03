from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Product, Retailer
from app.services.pricing_service import (
    InsufficientStockError,
    calculate_retailer_product_price,
)


def get_test_retailer_and_product():
    with SessionLocal() as session:
        retailer = session.scalar(
            select(Retailer).where(
                Retailer.business_name == "Sharma General Store"
            )
        )
        product = session.scalar(
            select(Product).where(Product.sku == "GB-075")
        )

        assert retailer is not None
        assert product is not None

        return retailer.id, product.id


def test_retailer_price_and_free_goods_scheme():
    retailer_id, product_id = get_test_retailer_and_product()

    with SessionLocal() as session:
        result = calculate_retailer_product_price(
            session,
            retailer_id=retailer_id,
            product_id=product_id,
            quantity=10,
        )

    assert result.unit_price == Decimal("777.60")
    assert result.free_quantity == 1
    assert result.stock_required == 11
    assert result.grand_total == Decimal("7776.00")


def test_insufficient_stock_rejects_pricing():
    with SessionLocal() as session:
        retailer = session.scalar(
            select(Retailer).where(
                Retailer.business_name == "Sharma General Store"
            )
        )
        product = session.scalar(
            select(Product).where(Product.sku == "TE-250")
        )

        assert retailer is not None
        assert product is not None

        with pytest.raises(InsufficientStockError):
            calculate_retailer_product_price(
                session,
                retailer_id=retailer.id,
                product_id=product.id,
                quantity=1,
            )
