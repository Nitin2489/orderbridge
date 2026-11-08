"""Tests for the request body size limit (app/api/request_limits.py).

Direct ASGI tests control exactly how the body arrives (declared length,
chunks, no length) and count how much of it is read; end-to-end tests run
through create_app() and FastAPI's own body parsing. No database is used.
"""

import asyncio
import json

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.api.request_limits import (
    INVALID_CONTENT_LENGTH,
    REQUEST_TOO_LARGE,
    RequestBodyLimitMiddleware,
)
from app.core.config import Settings
from app.main import create_app

LIMIT = 64 * 1024


# ---------------------------------------------------------------------------
# Direct ASGI harness
# ---------------------------------------------------------------------------


class Body:
    """A request body delivered as the given chunks; counts reads."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.reads = 0

    async def receive(self):
        index = self.reads
        self.reads += 1
        if index < len(self.chunks):
            return {
                "type": "http.request",
                "body": self.chunks[index],
                "more_body": index < len(self.chunks) - 1,
            }
        return {"type": "http.disconnect"}


async def read_all_then_echo_length(scope, receive, send):
    """An app that reads the whole body and replies with its length."""
    total = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        total += len(message.get("body", b""))
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(total).encode()})


def run(app, body: Body, headers: list[tuple[bytes, bytes]] | None = None):
    """Call app with an HTTP scope; return (status, response body, messages)."""
    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/", "headers": headers or []}
    asyncio.run(app(scope, body.receive, send))

    start = next(m for m in sent if m["type"] == "http.response.start")
    content = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return start["status"], content, sent


def limited(app=read_all_then_echo_length, limit=LIMIT):
    return RequestBodyLimitMiddleware(app, max_body_bytes=limit)


def length_header(value: int | str) -> list[tuple[bytes, bytes]]:
    return [(b"content-length", str(value).encode())]


# ---------------------------------------------------------------------------
# Declared Content-Length
# ---------------------------------------------------------------------------


def test_body_at_the_limit_with_content_length_is_accepted():
    body = Body([b"x" * LIMIT])

    status, content, _ = run(limited(), body, length_header(LIMIT))

    assert status == 200
    assert content == str(LIMIT).encode()


def test_declared_oversize_is_refused_without_reading_the_body():
    body = Body([b"x" * (LIMIT + 1)])

    status, content, _ = run(limited(), body, length_header(LIMIT + 1))

    assert status == 413
    assert json.loads(content) == {"detail": REQUEST_TOO_LARGE}
    assert body.reads == 0


@pytest.mark.parametrize(
    "headers",
    [
        length_header("abc"),
        length_header("-1"),
        length_header("1e3"),
        length_header(10) + length_header(20),
    ],
)
def test_unusable_content_length_is_refused(headers):
    body = Body([b"x" * 10])

    status, content, _ = run(limited(), body, headers)

    assert status == 400
    assert json.loads(content) == {"detail": INVALID_CONTENT_LENGTH}
    assert body.reads == 0


@pytest.mark.parametrize("raw", [b"\xb2", b"\xb9", b"6\xb3"])
def test_non_ascii_digits_in_content_length_are_refused(raw):
    # Decoded as latin-1 these are "²", "¹" and "6³": str.isdigit() accepts
    # them, but int() does not. They must be a 400, never an unhandled 500.
    assert raw.decode("latin-1").isdigit()
    body = Body([b"x" * 10])

    status, content, _ = run(limited(), body, [(b"content-length", raw)])

    assert status == 400
    assert json.loads(content) == {"detail": INVALID_CONTENT_LENGTH}
    assert body.reads == 0


def test_understated_content_length_is_still_counted():
    # Content-Length says 10, but more arrives: the byte count still applies.
    body = Body([b"x" * LIMIT, b"x"])

    status, _, _ = run(limited(), body, length_header(10))

    assert status == 413


# ---------------------------------------------------------------------------
# No Content-Length (chunked or streamed)
# ---------------------------------------------------------------------------


def test_chunked_body_at_the_limit_is_accepted():
    body = Body([b"x" * 1000] * 65 + [b"x" * (LIMIT - 65_000)])

    status, content, _ = run(limited(), body)

    assert status == 200
    assert content == str(LIMIT).encode()


def test_chunked_oversize_is_refused_as_soon_as_the_limit_is_passed():
    chunk = b"x" * 16 * 1024
    body = Body([chunk] * 100)  # 1.6 MB offered

    status, content, _ = run(limited(), body)

    assert status == 413
    assert json.loads(content) == {"detail": REQUEST_TOO_LARGE}
    # 4 chunks reach the limit exactly; the 5th passes it and reading stops.
    assert body.reads == 5


def test_empty_body_is_accepted():
    status, content, _ = run(limited(), Body([b""]))

    assert status == 200
    assert content == b"0"


# ---------------------------------------------------------------------------
# Pass-through behaviour
# ---------------------------------------------------------------------------


def test_streamed_responses_pass_through_unchanged():
    async def streaming_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for part in (b"one,", b"two,", b"three"):
            await send({"type": "http.response.body", "body": part, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    _, content, sent = run(limited(streaming_app), Body([b""]))

    assert content == b"one,two,three"
    assert [m.get("body") for m in sent if m["type"] == "http.response.body"] == [
        b"one,",
        b"two,",
        b"three",
        b"",
    ]


def test_non_http_scopes_are_not_touched():
    seen = []

    async def lifespan_app(scope, receive, send):
        seen.append(scope["type"])

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        return None

    asyncio.run(limited(lifespan_app)({"type": "lifespan"}, receive, send))

    assert seen == ["lifespan"]


def test_limit_must_be_positive():
    with pytest.raises(ValueError):
        RequestBodyLimitMiddleware(read_all_then_echo_length, max_body_bytes=0)


# ---------------------------------------------------------------------------
# End to end through create_app() and FastAPI body parsing
# ---------------------------------------------------------------------------

echo_router = APIRouter()


@echo_router.post("/test-echo")
def echo(payload: dict) -> dict:
    return {"size": len(payload.get("data", ""))}


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "testing",
        "database_url": "postgresql+psycopg://unused:unused@localhost:5432/unused",
        "auth_secret_key": "body-limit-test-9f8e7d6c5b4a39281706f5e4d3c2b1a0",
        "auth_debug_otp": False,
        "auth_session_cookie_secure": False,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def client():
    app = create_app(make_settings())
    app.include_router(echo_router)
    with TestClient(app) as test_client:
        yield test_client


def json_body(total_size: int) -> bytes:
    """A JSON object of exactly total_size bytes."""
    envelope = len(b'{"data":""}')
    payload = {"data": "x" * (total_size - envelope)}
    return json.dumps(payload, separators=(",", ":")).encode()


def test_default_limit_is_64_kib():
    assert make_settings().max_request_body_bytes == LIMIT


@pytest.mark.parametrize("size", [1023, 10 * 1024 * 1024 + 1])
def test_out_of_range_limits_are_rejected(size):
    with pytest.raises(ValueError):
        make_settings(max_request_body_bytes=size)


def test_json_body_at_the_limit_is_parsed_normally(client):
    body = json_body(LIMIT)
    assert len(body) == LIMIT

    response = client.post(
        "/test-echo", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 200
    assert response.json() == {"size": LIMIT - len(b'{"data":""}')}


def test_json_body_one_byte_over_with_content_length_gets_413(client):
    response = client.post(
        "/test-echo",
        content=json_body(LIMIT + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": REQUEST_TOO_LARGE}


def chunks(body: bytes, size: int = 4096):
    for start in range(0, len(body), size):
        yield body[start : start + size]


def test_chunked_body_without_content_length_over_the_limit_gets_413(client):
    response = client.post(
        "/test-echo",
        content=chunks(json_body(LIMIT + 1)),
        headers={"Content-Type": "application/json"},
    )

    # 413 from FastAPI's own body parsing, not a 400 "error parsing the body".
    assert response.status_code == 413
    assert response.json() == {"detail": REQUEST_TOO_LARGE}


def test_chunked_body_without_content_length_under_the_limit_is_parsed(client):
    response = client.post(
        "/test-echo",
        content=chunks(json_body(1000)),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200


def test_oversized_otp_request_is_refused_before_the_endpoint(client):
    response = client.post(
        "/api/v1/auth/request-otp",
        content=json.dumps(
            {"phone_number": "+919000000001", "padding": "x" * LIMIT}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
