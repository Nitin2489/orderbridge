"""Delivery of one-time sign-in codes.

A raw code leaves the backend only through an OtpDelivery. It is never put
in an HTTP response, a log record, an exception message or a repr.

No SMS provider is integrated yet, so:

- production, staging and testing use NoOtpDelivery: a code is issued and
  stored (as an HMAC digest) but not sent anywhere;
- local development, with APP_ENV=development and AUTH_DEBUG_OTP=true, uses
  DevelopmentConsoleOtpDelivery, which prints the code to the backend's own
  terminal (standard error). Configuration refuses AUTH_DEBUG_OTP=true in
  every other environment (Settings._check_auth_settings), so this delivery
  cannot be selected outside development;
- if AUTH_DEV_OTP_OUTBOX_PATH is also set (development only, enforced the
  same way), DevelopmentOutboxOtpDelivery additionally appends each code to
  that file, a local "mock SMS outbox" that end-to-end tests read from disk.
  Both channels receive the same code. The file is never served: no
  endpoint reads it;
- tests inject InMemoryOtpDelivery through create_app(otp_delivery=...).

A real SMS sender will be another OtpDelivery, selected in otp_delivery_for().
"""

import json
import logging
import os
import stat
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TextIO

try:  # POSIX only; development runs on Linux, macOS or WSL.
    import fcntl
except ImportError:  # pragma: no cover - Windows without WSL
    fcntl = None  # type: ignore[assignment]

from app.core.config import Settings

logger = logging.getLogger(__name__)


class OtpDelivery(Protocol):
    def deliver(self, phone_number: str, code: str) -> None:
        """Send `code` to the normalised `phone_number`."""


def mask_phone_number(phone_number: str) -> str:
    """ "+919000000001" -> "+91******0001": enough to tell demo accounts apart."""
    if len(phone_number) <= 4:
        return "*" * len(phone_number)
    visible_prefix = 3 if phone_number.startswith("+") else 0
    hidden = len(phone_number) - visible_prefix - 4
    return phone_number[:visible_prefix] + "*" * hidden + phone_number[-4:]


class NoOtpDelivery:
    """Sends nothing. Used wherever no delivery channel is configured."""

    def deliver(self, phone_number: str, code: str) -> None:
        return None

    def __repr__(self) -> str:
        return "NoOtpDelivery()"


class DevelopmentConsoleOtpDelivery:
    """Local development only: prints the code to the backend's terminal.

    It writes straight to standard error instead of using the logging
    module, so the code never becomes a log record that handlers could
    forward to files or log services. Only otp_delivery_for() selects it,
    and only when Settings.debug_otp_enabled (development) is true.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        # Resolved at delivery time by default, so test capture and
        # redirected stderr both work.
        self._stream = stream

    def deliver(self, phone_number: str, code: str) -> None:
        stream = self._stream or sys.stderr
        print(
            f"[OrderBridge development OTP] {mask_phone_number(phone_number)}: "
            f"{code} (local development only; not sent by SMS)",
            file=stream,
            flush=True,
        )

    def __repr__(self) -> str:
        return "DevelopmentConsoleOtpDelivery()"


class InMemoryOtpDelivery:
    """Records delivered codes in memory, for automated tests.

    Never selected from configuration; a test passes it to create_app().
    """

    def __init__(self) -> None:
        self._codes: dict[str, list[str]] = {}

    def deliver(self, phone_number: str, code: str) -> None:
        self._codes.setdefault(phone_number, []).append(code)

    def latest_code(self, phone_number: str) -> str:
        """The most recent code for the normalised number (KeyError if none)."""
        return self._codes[phone_number][-1]

    def delivered_count(self, phone_number: str) -> int:
        return len(self._codes.get(phone_number, []))

    def __repr__(self) -> str:
        # Never include the codes themselves.
        total = sum(len(codes) for codes in self._codes.values())
        return f"InMemoryOtpDelivery({total} code(s))"


class OutboxWriteError(OSError):
    """The outbox path is not a regular file the backend may write."""


OUTBOX_FILE_MODE = 0o600

_OUTBOX_OPEN_FLAGS = (
    os.O_WRONLY
    | os.O_APPEND
    | os.O_CREAT
    # Never block in open(): a FIFO with no reader fails at once (ENXIO)
    # instead of hanging the request. Cleared again for regular files.
    | getattr(os, "O_NONBLOCK", 0)
    # Refuse to follow a symbolic link planted at the outbox path.
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)

# Indirections over the system calls used for a record, so tests can
# simulate failures without patching the os module globally.
_write = os.write
_truncate = os.ftruncate
_current_uid: Callable[[], int] | None = getattr(os, "getuid", None)


def _check_outbox_descriptor(status: os.stat_result) -> None:
    """Refuse anything but a private, singly linked regular file we own.

    Runs on the opened descriptor, immediately before writing, so there is no
    gap between the check and the write. Nothing about the file is changed
    (not even its mode) until it has passed.
    """
    if not stat.S_ISREG(status.st_mode):
        raise OutboxWriteError("The OTP outbox is not a regular file.")
    if status.st_nlink != 1:
        # A hard link would let the outbox modify some other file.
        raise OutboxWriteError("The OTP outbox file has more than one link.")
    if _current_uid is not None and status.st_uid != _current_uid():
        raise OutboxWriteError("The OTP outbox file belongs to another user.")


class DevelopmentOutboxOtpDelivery:
    """Local development only: appends each code to a JSON Lines file.

    One record per line:
    {"phone_number": "+91...", "code": "123456", "issued_at": "<UTC ISO 8601>"}.
    issued_at is the time of delivery, which happens right after the code's
    challenge is committed.

    File safety, enforced on the opened descriptor before every write:
    - opened with O_NONBLOCK, O_NOFOLLOW and O_CLOEXEC: a symbolic link is
      refused, and a FIFO, socket or device never blocks the request;
    - only a regular file with exactly one link, owned by this process's
      user, is accepted; anything else is rejected before it is touched;
    - created with mode 0600 (owner read/write only); an existing file with
      other permissions is set to 0600 before writing;
    - each record is written as one complete line under an exclusive flock,
      with O_APPEND, so records from concurrent requests, threads or worker
      processes never interleave. If a write fails part-way, the file is
      truncated back to its size before the record, so a partial line never
      corrupts the next record.

    Failures never reach the caller: they are logged at ERROR with the
    exception type only (no path, number, code or error text), and the
    request continues. The console channel still shows the code, and the
    request-otp response stays identical for every number.

    Only otp_delivery_for() selects it, and only when
    Settings.dev_otp_outbox_file is set (development with AUTH_DEBUG_OTP).
    """

    def __init__(
        self, path: Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._path = Path(path)
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def path(self) -> Path:
        return self._path

    def deliver(self, phone_number: str, code: str) -> None:
        record = {
            "phone_number": phone_number,
            "code": code,
            "issued_at": self._clock().astimezone(UTC).isoformat(),
        }
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode()

        try:
            self._append(line)
        except OSError as error:
            logger.error(
                "Development OTP outbox write failed (%s); the code was not "
                "written to the outbox.",
                type(error).__name__,
            )

    def _append(self, line: bytes) -> None:
        descriptor = os.open(self._path, _OUTBOX_OPEN_FLAGS, OUTBOX_FILE_MODE)
        try:
            status = os.fstat(descriptor)
            _check_outbox_descriptor(status)
            if stat.S_IMODE(status.st_mode) != OUTBOX_FILE_MODE:
                os.fchmod(descriptor, OUTBOX_FILE_MODE)
            # A regular file: ordinary blocking writes from here on.
            os.set_blocking(descriptor, True)

            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                self._write_record(descriptor, line)
            finally:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_record(descriptor: int, line: bytes) -> None:
        """Write one whole record, or leave the file as it was. Lock held."""
        size_before = os.fstat(descriptor).st_size
        try:
            view = memoryview(line)
            while view:
                written = _write(descriptor, view)
                view = view[written:]
        except OSError:
            try:
                _truncate(descriptor, size_before)
            except OSError as rollback_error:
                logger.error(
                    "Development OTP outbox could not remove a partial record (%s).",
                    type(rollback_error).__name__,
                )
            raise

    def __repr__(self) -> str:
        return "DevelopmentOutboxOtpDelivery()"


class CompositeOtpDelivery:
    """Delivers each code to several channels, in order."""

    def __init__(self, channels: Sequence[OtpDelivery]) -> None:
        self._channels = tuple(channels)

    @property
    def channels(self) -> tuple[OtpDelivery, ...]:
        return self._channels

    def deliver(self, phone_number: str, code: str) -> None:
        for channel in self._channels:
            channel.deliver(phone_number, code)

    def __repr__(self) -> str:
        names = ", ".join(type(channel).__name__ for channel in self._channels)
        return f"CompositeOtpDelivery({names})"


def otp_delivery_for(settings: Settings) -> OtpDelivery:
    """The delivery channel configured for this environment."""
    if settings.debug_otp_enabled:
        console = DevelopmentConsoleOtpDelivery()
        outbox_file = settings.dev_otp_outbox_file
        if outbox_file is not None:
            return CompositeOtpDelivery(
                (console, DevelopmentOutboxOtpDelivery(outbox_file))
            )
        return console
    return NoOtpDelivery()
