"""Request body size limit (pure ASGI middleware).

Requests whose body exceeds MAX_REQUEST_BODY_BYTES are refused with 413
before more than that many bytes are buffered:

- a declared Content-Length above the limit is refused immediately, without
  reading any of the body;
- otherwise (no Content-Length, e.g. chunked transfer encoding) the bytes
  are counted as the application reads them, and reading stops with 413 as
  soon as the count passes the limit.

It is written as plain ASGI rather than BaseHTTPMiddleware so responses,
including streamed ones, pass through untouched; only the request side is
wrapped.
"""

import json

from fastapi import HTTPException, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_TOO_LARGE = "Request body is too large."
INVALID_CONTENT_LENGTH = "Invalid Content-Length header."


class RequestBodyTooLarge(HTTPException):
    """Raised while reading the body. FastAPI re-raises HTTPException from
    body parsing unchanged, so it becomes a normal 413 response."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=REQUEST_TOO_LARGE
        )


def _declared_length(scope: Scope) -> int | None | str:
    """The Content-Length as an int, None if absent, "invalid" if unusable."""
    values = {
        value.decode("latin-1").strip()
        for name, value in scope.get("headers", [])
        if name.lower() == b"content-length"
    }
    if not values:
        return None
    if len(values) > 1:
        return "invalid"
    (value,) = values
    # ASCII digits only: str.isdigit() alone also accepts characters such as
    # "²" (a single latin-1 byte), which int() then rejects.
    if not (value.isascii() and value.isdigit()):
        return "invalid"
    return int(value)


async def _reply(send: Send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                # The unread body cannot be skipped safely; end the connection.
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive.")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared == "invalid":
            await _reply(send, status.HTTP_400_BAD_REQUEST, INVALID_CONTENT_LENGTH)
            return
        if isinstance(declared, int) and declared > self.max_body_bytes:
            await _reply(send, status.HTTP_413_CONTENT_TOO_LARGE, REQUEST_TOO_LARGE)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestBodyTooLarge:
            # Only reached if something read the body outside FastAPI's
            # request parsing (which turns the exception into a response).
            if response_started:
                raise
            await _reply(send, status.HTTP_413_CONTENT_TOO_LARGE, REQUEST_TOO_LARGE)
