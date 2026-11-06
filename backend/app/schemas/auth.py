"""Request and response schemas for /api/v1/auth."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.phone import normalize_phone_number
from app.models import User, UserRole

OTP_REQUESTED_MESSAGE = (
    "If this number belongs to an active OrderBridge account, a one-time code "
    "has been issued. It expires in a few minutes."
)
INVALID_CODE_MESSAGE = "The code is invalid or has expired. Request a new code."


class PhoneNumberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_number: str = Field(
        min_length=10,
        max_length=20,
        description="Indian mobile number; normalised to +91XXXXXXXXXX.",
        examples=["+919000000001"],
    )

    @field_validator("phone_number")
    @classmethod
    def _normalise_phone(cls, value: str) -> str:
        return normalize_phone_number(value)


class OtpRequest(PhoneNumberRequest):
    """Request a one-time code."""


class OtpVerifyRequest(PhoneNumberRequest):
    """Submit a one-time code."""

    code: str = Field(
        pattern=r"^[0-9]{6}$", description="Six-digit code.", examples=["123456"]
    )


class OtpRequestAccepted(BaseModel):
    """Generic response, identical for known and unknown numbers."""

    message: str = Field(examples=[OTP_REQUESTED_MESSAGE])
    debug_otp: str | None = Field(
        default=None,
        description=(
            "Development mock delivery only (AUTH_DEBUG_OTP=true, never in "
            "production). Present only when a code was issued for an active "
            "account; omitted from the response otherwise."
        ),
    )


class RetailerProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_name: str
    owner_name: str
    address: str


class AuthenticatedUser(BaseModel):
    """The signed-in user. Contains no credit, pricing or session data."""

    id: UUID
    full_name: str
    phone_number: str
    role: UserRole
    retailer: RetailerProfile | None = Field(
        description="The retailer business, for retailer accounts only."
    )

    @classmethod
    def from_user(cls, user: User) -> "AuthenticatedUser":
        retailer = (
            RetailerProfile.model_validate(user.retailer)
            if user.role == UserRole.RETAILER and user.retailer is not None
            else None
        )
        return cls(
            id=user.id,
            full_name=user.full_name,
            phone_number=user.phone_number,
            role=UserRole(user.role),
            retailer=retailer,
        )


class ErrorResponse(BaseModel):
    detail: str
