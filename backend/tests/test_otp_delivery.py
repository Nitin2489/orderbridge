"""Tests for app/services/otp_delivery.py and its selection in create_app().

No database is needed: these exercise the delivery channels directly and
check which one each environment gets.
"""

import io
import json
import logging
import os
import socket
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.main import create_app
from app.services import otp_delivery as otp_delivery_module
from app.services.otp_delivery import (
    OUTBOX_FILE_MODE,
    CompositeOtpDelivery,
    DevelopmentConsoleOtpDelivery,
    DevelopmentOutboxOtpDelivery,
    InMemoryOtpDelivery,
    NoOtpDelivery,
    mask_phone_number,
    otp_delivery_for,
)

STRONG_SECRET = "delivery-test-9f8e7d6c5b4a39281706f5e4d3c2b1a0"
PHONE = "+919000000001"
CODE = "482913"


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "testing",
        "database_url": "postgresql+psycopg://unused:unused@localhost:5432/unused",
        "auth_secret_key": STRONG_SECRET,
        "auth_debug_otp": False,
        "auth_session_cookie_secure": False,
        "frontend_origins": ["http://localhost:3000"],
    }
    values.update(overrides)
    return Settings(**values)


def production_settings() -> Settings:
    return make_settings(
        app_env="production",
        auth_session_cookie_secure=True,
        frontend_origins=["https://app.orderbridge.example"],
    )


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_development_with_debug_otp_uses_the_console():
    settings = make_settings(app_env="development", auth_debug_otp=True)

    assert isinstance(otp_delivery_for(settings), DevelopmentConsoleOtpDelivery)


@pytest.mark.parametrize(
    "settings_factory",
    [
        lambda: make_settings(app_env="development", auth_debug_otp=False),
        lambda: make_settings(app_env="testing"),
        lambda: make_settings(app_env="staging"),
        production_settings,
    ],
    ids=["development-without-flag", "testing", "staging", "production"],
)
def test_every_other_configuration_delivers_nothing(settings_factory):
    assert isinstance(otp_delivery_for(settings_factory()), NoOtpDelivery)


def test_create_app_uses_the_configured_delivery():
    development = create_app(make_settings(app_env="development", auth_debug_otp=True))
    production = create_app(production_settings())

    assert isinstance(development.state.otp_delivery, DevelopmentConsoleOtpDelivery)
    assert isinstance(production.state.otp_delivery, NoOtpDelivery)


def test_create_app_accepts_an_injected_delivery():
    delivery = InMemoryOtpDelivery()

    app = create_app(production_settings(), otp_delivery=delivery)

    assert app.state.otp_delivery is delivery


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


def test_console_delivery_writes_to_stderr_and_not_to_logging(capsys, caplog):
    caplog.set_level(logging.DEBUG)

    DevelopmentConsoleOtpDelivery().deliver(PHONE, CODE)

    printed = capsys.readouterr()
    assert CODE in printed.err
    assert "+91******0001" in printed.err
    assert PHONE not in printed.err
    assert printed.out == ""
    assert CODE not in caplog.text
    assert caplog.records == []


def test_console_delivery_can_write_to_a_given_stream():
    stream = io.StringIO()

    DevelopmentConsoleOtpDelivery(stream).deliver(PHONE, CODE)

    assert CODE in stream.getvalue()


def test_no_delivery_sends_nothing(capsys, caplog):
    caplog.set_level(logging.DEBUG)

    NoOtpDelivery().deliver(PHONE, CODE)

    printed = capsys.readouterr()
    assert CODE not in printed.out + printed.err
    assert caplog.records == []


def test_in_memory_delivery_records_the_latest_code_per_number():
    delivery = InMemoryOtpDelivery()

    delivery.deliver(PHONE, "111111")
    delivery.deliver(PHONE, CODE)

    assert delivery.latest_code(PHONE) == CODE
    assert delivery.delivered_count(PHONE) == 2
    assert delivery.delivered_count("+919000000002") == 0
    with pytest.raises(KeyError):
        delivery.latest_code("+919000000002")


@pytest.mark.parametrize(
    "delivery",
    [NoOtpDelivery(), DevelopmentConsoleOtpDelivery(), InMemoryOtpDelivery()],
)
def test_repr_never_contains_a_code(delivery):
    if isinstance(delivery, InMemoryOtpDelivery):
        delivery.deliver(PHONE, CODE)

    assert CODE not in repr(delivery)
    assert PHONE not in repr(delivery)


@pytest.mark.parametrize(
    ("phone", "masked"),
    [
        ("+919000000001", "+91******0001"),
        ("+919876543210", "+91******3210"),
        ("9876543210", "******3210"),
        ("123", "***"),
    ],
)
def test_mask_phone_number(phone, masked):
    assert mask_phone_number(phone) == masked


# ---------------------------------------------------------------------------
# Development outbox
# ---------------------------------------------------------------------------

POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="file modes and O_NOFOLLOW are POSIX features"
)
ISSUED_AT = datetime(2026, 11, 7, 10, 30, 15, tzinfo=UTC)


def read_records(path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_development_with_an_outbox_uses_the_console_and_the_outbox(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    settings = make_settings(
        app_env="development",
        auth_debug_otp=True,
        auth_dev_otp_outbox_path=str(outbox),
    )

    delivery = otp_delivery_for(settings)

    assert isinstance(delivery, CompositeOtpDelivery)
    console, file_channel = delivery.channels
    assert isinstance(console, DevelopmentConsoleOtpDelivery)
    assert isinstance(file_channel, DevelopmentOutboxOtpDelivery)
    assert file_channel.path == outbox


def test_development_without_an_outbox_uses_the_console_only():
    settings = make_settings(app_env="development", auth_debug_otp=True)

    assert isinstance(otp_delivery_for(settings), DevelopmentConsoleOtpDelivery)


def test_outbox_writes_one_json_line_per_code(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    delivery = DevelopmentOutboxOtpDelivery(outbox, clock=lambda: ISSUED_AT)

    delivery.deliver(PHONE, CODE)
    delivery.deliver("+919000000002", "000123")

    raw = outbox.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert read_records(outbox) == [
        {
            "phone_number": PHONE,
            "code": CODE,
            "issued_at": "2026-11-07T10:30:15+00:00",
        },
        {
            "phone_number": "+919000000002",
            "code": "000123",
            "issued_at": "2026-11-07T10:30:15+00:00",
        },
    ]


def test_outbox_timestamps_are_utc_by_default(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    before = datetime.now(UTC)

    DevelopmentOutboxOtpDelivery(outbox).deliver(PHONE, CODE)

    (record,) = read_records(outbox)
    issued_at = datetime.fromisoformat(record["issued_at"])
    assert issued_at.utcoffset().total_seconds() == 0
    assert before <= issued_at <= datetime.now(UTC)


@POSIX_ONLY
def test_outbox_is_created_owner_read_write_only(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    previous_umask = os.umask(0)  # even with a permissive umask
    try:
        DevelopmentOutboxOtpDelivery(outbox).deliver(PHONE, CODE)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(outbox.stat().st_mode) == OUTBOX_FILE_MODE == 0o600


@POSIX_ONLY
def test_outbox_narrows_a_pre_existing_files_permissions(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    outbox.write_text("", encoding="utf-8")
    outbox.chmod(0o644)

    DevelopmentOutboxOtpDelivery(outbox).deliver(PHONE, CODE)

    assert stat.S_IMODE(outbox.stat().st_mode) == 0o600
    assert len(read_records(outbox)) == 1


OUTBOX_LOGGER = "app.services.otp_delivery"


def assert_logged_safely(caplog, *secrets: str) -> None:
    """One ERROR about the outbox, naming no path, number or code."""
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "expected the outbox failure to be logged"
    for record in caplog.records:
        message = record.getMessage()
        for secret in (PHONE, CODE, *secrets):
            assert secret not in message
        assert record.exc_info is None  # no traceback carrying the path


@POSIX_ONLY
def test_outbox_refuses_to_write_through_a_symbolic_link(tmp_path, caplog):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)
    target = tmp_path / "elsewhere.txt"
    target.write_text("untouched", encoding="utf-8")
    target.chmod(0o644)
    link = tmp_path / "outbox.jsonl"
    link.symlink_to(target)

    # Refused and logged, never raised to the caller.
    DevelopmentOutboxOtpDelivery(link).deliver(PHONE, CODE)

    assert target.read_text(encoding="utf-8") == "untouched"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert_logged_safely(caplog, str(link), str(target))


def test_outbox_refuses_a_directory(tmp_path, caplog):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)

    DevelopmentOutboxOtpDelivery(tmp_path).deliver(PHONE, CODE)

    assert list(tmp_path.iterdir()) == []
    assert_logged_safely(caplog, str(tmp_path))


def run_with_timeout(function, seconds: float = 5.0):
    """Run function in a thread; fail (instead of hanging) if it blocks.

    The pool is shut down without waiting, so a blocked call makes the test
    fail with TimeoutError rather than hang the test run.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(function).result(timeout=seconds)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_fifo_without_a_reader_cannot_hang_delivery(tmp_path, caplog):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)
    fifo = tmp_path / "outbox.jsonl"
    os.mkfifo(fifo)

    # A blocking open would wait forever for a reader.
    run_with_timeout(lambda: DevelopmentOutboxOtpDelivery(fifo).deliver(PHONE, CODE))

    assert stat.S_ISFIFO(fifo.stat().st_mode)
    assert_logged_safely(caplog, str(fifo))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_fifo_with_a_reader_is_rejected_and_receives_nothing(tmp_path, caplog):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)
    fifo = tmp_path / "outbox.jsonl"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        run_with_timeout(
            lambda: DevelopmentOutboxOtpDelivery(fifo).deliver(PHONE, CODE)
        )
        try:
            received = os.read(reader, 4096)
        except BlockingIOError:
            received = b""
    finally:
        os.close(reader)

    assert received == b""
    assert_logged_safely(caplog, str(fifo))


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")
def test_unix_socket_is_rejected(tmp_path, caplog):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)
    path = tmp_path / "outbox.jsonl"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        run_with_timeout(
            lambda: DevelopmentOutboxOtpDelivery(path).deliver(PHONE, CODE)
        )
    finally:
        server.close()

    assert stat.S_ISSOCK(path.stat().st_mode)
    assert_logged_safely(caplog, str(path))


@pytest.mark.skipif(not os.path.exists("/dev/null"), reason="needs /dev/null")
def test_device_file_is_rejected_without_being_changed(caplog):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)
    mode_before = os.stat("/dev/null").st_mode

    run_with_timeout(
        lambda: DevelopmentOutboxOtpDelivery("/dev/null").deliver(PHONE, CODE)
    )

    assert os.stat("/dev/null").st_mode == mode_before
    assert_logged_safely(caplog)


@pytest.mark.skipif(not hasattr(os, "link"), reason="needs hard links")
def test_hard_linked_outbox_is_rejected_and_its_target_unchanged(tmp_path, caplog):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)
    target = tmp_path / "important.txt"
    target.write_text("original content\n", encoding="utf-8")
    target.chmod(0o644)
    outbox = tmp_path / "outbox.jsonl"
    os.link(target, outbox)

    DevelopmentOutboxOtpDelivery(outbox).deliver(PHONE, CODE)

    assert target.read_text(encoding="utf-8") == "original content\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert_logged_safely(caplog, str(outbox), str(target))


def test_outbox_owned_by_another_user_is_rejected(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)
    outbox = tmp_path / "outbox.jsonl"
    outbox.write_text("", encoding="utf-8")
    owner = outbox.stat().st_uid
    # Pretend this process runs as someone else.
    monkeypatch.setattr(otp_delivery_module, "_current_uid", lambda: owner + 1)

    DevelopmentOutboxOtpDelivery(outbox).deliver(PHONE, CODE)

    assert outbox.read_text(encoding="utf-8") == ""
    assert_logged_safely(caplog, str(outbox))


def test_partial_write_is_rolled_back_so_the_next_record_is_clean(
    tmp_path, caplog, monkeypatch
):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)
    outbox = tmp_path / "outbox.jsonl"
    delivery = DevelopmentOutboxOtpDelivery(outbox, clock=lambda: ISSUED_AT)
    delivery.deliver(PHONE, "111111")
    size_after_first = outbox.stat().st_size

    real_write = os.write
    calls = {"count": 0}

    def partial_then_fail(descriptor, data):
        calls["count"] += 1
        if calls["count"] == 1:
            return real_write(descriptor, bytes(data[:10]))  # part of a line
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(otp_delivery_module, "_write", partial_then_fail)
    delivery.deliver(PHONE, CODE)
    monkeypatch.setattr(otp_delivery_module, "_write", real_write)

    # The partial bytes were removed again.
    assert calls["count"] == 2
    assert outbox.stat().st_size == size_after_first

    delivery.deliver(PHONE, "333333")

    assert [r["code"] for r in read_records(outbox)] == ["111111", "333333"]
    assert_logged_safely(caplog, str(outbox), "No space left")


def test_failed_rollback_is_logged_without_details(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.ERROR, logger=OUTBOX_LOGGER)
    outbox = tmp_path / "outbox.jsonl"

    def failing_write(descriptor, data):
        raise OSError(5, "Input/output error")

    def failing_truncate(descriptor, length):
        raise OSError(5, "Input/output error during truncate")

    monkeypatch.setattr(otp_delivery_module, "_write", failing_write)
    monkeypatch.setattr(otp_delivery_module, "_truncate", failing_truncate)

    DevelopmentOutboxOtpDelivery(outbox).deliver(PHONE, CODE)

    messages = [record.getMessage() for record in caplog.records]
    assert any("could not remove a partial record" in m for m in messages)
    assert any("outbox write failed" in m for m in messages)
    assert_logged_safely(caplog, str(outbox), "Input/output error")


def test_concurrent_outbox_writes_produce_complete_records(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    writers, per_writer = 8, 50
    barrier = threading.Barrier(writers, timeout=10)
    # Long phone values make each record bigger, so interleaving would show.
    padding = "9" * 2000

    def writer(index: int) -> None:
        # A separate instance per thread, like separate requests or workers.
        delivery = DevelopmentOutboxOtpDelivery(outbox)
        barrier.wait()
        for n in range(per_writer):
            delivery.deliver(f"+91{index}{padding}", f"{index:02d}{n:04d}")

    with ThreadPoolExecutor(max_workers=writers) as pool:
        for future in [pool.submit(writer, i) for i in range(writers)]:
            future.result(timeout=30)

    records = read_records(outbox)  # every line must be valid JSON
    assert len(records) == writers * per_writer
    assert {r["code"] for r in records} == {
        f"{i:02d}{n:04d}" for i in range(writers) for n in range(per_writer)
    }
    for record in records:
        assert record["phone_number"] == f"+91{record['code'][1]}{padding}"


def test_console_and_outbox_receive_the_same_code(tmp_path, capsys):
    outbox = tmp_path / "outbox.jsonl"
    delivery = CompositeOtpDelivery(
        (DevelopmentConsoleOtpDelivery(), DevelopmentOutboxOtpDelivery(outbox))
    )

    delivery.deliver(PHONE, CODE)

    assert CODE in capsys.readouterr().err
    assert [r["code"] for r in read_records(outbox)] == [CODE]


def test_outbox_reprs_never_contain_codes_or_paths(tmp_path):
    outbox = DevelopmentOutboxOtpDelivery(tmp_path / "outbox.jsonl")
    composite = CompositeOtpDelivery((DevelopmentConsoleOtpDelivery(), outbox))
    outbox.deliver(PHONE, CODE)

    for text in (repr(outbox), repr(composite)):
        assert CODE not in text
        assert PHONE not in text
        assert str(tmp_path) not in text


def test_create_app_wires_the_outbox_in_development(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    app = create_app(
        make_settings(
            app_env="development",
            auth_debug_otp=True,
            auth_dev_otp_outbox_path=str(outbox),
        )
    )

    assert isinstance(app.state.otp_delivery, CompositeOtpDelivery)
