from enum import StrEnum


class UserRole(StrEnum):
    RETAILER = "retailer"
    ORDER_DESK = "order_desk"


class ProductStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class SchemeType(StrEnum):
    FREE_GOODS = "free_goods"
    SLAB_DISCOUNT = "slab_discount"


class OrderStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    ON_HOLD = "on_hold"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPORTED = "exported"


class OrderEventType(StrEnum):
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    PLACED_ON_HOLD = "placed_on_hold"
    REJECTED = "rejected"
    EXPORTED = "exported"
    HOLD_RELEASED = "hold_released"  # reserved; no endpoint creates it yet


class OrderSource(StrEnum):
    RETAILER_APP = "retailer_app"
    AI_TEXT = "ai_text"
    SALESMAN = "salesman"


class CaptureStatus(StrEnum):
    """Lifecycle of a captured message (see app.models.capture).

    NEEDS_REVIEW: at least one line is still pending.
    READY: every line is included or excluded, at least one is included.
    SUBMITTED: a PENDING_REVIEW order was created from it (terminal). This is
        not order confirmation: the order desk still confirms the order.
    DISCARDED: discarded by a reviewer, with a reason (terminal).
    FAILED: reserved for a future AI provider whose interpretation failed;
        the rule-based interpreter never sets it.
    """

    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    SUBMITTED = "submitted"
    DISCARDED = "discarded"
    FAILED = "failed"


class CaptureChannel(StrEnum):
    """How the message arrived. Photo and voice are pending, not built."""

    TEXT = "text"


class CaptureLineResolution(StrEnum):
    """A reviewer's decision for one captured line."""

    PENDING = "pending"
    INCLUDED = "included"
    EXCLUDED = "excluded"


class ConfirmedUnit(StrEnum):
    """The unit a person confirmed for a line's quantity.

    Orders are placed in whole cases: a quantity confirmed in pieces is
    converted with the selected product's units_per_case, and only when it
    divides exactly. Nothing is ever confirmed automatically.
    """

    CASE = "case"
    PIECE = "piece"


class UnitHint(StrEnum):
    """What the unit word in a line suggests. Only a hint: a person always
    confirms the quantity in cases."""

    CASE_WORD = "case_word"  # e.g. "peti", "case", "carton"
    PIECE_WORD = "piece_word"  # e.g. "packet", "piece"
    NONE = "none"  # no unit word
