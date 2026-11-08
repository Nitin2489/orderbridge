"""Shared database fixtures for automated tests.

Database tests run only against a dedicated PostgreSQL database configured by
TEST_DATABASE_URL. They never fall back to DATABASE_URL (the development
database), and they refuse to run if the configured database looks like it
could be the development database.

The test database schema is created manually with Alembic (see .env.example
and the error message in check_schema_at_head). These fixtures only read the
schema version; they never run migrations.
"""

from collections.abc import Generator

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings

TEST_DATABASE_SUFFIX = "_test"


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when the test database configuration is missing or unsafe."""


class DatabaseTestSettings(BaseSettings):
    """Test-only settings, read from the same .env file as the app."""

    test_database_url: SecretStr | None = Field(default=None, repr=False)

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def database_name(url: str, variable: str) -> str:
    """Return the database name from a URL without exposing the URL."""
    try:
        name = make_url(url).database
    except ArgumentError:
        # The original error message contains the URL, including the
        # password, so it is deliberately not chained.
        raise UnsafeTestDatabaseError(
            f"{variable} is not a valid SQLAlchemy database URL."
        ) from None

    if not name:
        raise UnsafeTestDatabaseError(f"{variable} has no database name.")

    return name


def resolve_test_database_url() -> str:
    """Return TEST_DATABASE_URL after checking it is safe to use."""
    secret = DatabaseTestSettings().test_database_url
    url = secret.get_secret_value().strip() if secret else ""

    if not url:
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_URL is not set. Database tests never fall back "
            "to DATABASE_URL; configure a dedicated test database "
            "(see .env.example)."
        )

    test_name = database_name(url, "TEST_DATABASE_URL")
    dev_name = database_name(settings.database_url.get_secret_value(), "DATABASE_URL")

    if test_name == dev_name:
        raise UnsafeTestDatabaseError(
            f"TEST_DATABASE_URL points at '{test_name}', the same database "
            "as DATABASE_URL. Refusing to run tests against the development "
            "database."
        )

    if not test_name.endswith(TEST_DATABASE_SUFFIX):
        raise UnsafeTestDatabaseError(
            f"Test database '{test_name}' must have a name ending in "
            f"'{TEST_DATABASE_SUFFIX}'."
        )

    return url


def check_connected_database(connection: Connection, expected: str) -> None:
    """Confirm the server reports the expected test database."""
    actual = connection.scalar(text("SELECT current_database()"))

    if actual != expected or not actual.endswith(TEST_DATABASE_SUFFIX):
        raise UnsafeTestDatabaseError(
            f"Connected to database '{actual}', expected '{expected}'. "
            "Refusing to run tests."
        )


def check_schema_at_head(connection: Connection) -> None:
    """Fail unless the test database is migrated to the latest revision."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    current = MigrationContext.configure(connection).get_current_revision()

    if current != head:
        raise UnsafeTestDatabaseError(
            f"Test database schema is at revision {current!r}, expected "
            f"{head!r}. Migrate it with:\n"
            "  cd backend && DATABASE_URL='<your TEST_DATABASE_URL>' "
            "uv run alembic upgrade head"
        )


@pytest.fixture(autouse=True)
def no_exported_dev_otp_outbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore an AUTH_DEV_OTP_OUTBOX_PATH exported in the developer's shell.

    Tests build settings for testing, staging and production, where an outbox
    is refused at startup; an exported development value would otherwise fail
    unrelated tests. Tests that need an outbox pass the path explicitly.
    """
    monkeypatch.delenv("AUTH_DEV_OTP_OUTBOX_PATH", raising=False)


@pytest.fixture(scope="session")
def test_engine() -> Generator[Engine, None, None]:
    """Engine for the dedicated test database, created once per test run."""
    url = resolve_test_database_url()
    expected_name = database_name(url, "TEST_DATABASE_URL")
    engine = create_engine(url, pool_pre_ping=True)

    try:
        with engine.connect() as connection:
            check_connected_database(connection, expected_name)
            check_schema_at_head(connection)

        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Generator[Session, None, None]:
    """Session whose changes are always rolled back after the test.

    The session joins an outer transaction on a single connection. With
    join_transaction_mode="create_savepoint", a session.commit() inside the
    code under test only releases a savepoint, so the final rollback still
    undoes everything and every test starts from the same empty state.
    """
    connection = test_engine.connect()
    outer_transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
