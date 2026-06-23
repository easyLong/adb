"""Database-backed runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs.client import parse_doc_url
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("runtime_config")

_DATA_SOURCE_TABLE = "data_source_links"
_APP_CONFIG_TABLE = "app_config"


@dataclass(frozen=True, slots=True)
class RuntimeConfigItem:
    key: str
    value: str
    enabled: bool = True
    status: str = "active"
    description: str = ""
    secret: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeConfigDisplayItem:
    key: str
    label: str
    value: str
    description: str = ""
    enabled: bool = True
    status: str = "active"
    secret: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeConfigDisplayGroup:
    title: str
    description: str
    items: tuple[RuntimeConfigDisplayItem, ...]


_CONFIG_ATTRS: dict[str, str] = {
    "TENCENT_DOC_URL": "QQ_DOC_URL",
    "TENCENT_DOC_FILE_ID": "QQ_FILE_ID",
    "TENCENT_DOC_SHEET_ID": "QQ_SHEET_ID",
    "TENCENT_DOC_READ_RANGE": "QQ_READ_RANGE",
    "TENCENT_DOC_SCAN_MODE": "QQ_SCAN_MODE",
    "TENCENT_DOC_SCAN_DATE": "QQ_SCAN_DATE",
    "TENCENT_DOC_SHEET_TITLE_FILTER": "QQ_SHEET_TITLE_FILTER",
    "TENCENT_DOC_ACCESS_TOKEN": "QQ_ACCESS_TOKEN",
    "TENCENT_DOC_CLIENT_ID": "QQ_CLIENT_ID",
    "TENCENT_DOC_CLIENT_SECRET": "QQ_CLIENT_SECRET",
    "TENCENT_DOC_OPEN_ID": "QQ_OPEN_ID",
    "TENCENT_DOC_TOKEN_URL": "QQ_TOKEN_URL",
    "FETCH_INTERVAL_MINUTES": "FETCH_INTERVAL_MINUTES",
    "ENABLE_LEGACY_SCHEDULER_JOBS": "ENABLE_LEGACY_SCHEDULER_JOBS",
    "FETCH_LIMIT": "FETCH_LIMIT",
    "CHECK_INTERVAL_MINUTES": "CHECK_INTERVAL_MINUTES",
    "DETAIL_TIME": "DETAIL_TIME",
    "DETAIL_INTERVAL_MINUTES": "DETAIL_INTERVAL_MINUTES",
    "SUBMIT_WORKER_INTERVAL_SECONDS": "SUBMIT_WORKER_INTERVAL_SECONDS",
    "V2_CRAWL_WORKER_INTERVAL_SECONDS": "V2_CRAWL_WORKER_INTERVAL_SECONDS",
    "V2_WRITEBACK_WORKER_INTERVAL_SECONDS": "V2_WRITEBACK_WORKER_INTERVAL_SECONDS",
    "REPORT_TIME": "REPORT_TIME",
    "TENCENT_DOC_REPORT_SHEET_TITLE": "TENCENT_DOC_REPORT_SHEET_TITLE",
    "PROFILE_METRICS_CRAWL_LIMIT": "PROFILE_METRICS_CRAWL_LIMIT",
    "KOL_DAILY_CRAWL_TIME": "KOL_DAILY_CRAWL_TIME",
    "KOL_DAILY_CRAWL_LIMIT": "KOL_DAILY_CRAWL_LIMIT",
    "KOL_TENPAY_EXTERNAL_READS_SOURCE_DOC_URLS": "KOL_TENPAY_EXTERNAL_READS_SOURCE_DOC_URLS",
    "KOL_TENPAY_EXTERNAL_READS_TARGET_DOC_URL": "KOL_TENPAY_EXTERNAL_READS_TARGET_DOC_URL",
    "KOL_TENPAY_EXTERNAL_READS_SOURCE_RANGE": "KOL_TENPAY_EXTERNAL_READS_SOURCE_RANGE",
    "KOL_TENPAY_EXTERNAL_READS_TARGET_RANGE": "KOL_TENPAY_EXTERNAL_READS_TARGET_RANGE",
    "KOL_TENPAY_EXTERNAL_READS_TIME": "KOL_TENPAY_EXTERNAL_READS_TIME",
    "KOL_TENPAY_EXTERNAL_READS_TARGET_PLATFORM": "KOL_TENPAY_EXTERNAL_READS_TARGET_PLATFORM",
    "KOL_TENPAY_EXTERNAL_READS_WRITEBACK_FONT_SIZE": "KOL_TENPAY_EXTERNAL_READS_WRITEBACK_FONT_SIZE",
    "KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS": "KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS",
    "PROFILE_POST_READ_CRAWL_LIMIT": "PROFILE_POST_READ_CRAWL_LIMIT",
    "PROFILE_POST_READ_MAX_SCROLLS": "PROFILE_POST_READ_MAX_SCROLLS",
    "PROFILE_POST_READ_MAX_POSTS": "PROFILE_POST_READ_MAX_POSTS",
    "ARTICLE_DETAILS_DOC_URL": "ARTICLE_DETAILS_DOC_URL",
    "ARTICLE_DETAILS_READ_RANGE": "ARTICLE_DETAILS_READ_RANGE",
    "ARTICLE_DETAILS_CRAWL_LIMIT": "ARTICLE_DETAILS_CRAWL_LIMIT",
    "ARTICLE_DETAILS_WRITEBACK_ENABLED": "ARTICLE_DETAILS_WRITEBACK_ENABLED",
    "DOC_LINK_READS_READ_RANGE": "DOC_LINK_READS_READ_RANGE",
    "DOC_LINK_READS_SHEET_TITLE": "DOC_LINK_READS_SHEET_TITLE",
    "DOC_LINK_READS_CRAWL_LIMIT": "DOC_LINK_READS_CRAWL_LIMIT",
    "DOC_LINK_READS_ONLY_EMPTY": "DOC_LINK_READS_ONLY_EMPTY",
    "DOC_LINK_READS_LINK_COL": "DOC_LINK_READS_LINK_COL",
    "DOC_LINK_READS_READ_COL": "DOC_LINK_READS_READ_COL",
    "DOC_LINK_READS_ENABLE_OCR": "DOC_LINK_READS_ENABLE_OCR",
    "DOC_LINK_READS_OPEN_RETRIES": "DOC_LINK_READS_OPEN_RETRIES",
    "DOC_LINK_READS_RETRYABLE_COOLDOWN_SECONDS": "DOC_LINK_READS_RETRYABLE_COOLDOWN_SECONDS",
    "WECHAT_DEVICE_SERIAL": "WECHAT_DEVICE_SERIAL",
    "WECHAT_SYNC_PAGES": "WECHAT_SYNC_PAGES",
    "WECHAT_SYNC_OUT_DIR": "WECHAT_SYNC_OUT_DIR",
    "WECHAT_SYNC_LIMIT": "WECHAT_SYNC_LIMIT",
    "WECHAT_SYNC_PARSE_MODE": "WECHAT_SYNC_PARSE_MODE",
    "WECHAT_SYNC_CONTEXT_SIZE": "WECHAT_SYNC_CONTEXT_SIZE",
    "WECHAT_SCHEDULER_ENABLED": "WECHAT_SCHEDULER_ENABLED",
    "WECHAT_SCHEDULER_START_TIME": "WECHAT_SCHEDULER_START_TIME",
    "WECHAT_SCHEDULER_END_TIME": "WECHAT_SCHEDULER_END_TIME",
    "WECHAT_SCHEDULER_INTERVAL_MINUTES": "WECHAT_SCHEDULER_INTERVAL_MINUTES",
    "WECHAT_SCHEDULER_WORKDAYS": "WECHAT_SCHEDULER_WORKDAYS",
    "ANTFORTUNE_READ_COUNT_WARMUP_ENABLED": "ANTFORTUNE_READ_COUNT_WARMUP_ENABLED",
    "ANTFORTUNE_READ_COUNT_WARMUP_BEFORE_OPEN": "ANTFORTUNE_READ_COUNT_WARMUP_BEFORE_OPEN",
    "ANTFORTUNE_READ_COUNT_RECOVER_ON_RETRYABLE": "ANTFORTUNE_READ_COUNT_RECOVER_ON_RETRYABLE",
    "ANTFORTUNE_READ_COUNT_WARMUP_WAIT_SECONDS": "ANTFORTUNE_READ_COUNT_WARMUP_WAIT_SECONDS",
    "ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT": "ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT",
    "ANTFORTUNE_READ_COUNT_WARMUP_AFTER_SWIPE_SECONDS": "ANTFORTUNE_READ_COUNT_WARMUP_AFTER_SWIPE_SECONDS",
    "EXCEL_DETAIL_INPUT_PATH": "EXCEL_DETAIL_INPUT_PATH",
    "EXCEL_DETAIL_OUTPUT_PATH": "EXCEL_DETAIL_OUTPUT_PATH",
    "EXCEL_DETAIL_RESULT_JSONL_PATH": "EXCEL_DETAIL_RESULT_JSONL_PATH",
    "EXCEL_DETAIL_SHEET_NAME": "EXCEL_DETAIL_SHEET_NAME",
    "EXCEL_DETAIL_SOURCE_FILTER": "EXCEL_DETAIL_SOURCE_FILTER",
    "EXCEL_DETAIL_ONLY_EMPTY": "EXCEL_DETAIL_ONLY_EMPTY",
    "WRITEBACK_EXCEL_PATH": "WRITEBACK_EXCEL_PATH",
    "WRITEBACK_EXCEL_SAVE_AS": "WRITEBACK_EXCEL_SAVE_AS",
    "WRITEBACK_EXCEL_SHEET_NAME": "WRITEBACK_EXCEL_SHEET_NAME",
    "DEVICE_SERIAL": "DEVICE_SERIAL",
    "DEVICE_POOL_ENABLED": "DEVICE_POOL_ENABLED",
    "DEVICE_LOCK_WAIT_SECONDS": "DEVICE_LOCK_WAIT_SECONDS",
    "DEVICE_LOCK_POLL_SECONDS": "DEVICE_LOCK_POLL_SECONDS",
    "DEVICE_LEASE_SECONDS": "DEVICE_LEASE_SECONDS",
    "DEVICE_RISK_COOLDOWN_SECONDS": "DEVICE_RISK_COOLDOWN_SECONDS",
    "DEVICE_UNAVAILABLE_COOLDOWN_SECONDS": "DEVICE_UNAVAILABLE_COOLDOWN_SECONDS",
    "DEVICE_LOGIN_COOLDOWN_SECONDS": "DEVICE_LOGIN_COOLDOWN_SECONDS",
    "APP_OPEN_RECOVERY_RETRIES": "APP_OPEN_RECOVERY_RETRIES",
    "APP_RESTART_WAIT": "APP_RESTART_WAIT",
    "POST_DELAY_MIN": "POST_DELAY_MIN",
    "POST_DELAY_MAX": "POST_DELAY_MAX",
    "READ_COUNT_POST_DELAY_MIN": "READ_COUNT_POST_DELAY_MIN",
    "READ_COUNT_POST_DELAY_MAX": "READ_COUNT_POST_DELAY_MAX",
    "DETAIL_POST_DELAY_MIN": "DETAIL_POST_DELAY_MIN",
    "DETAIL_POST_DELAY_MAX": "DETAIL_POST_DELAY_MAX",
    "DETAIL_BLANK_REOPEN_WAIT": "DETAIL_BLANK_REOPEN_WAIT",
    "DETAIL_BLANK_REOPEN_RETRIES": "DETAIL_BLANK_REOPEN_RETRIES",
    "DETAIL_SCROLL_WAIT": "DETAIL_SCROLL_WAIT",
    "PAGE_LOAD_WAIT": "PAGE_LOAD_WAIT",
    "PAGE_STATUS_READY_TIMEOUT": "PAGE_STATUS_READY_TIMEOUT",
    "PAGE_STATUS_READY_INTERVAL": "PAGE_STATUS_READY_INTERVAL",
    "CRAWL_ACTIVE_START": "CRAWL_ACTIVE_START",
    "CRAWL_ACTIVE_END": "CRAWL_ACTIVE_END",
    "CRAWL_MAX_TASK_SECONDS": "CRAWL_MAX_TASK_SECONDS",
    "TASK_RUNNING_TIMEOUT_MINUTES": "TASK_RUNNING_TIMEOUT_MINUTES",
}

_DATA_SOURCE_KEYS: tuple[str, ...] = (
    "TENCENT_DOC_URL",
    "EXCEL_DETAIL_INPUT_PATH",
    "SINGLE_TEST_LINK",
    "KOL_TENPAY_EXTERNAL_READS_TARGET_DOC_URL",
    "ARTICLE_DETAILS_DOC_URL",
)

_OPENAPI_CONFIG_KEYS: tuple[str, ...] = (
    "TENCENT_DOC_CLIENT_ID",
    "TENCENT_DOC_OPEN_ID",
    "TENCENT_DOC_ACCESS_TOKEN",
    "TENCENT_DOC_CLIENT_SECRET",
    "TENCENT_DOC_TOKEN_URL",
)

_APP_BEHAVIOR_CONFIG_KEYS: tuple[str, ...] = (
    "APP_OPEN_RECOVERY_RETRIES",
    "APP_RESTART_WAIT",
    "POST_DELAY_MIN",
    "POST_DELAY_MAX",
    "READ_COUNT_POST_DELAY_MIN",
    "READ_COUNT_POST_DELAY_MAX",
    "DETAIL_POST_DELAY_MIN",
    "DETAIL_POST_DELAY_MAX",
    "DETAIL_BLANK_REOPEN_WAIT",
    "DETAIL_BLANK_REOPEN_RETRIES",
    "DETAIL_SCROLL_WAIT",
    "PAGE_LOAD_WAIT",
    "PAGE_STATUS_READY_TIMEOUT",
    "PAGE_STATUS_READY_INTERVAL",
    "ENABLE_LEGACY_SCHEDULER_JOBS",
    "DETAIL_INTERVAL_MINUTES",
    "SUBMIT_WORKER_INTERVAL_SECONDS",
    "V2_CRAWL_WORKER_INTERVAL_SECONDS",
    "V2_WRITEBACK_WORKER_INTERVAL_SECONDS",
    "TASK_RUNNING_TIMEOUT_MINUTES",
    "TENCENT_DOC_REPORT_SHEET_TITLE",
    "PROFILE_METRICS_CRAWL_LIMIT",
    "KOL_DAILY_CRAWL_TIME",
    "KOL_DAILY_CRAWL_LIMIT",
    "KOL_TENPAY_EXTERNAL_READS_SOURCE_DOC_URLS",
    "KOL_TENPAY_EXTERNAL_READS_SOURCE_RANGE",
    "KOL_TENPAY_EXTERNAL_READS_TARGET_RANGE",
    "KOL_TENPAY_EXTERNAL_READS_TIME",
    "KOL_TENPAY_EXTERNAL_READS_TARGET_PLATFORM",
    "KOL_TENPAY_EXTERNAL_READS_WRITEBACK_FONT_SIZE",
    "KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS",
    "PROFILE_POST_READ_CRAWL_LIMIT",
    "PROFILE_POST_READ_MAX_SCROLLS",
    "PROFILE_POST_READ_MAX_POSTS",
    "ARTICLE_DETAILS_READ_RANGE",
    "ARTICLE_DETAILS_CRAWL_LIMIT",
    "ARTICLE_DETAILS_WRITEBACK_ENABLED",
    "DOC_LINK_READS_READ_RANGE",
    "DOC_LINK_READS_SHEET_TITLE",
    "DOC_LINK_READS_CRAWL_LIMIT",
    "DOC_LINK_READS_ONLY_EMPTY",
    "DOC_LINK_READS_LINK_COL",
    "DOC_LINK_READS_READ_COL",
    "DOC_LINK_READS_ENABLE_OCR",
    "DOC_LINK_READS_OPEN_RETRIES",
    "DOC_LINK_READS_RETRYABLE_COOLDOWN_SECONDS",
    "WECHAT_DEVICE_SERIAL",
    "WECHAT_SYNC_PAGES",
    "WECHAT_SYNC_OUT_DIR",
    "WECHAT_SYNC_LIMIT",
    "WECHAT_SYNC_PARSE_MODE",
    "WECHAT_SYNC_CONTEXT_SIZE",
    "WECHAT_SCHEDULER_ENABLED",
    "WECHAT_SCHEDULER_START_TIME",
    "WECHAT_SCHEDULER_END_TIME",
    "WECHAT_SCHEDULER_INTERVAL_MINUTES",
    "WECHAT_SCHEDULER_WORKDAYS",
    "ANTFORTUNE_READ_COUNT_WARMUP_ENABLED",
    "ANTFORTUNE_READ_COUNT_WARMUP_BEFORE_OPEN",
    "ANTFORTUNE_READ_COUNT_RECOVER_ON_RETRYABLE",
    "ANTFORTUNE_READ_COUNT_WARMUP_WAIT_SECONDS",
    "ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT",
    "ANTFORTUNE_READ_COUNT_WARMUP_AFTER_SWIPE_SECONDS",
    "DEVICE_SERIAL",
    "DEVICE_POOL_ENABLED",
    "DEVICE_LOCK_WAIT_SECONDS",
    "DEVICE_LOCK_POLL_SECONDS",
    "DEVICE_LEASE_SECONDS",
    "DEVICE_RISK_COOLDOWN_SECONDS",
    "DEVICE_UNAVAILABLE_COOLDOWN_SECONDS",
    "DEVICE_LOGIN_COOLDOWN_SECONDS",
)

_APP_CONFIG_KEYS: tuple[str, ...] = _OPENAPI_CONFIG_KEYS + _APP_BEHAVIOR_CONFIG_KEYS

_SECRET_KEYS = {
    "TENCENT_DOC_CLIENT_ID",
    "TENCENT_DOC_OPEN_ID",
    "TENCENT_DOC_ACCESS_TOKEN",
    "TENCENT_DOC_CLIENT_SECRET",
}

_DESCRIPTIONS: dict[str, str] = {
    "TENCENT_DOC_URL": "在线腾讯文档链接。配置后调度器会持续读取目标文档。",
    "EXCEL_DETAIL_INPUT_PATH": "本地 Excel 输入文件。用于临时跑批，执行一次 excel-detail 即结束。",
    "SINGLE_TEST_LINK": "单条测试链接。用于临时测试，执行一次 link-detail 后自动停用。",
    "TENCENT_DOC_CLIENT_ID": "腾讯文档 OpenAPI Client-Id。",
    "TENCENT_DOC_OPEN_ID": "腾讯文档 OpenAPI Open-Id，对应授权账号身份。",
    "TENCENT_DOC_ACCESS_TOKEN": "腾讯文档 OpenAPI Access-Token。可选；配置后优先使用。",
    "TENCENT_DOC_CLIENT_SECRET": "腾讯文档 OpenAPI Client-Secret。未配置 Access-Token 时用于换 token。",
    "TENCENT_DOC_TOKEN_URL": "腾讯文档 OpenAPI token 换取地址。",
}

_DISPLAY_LABELS: dict[str, str] = {
    "TENCENT_DOC_URL": "在线文档链接",
    "EXCEL_DETAIL_INPUT_PATH": "本地 Excel 路径",
    "SINGLE_TEST_LINK": "单条测试链接",
    "TENCENT_DOC_CLIENT_ID": "Client-Id",
    "TENCENT_DOC_OPEN_ID": "Open-Id",
    "TENCENT_DOC_ACCESS_TOKEN": "Access-Token",
    "TENCENT_DOC_CLIENT_SECRET": "Client-Secret",
    "TENCENT_DOC_TOKEN_URL": "Token URL",
}

_DESCRIPTIONS.update(
    {
        "APP_OPEN_RECOVERY_RETRIES": "Retry count for transient blank/update/stuck app pages after force-stopping the target app.",
        "APP_RESTART_WAIT": "Seconds to wait after force-stopping the target app before reopening the link.",
        "POST_DELAY_MIN": "Minimum human pacing delay between v2 non-detail crawl tasks.",
        "POST_DELAY_MAX": "Maximum human pacing delay between v2 non-detail crawl tasks.",
        "READ_COUNT_POST_DELAY_MIN": "Minimum human pacing delay between v2 read-count crawl tasks.",
        "READ_COUNT_POST_DELAY_MAX": "Maximum human pacing delay between v2 read-count crawl tasks.",
        "DETAIL_POST_DELAY_MIN": "Minimum human pacing delay between v2 detail crawl tasks.",
        "DETAIL_POST_DELAY_MAX": "Maximum human pacing delay between v2 detail crawl tasks.",
        "DETAIL_BLANK_REOPEN_WAIT": "Seconds to wait after recovering a blank detail page before reopening.",
        "DETAIL_BLANK_REOPEN_RETRIES": "Retry count for blank detail pages after app restart and reopen.",
        "DETAIL_SCROLL_WAIT": "Seconds to wait after scroll actions while capturing detail pages.",
        "PAGE_LOAD_WAIT": "Seconds to wait after opening an App link before reading UI content.",
        "PAGE_STATUS_READY_TIMEOUT": "Max seconds to keep recapturing when the opened page exposes too few UI controls.",
        "PAGE_STATUS_READY_INTERVAL": "Seconds between page-status recaptures while the app page is still rendering.",
        "DOC_LINK_READS_RETRYABLE_COOLDOWN_SECONDS": "Seconds to pause after a read-count page shows a retryable app/network/rate-limit state.",
        "WECHAT_DEVICE_SERIAL": "ADB serial used by the scheduled WeChat group capture pipeline.",
        "WECHAT_SYNC_PAGES": "Number of WeChat chat screenshots to capture per group during hourly sync.",
        "WECHAT_SYNC_OUT_DIR": "Output directory for WeChat hourly-sync screenshots.",
        "WECHAT_SYNC_LIMIT": "Max WeChat groups per hourly sync. 0 means no limit.",
        "WECHAT_SYNC_PARSE_MODE": "Message parser mode for WeChat hourly sync: ocr or model.",
        "WECHAT_SYNC_CONTEXT_SIZE": "Number of previous active messages sent as context to incremental demand intake.",
        "WECHAT_SCHEDULER_ENABLED": "Enable scheduled WeChat hourly sync jobs.",
        "WECHAT_SCHEDULER_START_TIME": "Daily HH:MM start time for scheduled WeChat sync.",
        "WECHAT_SCHEDULER_END_TIME": "Daily HH:MM end time for scheduled WeChat sync, inclusive.",
        "WECHAT_SCHEDULER_INTERVAL_MINUTES": "Minutes between scheduled WeChat sync runs inside the time window.",
        "WECHAT_SCHEDULER_WORKDAYS": "ISO weekdays for scheduled WeChat sync, comma-separated. 1=Monday, 7=Sunday.",
        "DEVICE_LOCK_WAIT_SECONDS": "Max seconds an ADB task waits for the per-device global lock before failing.",
        "DEVICE_LOCK_POLL_SECONDS": "Seconds between ADB device-lock availability checks.",
        "ANTFORTUNE_READ_COUNT_WARMUP_ENABLED": "Enable Ant Fortune read-count warmup actions for read-count pages.",
        "ANTFORTUNE_READ_COUNT_WARMUP_BEFORE_OPEN": "Run Ant Fortune warmup before every read-count post open. Keep false for faster normal crawls.",
        "ANTFORTUNE_READ_COUNT_RECOVER_ON_RETRYABLE": "Restart Ant Fortune, warm up, and reopen the post when a read-count page shows a retryable app/network/rate-limit state.",
        "ANTFORTUNE_READ_COUNT_WARMUP_WAIT_SECONDS": "Seconds to wait after opening Ant Fortune home before the warmup swipe.",
        "ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT": "Number of full-screen swipes to run on Ant Fortune before opening a read-count post link.",
        "ANTFORTUNE_READ_COUNT_WARMUP_AFTER_SWIPE_SECONDS": "Seconds to wait after each Ant Fortune warmup swipe.",
        "ENABLE_LEGACY_SCHEDULER_JOBS": "Enable legacy fetch/check/detail scheduler jobs. Keep false for the v2 trigger-worker route.",
        "DETAIL_INTERVAL_MINUTES": "Minutes between due detail-crawl queue scans. Each scan consumes tasks with scheduled_at <= now.",
        "SUBMIT_WORKER_INTERVAL_SECONDS": "Seconds between v2 document-trigger scans. Each trigger config still controls its own scan_interval_seconds.",
        "V2_CRAWL_WORKER_INTERVAL_SECONDS": "Seconds between v2 pending crawl scans for initial_check, detail, and read_count task queues.",
        "V2_WRITEBACK_WORKER_INTERVAL_SECONDS": "Seconds between v2 pending writeback scans.",
        "TASK_RUNNING_TIMEOUT_MINUTES": "Minutes before a running task is considered abandoned and returned to retry/final state.",
        "TENCENT_DOC_REPORT_SHEET_TITLE": "Tencent Docs sheet title used for structured daily report writeback.",
        "PROFILE_METRICS_CRAWL_LIMIT": "Max KOL homepage fan crawl rows per run. 0 means no limit.",
        "KOL_DAILY_CRAWL_TIME": "Daily HH:MM time to scan today's KOL rows and crawl fans/read counts. Empty disables the job.",
        "KOL_DAILY_CRAWL_LIMIT": "Max KOL daily crawl rows per run. 0 means no limit.",
        "KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS": "Number of recent completed dates to update for KOL Tenpay external reads. 5 means T-1 through T-5.",
    }
)
_DISPLAY_LABELS.update(
    {
        "APP_OPEN_RECOVERY_RETRIES": "App recovery retries",
        "APP_RESTART_WAIT": "App restart wait",
        "POST_DELAY_MIN": "Post delay min",
        "POST_DELAY_MAX": "Post delay max",
        "READ_COUNT_POST_DELAY_MIN": "Read-count delay min",
        "READ_COUNT_POST_DELAY_MAX": "Read-count delay max",
        "DETAIL_POST_DELAY_MIN": "Detail delay min",
        "DETAIL_POST_DELAY_MAX": "Detail delay max",
        "DETAIL_BLANK_REOPEN_WAIT": "Blank reopen wait",
        "DETAIL_BLANK_REOPEN_RETRIES": "Blank reopen retries",
        "DETAIL_SCROLL_WAIT": "Detail scroll wait",
        "PAGE_LOAD_WAIT": "Page load wait",
        "PAGE_STATUS_READY_TIMEOUT": "Page ready timeout",
        "PAGE_STATUS_READY_INTERVAL": "Page ready interval",
        "DOC_LINK_READS_RETRYABLE_COOLDOWN_SECONDS": "Doc read cooldown",
        "WECHAT_DEVICE_SERIAL": "WeChat device serial",
        "WECHAT_SYNC_PAGES": "WeChat pages",
        "WECHAT_SYNC_OUT_DIR": "WeChat output dir",
        "WECHAT_SYNC_LIMIT": "WeChat group limit",
        "WECHAT_SYNC_PARSE_MODE": "WeChat parse mode",
        "WECHAT_SYNC_CONTEXT_SIZE": "WeChat context size",
        "WECHAT_SCHEDULER_ENABLED": "WeChat scheduler",
        "WECHAT_SCHEDULER_START_TIME": "WeChat start time",
        "WECHAT_SCHEDULER_END_TIME": "WeChat end time",
        "WECHAT_SCHEDULER_INTERVAL_MINUTES": "WeChat interval",
        "WECHAT_SCHEDULER_WORKDAYS": "WeChat workdays",
        "DEVICE_LOCK_WAIT_SECONDS": "Device lock wait",
        "DEVICE_LOCK_POLL_SECONDS": "Device lock poll",
        "ANTFORTUNE_READ_COUNT_WARMUP_ENABLED": "Ant read warmup",
        "ANTFORTUNE_READ_COUNT_WARMUP_BEFORE_OPEN": "Ant warmup before open",
        "ANTFORTUNE_READ_COUNT_RECOVER_ON_RETRYABLE": "Ant retry recovery",
        "ANTFORTUNE_READ_COUNT_WARMUP_WAIT_SECONDS": "Ant warmup wait",
        "ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT": "Ant warmup swipes",
        "ANTFORTUNE_READ_COUNT_WARMUP_AFTER_SWIPE_SECONDS": "Ant warmup after swipe",
        "ENABLE_LEGACY_SCHEDULER_JOBS": "Legacy scheduler jobs",
        "DETAIL_INTERVAL_MINUTES": "Detail queue interval",
        "SUBMIT_WORKER_INTERVAL_SECONDS": "Submit worker interval",
        "V2_CRAWL_WORKER_INTERVAL_SECONDS": "V2 crawl worker interval",
        "V2_WRITEBACK_WORKER_INTERVAL_SECONDS": "V2 writeback interval",
        "TASK_RUNNING_TIMEOUT_MINUTES": "Running task timeout",
        "TENCENT_DOC_REPORT_SHEET_TITLE": "Report sheet title",
        "PROFILE_METRICS_CRAWL_LIMIT": "Profile crawl limit",
        "KOL_DAILY_CRAWL_TIME": "KOL crawl time",
        "KOL_DAILY_CRAWL_LIMIT": "KOL crawl limit",
        "KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS": "Tenpay reads lookback days",
    }
)


def ensure_runtime_config_defaults(cursor) -> None:
    _ensure_data_source_defaults(cursor)
    _ensure_app_config_defaults(cursor)


def _ensure_data_source_defaults(cursor) -> None:
    rows = [
        (
            "TENCENT_DOC_URL",
            Config.QQ_DOC_URL,
            "active" if Config.QQ_DOC_URL else "unavailable",
            _DESCRIPTIONS["TENCENT_DOC_URL"],
        ),
        (
            "EXCEL_DETAIL_INPUT_PATH",
            Config.EXCEL_DETAIL_INPUT_PATH,
            "active" if Config.EXCEL_DETAIL_INPUT_PATH else "unavailable",
            _DESCRIPTIONS["EXCEL_DETAIL_INPUT_PATH"],
        ),
        ("SINGLE_TEST_LINK", "", "unavailable", _DESCRIPTIONS["SINGLE_TEST_LINK"]),
        (
            "ARTICLE_DETAILS_DOC_URL",
            Config.ARTICLE_DETAILS_DOC_URL,
            "active" if Config.ARTICLE_DETAILS_DOC_URL else "unavailable",
            "Tencent Docs sheet URL used by the demand-1 article detail workflow.",
        ),
    ]
    cursor.executemany(
        """
        INSERT INTO data_source_links (source_key, data_source_link, status, description, updated_by)
        VALUES (%s, %s, %s, %s, 'system')
        ON DUPLICATE KEY UPDATE
            description = VALUES(description)
        """,
        rows,
    )
    cursor.execute(
        """
        UPDATE data_source_links
        SET status = 'unavailable'
        WHERE source_key IN ('EXCEL_DETAIL_INPUT_PATH', 'SINGLE_TEST_LINK', 'ARTICLE_DETAILS_DOC_URL')
          AND (data_source_link IS NULL OR data_source_link = '')
        """
    )


def _ensure_app_config_defaults(cursor) -> None:
    rows = []
    for key in _APP_CONFIG_KEYS:
        default = _default_value_for_key(key)
        if key == "TENCENT_DOC_TOKEN_URL" and not default:
            default = "https://docs.qq.com/oauth/v2/token"
        rows.append(
            (
                key,
                default,
                "active" if default else "unavailable",
                1 if key in _SECRET_KEYS else 0,
                _DESCRIPTIONS.get(key, ""),
            )
        )
    cursor.executemany(
        """
        INSERT INTO app_config (config_key, config_value, status, is_secret, description, updated_by)
        VALUES (%s, %s, %s, %s, %s, 'system')
        ON DUPLICATE KEY UPDATE
            is_secret = VALUES(is_secret),
            description = VALUES(description)
        """,
        rows,
    )


def load_runtime_config() -> dict[str, str]:
    values = _load_data_source_values()
    values.update(_derive_tencent_doc_keys(values))
    values.update(_load_app_config_values())
    apply_runtime_config(values)
    if values:
        logger.info("runtime config loaded: %s", ", ".join(sorted(values)))
    return values


def _load_data_source_values() -> dict[str, str]:
    from apps.finance_crawler.storage.db import get_conn

    placeholders = ", ".join(["%s"] * len(_DATA_SOURCE_KEYS))
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT source_key, data_source_link
                FROM data_source_links
                WHERE status = 'active'
                  AND source_key IN ({placeholders})
                """,
                _DATA_SOURCE_KEYS,
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    return {
        str(row["source_key"]): "" if row["data_source_link"] is None else str(row["data_source_link"])
        for row in rows
    }


def _load_app_config_values() -> dict[str, str]:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT config_key, config_value
                FROM {_APP_CONFIG_TABLE}
                WHERE status = 'active'
                  AND config_key IN ({", ".join(["%s"] * len(_APP_CONFIG_KEYS))})
                  AND config_value IS NOT NULL
                  AND config_value <> ''
                """,
                _APP_CONFIG_KEYS,
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    return {str(row["config_key"]): str(row["config_value"]) for row in rows}


def apply_runtime_config(values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in _CONFIG_ATTRS:
            continue
        _apply_value(key, value)

    if values.get("TENCENT_DOC_URL"):
        _apply_tencent_doc_url(values["TENCENT_DOC_URL"])


def list_runtime_config() -> list[RuntimeConfigItem]:
    return _list_data_source_config() + _list_app_config()


def _list_data_source_config() -> list[RuntimeConfigItem]:
    from apps.finance_crawler.storage.db import get_conn

    placeholders = ", ".join(["%s"] * len(_DATA_SOURCE_KEYS))
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT source_key, data_source_link, status, description
                FROM data_source_links
                WHERE source_key IN ({placeholders})
                ORDER BY source_key
                """,
                _DATA_SOURCE_KEYS,
            )
            return [
                RuntimeConfigItem(
                    key=str(row["source_key"]),
                    value="" if row["data_source_link"] is None else str(row["data_source_link"]),
                    enabled=str(row["status"]) == "active",
                    status=str(row["status"] or ""),
                    description=str(row.get("description") or ""),
                )
                for row in cursor.fetchall()
            ]
    finally:
        conn.close()


def _list_app_config() -> list[RuntimeConfigItem]:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT config_key, config_value, status, description, is_secret
                FROM {_APP_CONFIG_TABLE}
                WHERE config_key IN ({", ".join(["%s"] * len(_APP_CONFIG_KEYS))})
                ORDER BY config_key
                """,
                _APP_CONFIG_KEYS,
            )
            return [
                RuntimeConfigItem(
                    key=str(row["config_key"]),
                    value="" if row["config_value"] is None else str(row["config_value"]),
                    enabled=str(row["status"]) == "active",
                    status=str(row["status"] or ""),
                    description=str(row.get("description") or ""),
                    secret=bool(row.get("is_secret")),
                )
                for row in cursor.fetchall()
            ]
    finally:
        conn.close()


def grouped_runtime_config() -> list[RuntimeConfigDisplayGroup]:
    values = {item.key: item for item in list_runtime_config()}
    return [
        RuntimeConfigDisplayGroup(
            title="任务源配置",
            description="数据从哪里来。",
            items=tuple(_display_items(values, _DATA_SOURCE_KEYS)),
        ),
        RuntimeConfigDisplayGroup(
            title="腾讯文档 OpenAPI",
            description="读写腾讯文档使用的 OpenAPI 身份；MySQL 连接不放这里。",
            items=tuple(_display_items(values, _OPENAPI_CONFIG_KEYS)),
        ),
        RuntimeConfigDisplayGroup(
            title="App 采集和调度保护",
            description="手机 App 白屏、系统更新弹窗、卡死时的自动恢复策略，以及到期详情任务轮询间隔。",
            items=tuple(_display_items(values, _APP_BEHAVIOR_CONFIG_KEYS)),
        ),
    ]


def _display_items(values: dict[str, RuntimeConfigItem], keys: tuple[str, ...]) -> list[RuntimeConfigDisplayItem]:
    items = []
    for key in keys:
        source = values.get(key)
        raw_value = source.value if source else _default_value_for_key(key)
        items.append(
            RuntimeConfigDisplayItem(
                key=key,
                label=_DISPLAY_LABELS.get(key, key),
                value=_format_display_value(key, raw_value, secret=bool(source.secret if source else key in _SECRET_KEYS)),
                description=(source.description if source else "") or _DESCRIPTIONS.get(key, ""),
                enabled=source.enabled if source else bool(raw_value),
                status=source.status if source else ("active" if raw_value else "unavailable"),
                secret=bool(source.secret if source else key in _SECRET_KEYS),
            )
        )
    return items


def format_runtime_config_for_cli() -> str:
    lines = [
        "运行时配置",
        "MySQL 连接只从项目根目录 .env / 环境变量读取；其它运行配置从 MySQL 配置表读取。",
    ]
    for group in grouped_runtime_config():
        lines.append("")
        lines.append(f"[{group.title}]")
        lines.append(group.description)
        for item in group.items:
            state = "" if item.enabled else "（未启用）"
            lines.append(f"  {item.label}: {item.value}{state}")
    return "\n".join(lines)


def set_runtime_config(values: dict[str, str], *, updated_by: str = "cli") -> None:
    source_values: dict[str, str] = {}
    app_values: dict[str, str] = {}
    for key, value in values.items():
        if key in _DATA_SOURCE_KEYS:
            source_values[key] = value
            continue
        if key in _APP_CONFIG_KEYS:
            app_values[key] = value
            continue
        raise ValueError(
            f"unsupported config key: {key}; source keys: {', '.join(_DATA_SOURCE_KEYS)}; "
            f"app keys: {', '.join(_APP_CONFIG_KEYS)}"
        )

    if source_values:
        _set_data_source_config(source_values, updated_by=updated_by)
    if app_values:
        _set_app_config(app_values, updated_by=updated_by)

    apply_runtime_config(values | _derive_tencent_doc_keys(values))


def _set_data_source_config(values: dict[str, str], *, updated_by: str) -> None:
    from apps.finance_crawler.storage.db import get_conn

    rows = []
    for key, value in values.items():
        if key in {
            "TENCENT_DOC_URL",
            "KOL_TENPAY_EXTERNAL_READS_TARGET_DOC_URL",
        } and value:
            parse_doc_url(value)
        rows.append((key, value, _DESCRIPTIONS.get(key, ""), updated_by))

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO data_source_links (source_key, data_source_link, status, description, updated_by)
                VALUES (%s, %s, 'active', %s, %s)
                ON DUPLICATE KEY UPDATE
                    data_source_link = VALUES(data_source_link),
                    status = 'active',
                    description = COALESCE(NULLIF(VALUES(description), ''), description),
                    updated_by = VALUES(updated_by)
                """,
                rows,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _set_app_config(values: dict[str, str], *, updated_by: str) -> None:
    from apps.finance_crawler.storage.db import get_conn

    rows = [
        (
            key,
            value,
            "active" if value.strip() else "unavailable",
            1 if key in _SECRET_KEYS else 0,
            _DESCRIPTIONS.get(key, ""),
            updated_by,
        )
        for key, value in values.items()
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(
                f"""
                INSERT INTO {_APP_CONFIG_TABLE} (config_key, config_value, status, is_secret, description, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    config_value = VALUES(config_value),
                    status = VALUES(status),
                    is_secret = VALUES(is_secret),
                    description = COALESCE(NULLIF(VALUES(description), ''), description),
                    updated_by = VALUES(updated_by)
                """,
                rows,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_data_source_link(source_key: str, *, require_enabled: bool = True) -> RuntimeConfigItem | None:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            enabled_clause = "AND status = 'active'" if require_enabled else ""
            cursor.execute(
                f"""
                SELECT source_key, data_source_link, status, description
                FROM data_source_links
                WHERE source_key = %s
                  {enabled_clause}
                LIMIT 1
                """,
                (source_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return RuntimeConfigItem(
                key=str(row["source_key"]),
                value="" if row["data_source_link"] is None else str(row["data_source_link"]),
                enabled=str(row["status"]) == "active",
                status=str(row["status"] or ""),
                description=str(row.get("description") or ""),
            )
    finally:
        conn.close()


def disable_data_source(source_key: str, *, updated_by: str = "system") -> None:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE data_source_links
                SET status = 'unavailable',
                    updated_by = %s
                WHERE source_key = %s
                """,
                (updated_by, source_key),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _apply_value(key: str, value: str) -> None:
    attr = _CONFIG_ATTRS[key]
    current = getattr(Config, attr)
    converted = _convert_value(value, current)
    setattr(Config, attr, converted)
    os.environ[key] = value


def _apply_tencent_doc_url(value: str) -> None:
    try:
        doc = parse_doc_url(value)
    except Exception as exc:
        logger.warning("runtime TENCENT_DOC_URL is not a Tencent Docs sheet URL: %s", exc)
        return
    Config.QQ_DOC_URL = value
    Config.QQ_FILE_ID = doc.file_id
    Config.QQ_SHEET_ID = doc.sheet_id
    os.environ["TENCENT_DOC_FILE_ID"] = doc.file_id
    os.environ["TENCENT_DOC_SHEET_ID"] = doc.sheet_id


def _derive_tencent_doc_keys(values: dict[str, str]) -> dict[str, str]:
    url = values.get("TENCENT_DOC_URL", "").strip()
    if not url:
        return {}
    try:
        doc = parse_doc_url(url)
    except Exception as exc:
        logger.warning("runtime TENCENT_DOC_URL is not a Tencent Docs sheet URL: %s", exc)
        return {}
    return {
        "TENCENT_DOC_FILE_ID": doc.file_id,
        "TENCENT_DOC_SHEET_ID": doc.sheet_id,
    }


def _convert_value(value: str, current: Any) -> Any:
    if isinstance(current, bool):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value) if value.strip() else 0
    if isinstance(current, float):
        return float(value) if value.strip() else 0.0
    return value


def _default_value_for_key(key: str) -> str:
    attr = _CONFIG_ATTRS.get(key)
    if not attr:
        return ""
    return str(getattr(Config, attr, ""))


def _format_display_value(key: str, value: str, *, secret: bool = False) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return "未配置"
    if secret:
        if len(cleaned) <= 8:
            return "***"
        return f"{cleaned[:4]}***{cleaned[-4:]}"
    return cleaned
