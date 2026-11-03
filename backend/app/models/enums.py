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


class OrderSource(StrEnum):
    RETAILER_APP = "retailer_app"
    AI_TEXT = "ai_text"
    SALESMAN = "salesman"


class CaptureStatus(StrEnum):
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    READY_FOR_REVIEW = "ready_for_review"
    CONFIRMED = "confirmed"
    FAILED = "failed"
