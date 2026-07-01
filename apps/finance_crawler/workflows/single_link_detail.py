"""One-shot detail crawl workflow for a single configured link."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Any

from apps.finance_crawler.domain.records import CrawlResult
from apps.finance_crawler.domain.task_types import DETAIL_CRAWL_TASK_TYPE
from apps.finance_crawler.mobile.crawler import (
    open_url,
    reset_device_session,
    resolve_short_url,
    scrape_record_content,
)
from apps.finance_crawler.services.runtime_config import (
    disable_data_source,
    get_data_source_link,
)
from apps.finance_crawler.storage.framework_db import (
    finish_task_execution,
    insert_crawl_result,
    start_task_execution,
    upsert_task_submission,
)
from apps.finance_crawler.storage.device_pool import acquire_device
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.link_source import resolve_source_app
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("single_link_detail")

SINGLE_TEST_LINK_KEY = "SINGLE_TEST_LINK"


def run_single_link_detail(url: str | None = None) -> dict[str, Any] | None:
    """Crawl one test link and then disable the one-shot data source."""

    source_item = None
    link = (url or "").strip()
    if not link:
        source_item = get_data_source_link(SINGLE_TEST_LINK_KEY)
        link = (source_item.value if source_item else "").strip()
    if not link:
        logger.info("single link detail skipped: no enabled link")
        return None

    started = time.perf_counter()
    source_app = resolve_source_app(None, link)
    run_token = datetime.now().strftime("%Y%m%d%H%M%S%f")
    record_id = int(datetime.now().strftime("%m%d%H%M%S"))
    submission_id = _upsert_submission(link, source_app=source_app, run_token=run_token)
    execution_id = start_task_execution(submission_id, worker_id="single_link_detail")
    result: dict[str, Any]
    opened_url = link

    try:
        with acquire_device(
            app_type=source_app,
            task_scope="single_link:detail",
            task_id=submission_id,
            worker_id="single_link_detail",
        ):
            assert_device_ready()
            opened_url = resolve_short_url(link)
            open_url(opened_url)
            result = scrape_record_content(record_id, source_app=source_app)
    except DeviceUnavailable as exc:
        reset_device_session()
        result = _error_result(str(exc))
    except Exception as exc:
        logger.exception("single link detail failed")
        result = _error_result(str(exc))
    finally:
        disable_data_source(SINGLE_TEST_LINK_KEY, updated_by="single_link_detail")

    duration = round(time.perf_counter() - started, 2)
    metrics = {
        "workflow": "single_link_detail",
        "read_count": int(result.get("read_count") or 0),
        "comment_count": int(result.get("comment_count") or 0),
        "like_count": int(result.get("like_count") or 0),
        "duration": duration,
        "capture_pages": result.get("capture_pages"),
        "ocr_attempted": result.get("ocr_attempted"),
        "opened_url": opened_url,
    }
    finish_task_execution(
        execution_id,
        status=result.get("status") or "error",
        account_name=result.get("account_name"),
        content=result.get("content"),
        metrics=metrics,
        result=result,
        screenshot_path=result.get("screenshot_path"),
        writeback_status="skipped",
        writeback_locator={"source_key": SINGLE_TEST_LINK_KEY},
        writeback_error="single link has no writeback sink",
        error=result.get("error"),
    )
    result_id = insert_crawl_result(
        CrawlResult(
            task_id=submission_id,
            url=link,
            app_type=source_app,
            status=result.get("status") or "error",
            account_name=result.get("account_name"),
            content=result.get("content"),
            metrics=metrics,
            screenshot_path=result.get("screenshot_path"),
            error=result.get("error"),
        )
    )
    payload = {
        "source_key": SINGLE_TEST_LINK_KEY,
        "submission_id": submission_id,
        "execution_id": execution_id,
        "result_id": result_id,
        "url": link,
        "opened_url": opened_url,
        "source_app": source_app,
        "status": result.get("status") or "error",
        "account_name": result.get("account_name"),
        "read_count": int(result.get("read_count") or 0),
        "comment_count": int(result.get("comment_count") or 0),
        "like_count": int(result.get("like_count") or 0),
        "duration": duration,
        "error": result.get("error"),
    }
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return payload


def _upsert_submission(url: str, *, source_app: str, run_token: str) -> int:
    object_key = _object_key(url, run_token)
    return upsert_task_submission(
        task_type=DETAIL_CRAWL_TASK_TYPE,
        source_type="single_link",
        source_name=SINGLE_TEST_LINK_KEY,
        crawl_object_key=object_key,
        source_locator={"source_key": SINGLE_TEST_LINK_KEY, "run_token": run_token},
        app_type=source_app,
        original_url=url,
        max_attempts=1,
        created_by="single_link_detail",
    )


def _object_key(url: str, run_token: str) -> str:
    raw = f"single_link\x1f{run_token}\x1f{url.strip()}"
    return f"single_link:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"


def _error_result(error: str) -> dict[str, Any]:
    return {
        "status": "error",
        "account_name": None,
        "content": None,
        "read_count": 0,
        "comment_count": 0,
        "like_count": 0,
        "screenshot_path": None,
        "error": error,
    }
