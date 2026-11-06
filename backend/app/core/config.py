import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.origins import InvalidOriginError, canonicalize_origin

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Minimum length of AUTH_SECRET_KEY in production.
MIN_PRODUCTION_SECRET_LENGTH = 32

# A random secret of 32+ characters has far more distinct characters than
# this; fewer indicates a repeated or patterned value such as "aaaa...".
MIN_PRODUCTION_SECRET_DISTINCT_CHARACTERS = 8

# Case-insensitive fragments that mark a placeholder or development value.
# All are 6+ characters, so a randomly generated secret practically never
# contains one by chance.
PLACEHOLDER_SECRET_MARKERS = (
    "changeme",
    "change-me",
    "change_me",
    "replaceme",
    "replace-me",
    "replace_me",
    "placeholder",
    "your-secret",
    "your_secret",
    "yoursecret",
    "secret-key",
    "secret_key",
    "secretkey",
    "example",
    "insecure",
    "password",
    "development",
)


def secret_looks_like_placeholder(secret: str) -> bool:
    """True for obvious placeholder, template or patterned values.

    This is a guard against configuration mistakes, not an entropy test: no
    check on a string can prove it was randomly generated. Generating the
    production secret randomly and storing it in the host's secret manager
    remain deployment responsibilities.
    """
    lowered = secret.lower()

    return (
        "<" in secret
        or ">" in secret
        or any(marker in lowered for marker in PLACEHOLDER_SECRET_MARKERS)
        or len(set(secret)) < MIN_PRODUCTION_SECRET_DISTINCT_CHARACTERS
    )


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    Authentication settings (all optional outside production):

    - AUTH_SECRET_KEY: HMAC key for OTP digests. Required in production with at
      least 32 characters; obvious placeholders are rejected (see
      secret_looks_like_placeholder). If empty outside production, a random
      key is generated per process; outstanding OTPs then stop verifying
      after a restart (sessions are unaffected).
    - AUTH_OTP_TTL_SECONDS, AUTH_OTP_MAX_ATTEMPTS,
      AUTH_OTP_RESEND_COOLDOWN_SECONDS, AUTH_SESSION_TTL_SECONDS: lifetimes
      and limits.
    - AUTH_SESSION_COOKIE_NAME, AUTH_SESSION_COOKIE_SECURE,
      AUTH_SESSION_COOKIE_SAMESITE: session cookie. Secure defaults to true;
      set it to false only for local HTTP development. SameSite "none"
      requires Secure.
    - AUTH_DEBUG_OTP: development mock delivery; returns the OTP in the API
      response. Rejected when APP_ENV=production.
    - FRONTEND_ORIGINS: JSON list of trusted frontend origins, used for CORS
      and for the CSRF origin check. Canonicalised and de-duplicated. Must be
      https in production.

    Production deployment assumption: browsers reach the API same-origin
    through the Next.js proxy (browser -> Next.js -> FastAPI). The session
    cookie is then first-party: HttpOnly, Secure, SameSite=lax, host-only
    (no Domain), Path=/. FRONTEND_ORIGINS lists the public frontend origin,
    and the proxy must forward the browser's Origin (or Referer) header and
    the Cookie / Set-Cookie headers unchanged.
    """

    app_name: str = "OrderBridge"
    app_version: str = "0.1.0"
    app_env: Literal["development", "testing", "production"] = "development"

    database_url: SecretStr = Field(repr=False)

    auth_secret_key: SecretStr | None = Field(default=None, repr=False)
    auth_otp_ttl_seconds: int = Field(default=300, gt=0)
    auth_otp_max_attempts: int = Field(default=5, gt=0)
    auth_otp_resend_cooldown_seconds: int = Field(default=60, ge=0)
    auth_session_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)
    auth_session_cookie_name: str = Field(
        default="orderbridge_session", pattern=r"^[A-Za-z0-9_-]+$"
    )
    auth_session_cookie_secure: bool = True
    auth_session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_debug_otp: bool = False
    frontend_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Validation errors must never echo secrets such as AUTH_SECRET_KEY
        # or the password inside DATABASE_URL.
        hide_input_in_errors=True,
    )

    @field_validator("frontend_origins")
    @classmethod
    def _normalise_origins(cls, origins: list[str]) -> list[str]:
        """Canonicalise (see app.core.origins) and de-duplicate, keeping order."""
        normalised: list[str] = []

        for origin in origins:
            try:
                canonical = canonicalize_origin(origin)
            except InvalidOriginError as error:
                raise ValueError(
                    f"Invalid frontend origin {origin!r}: {error} Use "
                    "scheme://host[:port] with no wildcard, path, query or "
                    "credentials."
                ) from None

            if canonical not in normalised:
                normalised.append(canonical)

        return normalised

    @model_validator(mode="after")
    def _check_auth_settings(self) -> Self:
        if (
            self.auth_session_cookie_samesite == "none"
            and not self.auth_session_cookie_secure
        ):
            raise ValueError(
                "AUTH_SESSION_COOKIE_SAMESITE=none requires "
                "AUTH_SESSION_COOKIE_SECURE=true."
            )

        secret = self.auth_secret_key.get_secret_value() if self.auth_secret_key else ""

        if self.app_env == "production":
            problems = []

            if len(secret) < MIN_PRODUCTION_SECRET_LENGTH:
                problems.append(
                    "AUTH_SECRET_KEY must be set to a random value of at least "
                    f"{MIN_PRODUCTION_SECRET_LENGTH} characters"
                )
            elif secret_looks_like_placeholder(secret):
                problems.append(
                    "AUTH_SECRET_KEY looks like a placeholder or development "
                    "value; generate a random one"
                )
            if self.auth_debug_otp:
                problems.append("AUTH_DEBUG_OTP must be false")
            if not self.auth_session_cookie_secure:
                problems.append("AUTH_SESSION_COOKIE_SECURE must be true")
            if not self.frontend_origins:
                problems.append("FRONTEND_ORIGINS must list the frontend origin")
            if any(not o.startswith("https://") for o in self.frontend_origins):
                problems.append("FRONTEND_ORIGINS must use https")

            if problems:
                raise ValueError(
                    "Unsafe production configuration: " + "; ".join(problems) + "."
                )
        elif not secret:
            # Development and testing only: a random key for this process.
            self.auth_secret_key = SecretStr(secrets.token_urlsafe(32))

        return self

    @property
    def debug_otp_enabled(self) -> bool:
        """True only when mock OTP delivery is on and this is not production."""
        return self.auth_debug_otp and self.app_env != "production"

    @property
    def auth_secret(self) -> str:
        """The OTP HMAC key. Always set once validation has completed."""
        assert self.auth_secret_key is not None
        return self.auth_secret_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Return a cached application settings instance."""
    return Settings()


settings = get_settings()
