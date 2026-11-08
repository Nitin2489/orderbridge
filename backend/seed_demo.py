"""Optional demonstration orders, placed through the real services.

[Assumption] The brief supplies products, prices, stock and schemes but no
orders. So that the order desk queue, credit holds and the Tally export
are not empty in a demo, this script places a few orders exactly as a
retailer and the order desk would: through retailer_order_service
(pricing, schemes, stock checks) and order_service.confirm_order (stock
deduction, credit, holds). Nothing is written directly.

- Gupta Kirana Store: 12 cases of biscuit (buy 10 get 1 free -> 1 free)
  and 5 cases of detergent (3% off at 5 or more), left pending review.
- Patel Provision Store: 3 cases of biscuit, confirmed by the order desk
  (stock deducted; waiting for the Tally export).
- Patel Provision Store: 15 cases of detergent, which exceeds Patel's
  available credit, so the desk's confirmation places it on hold.
- Gupta Kirana Store: 1 case of detergent, rejected by the order desk.

Sharma General Store gets no orders, so the brief's sample message for
Sharma is matched without order history (biscuit 60, detergent 85, tea 85).

Idempotent: each order uses a fixed client_request_id, so running the
script again returns the existing orders and changes nothing. Run after
seed.py:

    uv run python seed_demo.py
"""

from uuid import UUID, uuid5

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import OrderStatus, Product, Retailer, User
from app.services.desk_order_service import reject_order
from app.services.order_service import confirm_order, preview_order_confirmation
from app.services.retailer_order_service import OrderLineInput, submit_order

NAMESPACE = UUID("2f1d6c3e-9a51-4c47-9d0e-6b8f2a7c1e55")
DESK_PHONE = "+919000000010"

DEMO_ORDERS = [
    {
        "key": "gupta-pending",
        "retailer": "Gupta Kirana Store",
        "lines": [("SKU-BS-075", 12), ("SKU-DT-1KG", 5)],
        "notes": "Please deliver before 11 am.",
        "then": None,
    },
    {
        "key": "patel-confirmed",
        "retailer": "Patel Provision Store",
        "lines": [("SKU-BS-075", 3)],
        "notes": None,
        "then": "confirm",
    },
    {
        "key": "patel-credit-hold",
        "retailer": "Patel Provision Store",
        "lines": [("SKU-DT-1KG", 15)],
        "notes": None,
        "then": "confirm",
    },
    {
        "key": "gupta-rejected",
        "retailer": "Gupta Kirana Store",
        "lines": [("SKU-DT-1KG", 1)],
        "notes": None,
        "then": "reject",
    },
]


def seed_demo_orders(session) -> list[tuple[str, str, str]]:
    """Place the demonstration orders; returns (key, order number, status).

    Commits after each order, as the API would; the caller need not commit.
    """
    desk = session.scalar(select(User).where(User.phone_number == DESK_PHONE))
    if desk is None:
        raise RuntimeError("Run seed.py first: the order desk user is missing.")
    products = {p.sku: p for p in session.scalars(select(Product))}
    results = []

    for spec in DEMO_ORDERS:
        retailer = session.scalar(
            select(Retailer).where(Retailer.business_name == spec["retailer"])
        )
        if retailer is None:
            raise RuntimeError(f"Run seed.py first: {spec['retailer']} is missing.")
        order = submit_order(
            session,
            retailer,
            [
                OrderLineInput(product_id=products[sku].id, quantity=quantity)
                for sku, quantity in spec["lines"]
            ],
            client_request_id=uuid5(NAMESPACE, spec["key"]),
            notes=spec["notes"],
        ).order
        session.commit()

        if order.status == OrderStatus.PENDING_REVIEW and spec["then"] == "confirm":
            preview = preview_order_confirmation(session, order.id)
            confirm_order(
                session,
                order.id,
                approved_fingerprint=preview.fingerprint,
                confirmed_by_user_id=desk.id,
            )
            session.commit()
        elif order.status == OrderStatus.PENDING_REVIEW and spec["then"] == "reject":
            reject_order(
                session,
                order.id,
                reason="Duplicate of an earlier order.",
                rejected_by_user_id=desk.id,
            )
            session.commit()

        session.refresh(order)
        results.append((spec["key"], order.order_number, order.status))
    return results


def main() -> None:
    with SessionLocal() as session:
        for key, number, status in seed_demo_orders(session):
            print(f"{key}: {number} ({status})")


if __name__ == "__main__":
    main()
