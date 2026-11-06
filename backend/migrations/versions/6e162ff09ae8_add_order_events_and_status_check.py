"""add order events and order status check

Revision ID: 6e162ff09ae8
Revises: 8c0d10a71e3c
Create Date: 2026-11-06 00:00:00.000000

- order_events: append-only audit trail of order lifecycle changes. Orders
  and acting users cannot be deleted while events reference them
  (ON DELETE RESTRICT). event_type is limited to the known event types.
- orders.status: limited to the six OrderStatus values. Existing rows are
  checked first; if any row has another value, the upgrade stops with a
  clear error and changes nothing.

Existing orders get no backfilled events: their history before this
revision was never recorded, and inventing it would be misleading.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "6e162ff09ae8"
down_revision: str | Sequence[str] | None = "8c0d10a71e3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_STATUSES = (
    "draft",
    "pending_review",
    "on_hold",
    "confirmed",
    "rejected",
    "exported",
)
EVENT_TYPES = (
    "submitted",
    "confirmed",
    "placed_on_hold",
    "rejected",
    "exported",
    "hold_released",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _assert_existing_statuses_valid() -> None:
    if context.is_offline_mode():
        return

    invalid = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM orders WHERE NOT "
                f"({_in_list('status', ORDER_STATUSES)})"
            )
        )
        .scalar_one()
    )

    if invalid:
        raise RuntimeError(
            f"{invalid} order(s) have a status outside {ORDER_STATUSES}; "
            "correct them before upgrading."
        )


def upgrade() -> None:
    """Upgrade schema."""
    _assert_existing_statuses_valid()
    op.create_check_constraint(
        op.f("ck_orders_status_valid"), "orders", _in_list("status", ORDER_STATUSES)
    )

    op.create_table(
        "order_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("event_type", EVENT_TYPES),
            name=op.f("ck_order_events_event_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_events_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_order_events_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
    )
    op.create_index(
        "ix_order_event_order_created",
        "order_events",
        ["order_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_order_event_order_created", table_name="order_events")
    op.drop_table("order_events")
    op.drop_constraint(op.f("ck_orders_status_valid"), "orders", type_="check")
