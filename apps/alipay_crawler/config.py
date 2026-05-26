"""
Unified configuration for the Alipay crawler service.

Values can be overridden with environment variables so credentials do not have
to be hard-coded in the repository.
"""

from __future__ import annotations

import os
import shutil
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


def _default_adb_path(project_dir: Path) -> str:
    bundled = project_dir / "platform-tools" / "adb.exe"
    if bundled.exists():
        return str(bundled)
    return shutil.which("adb") or "adb"


class Config:
    BASE_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = BASE_DIR.parents[1]

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
    QQ_WRITE_DELAY = _env_float("TENCENT_DOC_WRITE_DELAY", 0.3)
    QQ_BATCH_UPDATE_SIZE = _env_int("TENCENT_DOC_BATCH_UPDATE_SIZE", 5)
    VALIDATE_DOC_ROW_BEFORE_WRITE = _env_bool("VALIDATE_DOC_ROW_BEFORE_WRITE", True)

    # Sheet column indexes, zero-based. Defaults match the test sheet:
    # J=post time, N=post link, O=read count, P=comment count.
    QQ_COL_POST_TIME = _env_int("TENCENT_DOC_COL_POST_TIME", 9)
    QQ_COL_URL = _env_int("TENCENT_DOC_COL_URL", 13)
    QQ_COL_ACCOUNT_NAME = _env_int("TENCENT_DOC_COL_ACCOUNT_NAME", 11)
    QQ_COL_READ_COUNT = _env_int("TENCENT_DOC_COL_READ_COUNT", 14)
    QQ_COL_COMMENT_COUNT = _env_int("TENCENT_DOC_COL_COMMENT_COUNT", 15)
    QQ_COL_CHECK_STATUS = _env_int("TENCENT_DOC_COL_CHECK_STATUS", 16)
    QQ_COL_BATCH_STATUS = _env_int("TENCENT_DOC_COL_BATCH_STATUS", 16)
    QQ_COL_SCREENSHOT = _env_int("TENCENT_DOC_COL_SCREENSHOT", 17)
    SCREENSHOT_PUBLIC_BASE_URL = _env("SCREENSHOT_PUBLIC_BASE_URL", "")
    QQ_UPLOAD_SCREENSHOTS = _env_bool(
        "TENCENT_DOC_UPLOAD_SCREENSHOTS",
        _env_bool("TENCENT_DOC_ENABLE_IMAGE_UPLOAD", True),
    )
    QQ_IMAGE_INSERT_WIDTH = _env_float("TENCENT_DOC_IMAGE_INSERT_WIDTH", 160.0)
    QQ_IMAGE_INSERT_HEIGHT = _env_float("TENCENT_DOC_IMAGE_INSERT_HEIGHT", 300.0)
    QQ_IMAGE_UPLOAD_DELAY = _env_float("TENCENT_DOC_IMAGE_UPLOAD_DELAY", 0.25)
    QQ_IMAGE_UPLOAD_TIMEOUT = _env_int("TENCENT_DOC_IMAGE_UPLOAD_TIMEOUT", 30)

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
    MAX_POSTS_PER_RUN = _env_int("MAX_POSTS_PER_RUN", 0)
    CRAWL_MAX_TASK_SECONDS = _env_int("CRAWL_MAX_TASK_SECONDS", 0)
    CRAWL_MAX_CONSECUTIVE_ERRORS = _env_int("CRAWL_MAX_CONSECUTIVE_ERRORS", 5)
    URL_RESOLVE_WORKERS = _env_int("URL_RESOLVE_WORKERS", 4)
    CRAWL_ACTIVE_START = _env("CRAWL_ACTIVE_START", "")
    CRAWL_ACTIVE_END = _env("CRAWL_ACTIVE_END", "")

    # ADB / device
    ADB_PATH = _env("ADB_PATH", _default_adb_path(PROJECT_DIR))
    DEVICE_SERIAL = _env("DEVICE_SERIAL", "")
    DEVICE_CHECK_TIMEOUT = _env_int("DEVICE_CHECK_TIMEOUT", 8)
    DEVICE_HEALTH_CACHE_SECONDS = _env_float("DEVICE_HEALTH_CACHE_SECONDS", 8.0)
    DEVICE_PREPARE_INTERVAL_SECONDS = _env_float("DEVICE_PREPARE_INTERVAL_SECONDS", 120.0)
    ALIPAY_PACKAGE = "com.eg.android.AlipayGphone"
    AFWEALTH_PACKAGE = "com.antfortune.wealth"
    TENPAY_PACKAGE = _env("TENPAY_PACKAGE", "com.tencent.fortuneplat")

    # Crawl behavior
    POST_DELAY_MIN = _env_float("POST_DELAY_MIN", 2.0)
    POST_DELAY_MAX = _env_float("POST_DELAY_MAX", 4.5)
    SCROLL_TIMES = _env_int("SCROLL_TIMES", 2)
    BATCH_MAX_CAPTURE_PAGES = _env_int("BATCH_MAX_CAPTURE_PAGES", 3)
    PAGE_LOAD_WAIT = _env_float("PAGE_LOAD_WAIT", 3.0)
    BATCH_POST_DELAY_MIN = _env_float("BATCH_POST_DELAY_MIN", 1.0)
    BATCH_POST_DELAY_MAX = _env_float("BATCH_POST_DELAY_MAX", 2.0)
    BATCH_SCROLL_WAIT = _env_float("BATCH_SCROLL_WAIT", 0.8)
    BATCH_ENABLE_OCR = _env_bool("BATCH_ENABLE_OCR", True)
    OCR_MIN_CONFIDENCE = _env_float("OCR_MIN_CONFIDENCE", 30.0)

    # Files
    SCREENSHOT_DIR = BASE_DIR / "screenshots"
    LOG_DIR = BASE_DIR / "logs"
    CAPTURE_DIR = BASE_DIR / "captures"
    REPORT_DIR = BASE_DIR / "reports"
    EXPORT_DIR = BASE_DIR / "exports"
    CACHE_FILE = BASE_DIR / ".alipay_scheme_cache.json"
    TOKEN_CACHE_FILE = BASE_DIR / ".qq_token_cache.json"
    LATEST_CANDIDATES_FILE = EXPORT_DIR / "latest_candidates.json"
    ALERT_LOG_FILE = LOG_DIR / "alerts.jsonl"

    # Reports
    READ_COUNT_THRESHOLD = _env_int("READ_COUNT_THRESHOLD", 30)
    REPORT_TOP_N = _env_int("REPORT_TOP_N", 3)

    # Alerts and supervision
    ALERT_ENABLED = _env_bool("ALERT_ENABLED", True)
    ALERT_WEBHOOK_URL = _env("ALERT_WEBHOOK_URL", "")
    ALERT_MIN_INTERVAL_SECONDS = _env_int("ALERT_MIN_INTERVAL_SECONDS", 300)
    HEARTBEAT_INTERVAL_MINUTES = _env_int("HEARTBEAT_INTERVAL_MINUTES", 30)
    SUPERVISOR_RESTART_DELAY_SECONDS = _env_int("SUPERVISOR_RESTART_DELAY_SECONDS", 10)
    SUPERVISOR_MAX_RESTARTS = _env_int("SUPERVISOR_MAX_RESTARTS", 0)


for path in [
    Config.SCREENSHOT_DIR,
    Config.LOG_DIR,
    Config.CAPTURE_DIR,
    Config.REPORT_DIR,
    Config.EXPORT_DIR,
]:
    path.mkdir(parents=True, exist_ok=True)
