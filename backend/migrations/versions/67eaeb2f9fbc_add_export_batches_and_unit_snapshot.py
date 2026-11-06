"""add export batches and order item unit snapshot

Revision ID: 67eaeb2f9fbc
Revises: 6e162ff09ae8
Create Date: 2026-11-06 00:00:00.000000

- export_batch_number_seq: source of EX-YYYY-NNNNNN batch numbers.
- export_batches: one row per Tally CSV export, holding the exact CSV text
  and its SHA-256, the creating desk user and the idempotency key.
- orders.export_batch_id: nullable, set once when an order is exported;
  ON DELETE RESTRICT, indexed.
- order_items.unit_name: the product's unit at the time the line was
  created. Existing lines are backfilled from their product's CURRENT
  unit_name. This is a best-effort historical backfill: the original unit
  was never stored, so it is correct only if the unit has not changed since.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "67eaeb2f9fbc"
down_revision: str | Sequence[str] | None = "6e162ff09ae8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH_NUMBER_SEQUENCE = "export_batch_number_seq"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.schema.CreateSequence(sa.Sequence(BATCH_NUMBER_SEQUENCE)))

    op.create_table(
        "export_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_number", sa.String(length=30), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("order_count", sa.Integer(), nullable=False),
        sa.Column("net_total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("csv_content", sa.Text(), nullable=False),
        sa.Column("csv_sha256", sa.String(length=64), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "order_count > 0", name=op.f("ck_export_batches_order_count_positive")
        ),
        sa.CheckConstraint(
            "net_total >= 0", name=op.f("ck_export_batches_net_total_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_export_batches_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_batches")),
        sa.UniqueConstraint(
            "batch_number", name=op.f("uq_export_batches_batch_number")
        ),
        sa.UniqueConstraint(
            "client_request_id", name=op.f("uq_export_batches_client_request_id")
        ),
    )
    op.create_index(
        "ix_export_batch_created", "export_batches", ["created_at"], unique=False
    )

    op.add_column("orders", sa.Column("export_batch_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_orders_export_batch_id_export_batches"),
        "orders",
        "export_batches",
        ["export_batch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_orders_export_batch_id"), "orders", ["export_batch_id"], unique=False
    )

    # Best-effort backfill from each product's current unit (see docstring).
    op.add_column(
        "order_items", sa.Column("unit_name", sa.String(length=30), nullable=True)
    )
    op.execute(
        "UPDATE order_items SET unit_name = products.unit_name "
        "FROM products WHERE products.id = order_items.product_id"
    )
    op.alter_column("order_items", "unit_name", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("order_items", "unit_name")

    op.drop_index(op.f("ix_orders_export_batch_id"), table_name="orders")
    op.drop_constraint(
        op.f("fk_orders_export_batch_id_export_batches"), "orders", type_="foreignkey"
    )
    op.drop_column("orders", "export_batch_id")

    op.drop_index("ix_export_batch_created", table_name="export_batches")
    op.drop_table("export_batches")

    op.execute(sa.schema.DropSequence(sa.Sequence(BATCH_NUMBER_SEQUENCE)))
