from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RateLimitBucket(Base):
    """Request count for one rate-limit key in one fixed window.

    key_hash is an HMAC of the rule name and the identifier (client IP or
    normalised phone number), so neither is stored in readable form. Rows
    whose window has ended (expires_at in the past) are no longer read and
    may be deleted at any time (app.services.rate_limit.purge_expired).
    """

    __tablename__ = "rate_limit_buckets"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    hits: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("hits > 0", name="hits_positive"),
        CheckConstraint("expires_at > window_start", name="window_valid"),
    )
