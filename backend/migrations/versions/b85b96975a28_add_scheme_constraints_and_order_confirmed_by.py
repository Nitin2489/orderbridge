"""add scheme constraints and order confirmed_by

Revision ID: b85b96975a28
Revises: 1aa9327febc3
Create Date: 2026-11-05 00:00:00.000000

The initial migration created the schemes table without its check
constraints and ix_scheme_product_active index, because the Scheme model's
__table_args__ was defined outside the class at the time. This revision adds
them to match the corrected model, and adds the nullable
orders.confirmed_by_user_id audit column.

Existing scheme rows are checked before the constraints are added. If any row
would violate a constraint, the upgrade stops with a clear error and changes
nothing; offending data must be corrected deliberately, never silently.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "b85b96975a28"
down_revision: str | Sequence[str] | None = "1aa9327febc3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (constraint name, SQL condition) pairs, matching Scheme.__table_args__.
SCHEME_CHECK_CONSTRAINTS = [
    (
        "ck_schemes_minimum_quantity_positive",
        "minimum_quantity > 0",
    ),
    (
        "ck_schemes_free_quantity_non_negative",
        "free_quantity >= 0",
    ),
    (
        "ck_schemes_discount_percent_valid",
        "discount_percent >= 0 AND discount_percent <= 100",
    ),
    (
        "ck_schemes_valid_scheme_dates",
        "ends_at IS NULL OR ends_at >= starts_at",
    ),
    (
        "ck_schemes_valid_scheme_configuration",
        (
            "(scheme_type = 'free_goods' AND free_quantity > 0 "
            "AND discount_percent = 0) "
            "OR (scheme_type = 'slab_discount' AND free_quantity = 0 "
            "AND discount_percent > 0)"
        ),
    ),
]


def assert_existing_schemes_valid() -> None:
    """Stop the upgrade if existing scheme rows violate a new constraint.

    A CHECK constraint rejects a row only when its condition is FALSE (NULL
    passes), so rows are counted with WHERE NOT (condition), which matches.
    The conditions are fixed strings defined above, not user input.
    """
    if context.is_offline_mode():
        # No database connection when generating SQL scripts.
        return

    connection = op.get_bind()
    violations = []

    for name, condition in SCHEME_CHECK_CONSTRAINTS:
        count = connection.execute(
            sa.text(f"SELECT count(*) FROM schemes WHERE NOT ({condition})")
        ).scalar_one()

        if count:
            violations.append(f"{name}: {count} row(s)")

    if violations:
        raise RuntimeError(
            "Existing scheme rows violate new check constraints; correct "
            "them before upgrading. " + "; ".join(violations)
        )


def upgrade() -> None:
    """Upgrade schema."""
    assert_existing_schemes_valid()

    for name, condition in SCHEME_CHECK_CONSTRAINTS:
        op.create_check_constraint(op.f(name), "schemes", condition)

    op.create_index(
        "ix_scheme_product_active",
        "schemes",
        ["product_id", "is_active"],
        unique=False,
    )

    op.add_column(
        "orders",
        sa.Column("confirmed_by_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_orders_confirmed_by_user_id_users"),
        "orders",
        "users",
        ["confirmed_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("fk_orders_confirmed_by_user_id_users"),
        "orders",
        type_="foreignkey",
    )
    op.drop_column("orders", "confirmed_by_user_id")

    op.drop_index("ix_scheme_product_active", table_name="schemes")

    for name, _condition in reversed(SCHEME_CHECK_CONSTRAINTS):
        op.drop_constraint(op.f(name), "schemes", type_="check")
