"""Transaction helpers shared by API endpoints."""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def rollback_after_error(db: Session, error: Exception, logger: logging.Logger) -> None:
    """Roll back after a handled service error; a failed rollback is a 500.

    If the rollback itself fails, the transaction state is unknown, so the
    endpoint must not return its normal error response. The failure is
    logged with the service error's class name (never its message or the
    database error text in the response) and re-raised; the request then
    ends as a generic 500. The re-raised exception keeps the service error
    as its __context__, so the log shows both.
    """
    try:
        db.rollback()
    except SQLAlchemyError:
        logger.exception(
            "Rollback failed after %s; responding with a server error.",
            type(error).__name__,
        )
        raise
