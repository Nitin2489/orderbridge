from decimal import Decimal

import pytest

from app.models.enums import SchemeType
from app.services.scheme_engine import SchemeInput, calculate_scheme


def test_glucose_buy_10_get_1_free():
    result = calculate_scheme(
        quantity=10,
        unit_price=Decimal("777.60"),
        scheme=SchemeInput(
            scheme_type=SchemeType.FREE_GOODS,
            minimum_quantity=10,
            free_quantity=1,
            discount_percent=Decimal(0),
        ),
    )

    assert result.ordered_quantity == 10
    assert result.free_quantity == 1
    assert result.stock_required == 11
    assert result.subtotal == Decimal("7776.00")
    assert result.grand_total == Decimal("7776.00")


def test_detergent_slab_discount():
    result = calculate_scheme(
        quantity=5,
        unit_price=Decimal("2030.00"),
        scheme=SchemeInput(
            scheme_type=SchemeType.SLAB_DISCOUNT,
            minimum_quantity=5,
            free_quantity=0,
            discount_percent=Decimal(3),
        ),
    )

    assert result.subtotal == Decimal("10150.00")
    assert result.discount_amount == Decimal("304.50")
    assert result.grand_total == Decimal("9845.50")
    assert result.stock_required == 5


def test_scheme_not_applied_below_threshold():
    result = calculate_scheme(
        quantity=4,
        unit_price=Decimal("2030.00"),
        scheme=SchemeInput(
            scheme_type=SchemeType.SLAB_DISCOUNT,
            minimum_quantity=5,
            free_quantity=0,
            discount_percent=Decimal(3),
        ),
    )

    assert result.discount_amount == Decimal("0.00")
    assert result.grand_total == Decimal("8120.00")


def test_free_goods_repeat_for_multiple_thresholds():
    result = calculate_scheme(
        quantity=20,
        unit_price=Decimal("777.60"),
        scheme=SchemeInput(
            scheme_type=SchemeType.FREE_GOODS,
            minimum_quantity=10,
            free_quantity=1,
            discount_percent=Decimal(0),
        ),
    )

    assert result.free_quantity == 2
    assert result.stock_required == 22


def test_zero_quantity_is_rejected():
    with pytest.raises(ValueError, match="greater than zero"):
        calculate_scheme(
            quantity=0,
            unit_price=Decimal("100.00"),
            scheme=None,
        )
