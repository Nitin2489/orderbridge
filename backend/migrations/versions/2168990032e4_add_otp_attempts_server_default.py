"""add server default for otp_challenges.attempts

Revision ID: 2168990032e4
Revises: 95ae435ad31e
Create Date: 2026-11-05 00:00:00.000000

The model already defaults attempts to 0 in Python; this adds the same
default in the database, so rows inserted outside the ORM also start at 0.
Existing rows and the attempts_non_negative check constraint are unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2168990032e4"
down_revision: str | Sequence[str] | None = "95ae435ad31e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "otp_challenges",
        "attempts",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=sa.text("0"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "otp_challenges",
        "attempts",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=None,
    )
