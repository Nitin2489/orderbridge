"""add rate limit buckets

Revision ID: c4e7a1d9b2f3
Revises: 67eaeb2f9fbc
Create Date: 2026-11-07 00:00:00.000000

Shared, database-backed rate limiting for the OTP endpoints (one row per
key and fixed window; keys are HMACs, never raw IPs or phone numbers). A new
table only: no existing table or row changes, so upgrading is safe on a
live database and downgrading only discards rate-limit counters.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e7a1d9b2f3"
down_revision: str | Sequence[str] | None = "67eaeb2f9fbc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "hits > 0", name=op.f("ck_rate_limit_buckets_hits_positive")
        ),
        sa.CheckConstraint(
            "expires_at > window_start",
            name=op.f("ck_rate_limit_buckets_window_valid"),
        ),
        sa.PrimaryKeyConstraint(
            "key_hash", "window_start", name=op.f("pk_rate_limit_buckets")
        ),
    )
    op.create_index(
        op.f("ix_rate_limit_buckets_expires_at"),
        "rate_limit_buckets",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_rate_limit_buckets_expires_at"), table_name="rate_limit_buckets"
    )
    op.drop_table("rate_limit_buckets")
