"""Cryptographic helpers for OTPs and session tokens (standard library only).

- OTPs are stored as HMAC-SHA256 digests keyed with AUTH_SECRET_KEY. A plain
  SHA-256 of a six-digit code could be reversed offline by trying all one
  million codes; without the key, a leaked digest is useless.
- The HMAC input includes the challenge id and phone number, so a digest is
  only valid for the challenge it was created for.
- Session tokens carry 256 bits of randomness, so a plain SHA-256 digest is
  enough to store them safely; the raw token exists only in the cookie.
"""

import hashlib
import hmac
import secrets
from uuid import UUID

OTP_DIGITS = 6
SESSION_TOKEN_BYTES = 32  # 256 bits


def generate_otp() -> str:
    """A uniformly random six-digit code, zero-padded."""
    return f"{secrets.randbelow(10**OTP_DIGITS):0{OTP_DIGITS}d}"


def hash_otp(
    secret_key: str, *, challenge_id: UUID, phone_number: str, code: str
) -> str:
    """HMAC-SHA256 hex digest of a code, bound to one challenge."""
    message = f"otp:v1:{challenge_id}:{phone_number}:{code}".encode()
    return hmac.new(secret_key.encode(), message, hashlib.sha256).hexdigest()


def otp_matches(
    secret_key: str,
    *,
    challenge_id: UUID,
    phone_number: str,
    code: str,
    expected_hash: str,
) -> bool:
    """Constant-time comparison of a submitted code with a stored digest."""
    candidate = hash_otp(
        secret_key, challenge_id=challenge_id, phone_number=phone_number, code=code
    )
    return hmac.compare_digest(candidate, expected_hash)


def generate_session_token() -> str:
    """A URL-safe session token with 256 bits of entropy."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """SHA-256 hex digest of a session token, as stored in user_sessions."""
    return hashlib.sha256(token.encode()).hexdigest()
