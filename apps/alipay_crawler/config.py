"""
Unified configuration for the Alipay crawler service.

Values can be overridden with environment variables so credentials do not have
to be hard-coded in the repository.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    return float(value) if value else default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


class Config:
    BASE_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = BASE_DIR.parent

    # MySQL
    DB_HOST = _env("MYSQL_HOST", "localhost")
    DB_PORT = _env_int("MYSQL_PORT", 3306)
    DB_USER = _env("MYSQL_USER", "root")
    DB_PASSWORD = _env("MYSQL_PASSWORD", "")
    DB_NAME = _env("MYSQL_DATABASE", "alipay_crawler")
    DB_CONNECT_TIMEOUT = _env_int("MYSQL_CONNECT_TIMEOUT", 10)

    # Tencent Docs OpenAPI
    QQ_DOC_URL = _env(
        "TENCENT_DOC_URL",
        "https://docs.qq.com/sheet/DY1hCSG96TkVySmp1?tab=BB08J2",
    )
    QQ_FILE_ID = _env("TENCENT_DOC_FILE_ID", "DY1hCSG96TkVySmp1")
    QQ_SHEET_ID = _env("TENCENT_DOC_SHEET_ID", "BB08J2")
    QQ_READ_RANGE = _env("TENCENT_DOC_READ_RANGE", "A1:P625")

    QQ_ACCESS_TOKEN = _env("TENCENT_DOC_ACCESS_TOKEN")
    QQ_CLIENT_ID = _env("TENCENT_DOC_CLIENT_ID")
    QQ_CLIENT_SECRET = _env("TENCENT_DOC_CLIENT_SECRET")
    QQ_OPEN_ID = _env("TENCENT_DOC_OPEN_ID")
    QQ_TOKEN_URL = _env("TENCENT_DOC_TOKEN_URL", "https://docs.qq.com/oauth/v2/token")
    QQ_WRITE_DELAY = _env_float("TENCENT_DOC_WRITE_DELAY", 1.0)

    # Sheet column indexes, zero-based. Defaults match the test sheet:
    # J=post time, N=post link, O=read count, P=comment count.
    QQ_COL_POST_TIME = _env_int("TENCENT_DOC_COL_POST_TIME", 9)
    QQ_COL_URL = _env_int("TENCENT_DOC_COL_URL", 13)
    QQ_COL_ACCOUNT_NAME = _env_int("TENCENT_DOC_COL_ACCOUNT_NAME", 11)
    QQ_COL_READ_COUNT = _env_int("TENCENT_DOC_COL_READ_COUNT", 14)
    QQ_COL_COMMENT_COUNT = _env_int("TENCENT_DOC_COL_COMMENT_COUNT", 15)
    QQ_COL_CHECK_STATUS = _env_int("TENCENT_DOC_COL_CHECK_STATUS", 16)
    QQ_COL_BATCH_STATUS = _env_int("TENCENT_DOC_COL_BATCH_STATUS", 16)

    # Polling and eligibility
    FETCH_INTERVAL_MINUTES = _env_int("FETCH_INTERVAL_MINUTES", 5)
    POST_ELIGIBLE_HOURS = _env_float("POST_ELIGIBLE_HOURS", 2.0)
    # 10 for testing. Set FETCH_LIMIT=0 to import all eligible rows.
    FETCH_LIMIT = _env_int("FETCH_LIMIT", 10)
    # 0 means all eligible rows for the next-day 10:00 batch.
    BATCH_LIMIT = _env_int("BATCH_LIMIT", 0)
    BATCH_TIME = _env("BATCH_TIME", "10:00")
    REPORT_TIME = _env("REPORT_TIME", "11:30")

    ENABLE_CHECKER = _env_bool("ENABLE_CHECKER", True)
    CHECK_INTERVAL_MINUTES = _env_int("CHECK_INTERVAL_MINUTES", 10)
    CHECK_MAX_RETRIES = _env_int("CHECK_MAX_RETRIES", 3)
    BATCH_MAX_RETRIES = _env_int("BATCH_MAX_RETRIES", 2)
    BATCH_REQUIRES_CHECK_SUCCESS = _env_bool("BATCH_REQUIRES_CHECK_SUCCESS", True)
    BATCH_NEXT_DAY_ONLY = _env_bool("BATCH_NEXT_DAY_ONLY", True)

    # ADB / device
    ADB_PATH = _env("ADB_PATH", str(PROJECT_DIR / "platform-tools" / "adb.exe"))
    DEVICE_SERIAL = _env("DEVICE_SERIAL", "")
    ALIPAY_PACKAGE = "com.eg.android.AlipayGphone"

    # Crawl behavior
    POST_DELAY_MIN = _env_float("POST_DELAY_MIN", 2.0)
    POST_DELAY_MAX = _env_float("POST_DELAY_MAX", 4.5)
    SCROLL_TIMES = _env_int("SCROLL_TIMES", 15)
    PAGE_LOAD_WAIT = _env_float("PAGE_LOAD_WAIT", 4.0)

    # Files
    SCREENSHOT_DIR = BASE_DIR / "screenshots"
    LOG_DIR = BASE_DIR / "logs"
    CAPTURE_DIR = BASE_DIR / "captures"
    REPORT_DIR = BASE_DIR / "reports"
    EXPORT_DIR = BASE_DIR / "exports"
    CACHE_FILE = BASE_DIR / ".alipay_scheme_cache.json"
    TOKEN_CACHE_FILE = BASE_DIR / ".qq_token_cache.json"
    LATEST_CANDIDATES_FILE = EXPORT_DIR / "latest_candidates.json"

    # Reports
    READ_COUNT_THRESHOLD = _env_int("READ_COUNT_THRESHOLD", 30)
    REPORT_TOP_N = _env_int("REPORT_TOP_N", 3)


for path in [
    Config.SCREENSHOT_DIR,
    Config.LOG_DIR,
    Config.CAPTURE_DIR,
    Config.REPORT_DIR,
    Config.EXPORT_DIR,
]:
    path.mkdir(parents=True, exist_ok=True)
