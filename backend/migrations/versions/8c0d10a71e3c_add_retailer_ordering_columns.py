"""add retailer ordering columns and order number sequence

Revision ID: 8c0d10a71e3c
Revises: 2168990032e4
Create Date: 2026-11-05 00:00:00.000000

- order_number_seq: source of readable order numbers (OB-YYYY-000001). If
  existing orders already use that format, the sequence starts after the
  highest number in use, so new numbers cannot collide with them.
- orders.client_request_id and orders.request_fingerprint: idempotent
  submission. Both are nullable (orders not created by the retailer app have
  neither) but must be set together, and client_request_id is unique per
  retailer. PostgreSQL unique constraints allow many NULLs.
- order_items.line_number: stable line order. Existing items are numbered
  per order by (created_at, id) before the column becomes NOT NULL; then
  line numbers must be positive and unique within an order.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c0d10a71e3c"
down_revision: str | Sequence[str] | None = "2168990032e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_NUMBER_SEQUENCE = "order_number_seq"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.schema.CreateSequence(sa.Sequence(ORDER_NUMBER_SEQUENCE)))
    # Continue after any existing OB-YYYY-NNNNNN number. setval(..., false)
    # makes the next nextval() return exactly the given value.
    op.execute(
        "SELECT setval('order_number_seq', COALESCE(MAX(CAST(substring("
        "order_number FROM '^OB-[0-9]{4}-([0-9]+)$') AS bigint)), 0) + 1, false) "
        "FROM orders"
    )

    op.add_column("orders", sa.Column("client_request_id", sa.Uuid(), nullable=True))
    op.add_column(
        "orders", sa.Column("request_fingerprint", sa.String(length=64), nullable=True)
    )
    op.create_unique_constraint(
        "order_retailer_client_request_unique",
        "orders",
        ["retailer_id", "client_request_id"],
    )
    op.create_check_constraint(
        op.f("ck_orders_client_request_has_fingerprint"),
        "orders",
        "(client_request_id IS NULL) = (request_fingerprint IS NULL)",
    )

    op.add_column("order_items", sa.Column("line_number", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE order_items AS item SET line_number = numbered.position "
        "FROM (SELECT id, row_number() OVER ("
        "PARTITION BY order_id ORDER BY created_at, id) AS position "
        "FROM order_items) AS numbered "
        "WHERE item.id = numbered.id"
    )
    op.alter_column("order_items", "line_number", nullable=False)
    op.create_unique_constraint(
        "order_item_line_number_unique",
        "order_items",
        ["order_id", "line_number"],
    )
    op.create_check_constraint(
        op.f("ck_order_items_line_number_positive"),
        "order_items",
        "line_number > 0",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("ck_order_items_line_number_positive"), "order_items", type_="check"
    )
    op.drop_constraint("order_item_line_number_unique", "order_items", type_="unique")
    op.drop_column("order_items", "line_number")

    op.drop_constraint(
        op.f("ck_orders_client_request_has_fingerprint"), "orders", type_="check"
    )
    op.drop_constraint("order_retailer_client_request_unique", "orders", type_="unique")
    op.drop_column("orders", "request_fingerprint")
    op.drop_column("orders", "client_request_id")

    op.execute(sa.schema.DropSequence(sa.Sequence(ORDER_NUMBER_SEQUENCE)))
