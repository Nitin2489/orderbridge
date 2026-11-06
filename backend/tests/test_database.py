from sqlalchemy import text
from sqlalchemy.engine import Engine


def test_database_connection(test_engine: Engine) -> None:
    """Verify that the application can connect to PostgreSQL."""

    with test_engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))

    assert result.scalar_one() == 1
