"""PostgreSQL concurrency tests for idempotent retailer order submission.

The guarantee under test: concurrent submissions with the same
client_request_id create at most one order. retailer_order_service locks the
retailers row before looking the id up, so the second submission waits and
then sees the first one's committed order.

As in tests/test_auth_concurrency.py, these tests need committed data
visible to several connections, so they use the safety-checked test_engine
with their own sessions instead of the rolled-back db_session. They commit
one retailer account, product and price under identifiers reserved for this
module, and delete everything for them before and after each test. A
"pinned" test waits until PostgreSQL reports the second transaction as
blocked on a lock before committing the first; a "race" test releases both
with a barrier. Every transaction sets lock_timeout so a regression fails
instead of hanging.
"""

import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
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
    OrderItem,
    Product,
    ProductStatus,
    Retailer,
    RetailerProductPrice,
    User,
    UserRole,
)
from app.services.retailer_order_service import (
    IdempotencyConflictError,
    OrderLineInput,
    repeat_order,
    submit_order,
)

# Reserved for this module; not used by the seed data or any other test.
PHONE = "+919000000902"
SKU = "SKU-CONC-001"

WAIT_SECONDS = 10.0
CONFLICT = "conflict"


def _delete_rows(engine: Engine) -> None:
    """Remove everything this module may have committed."""
    retailer_ids = select(Retailer.id).join(User).where(User.phone_number == PHONE)
    order_ids = select(Order.id).where(Order.retailer_id.in_(retailer_ids))
    product_ids = select(Product.id).where(Product.sku == SKU)

    with Session(engine) as session:
        for statement in (
            # Audit events reference orders and users with ON DELETE RESTRICT.
            delete(OrderEvent).where(OrderEvent.order_id.in_(order_ids)),
            delete(OrderItem).where(OrderItem.order_id.in_(order_ids)),
            delete(Order).where(Order.id.in_(order_ids)),
            delete(RetailerProductPrice).where(
                RetailerProductPrice.product_id.in_(product_ids)
            ),
            delete(Product).where(Product.sku == SKU),
            delete(Retailer).where(Retailer.id.in_(retailer_ids)),
            delete(User).where(User.phone_number == PHONE),
        ):
            session.execute(statement.execution_options(synchronize_session=False))
        session.commit()


@pytest.fixture
def account(test_engine: Engine) -> Generator[tuple[UUID, UUID], None, None]:
    """A committed retailer with one priced product: (retailer_id, product_id)."""
    _delete_rows(test_engine)

    with Session(test_engine) as session:
        user = User(
            phone_number=PHONE,
            full_name="Concurrency Order Owner",
            role=UserRole.RETAILER,
            is_active=True,
        )
        product = Product(
            sku=SKU,
            name="Concurrency Test Product",
            brand="Test Brand",
            category="Test",
            unit_name="case",
            units_per_case=10,
            stock_quantity=1000,
            status=ProductStatus.ACTIVE,
        )
        session.add_all([user, product])
        session.flush()
        retailer = Retailer(
            user_id=user.id,
            business_name="Concurrency Order Store",
            owner_name="Concurrency Order Owner",
            address="Test Address, Jabalpur",
            credit_limit=Decimal("0.00"),
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
        ids = (retailer.id, product.id)

    try:
        yield ids
    finally:
        _delete_rows(test_engine)


def _new_session(engine: Engine) -> Session:
    session = Session(engine, autoflush=False, expire_on_commit=False)
    session.execute(text("SET LOCAL lock_timeout = '10s'"))
    return session


def _submit(
    session: Session, retailer_id: UUID, product_id: UUID, request_id: UUID, qty: int
) -> UUID | str:
    """Submit and commit as the API does; returns the order id or CONFLICT."""
    retailer = session.get(Retailer, retailer_id)
    try:
        result = submit_order(
            session,
            retailer,
            [OrderLineInput(product_id=product_id, quantity=qty)],
            client_request_id=request_id,
        )
    except IdempotencyConflictError:
        session.rollback()
        return CONFLICT

    session.commit()
    return result.order.id


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


def _orders_for(engine: Engine, retailer_id: UUID) -> int:
    with Session(engine) as session:
        return session.scalar(
            select(func.count(Order.id)).where(Order.retailer_id == retailer_id)
        )


def _race(engine: Engine, action: Callable[[Session], object]) -> list[object]:
    barrier = threading.Barrier(2, timeout=WAIT_SECONDS)

    def participant() -> object:
        with _new_session(engine) as session:
            barrier.wait()
            return action(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(participant) for _ in range(2)]
        return [future.result(timeout=WAIT_SECONDS * 3) for future in futures]


def test_duplicate_submission_waits_and_returns_the_first_order(test_engine, account):
    retailer_id, product_id = account
    request_id = uuid4()
    pids: Queue[int] = Queue()

    def contender() -> UUID | str:
        with _new_session(test_engine) as session:
            pids.put(session.scalar(text("SELECT pg_backend_pid()")))
            return _submit(session, retailer_id, product_id, request_id, 5)

    holder = _new_session(test_engine)
    try:
        first = submit_order(
            holder,
            holder.get(Retailer, retailer_id),
            [OrderLineInput(product_id=product_id, quantity=5)],
            client_request_id=request_id,
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(contender)
            _wait_until_blocked_on_lock(test_engine, pids.get(timeout=WAIT_SECONDS))
            holder.commit()
            second = future.result(timeout=WAIT_SECONDS * 3)
    finally:
        holder.close()

    assert first.created is True
    assert second == first.order.id
    assert _orders_for(test_engine, retailer_id) == 1


def test_racing_identical_submissions_create_one_order(test_engine, account):
    retailer_id, product_id = account
    request_id = uuid4()

    outcomes = _race(
        test_engine,
        lambda session: _submit(session, retailer_id, product_id, request_id, 5),
    )

    assert outcomes[0] == outcomes[1]
    assert _orders_for(test_engine, retailer_id) == 1


def test_racing_different_requests_with_one_id_conflict(test_engine, account):
    retailer_id, product_id = account
    request_id = uuid4()
    quantities = iter([5, 6])
    lock = threading.Lock()

    def action(session: Session) -> UUID | str:
        with lock:
            quantity = next(quantities)
        return _submit(session, retailer_id, product_id, request_id, quantity)

    outcomes = _race(test_engine, action)

    assert outcomes.count(CONFLICT) == 1
    assert _orders_for(test_engine, retailer_id) == 1


# ---------------------------------------------------------------------------
# Repeat ordering
# ---------------------------------------------------------------------------


def _committed_source(engine: Engine, retailer_id: UUID, product_id: UUID) -> UUID:
    """A committed order for this retailer, to repeat."""
    with _new_session(engine) as session:
        return _submit(session, retailer_id, product_id, uuid4(), 3)


def _repeat(
    session: Session, retailer_id: UUID, source_id: UUID, request_id: UUID
) -> UUID | str:
    """Repeat and commit as the API does; returns the order id or CONFLICT."""
    retailer = session.get(Retailer, retailer_id)
    try:
        result = repeat_order(
            session, retailer, source_id, client_request_id=request_id
        )
    except IdempotencyConflictError:
        session.rollback()
        return CONFLICT

    session.commit()
    return result.order.id


def test_duplicate_repeat_waits_and_returns_the_first_new_order(test_engine, account):
    retailer_id, product_id = account
    source_id = _committed_source(test_engine, retailer_id, product_id)
    request_id = uuid4()
    pids: Queue[int] = Queue()

    def contender() -> UUID | str:
        with _new_session(test_engine) as session:
            pids.put(session.scalar(text("SELECT pg_backend_pid()")))
            return _repeat(session, retailer_id, source_id, request_id)

    holder = _new_session(test_engine)
    try:
        first = repeat_order(
            holder,
            holder.get(Retailer, retailer_id),
            source_id,
            client_request_id=request_id,
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(contender)
            _wait_until_blocked_on_lock(test_engine, pids.get(timeout=WAIT_SECONDS))
            holder.commit()
            second = future.result(timeout=WAIT_SECONDS * 3)
    finally:
        holder.close()

    assert first.created is True
    assert first.order.id != source_id
    assert second == first.order.id
    assert _orders_for(test_engine, retailer_id) == 2  # source + one repeat


def test_racing_identical_repeats_create_one_order(test_engine, account):
    retailer_id, product_id = account
    source_id = _committed_source(test_engine, retailer_id, product_id)
    request_id = uuid4()

    outcomes = _race(
        test_engine,
        lambda session: _repeat(session, retailer_id, source_id, request_id),
    )

    assert outcomes[0] == outcomes[1]
    assert outcomes[0] != source_id
    assert _orders_for(test_engine, retailer_id) == 2


def test_racing_repeats_of_different_sources_with_one_id_conflict(test_engine, account):
    retailer_id, product_id = account
    sources = iter(
        [
            _committed_source(test_engine, retailer_id, product_id),
            _committed_source(test_engine, retailer_id, product_id),
        ]
    )
    request_id = uuid4()
    lock = threading.Lock()

    def action(session: Session) -> UUID | str:
        with lock:
            source_id = next(sources)
        return _repeat(session, retailer_id, source_id, request_id)

    outcomes = _race(test_engine, action)

    assert outcomes.count(CONFLICT) == 1
    assert _orders_for(test_engine, retailer_id) == 3  # two sources + one repeat
