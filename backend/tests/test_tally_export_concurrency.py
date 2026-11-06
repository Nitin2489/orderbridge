"""PostgreSQL concurrency tests for Tally CSV export.

Guarantees under test:
- two exports of the same orders: the second waits on the order row locks,
  then sees them exported and exports nothing (no order in two batches);
- two requests with the same client_request_id: the second waits on the
  request's advisory lock, then returns the first batch (one batch only);
- export versus rejection: serialised on the order row; a confirmed order
  ends exported, never rejected.

As in the other *_concurrency.py modules, these use the safety-checked
test_engine with separate sessions, commit their own data under reserved
identifiers, and delete it before and after each test. Pinned tests wait
until PostgreSQL reports the second transaction as blocked on a lock before
committing the first; lock_timeout makes a regression fail instead of hang.
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
    ExportBatch,
    Order,
    OrderEvent,
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
from app.services.tally_export_service import IneligibleOrdersError, create_export

# Reserved for this module; not used by the seed data or any other test.
RETAILER_PHONE = "+919000000905"
DESK_PHONE = "+919000000906"
SKU = "SKU-EXPCONC-001"

WAIT_SECONDS = 10.0
INELIGIBLE = "ineligible"
INVALID_STATE = "invalid_state"


@dataclass(frozen=True)
class Account:
    order_ids: tuple[UUID, UUID]
    desk_user_id: UUID


def _delete_rows(engine: Engine) -> None:
    """Remove everything this module may have committed."""
    retailer_ids = (
        select(Retailer.id).join(User).where(User.phone_number == RETAILER_PHONE)
    )
    desk_ids = select(User.id).where(User.phone_number == DESK_PHONE)
    order_ids = select(Order.id).where(Order.retailer_id.in_(retailer_ids))
    product_ids = select(Product.id).where(Product.sku == SKU)

    with Session(engine) as session:
        for statement in (
            delete(OrderEvent).where(OrderEvent.order_id.in_(order_ids)),
            delete(OrderItem).where(OrderItem.order_id.in_(order_ids)),
            delete(Order).where(Order.id.in_(order_ids)),
            delete(ExportBatch).where(ExportBatch.created_by_user_id.in_(desk_ids)),
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
    """A committed retailer and desk user with two confirmed orders."""
    _delete_rows(test_engine)

    with Session(test_engine) as session:
        retailer_user = User(
            phone_number=RETAILER_PHONE,
            full_name="Export Concurrency Retailer",
            role=UserRole.RETAILER,
            is_active=True,
        )
        desk_user = User(
            phone_number=DESK_PHONE,
            full_name="Export Concurrency Operator",
            role=UserRole.ORDER_DESK,
            is_active=True,
        )
        product = Product(
            sku=SKU,
            name="Export Concurrency Product",
            brand="Test Brand",
            category="Test",
            unit_name="case",
            units_per_case=10,
            stock_quantity=1000,
            status=ProductStatus.ACTIVE,
        )
        session.add_all([retailer_user, desk_user, product])
        session.flush()
        retailer = Retailer(
            user_id=retailer_user.id,
            business_name="Export Concurrency Store",
            owner_name="Export Concurrency Retailer",
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
        session.commit()

        order_ids = []
        for quantity in (2, 3):
            order = submit_order(
                session,
                retailer,
                [OrderLineInput(product_id=product.id, quantity=quantity)],
                client_request_id=uuid4(),
            ).order
            session.commit()
            preview = preview_order_confirmation(session, order.id)
            confirm_order(
                session,
                order.id,
                approved_fingerprint=preview.fingerprint,
                confirmed_by_user_id=desk_user.id,
            )
            session.commit()
            order_ids.append(order.id)

        ids = Account(order_ids=tuple(order_ids), desk_user_id=desk_user.id)

    try:
        yield ids
    finally:
        _delete_rows(test_engine)


def _new_session(engine: Engine) -> Session:
    session = Session(engine, autoflush=False, expire_on_commit=False)
    session.execute(text("SET LOCAL lock_timeout = '10s'"))
    return session


# Actions run without committing; the harness commits on success, as the
# API does. On an expected service error they roll back, as the API does.


def _export(
    account: Account, request_id: UUID, order_ids: list[UUID]
) -> Callable[[Session], object]:
    def action(session: Session) -> object:
        try:
            result = create_export(
                session,
                exported_by_user_id=account.desk_user_id,
                client_request_id=request_id,
                order_ids=order_ids,
            )
        except IneligibleOrdersError:
            session.rollback()
            return INELIGIBLE
        return (result.batch.id, result.created)

    return action


def _reject(account: Account, order_id: UUID) -> Callable[[Session], object]:
    def action(session: Session) -> object:
        try:
            reject_order(
                session,
                order_id,
                reason="Cancelled by retailer",
                rejected_by_user_id=account.desk_user_id,
            )
        except InvalidOrderStateError:
            session.rollback()
            return INVALID_STATE
        return "rejected"

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
    first: Callable[[Session], object],
    second: Callable[[Session], object],
) -> tuple[object, object]:
    """first runs and holds its locks; second starts and blocks; only then is
    first committed, after which second proceeds and commits."""
    pids: Queue[int] = Queue()

    def contender() -> object:
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


def _state(engine: Engine, account: Account) -> dict:
    with Session(engine) as session:
        orders = session.execute(
            select(Order.id, Order.status, Order.export_batch_id).where(
                Order.id.in_(account.order_ids)
            )
        ).all()
        batches = session.scalar(
            select(func.count(ExportBatch.id)).where(
                ExportBatch.created_by_user_id == account.desk_user_id
            )
        )
        events = dict(
            session.execute(
                select(OrderEvent.event_type, func.count(OrderEvent.id))
                .where(OrderEvent.order_id.in_(account.order_ids))
                .group_by(OrderEvent.event_type)
            ).all()
        )
    return {
        "orders": {order_id: (status, batch) for order_id, status, batch in orders},
        "batches": batches,
        "events": events,
    }


def test_two_exports_of_the_same_orders(test_engine, account):
    orders = list(account.order_ids)

    first, second = _run_while_holding_lock(
        test_engine,
        _export(account, uuid4(), orders),
        _export(account, uuid4(), orders),
    )

    batch_id, created = first
    assert created is True
    assert second == INELIGIBLE
    state = _state(test_engine, account)
    assert state["batches"] == 1
    assert state["orders"] == {
        order_id: (OrderStatus.EXPORTED, batch_id) for order_id in orders
    }
    assert state["events"]["exported"] == 2  # one per order


def test_concurrent_requests_with_the_same_id_create_one_batch(test_engine, account):
    orders = list(account.order_ids)
    request_id = uuid4()

    first, second = _run_while_holding_lock(
        test_engine,
        _export(account, request_id, orders),
        _export(account, request_id, orders),
    )

    assert first[1] is True
    assert second == (first[0], False)  # the same batch, not a new one
    state = _state(test_engine, account)
    assert state["batches"] == 1
    assert state["events"]["exported"] == 2


def test_export_versus_rejection(test_engine, account):
    order_id = account.order_ids[0]

    first, second = _run_while_holding_lock(
        test_engine,
        _export(account, uuid4(), [order_id]),
        _reject(account, order_id),
    )

    assert first[1] is True
    assert second == INVALID_STATE
    state = _state(test_engine, account)
    assert state["orders"][order_id] == (OrderStatus.EXPORTED, first[0])
    assert state["events"].get("rejected", 0) == 0
    assert state["events"]["exported"] == 1
