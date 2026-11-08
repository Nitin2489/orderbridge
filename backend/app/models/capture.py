"""Captured order messages, their reviewed lines and retailer aliases.

A capture holds a message exactly as received and the lines an interpreter
proposed from it. Proposals are suggestions only: every line is decided by a
person (included with a product and a confirmed case quantity, or excluded
with a reason). Submitting a capture creates a PENDING_REVIEW order through
the existing retailer order service; only the existing order desk
confirmation deducts stock or uses credit.

Original text is never modified: original_message is stored as received,
and each line's original_text and product_text are exact substrings of it,
located by their offsets.

Phase A uses a deterministic rule-based interpreter (interpreter
"rules@1"); it is not an AI model.
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import (
    CaptureChannel,
    CaptureLineResolution,
    CaptureStatus,
    ConfirmedUnit,
    UnitHint,
)

if TYPE_CHECKING:
    from app.models.catalog import Product, Retailer
    from app.models.sales import Order

#: Longest message accepted, in characters.
MAX_CAPTURE_MESSAGE_LENGTH = 2000
#: Most lines one capture may produce (the order line limit).
MAX_CAPTURE_LINES = 50
#: Largest case quantity a line may order (the order line limit).
MAX_CAPTURE_QUANTITY = 10_000
#: Largest quantity a reviewer may enter in the confirmed unit (cases or
#: pieces); the case quantity derived from it is still limited above.
MAX_CONFIRMED_QUANTITY = 1_000_000
#: Reason length limits, after trimming.
MIN_REASON_LENGTH = 3
MAX_REASON_LENGTH = 500
#: Longest product phrase stored as an alias.
MAX_ALIAS_LENGTH = 200


def _values_in(column: str, enum: type[StrEnum]) -> str:
    """SQL check: column IN ('a', 'b', ...) for every value of the enum."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


def _reason_valid(column: str) -> str:
    """SQL check: NULL, or a trimmed length within the reason limits."""
    return (
        f"{column} IS NULL OR char_length(btrim({column})) "
        f"BETWEEN {MIN_REASON_LENGTH} AND {MAX_REASON_LENGTH}"
    )


class OrderCapture(Base):
    __tablename__ = "order_captures"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    retailer_id: Mapped[UUID] = mapped_column(
        ForeignKey("retailers.id", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    channel: Mapped[CaptureChannel] = mapped_column(
        String(20), default=CaptureChannel.TEXT
    )
    # Exactly as received: never trimmed or normalised.
    original_message: Mapped[str] = mapped_column(Text)
    # Which interpreter produced the lines, e.g. "rules@1".
    interpreter: Mapped[str] = mapped_column(String(40))
    # The interpreter's validated raw output, kept for audit.
    extracted_data: Mapped[dict] = mapped_column(JSON)
    status: Mapped[CaptureStatus] = mapped_column(
        String(30), default=CaptureStatus.NEEDS_REVIEW
    )
    # Incremented on every change; changes must name the version they saw.
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Idempotent creation: unique per creating user, with the fingerprint of
    # the request it was first used for.
    client_request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    # The PENDING_REVIEW order created on submission. Orders are never
    # deleted (RESTRICT), so a submitted capture always keeps its order.
    order_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    discard_reason: Mapped[str | None] = mapped_column(Text)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discarded_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    retailer: Mapped["Retailer"] = relationship()
    order: Mapped["Order | None"] = relationship(back_populates="captures")
    lines: Mapped[list["OrderCaptureLine"]] = relationship(
        back_populates="capture",
        cascade="all, delete-orphan",
        order_by="OrderCaptureLine.line_number",
    )

    __table_args__ = (
        CheckConstraint(_values_in("status", CaptureStatus), name="status_valid"),
        CheckConstraint(_values_in("channel", CaptureChannel), name="channel_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "original_message ~ '[^[:space:]]' AND "
            f"char_length(original_message) <= {MAX_CAPTURE_MESSAGE_LENGTH}",
            name="message_valid",
        ),
        CheckConstraint(
            "(status = 'submitted') = (order_id IS NOT NULL "
            "AND submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL)",
            name="submitted_state",
        ),
        CheckConstraint(
            "(status = 'discarded') = (discard_reason IS NOT NULL "
            "AND discarded_at IS NOT NULL AND discarded_by_user_id IS NOT NULL)",
            name="discarded_state",
        ),
        CheckConstraint(_reason_valid("discard_reason"), name="discard_reason_valid"),
        UniqueConstraint("order_id", name="order_capture_order_unique"),
        UniqueConstraint(
            "created_by_user_id",
            "client_request_id",
            name="order_capture_client_request_unique",
        ),
        Index("ix_capture_retailer_created", "retailer_id", "created_at"),
        Index("ix_capture_status_created", "status", "created_at"),
    )


class OrderCaptureLine(Base):
    """One proposed line of a captured message and the reviewer's decision.

    Interpretation fields (offsets, texts, suggestions, proposal, candidates)
    are fixed when the capture is created. Review fields (unit confirmation,
    resolution, selected product, alias choice) are set only by a person.
    """

    __tablename__ = "order_capture_lines"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    capture_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_captures.id", ondelete="CASCADE")
    )
    # 1-based position within the capture.
    line_number: Mapped[int] = mapped_column(Integer)

    # Interpretation (fixed at creation). original_text is
    # original_message[start_offset:end_offset]; product_text likewise.
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    original_text: Mapped[str] = mapped_column(Text)
    product_start_offset: Mapped[int | None] = mapped_column(Integer)
    product_end_offset: Mapped[int | None] = mapped_column(Integer)
    product_text: Mapped[str | None] = mapped_column(Text)
    quantity_text: Mapped[str | None] = mapped_column(String(40))
    suggested_quantity: Mapped[int | None] = mapped_column(Integer)
    unit_text: Mapped[str | None] = mapped_column(String(40))
    unit_hint: Mapped[UnitHint] = mapped_column(String(20), default=UnitHint.NONE)
    proposed_product_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT")
    )
    # Heuristic match score of the proposal (0-100); not a probability.
    proposed_score: Mapped[int | None] = mapped_column(Integer)
    # Reasons for the proposal (or for having none), as display strings.
    match_reasons: Mapped[list] = mapped_column(JSON, default=list)
    # Every ranked candidate as scored at creation: a list of
    # {product_id, sku, name, score, band, weak, reasons}. Never recomputed,
    # so the alternatives shown do not change during review.
    candidates: Mapped[list] = mapped_column(JSON, default=list)

    # Review (set only by a person).
    # The unit decision exactly as made: "case" or "piece", and the quantity
    # entered in that unit. Independent of the product selection.
    unit_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_unit: Mapped[ConfirmedUnit | None] = mapped_column(String(10))
    confirmed_quantity: Mapped[int | None] = mapped_column(Integer)
    # Whole cases to order, derived from the unit decision and the selected
    # product's units_per_case. Set only while the line is included.
    quantity_cases: Mapped[int | None] = mapped_column(Integer)
    unit_confirmed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    unit_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[CaptureLineResolution] = mapped_column(
        String(20), default=CaptureLineResolution.PENDING
    )
    selected_product_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT")
    )
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    # Opt-in alias for a corrected line, created when the capture is
    # submitted. replace_existing_alias is the explicit decision to replace
    # an active alias of this retailer that maps the phrase elsewhere.
    remember_alias: Mapped[bool] = mapped_column(Boolean, default=False)
    replace_existing_alias: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    capture: Mapped["OrderCapture"] = relationship(back_populates="lines")
    proposed_product: Mapped["Product | None"] = relationship(
        foreign_keys=[proposed_product_id]
    )
    selected_product: Mapped["Product | None"] = relationship(
        foreign_keys=[selected_product_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "capture_id", "line_number", name="order_capture_line_number_unique"
        ),
        CheckConstraint("line_number > 0", name="line_number_positive"),
        CheckConstraint(
            "start_offset >= 0 AND end_offset > start_offset", name="offsets_valid"
        ),
        CheckConstraint(
            "(product_text IS NULL) = (product_start_offset IS NULL) "
            "AND (product_text IS NULL) = (product_end_offset IS NULL) "
            "AND (product_start_offset IS NULL OR ("
            "product_start_offset >= start_offset "
            "AND product_end_offset > product_start_offset "
            "AND product_end_offset <= end_offset))",
            name="product_offsets_valid",
        ),
        CheckConstraint(
            "suggested_quantity IS NULL OR suggested_quantity > 0",
            name="suggested_quantity_positive",
        ),
        CheckConstraint(_values_in("unit_hint", UnitHint), name="unit_hint_valid"),
        CheckConstraint(
            "(proposed_product_id IS NULL) = (proposed_score IS NULL)",
            name="proposal_has_score",
        ),
        CheckConstraint(
            "proposed_score IS NULL OR proposed_score BETWEEN 0 AND 100",
            name="proposed_score_range",
        ),
        CheckConstraint(
            "unit_confirmed = (confirmed_unit IS NOT NULL) "
            "AND unit_confirmed = (confirmed_quantity IS NOT NULL) "
            "AND unit_confirmed = (unit_confirmed_by_user_id IS NOT NULL) "
            "AND unit_confirmed = (unit_confirmed_at IS NOT NULL)",
            name="unit_confirmation_complete",
        ),
        CheckConstraint(
            "confirmed_unit IS NULL OR " + _values_in("confirmed_unit", ConfirmedUnit),
            name="confirmed_unit_valid",
        ),
        CheckConstraint(
            f"confirmed_quantity IS NULL OR confirmed_quantity BETWEEN 1 "
            f"AND {MAX_CONFIRMED_QUANTITY}",
            name="confirmed_quantity_range",
        ),
        CheckConstraint(
            f"quantity_cases IS NULL OR quantity_cases BETWEEN 1 "
            f"AND {MAX_CAPTURE_QUANTITY}",
            name="quantity_cases_range",
        ),
        CheckConstraint(
            "resolution = 'included' OR quantity_cases IS NULL",
            name="cases_only_when_included",
        ),
        CheckConstraint(
            _values_in("resolution", CaptureLineResolution), name="resolution_valid"
        ),
        CheckConstraint(
            "resolution <> 'included' OR (selected_product_id IS NOT NULL "
            "AND unit_confirmed AND quantity_cases IS NOT NULL)",
            name="included_complete",
        ),
        CheckConstraint(
            "resolution = 'included' OR selected_product_id IS NULL",
            name="selection_only_when_included",
        ),
        CheckConstraint(
            "(resolution = 'excluded') = (exclusion_reason IS NOT NULL)",
            name="excluded_has_reason",
        ),
        CheckConstraint(
            _reason_valid("exclusion_reason"), name="exclusion_reason_valid"
        ),
        CheckConstraint(
            "NOT remember_alias OR (resolution = 'included' "
            "AND product_text IS NOT NULL "
            "AND selected_product_id IS DISTINCT FROM proposed_product_id)",
            name="alias_only_when_corrected",
        ),
        CheckConstraint(
            "NOT replace_existing_alias OR remember_alias",
            name="replace_requires_alias",
        ),
        CheckConstraint(
            "(resolution = 'pending') = (reviewed_by_user_id IS NULL) "
            "AND (reviewed_by_user_id IS NULL) = (reviewed_at IS NULL)",
            name="review_recorded",
        ),
        Index("ix_capture_line_capture", "capture_id"),
    )


class RetailerProductAlias(Base):
    """A retailer's own phrase for a product, saved from a corrected line.

    Stores the product phrase only (for example "biscuit chhota"), never
    the quantity or unit. One active alias per retailer and normalised
    phrase. An alias is never overwritten: replacing it deactivates the old
    row (recording who, when and its replacement) and adds a new one, so
    the history stays intact.
    """

    __tablename__ = "retailer_product_aliases"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    retailer_id: Mapped[UUID] = mapped_column(
        ForeignKey("retailers.id", ondelete="RESTRICT")
    )
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT")
    )
    # The phrase as written in the message, and its normalised lookup form.
    alias_text: Mapped[str] = mapped_column(String(MAX_ALIAS_LENGTH))
    normalized_alias: Mapped[str] = mapped_column(String(MAX_ALIAS_LENGTH))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    source_capture_line_id: Mapped[UUID | None] = mapped_column(
        # Explicit name: the conventional one exceeds PostgreSQL's
        # 63-character identifier limit.
        ForeignKey(
            "order_capture_lines.id",
            ondelete="RESTRICT",
            name="fk_retailer_aliases_source_capture_line",
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    replaced_by_alias_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "retailer_product_aliases.id",
            ondelete="RESTRICT",
            name="fk_retailer_aliases_replaced_by",
        )
    )

    product: Mapped["Product"] = relationship()

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(alias_text)) > 0 "
            "AND char_length(btrim(normalized_alias)) > 0",
            name="alias_not_blank",
        ),
        CheckConstraint(
            "is_active = (deactivated_at IS NULL) "
            "AND (deactivated_at IS NULL) = (deactivated_by_user_id IS NULL)",
            name="deactivation_recorded",
        ),
        CheckConstraint(
            "replaced_by_alias_id IS NULL OR NOT is_active",
            name="replaced_alias_inactive",
        ),
        # At most one active alias per retailer and phrase.
        Index(
            "uq_retailer_alias_active",
            "retailer_id",
            "normalized_alias",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_retailer_alias_retailer", "retailer_id", "is_active"),
    )
