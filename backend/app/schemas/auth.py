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
    """Generic response, identical for known and unknown numbers.

    demo_code is the only way a code can appear in a response: only for
    the demonstration accounts listed in AUTH_DEMO_OTP_NUMBERS (empty by
    default), and only to a browser that has unlocked reviewer access with
    the private AUTH_DEMO_ACCESS_KEY. A reviewer of a deployment without an
    SMS provider can then sign in to those synthetic accounts. In every
    other case it is null, so the response reveals nothing about accounts.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(examples=[OTP_REQUESTED_MESSAGE])
    demo_code: str | None = Field(
        default=None,
        description=(
            "Only for numbers in AUTH_DEMO_OTP_NUMBERS (demo accounts), only "
            "for a browser with reviewer access, and only when a new code was "
            "issued; null otherwise."
        ),
    )


DEMO_ACCESS_DENIED_MESSAGE = "The reviewer access key is not valid."


class DemoAccessStatus(BaseModel):
    """Whether reviewer access exists here and whether this browser has it.

    Never contains the access key or the access token.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(description="True when this deployment offers demo accounts.")
    active: bool = Field(
        description="True when this browser has unlocked reviewer access."
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
