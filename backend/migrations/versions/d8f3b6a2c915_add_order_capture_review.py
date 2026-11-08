"""add order capture review, capture lines and retailer aliases

Revision ID: d8f3b6a2c915
Revises: c4e7a1d9b2f3
Create Date: 2026-11-07 00:00:00.000000

Phase A of AI order capture (text messages, rule-based interpretation):

- order_captures (created by the initial schema, never written by any code
  so far) gains its review workflow: the creating desk user, channel,
  interpreter, an optimistic-concurrency version, an idempotency key and
  fingerprint, submission and discard records, and updated_at.
  review_notes is renamed discard_reason and reviewed_at is replaced by
  submitted_at / discarded_at. status is limited to the new capture
  statuses. order_id becomes unique and ON DELETE RESTRICT (it was SET
  NULL): a submitted capture must always keep its order, and orders are
  never deleted.
- order_capture_lines: one row per proposed line, with exact text offsets
  into the unmodified message, the interpreter's suggestions and ranked
  candidates, and the reviewer's decision.
- retailer_product_aliases: per-retailer product phrases saved from
  corrected lines; at most one active row per retailer and phrase, and
  replaced rows are kept (deactivated), never overwritten.

The upgrade requires order_captures to be empty: its new columns are NOT
NULL and no meaningful values exist for older rows. It stops with a clear
error and changes nothing otherwise. The downgrade likewise refuses to run
while any capture or alias exists, so review and alias history is never
discarded silently.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "d8f3b6a2c915"
down_revision: str | Sequence[str] | None = "c4e7a1d9b2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the enum values at this revision.
CAPTURE_STATUSES = ("needs_review", "ready", "submitted", "discarded", "failed")
CAPTURE_CHANNELS = ("text",)
UNIT_HINTS = ("case_word", "piece_word", "none")
LINE_RESOLUTIONS = ("pending", "included", "excluded")

MAX_MESSAGE_LENGTH = 2000
MAX_QUANTITY = 10_000
MIN_REASON = 3
MAX_REASON = 500
MAX_ALIAS = 200

# order_captures columns referencing the acting desk user.
CAPTURE_ACTOR_COLUMNS = (
    "created_by_user_id",
    "submitted_by_user_id",
    "discarded_by_user_id",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _reason_valid(column: str) -> str:
    return (
        f"{column} IS NULL OR char_length(btrim({column})) "
        f"BETWEEN {MIN_REASON} AND {MAX_REASON}"
    )


def _count(table: str) -> int:
    return op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


def _require_empty(tables: tuple[str, ...], action: str) -> None:
    if context.is_offline_mode():
        return
    for table in tables:
        rows = _count(table)
        if rows:
            raise RuntimeError(
                f"Cannot {action}: {table} has {rows} row(s). This revision "
                "only changes empty capture tables; resolve the data manually."
            )


def upgrade() -> None:
    """Upgrade schema."""
    _require_empty(("order_captures",), "upgrade")

    # --- order_captures -------------------------------------------------
    op.add_column(
        "order_captures", sa.Column("created_by_user_id", sa.Uuid(), nullable=False)
    )
    op.add_column(
        "order_captures", sa.Column("channel", sa.String(length=20), nullable=False)
    )
    op.add_column(
        "order_captures",
        sa.Column("interpreter", sa.String(length=40), nullable=False),
    )
    op.add_column("order_captures", sa.Column("version", sa.Integer(), nullable=False))
    op.add_column(
        "order_captures", sa.Column("client_request_id", sa.Uuid(), nullable=False)
    )
    op.add_column(
        "order_captures",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
    )
    op.add_column(
        "order_captures",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_captures", sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "order_captures",
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_captures", sa.Column("discarded_by_user_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "order_captures",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.alter_column("order_captures", "review_notes", new_column_name="discard_reason")
    op.drop_column("order_captures", "reviewed_at")

    for column in CAPTURE_ACTOR_COLUMNS:
        op.create_foreign_key(
            op.f(f"fk_order_captures_{column}_users"),
            "order_captures",
            "users",
            [column],
            ["id"],
            ondelete="RESTRICT",
        )

    # order_id: SET NULL -> RESTRICT, and unique.
    op.drop_constraint(
        op.f("fk_order_captures_order_id_orders"), "order_captures", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_order_captures_order_id_orders"),
        "order_captures",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "order_capture_order_unique", "order_captures", ["order_id"]
    )
    op.create_unique_constraint(
        "order_capture_client_request_unique",
        "order_captures",
        ["created_by_user_id", "client_request_id"],
    )

    op.create_check_constraint(
        op.f("ck_order_captures_status_valid"),
        "order_captures",
        _in_list("status", CAPTURE_STATUSES),
    )
    op.create_check_constraint(
        op.f("ck_order_captures_channel_valid"),
        "order_captures",
        _in_list("channel", CAPTURE_CHANNELS),
    )
    op.create_check_constraint(
        op.f("ck_order_captures_version_positive"), "order_captures", "version >= 1"
    )
    op.create_check_constraint(
        op.f("ck_order_captures_message_valid"),
        "order_captures",
        "original_message ~ '[^[:space:]]' AND "
        f"char_length(original_message) <= {MAX_MESSAGE_LENGTH}",
    )
    op.create_check_constraint(
        op.f("ck_order_captures_submitted_state"),
        "order_captures",
        "(status = 'submitted') = (order_id IS NOT NULL "
        "AND submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_order_captures_discarded_state"),
        "order_captures",
        "(status = 'discarded') = (discard_reason IS NOT NULL "
        "AND discarded_at IS NOT NULL AND discarded_by_user_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_order_captures_discard_reason_valid"),
        "order_captures",
        _reason_valid("discard_reason"),
    )

    # --- order_capture_lines ----------------------------------------------
    op.create_table(
        "order_capture_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("capture_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("product_start_offset", sa.Integer(), nullable=True),
        sa.Column("product_end_offset", sa.Integer(), nullable=True),
        sa.Column("product_text", sa.Text(), nullable=True),
        sa.Column("quantity_text", sa.String(length=40), nullable=True),
        sa.Column("suggested_quantity", sa.Integer(), nullable=True),
        sa.Column("unit_text", sa.String(length=40), nullable=True),
        sa.Column("unit_hint", sa.String(length=20), nullable=False),
        sa.Column("proposed_product_id", sa.Uuid(), nullable=True),
        sa.Column("proposed_score", sa.Integer(), nullable=True),
        sa.Column("match_reasons", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("unit_confirmed", sa.Boolean(), nullable=False),
        sa.Column("quantity_cases", sa.Integer(), nullable=True),
        sa.Column("unit_confirmed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("unit_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(length=20), nullable=False),
        sa.Column("selected_product_id", sa.Uuid(), nullable=True),
        sa.Column("exclusion_reason", sa.Text(), nullable=True),
        sa.Column("remember_alias", sa.Boolean(), nullable=False),
        sa.Column("replace_existing_alias", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "line_number > 0", name=op.f("ck_order_capture_lines_line_number_positive")
        ),
        sa.CheckConstraint(
            "start_offset >= 0 AND end_offset > start_offset",
            name=op.f("ck_order_capture_lines_offsets_valid"),
        ),
        sa.CheckConstraint(
            "(product_text IS NULL) = (product_start_offset IS NULL) "
            "AND (product_text IS NULL) = (product_end_offset IS NULL) "
            "AND (product_start_offset IS NULL OR ("
            "product_start_offset >= start_offset "
            "AND product_end_offset > product_start_offset "
            "AND product_end_offset <= end_offset))",
            name=op.f("ck_order_capture_lines_product_offsets_valid"),
        ),
        sa.CheckConstraint(
            "suggested_quantity IS NULL OR suggested_quantity > 0",
            name=op.f("ck_order_capture_lines_suggested_quantity_positive"),
        ),
        sa.CheckConstraint(
            _in_list("unit_hint", UNIT_HINTS),
            name=op.f("ck_order_capture_lines_unit_hint_valid"),
        ),
        sa.CheckConstraint(
            "(proposed_product_id IS NULL) = (proposed_score IS NULL)",
            name=op.f("ck_order_capture_lines_proposal_has_score"),
        ),
        sa.CheckConstraint(
            "proposed_score IS NULL OR proposed_score BETWEEN 0 AND 100",
            name=op.f("ck_order_capture_lines_proposed_score_range"),
        ),
        sa.CheckConstraint(
            "unit_confirmed = (quantity_cases IS NOT NULL) "
            "AND unit_confirmed = (unit_confirmed_by_user_id IS NOT NULL) "
            "AND unit_confirmed = (unit_confirmed_at IS NOT NULL)",
            name=op.f("ck_order_capture_lines_unit_confirmation_complete"),
        ),
        sa.CheckConstraint(
            f"quantity_cases IS NULL OR quantity_cases BETWEEN 1 AND {MAX_QUANTITY}",
            name=op.f("ck_order_capture_lines_quantity_cases_range"),
        ),
        sa.CheckConstraint(
            _in_list("resolution", LINE_RESOLUTIONS),
            name=op.f("ck_order_capture_lines_resolution_valid"),
        ),
        sa.CheckConstraint(
            "resolution <> 'included' "
            "OR (selected_product_id IS NOT NULL AND unit_confirmed)",
            name=op.f("ck_order_capture_lines_included_complete"),
        ),
        sa.CheckConstraint(
            "resolution = 'included' OR selected_product_id IS NULL",
            name=op.f("ck_order_capture_lines_selection_only_when_included"),
        ),
        sa.CheckConstraint(
            "(resolution = 'excluded') = (exclusion_reason IS NOT NULL)",
            name=op.f("ck_order_capture_lines_excluded_has_reason"),
        ),
        sa.CheckConstraint(
            _reason_valid("exclusion_reason"),
            name=op.f("ck_order_capture_lines_exclusion_reason_valid"),
        ),
        sa.CheckConstraint(
            "NOT remember_alias OR (resolution = 'included' "
            "AND product_text IS NOT NULL "
            "AND selected_product_id IS DISTINCT FROM proposed_product_id)",
            name=op.f("ck_order_capture_lines_alias_only_when_corrected"),
        ),
        sa.CheckConstraint(
            "NOT replace_existing_alias OR remember_alias",
            name=op.f("ck_order_capture_lines_replace_requires_alias"),
        ),
        sa.CheckConstraint(
            "(resolution = 'pending') = (reviewed_by_user_id IS NULL) "
            "AND (reviewed_by_user_id IS NULL) = (reviewed_at IS NULL)",
            name=op.f("ck_order_capture_lines_review_recorded"),
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"],
            ["order_captures.id"],
            name=op.f("fk_order_capture_lines_capture_id_order_captures"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_product_id"],
            ["products.id"],
            name=op.f("fk_order_capture_lines_proposed_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["selected_product_id"],
            ["products.id"],
            name=op.f("fk_order_capture_lines_selected_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["unit_confirmed_by_user_id"],
            ["users.id"],
            name=op.f("fk_order_capture_lines_unit_confirmed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name=op.f("fk_order_capture_lines_reviewed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_capture_lines")),
        sa.UniqueConstraint(
            "capture_id", "line_number", name="order_capture_line_number_unique"
        ),
    )
    op.create_index(
        "ix_capture_line_capture", "order_capture_lines", ["capture_id"], unique=False
    )

    # --- retailer_product_aliases -----------------------------------------
    op.create_table(
        "retailer_product_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("retailer_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("alias_text", sa.String(length=MAX_ALIAS), nullable=False),
        sa.Column("normalized_alias", sa.String(length=MAX_ALIAS), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_capture_line_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("replaced_by_alias_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "char_length(btrim(alias_text)) > 0 "
            "AND char_length(btrim(normalized_alias)) > 0",
            name=op.f("ck_retailer_product_aliases_alias_not_blank"),
        ),
        sa.CheckConstraint(
            "is_active = (deactivated_at IS NULL) "
            "AND (deactivated_at IS NULL) = (deactivated_by_user_id IS NULL)",
            name=op.f("ck_retailer_product_aliases_deactivation_recorded"),
        ),
        sa.CheckConstraint(
            "replaced_by_alias_id IS NULL OR NOT is_active",
            name=op.f("ck_retailer_product_aliases_replaced_alias_inactive"),
        ),
        sa.ForeignKeyConstraint(
            ["retailer_id"],
            ["retailers.id"],
            name=op.f("fk_retailer_product_aliases_retailer_id_retailers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_retailer_product_aliases_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_retailer_product_aliases_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deactivated_by_user_id"],
            ["users.id"],
            name=op.f("fk_retailer_product_aliases_deactivated_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        # Explicit short names: the conventional ones exceed PostgreSQL's
        # 63-character identifier limit (they match app/models/capture.py).
        sa.ForeignKeyConstraint(
            ["source_capture_line_id"],
            ["order_capture_lines.id"],
            name="fk_retailer_aliases_source_capture_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_alias_id"],
            ["retailer_product_aliases.id"],
            name="fk_retailer_aliases_replaced_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retailer_product_aliases")),
    )
    op.create_index(
        "uq_retailer_alias_active",
        "retailer_product_aliases",
        ["retailer_id", "normalized_alias"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_retailer_alias_retailer",
        "retailer_product_aliases",
        ["retailer_id", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    _require_empty(("retailer_product_aliases", "order_captures"), "downgrade")

    op.drop_index("ix_retailer_alias_retailer", table_name="retailer_product_aliases")
    op.drop_index("uq_retailer_alias_active", table_name="retailer_product_aliases")
    op.drop_table("retailer_product_aliases")

    op.drop_index("ix_capture_line_capture", table_name="order_capture_lines")
    op.drop_table("order_capture_lines")

    for name in (
        "discard_reason_valid",
        "discarded_state",
        "submitted_state",
        "message_valid",
        "version_positive",
        "channel_valid",
        "status_valid",
    ):
        op.drop_constraint(
            op.f(f"ck_order_captures_{name}"), "order_captures", type_="check"
        )
    op.drop_constraint(
        "order_capture_client_request_unique", "order_captures", type_="unique"
    )
    op.drop_constraint("order_capture_order_unique", "order_captures", type_="unique")

    op.drop_constraint(
        op.f("fk_order_captures_order_id_orders"), "order_captures", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_order_captures_order_id_orders"),
        "order_captures",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in reversed(CAPTURE_ACTOR_COLUMNS):
        op.drop_constraint(
            op.f(f"fk_order_captures_{column}_users"),
            "order_captures",
            type_="foreignkey",
        )

    op.add_column(
        "order_captures",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("order_captures", "discard_reason", new_column_name="review_notes")
    for column in (
        "updated_at",
        "discarded_by_user_id",
        "discarded_at",
        "submitted_by_user_id",
        "submitted_at",
        "request_fingerprint",
        "client_request_id",
        "version",
        "interpreter",
        "channel",
        "created_by_user_id",
    ):
        op.drop_column("order_captures", column)
