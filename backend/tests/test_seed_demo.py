"""seed_demo.py: demonstration orders placed through the real services."""

from decimal import Decimal

from sqlalchemy import func, select

from app.models import Order, Retailer
from seed import seed_all
from seed_demo import seed_demo_orders


def test_demo_orders_reach_the_intended_states_through_the_real_services(db_session):
    seed_all(db_session)
    db_session.commit()

    results = {key: status for key, _number, status in seed_demo_orders(db_session)}

    assert results == {
        "gupta-pending": "pending_review",
        "patel-confirmed": "confirmed",
        "patel-credit-hold": "on_hold",
        "gupta-rejected": "rejected",
    }

    pending = db_session.scalar(select(Order).where(Order.status == "pending_review"))
    lines = {item.sku: item for item in pending.items}
    # Priced by the scheme engine: 12 biscuit cases earn 1 free; 5 detergent
    # cases get 3% off (2030.00 x 5 = 10150.00, less 304.50).
    assert lines["SKU-BS-075"].free_quantity == 1
    assert lines["SKU-DT-1KG"].discount_amount == Decimal("304.50")


def test_demo_orders_are_idempotent_and_leave_sharma_without_history(db_session):
    seed_all(db_session)
    db_session.commit()

    first = seed_demo_orders(db_session)
    again = seed_demo_orders(db_session)

    assert again == first
    assert db_session.scalar(select(func.count(Order.id))) == 4
    sharma = db_session.scalar(
        select(Retailer).where(Retailer.business_name == "Sharma General Store")
    )
    assert (
        db_session.scalar(
            select(func.count(Order.id)).where(Order.retailer_id == sharma.id)
        )
        == 0
    )
