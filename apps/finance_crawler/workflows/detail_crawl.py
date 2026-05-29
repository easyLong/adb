"""Detail crawl workflow: scrape eligible records and write business results."""

from __future__ import annotations

import random
import time

from apps.finance_crawler.mobile.crawler import (
    open_url,
    reset_device_session,
    resolve_short_url,
    scrape_record_content,
)
from apps.finance_crawler.config import Config
from apps.finance_crawler.services.alerts import send_alert
from apps.finance_crawler.services.report import generate_report
from apps.finance_crawler.services.writeback import WritebackPlan, default_writeback_service
from apps.finance_crawler.storage.crawl_repository import (
    PendingWriteback,
    get_pending_detail_records,
    record_crawl_result,
    record_pending_writebacks,
    record_sink_writeback,
)
from apps.finance_crawler.storage.db import (
    log_task,
)
from apps.finance_crawler.storage.framework_db import (
    finish_task_execution,
    start_task_execution,
    update_task_execution_writeback,
)
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.link_source import resolve_source_app
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.utils.rate_limiter import OperationBudget, TaskBudgetExceeded
from apps.finance_crawler.utils.record_identity import workflow_record_id, workflow_record_url
from apps.finance_crawler.utils.url_resolver import resolve_urls

logger = get_logger("detail_crawl_workflow")


def _empty_result(status: str = "error", error: str | None = None) -> dict:
    return {
        "status": status,
        "content": None,
        "read_count": 0,
        "comment_count": 0,
        "screenshot_path": None,
        "error": error,
    }


def run_detail_crawl(limit: int | None = None) -> list[dict]:
    start_time = time.time()
    budget = OperationBudget("detail_crawl")
    writeback_service = default_writeback_service()
    if limit is not None:
        logger.info("detail crawl count limit ignored: %s", limit)
    records = get_pending_detail_records()
    records = budget.limit_items(records)
    total = len(records)
    logger.info("detail crawl started: total=%s", total)

    if not records:
        log_task("detail_crawl", "success", "no pending records", time.time() - start_time)
        return []

    budget.check()

    try:
        assert_device_ready()
    except DeviceUnavailable as exc:
        reset_device_session()
        send_alert("ADB device unavailable", str(exc), dedupe_key="device_unavailable")
        log_task("detail_crawl", "error", str(exc), time.time() - start_time)
        raise

    writeback_service.load_snapshot(
        alert=lambda error, dedupe_key: send_alert(
            "Tencent Docs row snapshot failed",
            error,
            level="warning",
            dedupe_key=dedupe_key or "docs_snapshot",
        ),
        warning_dedupe_key="docs_snapshot",
    )

    deep_links = resolve_urls(records, resolve_short_url, logger)
    results: list[dict] = []
    stop_reason: str | None = None

    for idx, record in enumerate(records, start=1):
        try:
            budget.check()
        except TaskBudgetExceeded as exc:
            stop_reason = str(exc)
            logger.warning("detail crawl stopped by runtime budget: %s", stop_reason)
            break

        record_id = workflow_record_id(record)
        url = workflow_record_url(record)
        source_app = resolve_source_app(record.get("source_app"), url)
        execution_id = _start_execution_for_record(record)
        if execution_id is None:
            logger.info("detail crawl skipped by task submission state id=%s", record_id)
            continue
        logger.info("[%s/%s] scrape source=%s id=%s", idx, total, source_app, record_id)
        item_start = time.perf_counter()
        open_duration = 0.0

        try:
            open_start = time.perf_counter()
            open_url(deep_links.get(record_id, url))
            open_duration = time.perf_counter() - open_start
            result = scrape_record_content(record_id, source_app=source_app)
        except DeviceUnavailable as exc:
            reset_device_session()
            _finish_execution(
                execution_id,
                result=_empty_result("error", str(exc)),
                metrics={
                    "read_count": 0,
                    "comment_count": 0,
                    "open_duration": round(open_duration, 3),
                    "scrape_duration": round(time.perf_counter() - item_start, 3),
                },
                writeback_status="skipped",
                writeback_error=str(exc),
            )
            raise
        except Exception as exc:
            result = _empty_result("error", str(exc))
            logger.exception("scrape failed id=%s", record_id)

        budget.record_status(result["status"])
        logger.info(
            "detail crawl timing id=%s status=%s open=%.2fs scrape_total=%.2fs pages=%s ocr=%s",
            record_id,
            result["status"],
            open_duration,
            time.perf_counter() - item_start,
            result.get("capture_pages"),
            result.get("ocr_attempted"),
        )
        scrape_duration = time.perf_counter() - item_start

        writeback_plan = writeback_service.prepare_detail(record=record, result=result)
        row_index = writeback_plan.row_index

        metrics = {
            "read_count": int(result.get("read_count") or 0),
            "comment_count": int(result.get("comment_count") or 0),
            "row_index": row_index,
            "capture_pages": result.get("capture_pages"),
            "ocr_attempted": result.get("ocr_attempted"),
            "open_duration": round(open_duration, 3),
            "scrape_duration": round(scrape_duration, 3),
            **(result.get("app_metrics") or {}),
        }

        task_id, result_id = record_crawl_result(
            record=record,
            workflow="detail_crawl",
            status=result["status"],
            account_name=result.get("account_name"),
            content=result.get("content"),
            screenshot_path=result.get("screenshot_path"),
            metrics=metrics,
            error=result.get("error"),
        )

        if writeback_plan.can_write and row_index:
            writeback_status, writeback_error = _write_single_detail_result(
                writeback_service=writeback_service,
                writeback_plan=writeback_plan,
                pending_writeback=PendingWriteback(
                    record_id=record_id,
                    task_id=task_id,
                    result_id=result_id,
                    row_index=row_index,
                ),
            )
        else:
            logger.warning("skipped %s writeback id=%s: %s", writeback_plan.sink_type, record_id, writeback_plan.skip_reason)
            record_sink_writeback(
                record_id=record_id,
                sink_type=writeback_plan.sink_type,
                status="skipped",
                task_id=task_id,
                result_id=result_id,
                error=writeback_plan.skip_reason,
            )
            writeback_status = "skipped"
            writeback_error = writeback_plan.skip_reason

        _finish_execution(
            execution_id,
            result=result,
            metrics=metrics,
            writeback_status=writeback_status,
            writeback_locator=writeback_plan.locator,
            writeback_error=writeback_error,
        )
        if writeback_status == "error":
            _update_execution_writeback(
                execution_id,
                writeback_status="error",
                writeback_locator=writeback_plan.locator,
                writeback_error=writeback_error,
            )

        result_with_record = dict(result)
        result_with_record.update(
            {"record_id": record_id, "url": url, "source_app": source_app, "row_index": row_index}
        )
        results.append(result_with_record)
        time.sleep(
            random.uniform(
                max(Config.DETAIL_POST_DELAY_MIN, 0),
                max(Config.DETAIL_POST_DELAY_MAX, Config.DETAIL_POST_DELAY_MIN),
            )
        )

    success_count = sum(1 for item in results if item["status"] == "success")
    deleted_count = sum(1 for item in results if item["status"] == "deleted")
    error_count = sum(1 for item in results if item["status"] == "error")
    duration = time.time() - start_time
    msg = (
        f"total={total}, success={success_count}, deleted={deleted_count}, "
        f"error={error_count}, duration={duration:.1f}s"
    )
    if stop_reason:
        msg = f"{msg}, stopped={stop_reason}"
    logger.info("detail crawl finished: %s", msg)
    log_task("detail_crawl", "success", msg, duration)

    if error_count:
        send_alert("Detail crawl has errors", msg, level="warning", dedupe_key="detail_crawl_errors")
    if stop_reason:
        send_alert("Detail crawl stopped by budget", stop_reason, level="warning", dedupe_key="detail_crawl_budget")

    try:
        generate_report()
    except Exception as exc:
        send_alert("Report generation failed", str(exc), level="warning", dedupe_key="report_failed")
        logger.warning("report generation failed: %s", exc)

    return results


def _write_single_detail_result(
    *,
    writeback_service,
    writeback_plan: WritebackPlan,
    pending_writeback: PendingWriteback,
) -> tuple[str, str | None]:
    try:
        writeback_service.write_detail_results([writeback_plan])
        record_pending_writebacks(
            records=[pending_writeback],
            sink_type=writeback_service.sink_type,
            status="success",
        )
        return "success", None
    except Exception as exc:
        error = str(exc)
        logger.warning("detail writeback failed row=%s: %s", pending_writeback.row_index, error)
        record_pending_writebacks(
            records=[pending_writeback],
            sink_type=writeback_service.sink_type,
            status="error",
            error=error,
        )
        send_alert(
            "Tencent Docs detail writeback failed",
            error,
            level="warning",
            dedupe_key="docs_detail_writeback",
        )
        return "error", error


def _start_execution_for_record(record: dict) -> int | None:
    submission_id = record.get("submission_id")
    if not submission_id:
        logger.warning("detail crawl record has no submission_id id=%s", workflow_record_id(record))
        return None
    try:
        return start_task_execution(submission_id, worker_id="detail_crawl")
    except ValueError as exc:
        logger.warning("task submission not runnable record_id=%s: %s", workflow_record_id(record), exc)
        return None


def _finish_execution(
    execution_id: int,
    *,
    result: dict,
    metrics: dict,
    writeback_status: str | None = None,
    writeback_locator: dict | None = None,
    writeback_error: str | None = None,
) -> None:
    try:
        finish_task_execution(
            execution_id,
            status=result.get("status") or "error",
            account_name=result.get("account_name"),
            content=result.get("content"),
            metrics=metrics,
            result=result,
            screenshot_path=result.get("screenshot_path"),
            writeback_status=writeback_status,
            writeback_locator=writeback_locator,
            writeback_error=writeback_error,
            error=result.get("error"),
        )
    except Exception as exc:
        logger.warning("failed to finish task execution id=%s: %s", execution_id, exc)


def _update_execution_writeback(
    execution_id: int,
    *,
    writeback_status: str,
    writeback_locator: dict | None = None,
    writeback_error: str | None = None,
) -> None:
    try:
        update_task_execution_writeback(
            execution_id,
            writeback_status=writeback_status,
            writeback_locator=writeback_locator,
            writeback_error=writeback_error,
        )
    except Exception as exc:
        logger.warning("failed to update task execution writeback id=%s: %s", execution_id, exc)
