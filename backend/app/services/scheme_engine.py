from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.models.enums import SchemeType

MONEY_PRECISION = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SchemeInput:
    scheme_type: SchemeType
    minimum_quantity: int
    free_quantity: int
    discount_percent: Decimal


@dataclass(frozen=True)
class SchemeResult:
    ordered_quantity: int
    free_quantity: int
    stock_required: int
    unit_price: Decimal
    subtotal: Decimal
    discount_amount: Decimal
    grand_total: Decimal


def calculate_scheme(
    *,
    quantity: int,
    unit_price: Decimal,
    scheme: SchemeInput | None,
) -> SchemeResult:
    if quantity <= 0:
        raise ValueError("Order quantity must be greater than zero.")

    if unit_price < ZERO:
        raise ValueError("Unit price cannot be negative.")

    free_quantity = 0
    discount_percent = ZERO

    if scheme is not None:
        if scheme.minimum_quantity <= 0:
            raise ValueError("Scheme minimum quantity must be positive.")

        if scheme.free_quantity < 0:
            raise ValueError("Free quantity cannot be negative.")

        if not ZERO <= scheme.discount_percent <= Decimal(100):
            raise ValueError("Discount must be between 0 and 100.")

        if quantity >= scheme.minimum_quantity:
            if scheme.scheme_type == SchemeType.FREE_GOODS:
                free_quantity = (
                    quantity // scheme.minimum_quantity
                ) * scheme.free_quantity

            elif scheme.scheme_type == SchemeType.SLAB_DISCOUNT:
                discount_percent = scheme.discount_percent

            else:
                raise ValueError("Unsupported scheme type.")

    subtotal = money(unit_price * quantity)
    discount_amount = money(
        subtotal * discount_percent / Decimal(100)
    )
    grand_total = money(subtotal - discount_amount)

    return SchemeResult(
        ordered_quantity=quantity,
        free_quantity=free_quantity,
        stock_required=quantity + free_quantity,
        unit_price=money(unit_price),
        subtotal=subtotal,
        discount_amount=discount_amount,
        grand_total=grand_total,
    )
