import hmac
import ipaddress
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.demo_accounts import DEMO_ACCOUNT_NUMBERS
from app.core.origins import InvalidOriginError, canonicalize_origin
from app.core.phone import InvalidPhoneNumberError, normalize_phone_number

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

    APP_ENV is required and must be one of development, testing, staging or
    production. A missing or misspelt value fails startup instead of
    silently running with development settings.

    Authentication settings:

    - AUTH_SECRET_KEY: HMAC key for OTP digests. Required in staging and
      production with at least 32 characters; obvious placeholders are
      rejected (see secret_looks_like_placeholder). If empty in development
      or testing, a random key is generated per process; outstanding OTPs
      then stop verifying after a restart (sessions are unaffected).
    - AUTH_OTP_TTL_SECONDS, AUTH_OTP_MAX_ATTEMPTS,
      AUTH_OTP_RESEND_COOLDOWN_SECONDS, AUTH_SESSION_TTL_SECONDS: lifetimes
      and limits.
    - AUTH_OTP_LOCKOUT_THRESHOLD, AUTH_OTP_LOCKOUT_WINDOW_SECONDS: cumulative
      brute-force protection. Once a phone number has this many failed
      verifications within the rolling window, across all of its challenges,
      verification and issuance are refused until enough failures leave the
      window. A successful sign-in clears earlier failures. Defaults: 10
      failures in 24 hours.
    - AUTH_SESSION_COOKIE_NAME, AUTH_SESSION_COOKIE_SECURE,
      AUTH_SESSION_COOKIE_SAMESITE: session cookie. Secure defaults to true;
      set it to false only for local HTTP development. SameSite "none"
      requires Secure.
    - AUTH_DEBUG_OTP: local development delivery. The backend prints each
      issued code to its own terminal (app.services.otp_delivery); codes are
      never returned in API responses in any environment. Allowed only when
      APP_ENV=development; rejected in testing, staging and production.
    - AUTH_DEV_OTP_OUTBOX_PATH: optional local development "mock SMS outbox".
      Each issued code is also appended, as a JSON line, to this file, so
      end-to-end tests on the same machine can read it from disk. Only
      allowed with APP_ENV=development and AUTH_DEBUG_OTP=true. A relative
      path is resolved against the repository root; the directory must exist.
    - AUTH_DEMO_OTP_NUMBERS, AUTH_DEMO_ACCESS_KEY: reviewer access for a
      deployment without an SMS provider (app.services.demo_access). Empty
      by default. AUTH_DEMO_OTP_NUMBERS may list only the pre-provisioned
      synthetic accounts (app.core.demo_accounts). Whenever it lists any,
      AUTH_DEMO_ACCESS_KEY is required in every environment: at least 32
      random characters, not a placeholder and not AUTH_SECRET_KEY. Only a
      browser that has unlocked reviewer access with that key sees the
      codes of those numbers, and only such a browser can request or
      verify codes for them.
    - FRONTEND_ORIGINS: JSON list of trusted frontend origins, used for CORS
      and for the CSRF origin check. Canonicalised and de-duplicated. Must be
      https in production.

    Abuse protection:

    - RATE_LIMIT_OTP_{REQUEST,VERIFY}_{IP,PHONE}_LIMIT and ..._WINDOW_SECONDS:
      fixed-window limits on the two unauthenticated OTP endpoints, per
      client IP and per normalised phone number, shared through PostgreSQL
      (app.services.rate_limit). Defaults: request-otp 30 per IP per 15
      minutes and 5 per number per hour; verify-otp 60 per IP per 15 minutes
      and 15 per number per hour. Exceeding one returns 429 + Retry-After.
    - RATE_LIMIT_CLEANUP_PROBABILITY: chance (0-1) that a rate-limited
      request also deletes a bounded batch of expired counters.
    - TRUSTED_PROXY_CIDRS: JSON list of networks whose X-Forwarded-For header
      is believed (app.core.client_ip). Empty (default): forwarded headers are
      ignored and the TCP peer address is the client. Never "everything".
    - MAX_REQUEST_BODY_BYTES: largest accepted request body (default 64 KiB);
      larger bodies get 413 before they are read into memory.
    - CAPTURE_INTERPRETER: "rules" (default, deterministic) or "gemini".
      GEMINI_API_KEY (secret; required for "gemini"), GEMINI_MODEL and
      GEMINI_TIMEOUT_SECONDS (default 8) configure the Gemini reader. Only
      the message text and product codes and names are sent to Gemini; any
      failure falls back to the rules, never failing a capture.
    - TALLY_SALES_LEDGER_NAME, TALLY_DISCOUNT_LEDGER_NAME and
      TALLY_VOUCHER_TYPE_NAME (default "Sales"): names used in the TallyPrime
      XML voucher download. The discount ledger is needed only for batches
      with a discount.

    Production deployment assumption: browsers reach the API same-origin
    through the Next.js proxy (browser -> Next.js -> FastAPI). The session
    cookie is then first-party: HttpOnly, Secure, SameSite=lax, host-only
    (no Domain), Path=/. FRONTEND_ORIGINS lists the public frontend origin,
    and the proxy must forward the browser's Origin (or Referer) header and
    the Cookie / Set-Cookie headers unchanged.
    """

    app_name: str = "OrderBridge"
    app_version: str = "0.1.0"
    # Required: no default, so a deployment cannot silently run as development.
    app_env: Literal["development", "testing", "staging", "production"]

    database_url: SecretStr = Field(repr=False)

    auth_secret_key: SecretStr | None = Field(default=None, repr=False)
    auth_otp_ttl_seconds: int = Field(default=300, gt=0)
    auth_otp_max_attempts: int = Field(default=5, gt=0)
    auth_otp_resend_cooldown_seconds: int = Field(default=60, ge=0)
    auth_otp_lockout_threshold: int = Field(default=10, gt=0)
    auth_otp_lockout_window_seconds: int = Field(default=24 * 60 * 60, gt=0)
    auth_session_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)
    auth_session_cookie_name: str = Field(
        default="orderbridge_session", pattern=r"^[A-Za-z0-9_-]+$"
    )
    auth_session_cookie_secure: bool = True
    auth_session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_debug_otp: bool = False
    auth_dev_otp_outbox_path: str | None = None
    # Demonstration accounts whose codes are returned in the request-otp
    # response to a browser with reviewer access, for a reviewed deployment
    # without an SMS provider. Empty by default; limited to the synthetic
    # accounts in app.core.demo_accounts.
    auth_demo_otp_numbers: list[str] = Field(default_factory=list)
    # The private reviewer access key. Never commit, log or return it.
    auth_demo_access_key: SecretStr | None = None
    frontend_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # Rate limits (fixed windows; windows at most one day).
    rate_limit_otp_request_ip_limit: int = Field(default=30, gt=0)
    rate_limit_otp_request_ip_window_seconds: int = Field(
        default=15 * 60, gt=0, le=24 * 60 * 60
    )
    rate_limit_otp_request_phone_limit: int = Field(default=5, gt=0)
    rate_limit_otp_request_phone_window_seconds: int = Field(
        default=60 * 60, gt=0, le=24 * 60 * 60
    )
    rate_limit_otp_verify_ip_limit: int = Field(default=60, gt=0)
    rate_limit_otp_verify_ip_window_seconds: int = Field(
        default=15 * 60, gt=0, le=24 * 60 * 60
    )
    rate_limit_otp_verify_phone_limit: int = Field(default=15, gt=0)
    rate_limit_otp_verify_phone_window_seconds: int = Field(
        default=60 * 60, gt=0, le=24 * 60 * 60
    )
    rate_limit_cleanup_probability: float = Field(default=0.01, ge=0, le=1)

    trusted_proxy_cidrs: list[str] = Field(default_factory=list)
    max_request_body_bytes: int = Field(default=64 * 1024, ge=1024, le=10 * 1024 * 1024)

    # Message capture interpreter (app.services.gemini_interpreter).
    # "rules" (default) needs nothing else; "gemini" needs GEMINI_API_KEY and
    # falls back to the rules whenever Gemini's answer cannot be used.
    capture_interpreter: Literal["rules", "gemini"] = "rules"
    gemini_api_key: SecretStr | None = None
    # Restricted to model-name characters: it is placed in the request URL.
    gemini_model: str = Field(
        default="gemini-flash-latest", pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,63}$"
    )
    gemini_timeout_seconds: float = Field(default=8.0, gt=0, le=30)

    # Tally XML voucher export (app.services.tally_xml_service). Ledger names
    # must match ledgers that exist in the Tally company; they have no
    # defaults so none is invented. Unset: the XML download explains what is
    # missing, and the CSV export is unaffected.
    tally_sales_ledger_name: str | None = Field(default=None, max_length=200)
    tally_discount_ledger_name: str | None = Field(default=None, max_length=200)
    tally_voucher_type_name: str = Field(default="Sales", max_length=200)

    @field_validator("tally_voucher_type_name")
    @classmethod
    def _voucher_type_not_blank(cls, value: str) -> str:
        """A blank voucher type would produce vouchers Tally cannot place."""
        if not value.strip():
            raise ValueError("TALLY_VOUCHER_TYPE_NAME must not be blank.")
        return value.strip()

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Validation errors must never echo secrets such as AUTH_SECRET_KEY
        # or the password inside DATABASE_URL.
        hide_input_in_errors=True,
    )

    @field_validator("auth_demo_otp_numbers")
    @classmethod
    def _normalise_demo_numbers(cls, numbers: list[str]) -> list[str]:
        """Normalise to +91XXXXXXXXXX and de-duplicate, keeping order."""
        normalised: list[str] = []
        for number in numbers:
            try:
                phone = normalize_phone_number(number)
            except InvalidPhoneNumberError:
                raise ValueError(
                    f"AUTH_DEMO_OTP_NUMBERS contains an invalid number: {number!r}."
                ) from None
            if phone not in DEMO_ACCOUNT_NUMBERS:
                raise ValueError(
                    "AUTH_DEMO_OTP_NUMBERS may list only the pre-provisioned "
                    f"demonstration accounts; {phone} is not one of them."
                )
            if phone not in normalised:
                normalised.append(phone)
        return normalised

    @field_validator("auth_demo_access_key", mode="before")
    @classmethod
    def _blank_access_key_is_unset(cls, value: object) -> object:
        """An empty AUTH_DEMO_ACCESS_KEY= line means "not configured"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _blank_gemini_key_is_unset(cls, value: object) -> object:
        """An empty GEMINI_API_KEY= line means "not configured"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_capture_interpreter(self) -> Self:
        if self.capture_interpreter == "gemini" and self.gemini_api_key is None:
            raise ValueError(
                "CAPTURE_INTERPRETER=gemini requires GEMINI_API_KEY; set the key "
                "or use CAPTURE_INTERPRETER=rules."
            )
        return self

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

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _normalise_trusted_proxies(cls, cidrs: list[str]) -> list[str]:
        """Canonical network strings; reject malformed and catch-all entries."""
        normalised: list[str] = []

        for cidr in cidrs:
            try:
                network = ipaddress.ip_network(cidr.strip(), strict=False)
            except ValueError:
                raise ValueError(
                    f"Invalid trusted proxy network {cidr!r}: use an IP address "
                    "or CIDR such as 10.0.0.0/8."
                ) from None

            if network.prefixlen == 0:
                raise ValueError(
                    f"Trusted proxy network {cidr!r} would trust every client's "
                    "X-Forwarded-For header; list the proxies' own networks."
                )

            if str(network) not in normalised:
                normalised.append(str(network))

        return normalised

    @property
    def trusted_proxy_networks(
        self,
    ) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(ipaddress.ip_network(cidr) for cidr in self.trusted_proxy_cidrs)

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

        if self.auth_debug_otp and self.app_env != "development":
            raise ValueError(
                "AUTH_DEBUG_OTP=true is only allowed when APP_ENV=development "
                f"(APP_ENV is {self.app_env})."
            )

        self._check_dev_otp_outbox()

        secret = self.auth_secret_key.get_secret_value() if self.auth_secret_key else ""
        self._check_demo_access(secret)
        deployed = self.app_env in ("staging", "production")

        if deployed:
            if len(secret) < MIN_PRODUCTION_SECRET_LENGTH:
                raise ValueError(
                    f"Unsafe {self.app_env} configuration: AUTH_SECRET_KEY must "
                    "be set to a random value of at least "
                    f"{MIN_PRODUCTION_SECRET_LENGTH} characters."
                )
            if secret_looks_like_placeholder(secret):
                raise ValueError(
                    f"Unsafe {self.app_env} configuration: AUTH_SECRET_KEY looks "
                    "like a placeholder or development value; generate a random "
                    "one."
                )

        if self.app_env == "production":
            problems = []

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
        elif not deployed and not secret:
            # Development and testing only: a random key for this process.
            self.auth_secret_key = SecretStr(secrets.token_urlsafe(32))

        return self

    def _check_demo_access(self, secret: str) -> None:
        """Demo numbers need a strong, separate reviewer access key.

        Applies in every environment, so a deployment can never return demo
        codes to a browser that has not unlocked reviewer access. Messages
        never contain the key.
        """
        if not self.auth_demo_otp_numbers:
            return

        key = (
            self.auth_demo_access_key.get_secret_value()
            if self.auth_demo_access_key
            else ""
        )
        if not key:
            raise ValueError(
                "AUTH_DEMO_OTP_NUMBERS is set, so AUTH_DEMO_ACCESS_KEY must be "
                "set to a private random value of at least "
                f"{MIN_PRODUCTION_SECRET_LENGTH} characters."
            )
        if len(key) < MIN_PRODUCTION_SECRET_LENGTH:
            raise ValueError(
                "AUTH_DEMO_ACCESS_KEY must be at least "
                f"{MIN_PRODUCTION_SECRET_LENGTH} characters."
            )
        if secret_looks_like_placeholder(key):
            raise ValueError(
                "AUTH_DEMO_ACCESS_KEY looks like a placeholder or patterned "
                "value; generate a random one."
            )
        if secret and hmac.compare_digest(key.encode(), secret.encode()):
            raise ValueError(
                "AUTH_DEMO_ACCESS_KEY must be different from AUTH_SECRET_KEY."
            )

    @field_validator("auth_dev_otp_outbox_path")
    @classmethod
    def _blank_outbox_is_unset(cls, value: str | None) -> str | None:
        """An empty AUTH_DEV_OTP_OUTBOX_PATH means "not configured"."""
        if value is None or not value.strip():
            return None
        return value.strip()

    def _check_dev_otp_outbox(self) -> None:
        """The outbox is a development-only file in an existing directory."""
        if self.auth_dev_otp_outbox_path is None:
            return

        if self.app_env != "development" or not self.auth_debug_otp:
            raise ValueError(
                "AUTH_DEV_OTP_OUTBOX_PATH is only allowed with "
                "APP_ENV=development and AUTH_DEBUG_OTP=true "
                f"(APP_ENV is {self.app_env})."
            )

        path = self._resolved_outbox_path(self.auth_dev_otp_outbox_path)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(
                "AUTH_DEV_OTP_OUTBOX_PATH must name a regular file, not a "
                "directory or a symbolic link."
            )
        if not path.parent.is_dir():
            raise ValueError(
                "AUTH_DEV_OTP_OUTBOX_PATH: the directory for the outbox file "
                "does not exist."
            )

    @staticmethod
    def _resolved_outbox_path(value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            # The repository root, which also holds the .env file.
            path = PROJECT_ROOT.parent / path
        return path

    @property
    def dev_otp_outbox_file(self) -> Path | None:
        """The outbox file, only when development OTP delivery is enabled."""
        if self.auth_dev_otp_outbox_path is None or not self.debug_otp_enabled:
            return None
        return self._resolved_outbox_path(self.auth_dev_otp_outbox_path)

    @property
    def debug_otp_enabled(self) -> bool:
        """True only for local development console delivery of OTPs.

        Validation already refuses AUTH_DEBUG_OTP outside development; the
        environment is checked again here so this can never be true there.
        """
        return self.auth_debug_otp and self.app_env == "development"

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
