"""add the confirmed unit decision to capture lines

Revision ID: f2b6c8d4a173
Revises: d8f3b6a2c915
Create Date: 2026-11-07 00:00:00.000000

A reviewer confirms a line's unit as cases or pieces. Pieces become cases
only through the *selected* product's units_per_case, and unit confirmation
is independent of product selection, so the decision itself must be stored:

- order_capture_lines.confirmed_unit ('case' | 'piece') and
  confirmed_quantity (the number entered in that unit).
- quantity_cases is now the derived case quantity, set only while the line
  is included (it was required as soon as a unit was confirmed).
- unit_confirmation_complete: confirmed exactly when unit, quantity, who
  and when are all recorded (quantity_cases no longer takes part).
- included_complete now also requires quantity_cases.

Like d8f3b6a2c915, the upgrade and downgrade refuse to run while any
capture line exists, so no review decision is ever rewritten.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "f2b6c8d4a173"
down_revision: str | Sequence[str] | None = "d8f3b6a2c915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "order_capture_lines"
CONFIRMED_UNITS = ("case", "piece")
MAX_CONFIRMED_QUANTITY = 1_000_000


def _ck(name: str) -> str:
    return op.f(f"ck_{TABLE}_{name}")


def _require_empty(action: str) -> None:
    if context.is_offline_mode():
        return
    rows = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
    if rows:
        raise RuntimeError(
            f"Cannot {action}: {TABLE} has {rows} row(s). This revision only "
            "changes an empty table; resolve the data manually."
        )


def upgrade() -> None:
    """Upgrade schema."""
    _require_empty("upgrade")

    op.add_column(
        TABLE, sa.Column("confirmed_unit", sa.String(length=10), nullable=True)
    )
    op.add_column(TABLE, sa.Column("confirmed_quantity", sa.Integer(), nullable=True))

    op.drop_constraint(_ck("unit_confirmation_complete"), TABLE, type_="check")
    op.create_check_constraint(
        _ck("unit_confirmation_complete"),
        TABLE,
        "unit_confirmed = (confirmed_unit IS NOT NULL) "
        "AND unit_confirmed = (confirmed_quantity IS NOT NULL) "
        "AND unit_confirmed = (unit_confirmed_by_user_id IS NOT NULL) "
        "AND unit_confirmed = (unit_confirmed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        _ck("confirmed_unit_valid"),
        TABLE,
        "confirmed_unit IS NULL OR confirmed_unit IN "
        f"({', '.join(repr(unit) for unit in CONFIRMED_UNITS)})",
    )
    op.create_check_constraint(
        _ck("confirmed_quantity_range"),
        TABLE,
        "confirmed_quantity IS NULL OR confirmed_quantity BETWEEN 1 "
        f"AND {MAX_CONFIRMED_QUANTITY}",
    )
    op.create_check_constraint(
        _ck("cases_only_when_included"),
        TABLE,
        "resolution = 'included' OR quantity_cases IS NULL",
    )
    op.drop_constraint(_ck("included_complete"), TABLE, type_="check")
    op.create_check_constraint(
        _ck("included_complete"),
        TABLE,
        "resolution <> 'included' OR (selected_product_id IS NOT NULL "
        "AND unit_confirmed AND quantity_cases IS NOT NULL)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    _require_empty("downgrade")

    op.drop_constraint(_ck("included_complete"), TABLE, type_="check")
    op.create_check_constraint(
        _ck("included_complete"),
        TABLE,
        "resolution <> 'included' "
        "OR (selected_product_id IS NOT NULL AND unit_confirmed)",
    )
    op.drop_constraint(_ck("cases_only_when_included"), TABLE, type_="check")
    op.drop_constraint(_ck("confirmed_quantity_range"), TABLE, type_="check")
    op.drop_constraint(_ck("confirmed_unit_valid"), TABLE, type_="check")
    op.drop_constraint(_ck("unit_confirmation_complete"), TABLE, type_="check")
    op.create_check_constraint(
        _ck("unit_confirmation_complete"),
        TABLE,
        "unit_confirmed = (quantity_cases IS NOT NULL) "
        "AND unit_confirmed = (unit_confirmed_by_user_id IS NOT NULL) "
        "AND unit_confirmed = (unit_confirmed_at IS NOT NULL)",
    )
    op.drop_column(TABLE, "confirmed_quantity")
    op.drop_column(TABLE, "confirmed_unit")
