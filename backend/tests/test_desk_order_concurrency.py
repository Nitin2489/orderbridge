"""PostgreSQL concurrency tests for order desk state transitions.

confirm_order() and reject_order() both lock the orders row first, so two
desk actions on one order are serialised: the second waits for the first to
commit and then sees its final state. These tests check that no combination
produces conflicting final states or duplicate transition events.

As in tests/test_auth_concurrency.py and test_retailer_order_concurrency.py,
they use the safety-checked test_engine with separate sessions, commit their
own data under identifiers reserved for this module, and delete it before
and after each test. Pinned tests wait until PostgreSQL reports the second
transaction as blocked on a lock before committing the first; every
transaction sets lock_timeout so a regression fails instead of hanging.
"""

import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from queue import Queue
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import (
    Order,
    OrderEvent,
    OrderEventType,
    OrderItem,
    OrderStatus,
    Product,
    ProductStatus,
    Retailer,
    RetailerProductPrice,
    User,
    UserRole,
)
from app.services.desk_order_service import reject_order
from app.services.order_service import (
    InvalidOrderStateError,
    confirm_order,
    preview_order_confirmation,
)
from app.services.retailer_order_service import OrderLineInput, submit_order

# Reserved for this module; not used by the seed data or any other test.
RETAILER_PHONE = "+919000000903"
DESK_PHONE = "+919000000904"
SKU = "SKU-DESKCONC-001"
STOCK = 1000

WAIT_SECONDS = 10.0
INVALID_STATE = "invalid_state"


@dataclass(frozen=True)
class Account:
    order_id: UUID
    product_id: UUID
    desk_user_id: UUID


def _delete_rows(engine: Engine) -> None:
    """Remove everything this module may have committed."""
    retailer_ids = (
        select(Retailer.id).join(User).where(User.phone_number == RETAILER_PHONE)
    )
    order_ids = select(Order.id).where(Order.retailer_id.in_(retailer_ids))
    product_ids = select(Product.id).where(Product.sku == SKU)

    with Session(engine) as session:
        for statement in (
            delete(OrderEvent).where(OrderEvent.order_id.in_(order_ids)),
            delete(OrderItem).where(OrderItem.order_id.in_(order_ids)),
            delete(Order).where(Order.id.in_(order_ids)),
            delete(RetailerProductPrice).where(
                RetailerProductPrice.product_id.in_(product_ids)
            ),
            delete(Product).where(Product.sku == SKU),
            delete(Retailer).where(Retailer.id.in_(retailer_ids)),
            delete(User).where(User.phone_number.in_([RETAILER_PHONE, DESK_PHONE])),
        ):
            session.execute(statement.execution_options(synchronize_session=False))
        session.commit()


@pytest.fixture
def account(test_engine: Engine) -> Generator[Account, None, None]:
    """A committed retailer, desk user, product and pending order."""
    _delete_rows(test_engine)

    with Session(test_engine) as session:
        retailer_user = User(
            phone_number=RETAILER_PHONE,
            full_name="Desk Concurrency Retailer",
            role=UserRole.RETAILER,
            is_active=True,
        )
        desk_user = User(
            phone_number=DESK_PHONE,
            full_name="Desk Concurrency Operator",
            role=UserRole.ORDER_DESK,
            is_active=True,
        )
        product = Product(
            sku=SKU,
            name="Desk Concurrency Product",
            brand="Test Brand",
            category="Test",
            unit_name="case",
            units_per_case=10,
            stock_quantity=STOCK,
            status=ProductStatus.ACTIVE,
        )
        session.add_all([retailer_user, desk_user, product])
        session.flush()
        retailer = Retailer(
            user_id=retailer_user.id,
            business_name="Desk Concurrency Store",
            owner_name="Desk Concurrency Retailer",
            address="Test Address, Jabalpur",
            credit_limit=Decimal("100000.00"),
            outstanding_balance=Decimal("0.00"),
            is_active=True,
        )
        session.add(retailer)
        session.flush()
        session.add(
            RetailerProductPrice(
                retailer_id=retailer.id,
                product_id=product.id,
                unit_price=Decimal("100.00"),
                is_active=True,
            )
        )
        session.flush()
        order = submit_order(
            session,
            retailer,
            [OrderLineInput(product_id=product.id, quantity=5)],
            client_request_id=uuid4(),
        ).order
        session.commit()
        ids = Account(
            order_id=order.id, product_id=product.id, desk_user_id=desk_user.id
        )

    try:
        yield ids
    finally:
        _delete_rows(test_engine)


def _new_session(engine: Engine) -> Session:
    session = Session(engine, autoflush=False, expire_on_commit=False)
    session.execute(text("SET LOCAL lock_timeout = '10s'"))
    return session


def _fingerprint(engine: Engine, order_id: UUID) -> str:
    with Session(engine) as session:
        return preview_order_confirmation(session, order_id).fingerprint


# Actions run a desk operation without committing; the harness commits, as
# the API does on success. On InvalidOrderStateError they roll back, as the
# API does.


def _confirm(account: Account, fingerprint: str) -> Callable[[Session], str]:
    def action(session: Session) -> str:
        try:
            result = confirm_order(
                session,
                account.order_id,
                approved_fingerprint=fingerprint,
                confirmed_by_user_id=account.desk_user_id,
            )
        except InvalidOrderStateError:
            session.rollback()
            return INVALID_STATE
        return result.outcome

    return action


def _reject(account: Account, reason: str) -> Callable[[Session], str]:
    def action(session: Session) -> str:
        try:
            result = reject_order(
                session,
                account.order_id,
                reason=reason,
                rejected_by_user_id=account.desk_user_id,
            )
        except InvalidOrderStateError:
            session.rollback()
            return INVALID_STATE
        return "already_rejected" if result.already_rejected else "rejected"

    return action


def _wait_until_blocked_on_lock(engine: Engine, pid: int) -> None:
    deadline = time.monotonic() + WAIT_SECONDS

    with engine.connect() as connection:
        while time.monotonic() < deadline:
            wait_type = connection.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            connection.rollback()

            if wait_type == "Lock":
                return

            time.sleep(0.02)

    pytest.fail(f"Backend {pid} never waited on a lock; it was not serialised.")


def _run_while_holding_lock(
    engine: Engine,
    first: Callable[[Session], str],
    second: Callable[[Session], str],
) -> tuple[str, str]:
    """first runs and holds its locks; second starts and blocks; only then is
    first committed, after which second proceeds and commits."""
    pids: Queue[int] = Queue()

    def contender() -> str:
        with _new_session(engine) as session:
            pids.put(session.scalar(text("SELECT pg_backend_pid()")))
            outcome = second(session)
            session.commit()
            return outcome

    holder = _new_session(engine)
    try:
        first_outcome = first(holder)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(contender)
            _wait_until_blocked_on_lock(engine, pids.get(timeout=WAIT_SECONDS))
            holder.commit()
            second_outcome = future.result(timeout=WAIT_SECONDS * 3)
    finally:
        holder.close()

    return first_outcome, second_outcome


def _state(engine: Engine, account: Account) -> tuple[str, list[str], int]:
    """(order status, event types oldest first, product stock)."""
    with Session(engine) as session:
        status = session.scalar(
            select(Order.status).where(Order.id == account.order_id)
        )
        events = list(
            session.scalars(
                select(OrderEvent.event_type)
                .where(OrderEvent.order_id == account.order_id)
                .order_by(OrderEvent.created_at, OrderEvent.id)
            )
        )
        stock = session.scalar(
            select(Product.stock_quantity).where(Product.id == account.product_id)
        )
    return status, events, stock


def _count(engine: Engine, account: Account, event_type: OrderEventType) -> int:
    with Session(engine) as session:
        return session.scalar(
            select(func.count(OrderEvent.id)).where(
                OrderEvent.order_id == account.order_id,
                OrderEvent.event_type == event_type,
            )
        )


def test_confirm_versus_confirm(test_engine, account):
    fingerprint = _fingerprint(test_engine, account.order_id)

    first, second = _run_while_holding_lock(
        test_engine, _confirm(account, fingerprint), _confirm(account, fingerprint)
    )

    assert (first, second) == ("confirmed", "already_confirmed")
    status, events, stock = _state(test_engine, account)
    assert status == OrderStatus.CONFIRMED
    assert events == ["submitted", "confirmed"]
    assert stock == STOCK - 5  # deducted once


def test_confirm_versus_reject(test_engine, account):
    fingerprint = _fingerprint(test_engine, account.order_id)

    first, second = _run_while_holding_lock(
        test_engine, _confirm(account, fingerprint), _reject(account, "Too late")
    )

    assert (first, second) == ("confirmed", INVALID_STATE)
    status, events, stock = _state(test_engine, account)
    assert status == OrderStatus.CONFIRMED
    assert events == ["submitted", "confirmed"]
    assert stock == STOCK - 5


def test_reject_versus_confirm(test_engine, account):
    fingerprint = _fingerprint(test_engine, account.order_id)

    first, second = _run_while_holding_lock(
        test_engine, _reject(account, "Cancelled"), _confirm(account, fingerprint)
    )

    assert (first, second) == ("rejected", INVALID_STATE)
    status, events, stock = _state(test_engine, account)
    assert status == OrderStatus.REJECTED
    assert events == ["submitted", "rejected"]
    assert stock == STOCK  # nothing deducted


def test_reject_versus_reject(test_engine, account):
    first, second = _run_while_holding_lock(
        test_engine, _reject(account, "First"), _reject(account, "Second")
    )

    assert (first, second) == ("rejected", "already_rejected")
    status, events, _ = _state(test_engine, account)
    assert status == OrderStatus.REJECTED
    assert events == ["submitted", "rejected"]
    assert _count(test_engine, account, OrderEventType.REJECTED) == 1
