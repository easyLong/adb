"""Shared MySQL connection retry helpers."""

from __future__ import annotations

import time
from typing import Any, Callable

import pymysql

from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("mysql_resilience")


TRANSIENT_CONNECT_ERRORS = (
    pymysql.err.OperationalError,
    pymysql.err.InterfaceError,
    TimeoutError,
    OSError,
)


def connect_with_retry(
    connect_factory: Callable[..., Any],
    *,
    kwargs: dict[str, Any],
    label: str,
    attempts: int,
    retry_delay: float,
    retry_max_delay: float,
) -> Any:
    """Open a MySQL connection with bounded retry and backoff.

    This helper retries only connection creation. It intentionally does not
    replay SQL statements, because many crawler writes are not safe to execute
    twice outside their existing transaction/queue boundaries.
    """

    max_attempts = max(1, int(attempts or 1))
    delay = max(0.0, float(retry_delay or 0.0))
    max_delay = max(delay, float(retry_max_delay or delay or 0.0))
    last_error: BaseException | None = None

    for attempt_no in range(1, max_attempts + 1):
        try:
            return connect_factory(**kwargs)
        except TRANSIENT_CONNECT_ERRORS as exc:
            last_error = exc
            if attempt_no >= max_attempts:
                break
            sleep_seconds = min(delay * (2 ** (attempt_no - 1)), max_delay) if delay else 0.0
            logger.warning(
                "MySQL connect failed label=%s attempt=%s/%s target=%s error=%s; retry in %.1fs",
                label,
                attempt_no,
                max_attempts,
                describe_mysql_target(kwargs),
                exc,
                sleep_seconds,
            )
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    logger.error(
        "MySQL connect exhausted label=%s attempts=%s target=%s error=%s",
        label,
        max_attempts,
        describe_mysql_target(kwargs),
        last_error,
    )
    if last_error:
        raise last_error
    return connect_factory(**kwargs)


def describe_mysql_target(kwargs: dict[str, Any]) -> str:
    host = str(kwargs.get("host") or "")
    port = kwargs.get("port") or 3306
    database = kwargs.get("db") or "<server>"
    return f"{host}:{port}/{database}"

