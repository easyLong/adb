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


def crawler_app_database_settings() -> DatabaseSettings:
    return DatabaseSettings(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.CRAWLER_APP_DB_NAME,
        connect_timeout=Config.DB_CONNECT_TIMEOUT,
    )

