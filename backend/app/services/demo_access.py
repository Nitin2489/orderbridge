"""Reviewer access for the demonstration accounts (no SMS provider).

A review deployment has no SMS provider, so the evaluator needs another
way to receive the one-time codes of the four synthetic demo accounts.
Showing them to anyone who types a published number would let anyone
sign in as the order desk, so the codes are shown only to a browser that
has proved it knows AUTH_DEMO_ACCESS_KEY, a private server-side secret
given to the evaluator outside the repository.

The proof is a signed, expiring token in an HttpOnly cookie:

    v1.<expiry unix seconds>.<hex HMAC-SHA256>

The HMAC key is AUTH_SECRET_KEY and the message binds the expiry to a
SHA-256 digest of the access key, so:

- the token contains neither secret and cannot be forged without both;
- rotating AUTH_DEMO_ACCESS_KEY (or AUTH_SECRET_KEY) invalidates every
  token already issued;
- nothing is stored in the database.

The token grants no session, role or account. It only lets request-otp
return the freshly issued code of a listed demo number, and lets
verify-otp accept codes for those numbers; every other OTP rule (expiry,
single use, attempts, cooldown, lockout, rate limits) still applies.

Neither the access key nor a token is ever logged, put in an exception
message or returned in a response body.
"""

import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta

from app.core.config import Settings

ACCESS_COOKIE_NAME = "orderbridge_demo_access"
# Sent only to the auth endpoints, the only ones that read it.
ACCESS_COOKIE_PATH = "/api/v1/auth"
ACCESS_TTL = timedelta(hours=8)

# Longer submitted keys are refused before hashing.
MAX_ACCESS_KEY_LENGTH = 256

_TOKEN_VERSION = "v1"
# Tolerated clock difference between workers for the expiry upper bound.
_CLOCK_SKEW = timedelta(minutes=1)
_TOKEN_PATTERN = re.compile(r"v1\.([0-9]{1,12})\.([0-9a-f]{64})")


def demo_access_enabled(settings: Settings) -> bool:
    """True when demo numbers are configured (Settings then requires a key)."""
    return bool(settings.auth_demo_otp_numbers) and (
        settings.auth_demo_access_key is not None
    )


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def access_key_matches(submitted: str, settings: Settings) -> bool:
    """Constant-time check of a submitted key against AUTH_DEMO_ACCESS_KEY.

    Both values are hashed to fixed-length digests first, so the comparison
    time reveals neither the key's content nor its length.
    """
    if not demo_access_enabled(settings):
        return False
    if not submitted or len(submitted) > MAX_ACCESS_KEY_LENGTH:
        return False
    expected = settings.auth_demo_access_key.get_secret_value()
    return hmac.compare_digest(_digest(submitted), _digest(expected))


def _signature(expires: int, settings: Settings) -> str:
    key_digest = _digest(settings.auth_demo_access_key.get_secret_value()).hex()
    message = f"demo-access:{_TOKEN_VERSION}:{expires}:{key_digest}".encode()
    return hmac.new(settings.auth_secret.encode(), message, hashlib.sha256).hexdigest()


def issue_access_token(settings: Settings, *, now: datetime | None = None) -> str:
    """A token valid for ACCESS_TTL. Call only after access_key_matches()."""
    if not demo_access_enabled(settings):
        raise RuntimeError("Reviewer access is not configured.")
    now = now or datetime.now(UTC)
    expires = int((now + ACCESS_TTL).timestamp())
    return f"{_TOKEN_VERSION}.{expires}.{_signature(expires, settings)}"


def access_token_is_valid(
    token: str | None, settings: Settings, *, now: datetime | None = None
) -> bool:
    """True only for a well-formed, unexpired token signed with the current keys.

    Missing, malformed, expired, forged and rotated-key tokens are all
    simply False; the caller treats them as "no reviewer access".
    """
    if not token or not demo_access_enabled(settings):
        return False
    match = _TOKEN_PATTERN.fullmatch(token)
    if match is None:
        return False

    expires = int(match.group(1))
    now = now or datetime.now(UTC)
    current = now.timestamp()
    # Expired, or further in the future than this code ever issues.
    if expires <= current:
        return False
    if expires > (now + ACCESS_TTL + _CLOCK_SKEW).timestamp():
        return False

    return hmac.compare_digest(match.group(2), _signature(expires, settings))
