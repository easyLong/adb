"""Settings for the isolated crawler_app database."""

from __future__ import annotations

from dataclasses import dataclass

from apps.finance_crawler.config import Config


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    connect_timeout: int
    read_timeout: int
    write_timeout: int
    connect_retries: int
    connect_retry_delay: float
    connect_retry_max_delay: float


def crawler_app_database_settings() -> DatabaseSettings:
    return DatabaseSettings(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.CRAWLER_APP_DB_NAME,
        connect_timeout=Config.DB_CONNECT_TIMEOUT,
        read_timeout=Config.DB_READ_TIMEOUT,
        write_timeout=Config.DB_WRITE_TIMEOUT,
        connect_retries=Config.DB_CONNECT_RETRIES,
        connect_retry_delay=Config.DB_CONNECT_RETRY_DELAY,
        connect_retry_max_delay=Config.DB_CONNECT_RETRY_MAX_DELAY,
    )


def ops_platform_database_settings() -> DatabaseSettings:
    return DatabaseSettings(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.OPS_PLATFORM_DB_NAME,
        connect_timeout=Config.DB_CONNECT_TIMEOUT,
        read_timeout=Config.DB_READ_TIMEOUT,
        write_timeout=Config.DB_WRITE_TIMEOUT,
        connect_retries=Config.DB_CONNECT_RETRIES,
        connect_retry_delay=Config.DB_CONNECT_RETRY_DELAY,
        connect_retry_max_delay=Config.DB_CONNECT_RETRY_MAX_DELAY,
    )

