"""Batch crawl workflow: scrape eligible posts and write business results."""

from __future__ import annotations

import random
import time

from apps.finance_crawler.mobile.crawler import (
    open_url,
    reset_device_session,
    resolve_short_url,
    scrape_post_content,
)
from apps.finance_crawler.config import Config
from apps.finance_crawler.services.alerts import send_alert
from apps.finance_crawler.services.report import generate_report
from apps.finance_crawler.services.writeback import WritebackPlan, default_writeback_service
from apps.finance_crawler.storage.crawl_repository import (
    PendingWriteback,
    get_pending_batch_records,
    mark_writebacks_done,
    record_crawl_result,
    record_pending_writebacks,
    record_sink_writeback,
    save_batch_result,
)
from apps.finance_crawler.storage.db import (
    log_task,
)
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.link_source import resolve_source_app
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.utils.rate_limiter import OperationBudget, TaskBudgetExceeded
from apps.finance_crawler.utils.url_resolver import resolve_urls

logger = get_logger("batch_workflow")


def _empty_result(status: str = "error", error: str | None = None) -> dict:
    return {
        "status": status,
        "content": None,
        "read_count": 0,
        "comment_count": 0,
        "screenshot_path": None,
        "error": error,
    }


def run_batch_crawl(limit: int | None = None) -> list[dict]:
    start_time = time.time()
    budget = OperationBudget("batch")
    writeback_service = default_writeback_service()
    task_limit = Config.BATCH_LIMIT if limit is None else limit
    posts = get_pending_batch_records(task_limit)
    posts = budget.limit_items(posts)
    total = len(posts)
    logger.info("batch started: total=%s limit=%s", total, task_limit)

    if not posts:
        log_task("batch", "success", "no pending posts", time.time() - start_time)
        return []

    budget.check()

    try:
        assert_device_ready()
    except DeviceUnavailable as exc:
        reset_device_session()
        send_alert("ADB device unavailable", str(exc), dedupe_key="device_unavailable")
        log_task("batch", "error", str(exc), time.time() - start_time)
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

    deep_links = resolve_urls(posts, resolve_short_url, logger)
    results: list[dict] = []
    writebacks: list[WritebackPlan] = []
    writeback_records: list[PendingWriteback] = []
    written_post_ids: list[int] = []
    stop_reason: str | None = None

    for idx, post in enumerate(posts, start=1):
        try:
            budget.check()
        except TaskBudgetExceeded as exc:
            stop_reason = str(exc)
            logger.warning("batch stopped by runtime budget: %s", stop_reason)
            break

        post_id = post["id"]
        url = post["url"]
        source_app = resolve_source_app(post.get("source_app"), url)
        logger.info("[%s/%s] scrape source=%s id=%s", idx, total, source_app, post_id)
        item_start = time.perf_counter()
        open_duration = 0.0

        try:
            open_start = time.perf_counter()
            open_url(deep_links.get(post_id, url))
            open_duration = time.perf_counter() - open_start
            result = scrape_post_content(post_id, source_app=source_app)
        except DeviceUnavailable:
            reset_device_session()
            raise
        except Exception as exc:
            result = _empty_result("error", str(exc))
            logger.exception("scrape failed id=%s", post_id)

        save_batch_result(
            post_id=post_id,
            status=result["status"],
            content=result.get("content"),
            read_count=result.get("read_count") or 0,
            comment_count=result.get("comment_count") or 0,
            screenshot_path=result.get("screenshot_path"),
            error=result.get("error"),
        )
        budget.record_status(result["status"])
        logger.info(
            "batch timing id=%s status=%s open=%.2fs scrape_total=%.2fs pages=%s ocr=%s",
            post_id,
            result["status"],
            open_duration,
            time.perf_counter() - item_start,
            result.get("capture_pages"),
            result.get("ocr_attempted"),
        )
        scrape_duration = time.perf_counter() - item_start

        writeback_plan = writeback_service.prepare_batch(post=post, result=result)
        row_index = writeback_plan.row_index

        task_id, result_id = record_crawl_result(
            post=post,
            workflow="batch_crawl",
            status=result["status"],
            account_name=result.get("account_name"),
            content=result.get("content"),
            screenshot_path=result.get("screenshot_path"),
            metrics={
                "read_count": int(result.get("read_count") or 0),
                "comment_count": int(result.get("comment_count") or 0),
                "row_index": row_index,
                "capture_pages": result.get("capture_pages"),
                "ocr_attempted": result.get("ocr_attempted"),
                "open_duration": round(open_duration, 3),
                "scrape_duration": round(scrape_duration, 3),
                **(result.get("app_metrics") or {}),
            },
            error=result.get("error"),
        )

        if writeback_plan.can_write and row_index:
            writebacks.append(writeback_plan)
            written_post_ids.append(post_id)
            writeback_records.append(
                PendingWriteback(
                    post_id=post_id,
                    task_id=task_id,
                    result_id=result_id,
                    row_index=row_index,
                )
            )
        else:
            logger.warning("skipped %s writeback id=%s: %s", writeback_plan.sink_type, post_id, writeback_plan.skip_reason)
            record_sink_writeback(
                post_id=post_id,
                sink_type=writeback_plan.sink_type,
                status="skipped",
                task_id=task_id,
                result_id=result_id,
                error=writeback_plan.skip_reason,
            )

        result_with_post = dict(result)
        result_with_post.update(
            {"id": post_id, "url": url, "source_app": source_app, "row_index": row_index}
        )
        results.append(result_with_post)
        time.sleep(
            random.uniform(
                max(Config.BATCH_POST_DELAY_MIN, 0),
                max(Config.BATCH_POST_DELAY_MAX, Config.BATCH_POST_DELAY_MIN),
            )
        )

    if writebacks:
        try:
            writeback_service.write_batch_results(writebacks)
            mark_writebacks_done(written_post_ids)
            record_pending_writebacks(
                records=writeback_records,
                sink_type=writeback_service.sink_type,
                status="success",
            )
        except Exception as exc:
            send_alert("Tencent Docs batch writeback failed", str(exc), dedupe_key="docs_batch_writeback")
            logger.warning("batch writeback failed: %s", exc)
            record_pending_writebacks(
                records=writeback_records,
                sink_type=writeback_service.sink_type,
                status="error",
                error=str(exc),
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
    logger.info("batch finished: %s", msg)
    log_task("batch", "success", msg, duration)

    if error_count:
        send_alert("Batch crawl has errors", msg, level="warning", dedupe_key="batch_errors")
    if stop_reason:
        send_alert("Batch stopped by budget", stop_reason, level="warning", dedupe_key="batch_budget")

    try:
        generate_report()
    except Exception as exc:
        send_alert("Report generation failed", str(exc), level="warning", dedupe_key="report_failed")
        logger.warning("report generation failed: %s", exc)

    return results
