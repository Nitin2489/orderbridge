"""Authentication configuration, security primitives and phone normalisation.

These tests need no database.
"""

import hashlib
import hmac
import re
import secrets
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.config import Settings, secret_looks_like_placeholder
from app.core.origins import (
    InvalidOriginError,
    canonicalize_origin,
    origin_from_referer,
)
from app.core.phone import InvalidPhoneNumberError, normalize_phone_number
from app.core.security import (
    generate_otp,
    generate_session_token,
    hash_otp,
    hash_session_token,
    otp_matches,
)

STRONG_SECRET = "prod-test-secret-9f8e7d6c5b4a39281706f5e4d3c2b1a0"
DATABASE_URL = "postgresql+psycopg://user:db-password@localhost:5432/unused"


def make_settings(**overrides) -> Settings:
    """Settings independent of the local .env file."""
    values = {
        "_env_file": None,
        "app_env": "testing",
        "database_url": DATABASE_URL,
        "auth_secret_key": STRONG_SECRET,
        "auth_debug_otp": True,
        "auth_session_cookie_secure": False,
        "auth_session_cookie_samesite": "lax",
        "frontend_origins": ["http://localhost:3000"],
    }
    values.update(overrides)
    return Settings(**values)


def production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "auth_debug_otp": False,
        "auth_session_cookie_secure": True,
        "frontend_origins": ["https://app.orderbridge.example"],
    }
    values.update(overrides)
    return make_settings(**values)


# ---------------------------------------------------------------------------
# Production configuration
# ---------------------------------------------------------------------------


def test_valid_production_configuration_disables_debug_otp():
    settings = production_settings()

    assert settings.debug_otp_enabled is False
    assert settings.auth_session_cookie_secure is True


@pytest.mark.parametrize("secret", [None, "", "too-short-secret-0123456789abc"])
def test_production_rejects_missing_or_weak_secret(secret):
    with pytest.raises(ValidationError, match="AUTH_SECRET_KEY"):
        production_settings(auth_secret_key=secret)


def test_production_rejects_debug_otp():
    with pytest.raises(ValidationError, match="AUTH_DEBUG_OTP"):
        production_settings(auth_debug_otp=True)


def test_production_rejects_insecure_cookie():
    with pytest.raises(ValidationError, match="AUTH_SESSION_COOKIE_SECURE"):
        production_settings(auth_session_cookie_secure=False)


def test_production_rejects_http_or_missing_origins():
    with pytest.raises(ValidationError, match="https"):
        production_settings(frontend_origins=["http://app.orderbridge.example"])

    with pytest.raises(ValidationError, match="FRONTEND_ORIGINS"):
        production_settings(frontend_origins=[])


def test_samesite_none_requires_secure_cookie_in_any_environment():
    with pytest.raises(ValidationError, match="SAMESITE=none"):
        make_settings(
            auth_session_cookie_samesite="none", auth_session_cookie_secure=False
        )


def test_validation_errors_do_not_echo_secrets():
    weak = "weak-secret-value-do-not-print"

    with pytest.raises(ValidationError) as raised:
        production_settings(auth_secret_key=weak)

    assert weak not in str(raised.value)
    assert "db-password" not in str(raised.value)


def test_secrets_are_not_in_repr():
    settings = make_settings()

    assert STRONG_SECRET not in repr(settings)
    assert STRONG_SECRET not in str(settings)
    assert "db-password" not in repr(settings)


def test_development_without_secret_uses_random_per_process_key():
    first = make_settings(app_env="development", auth_secret_key=None)
    second = make_settings(app_env="development", auth_secret_key="")

    assert len(first.auth_secret) >= 32
    assert len(second.auth_secret) >= 32
    assert first.auth_secret != second.auth_secret


def test_debug_otp_enabled_only_when_configured():
    assert make_settings(auth_debug_otp=True).debug_otp_enabled is True
    assert make_settings(auth_debug_otp=False).debug_otp_enabled is False


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.orderbridge.example",
        "app.orderbridge.example",
        "ftp://app.orderbridge.example",
        "https://app.orderbridge.example/login",
        "https://user:pass@app.orderbridge.example",
    ],
)
def test_invalid_frontend_origins_are_rejected(origin):
    with pytest.raises(ValidationError, match="Invalid frontend origin"):
        make_settings(frontend_origins=[origin])


def test_frontend_origins_are_normalised():
    settings = make_settings(frontend_origins=["HTTP://LocalHost:3000/"])

    assert settings.frontend_origins == ["http://localhost:3000"]


def test_frontend_origins_are_deduplicated_after_canonicalisation():
    settings = make_settings(
        frontend_origins=[
            "http://localhost:3000",
            "HTTP://LOCALHOST:3000/",
            "https://app.orderbridge.example:443",
            "https://app.orderbridge.example",
        ]
    )

    assert settings.frontend_origins == [
        "http://localhost:3000",
        "https://app.orderbridge.example",
    ]


# ---------------------------------------------------------------------------
# Origin canonicalisation (shared by configuration and the CSRF check)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("http://localhost:3000", "http://localhost:3000"),
        (" HTTPS://App.OrderBridge.Example/ ", "https://app.orderbridge.example"),
        ("https://app.orderbridge.example:443", "https://app.orderbridge.example"),
        ("http://localhost:80", "http://localhost"),
        ("http://localhost:443", "http://localhost:443"),
        ("https://localhost:80", "https://localhost:80"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://[::1]:3000", "http://[::1]:3000"),
        ("http://[0:0:0:0:0:0:0:1]", "http://[::1]"),
        ("http://[2001:DB8::1]:8080", "http://[2001:db8::1]:8080"),
        ("https://xn--bcher-kva.example", "https://xn--bcher-kva.example"),
    ],
)
def test_origins_canonicalise(raw, canonical):
    assert canonicalize_origin(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "null",
        "*",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://",
        "http://localhost:",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:99999",
        "http://localhost:abc",
        "http://[::1",
        "http://::1",
        "http://1.2.3.999",
        "http://010.0.0.1",
        "http://-bad-.example",
        "http://bad..example",
        "http://exa mple.example",
        "https://bücher.example",
        "https://app.orderbridge.example/?",
        "https://app.orderbridge.example/#",
        "https://app.orderbridge.example/path",
        "https://app.orderbridge.example?x=1",
        "https://user@app.orderbridge.example",
        "https://app.orderbridge.example%2f",
    ],
)
def test_malformed_origins_are_rejected(raw):
    with pytest.raises(InvalidOriginError):
        canonicalize_origin(raw)

    with pytest.raises(ValidationError, match="Invalid frontend origin"):
        make_settings(frontend_origins=[raw])


def test_origin_from_referer_ignores_path_and_query():
    assert (
        origin_from_referer("HTTP://LocalHost:3000/orders/new?x=1#top")
        == "http://localhost:3000"
    )

    with pytest.raises(InvalidOriginError):
        origin_from_referer("/relative/path")

    with pytest.raises(InvalidOriginError):
        origin_from_referer("https://user:pass@app.orderbridge.example/page")


# ---------------------------------------------------------------------------
# Production secret placeholders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "changeme-changeme-changeme-changeme",
        "<generate-a-random-secret-of-48-chars>",
        "replace_me_with_a_real_production_key",
        "example-secret-for-local-development-only",
        "YourSecretKeyGoesHere-0123456789abcdef",
        "a" * 40,
        "ab" * 20,
    ],
)
def test_production_rejects_placeholder_secrets(secret):
    with pytest.raises(ValidationError, match="placeholder") as raised:
        production_settings(auth_secret_key=secret)

    assert secret not in str(raised.value)


def test_randomly_generated_secrets_are_accepted():
    for _ in range(200):
        for secret in (
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(48),
            secrets.token_hex(16),
            secrets.token_hex(32),
        ):
            assert not secret_looks_like_placeholder(secret)

    settings = production_settings(auth_secret_key=secrets.token_urlsafe(48))
    assert settings.debug_otp_enabled is False


def test_placeholder_check_does_not_apply_outside_production():
    settings = make_settings(app_env="development", auth_secret_key="a" * 40)

    assert settings.auth_secret == "a" * 40


# ---------------------------------------------------------------------------
# Security primitives
# ---------------------------------------------------------------------------

CHALLENGE_A = UUID(int=1)
CHALLENGE_B = UUID(int=2)
PHONE = "+919000000001"


def test_generated_otp_is_six_digits():
    for _ in range(50):
        assert re.fullmatch(r"[0-9]{6}", generate_otp())


def test_otp_hash_is_keyed_hmac_not_plain_sha256():
    digest = hash_otp(
        STRONG_SECRET, challenge_id=CHALLENGE_A, phone_number=PHONE, code="123456"
    )

    assert len(digest) == 64
    assert "123456" not in digest
    assert digest != hashlib.sha256(b"123456").hexdigest()
    assert (
        digest
        == hmac.new(
            STRONG_SECRET.encode(),
            f"otp:v1:{CHALLENGE_A}:{PHONE}:123456".encode(),
            hashlib.sha256,
        ).hexdigest()
    )


def test_otp_hash_is_bound_to_challenge_phone_and_key():
    base = hash_otp(
        STRONG_SECRET, challenge_id=CHALLENGE_A, phone_number=PHONE, code="123456"
    )

    assert base != hash_otp(
        STRONG_SECRET, challenge_id=CHALLENGE_B, phone_number=PHONE, code="123456"
    )
    assert base != hash_otp(
        STRONG_SECRET,
        challenge_id=CHALLENGE_A,
        phone_number="+919000000002",
        code="123456",
    )
    assert base != hash_otp(
        "another-secret", challenge_id=CHALLENGE_A, phone_number=PHONE, code="123456"
    )


def test_otp_matches_compares_digests():
    digest = hash_otp(
        STRONG_SECRET, challenge_id=CHALLENGE_A, phone_number=PHONE, code="654321"
    )
    common = {"challenge_id": CHALLENGE_A, "phone_number": PHONE}

    assert otp_matches(STRONG_SECRET, code="654321", expected_hash=digest, **common)
    assert not otp_matches(STRONG_SECRET, code="654320", expected_hash=digest, **common)


def test_session_tokens_have_256_bits_and_are_unique():
    tokens = {generate_session_token() for _ in range(100)}

    assert len(tokens) == 100
    # 32 random bytes encode to 43 URL-safe base64 characters.
    assert all(len(token) >= 43 for token in tokens)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", token) for token in tokens)


def test_session_token_hash_is_sha256_hex():
    token = generate_session_token()

    assert hash_session_token(token) == hashlib.sha256(token.encode()).hexdigest()
    assert hash_session_token(token) != token


# ---------------------------------------------------------------------------
# Phone normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+919000000001",
        "919000000001",
        "09000000001",
        "9000000001",
        "+91 90000 00001",
        "+91-9000-000-001",
        " (+91) 9000000001 ",
    ],
)
def test_phone_numbers_normalise_to_e164(raw):
    assert normalize_phone_number(raw) == "+919000000001"


@pytest.mark.parametrize(
    "raw",
    ["", "12345", "+915000000001", "+1 202 555 0100", "90000000012", "abcdefghij"],
)
def test_invalid_phone_numbers_are_rejected(raw):
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone_number(raw)
