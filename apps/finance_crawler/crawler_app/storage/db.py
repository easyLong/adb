"""MySQL connection and initialization for the isolated crawler_app database."""

from __future__ import annotations

import pymysql
import pymysql.cursors

from apps.finance_crawler.crawler_app.settings import crawler_app_database_settings
from apps.finance_crawler.crawler_app.storage.schema import ensure_crawler_app_tables
from apps.finance_crawler.storage.mysql_resilience import connect_with_retry
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("crawler_app_db")


def _connect(*, database: str | None = None) -> pymysql.connections.Connection:
    settings = crawler_app_database_settings()
    kwargs = {
        "host": settings.host,
        "port": settings.port,
        "user": settings.user,
        "password": settings.password,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": settings.connect_timeout,
        "read_timeout": settings.read_timeout,
        "write_timeout": settings.write_timeout,
        "autocommit": False,
    }
    if database:
        kwargs["db"] = database
    return connect_with_retry(
        pymysql.connect,
        kwargs=kwargs,
        label="crawler_app",
        attempts=settings.connect_retries,
        retry_delay=settings.connect_retry_delay,
        retry_max_delay=settings.connect_retry_max_delay,
    )


def get_conn() -> pymysql.connections.Connection:
    settings = crawler_app_database_settings()
    return _connect(database=settings.database)


def ensure_database() -> None:
    settings = crawler_app_database_settings()
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.database}` "
                "DEFAULT CHARACTER SET utf8mb4 "
                "DEFAULT COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()


def init_crawler_app_db() -> None:
    ensure_database()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            ensure_crawler_app_tables(cursor)
        conn.commit()
        logger.info("crawler_app database initialized")
    except Exception:
        conn.rollback()
        logger.exception("crawler_app database initialization failed")
        raise
    finally:
        conn.close()

