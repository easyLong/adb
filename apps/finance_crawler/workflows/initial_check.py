"""Initial check workflow: crawl existence/account and write business results."""

from __future__ import annotations

import time

from apps.finance_crawler.mobile.crawler import (
    check_post_exists_and_account,
    open_url,
    reset_device_session,
    resolve_short_url,
)
from apps.finance_crawler.services.alerts import send_alert
from apps.finance_crawler.sinks.tencent_docs import TencentDocsSink
from apps.finance_crawler.storage.crawl_repository import (
    PendingWriteback,
    get_pending_initial_check_records,
    record_crawl_result,
    record_pending_writebacks,
    record_sink_writeback,
    save_initial_check_result,
)
from apps.finance_crawler.storage.db import (
    log_task,
)
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.link_source import resolve_source_app
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.utils.rate_limiter import OperationBudget, TaskBudgetExceeded
from apps.finance_crawler.utils.url_resolver import resolve_urls

logger = get_logger("initial_check_workflow")


def _status_counts(results: list[dict]) -> tuple[int, int, int]:
    success = sum(1 for item in results if item["status"] == "success")
    not_found = sum(1 for item in results if item["status"] == "not_found")
    error = sum(1 for item in results if item["status"] == "error")
    return success, not_found, error


def run_initial_check() -> list[dict]:
    start_time = time.time()
    budget = OperationBudget("check")
    sink = TencentDocsSink()
    posts = budget.limit_items(get_pending_initial_check_records())
    total = len(posts)
    logger.info("initial check started: total=%s", total)

    if not posts:
        log_task("check", "success", "no pending posts", 0)
        return []

    budget.check()

    try:
        assert_device_ready()
    except DeviceUnavailable as exc:
        reset_device_session()
        send_alert("ADB device unavailable", str(exc), dedupe_key="device_unavailable")
        log_task("check", "error", str(exc), time.time() - start_time)
        raise

    try:
        sheet_rows, start_row = sink.fetch_grid()
    except Exception as exc:
        logger.warning("failed to load Tencent Docs row snapshot; writeback will be skipped: %s", exc)
        sheet_rows, start_row = [], 0

    deep_links = resolve_urls(posts, resolve_short_url, logger)
    results: list[dict] = []
    writebacks: list[dict] = []
    writeback_records: list[PendingWriteback] = []
    stop_reason: str | None = None

    for idx, post in enumerate(posts, start=1):
        try:
            budget.check()
        except TaskBudgetExceeded as exc:
            stop_reason = str(exc)
            logger.warning("initial check stopped by runtime budget: %s", stop_reason)
            break

        post_id = post["id"]
        url = post["url"]
        source_app = resolve_source_app(post.get("source_app"), url)
        logger.info("[%s/%s] initial check source=%s id=%s", idx, total, source_app, post_id)

        try:
            open_url(deep_links.get(post_id, url))
            result = check_post_exists_and_account(post_id)
        except DeviceUnavailable:
            reset_device_session()
            raise
        except Exception as exc:
            logger.exception("initial check failed id=%s", post_id)
            result = {
                "status": "error",
                "exists": False,
                "account_name": None,
                "error": str(exc),
            }

        save_initial_check_result(
            post_id=post_id,
            status=result["status"],
            error=result.get("error"),
            account_name=result.get("account_name"),
        )
        budget.record_status(result["status"])

        row_index = None
        if result["status"] in {"success", "not_found"} and sheet_rows:
            try:
                row_index = sink.resolve_row_index_for_url(
                    url,
                    preferred_row_index=post.get("doc_row_index"),
                    rows=sheet_rows,
                    start_row=start_row,
                )
            except Exception as exc:
                logger.warning("unsafe initial writeback skipped id=%s: %s", post_id, exc)

        task_id, result_id = record_crawl_result(
            post=post,
            workflow="initial_check",
            status=result["status"],
            account_name=result.get("account_name"),
            metrics={
                "exists": result.get("exists"),
                "row_index": row_index,
            },
            error=result.get("error"),
        )

        if row_index:
            writebacks.append(
                {
                    "row_index": row_index,
                    "exists": result["status"] == "success",
                    "account_name": result.get("account_name"),
                }
            )
            writeback_records.append(
                PendingWriteback(
                    post_id=post_id,
                    task_id=task_id,
                    result_id=result_id,
                    row_index=row_index,
                )
            )
        elif result["status"] == "error":
            logger.warning("technical error skipped Tencent Docs writeback id=%s", post_id)
            record_sink_writeback(
                post_id=post_id,
                sink_type="tencent_docs",
                status="skipped",
                task_id=task_id,
                result_id=result_id,
                error=result.get("error") or "technical error skipped writeback",
            )
        else:
            logger.warning("row not found; skipped Tencent Docs writeback id=%s", post_id)
            record_sink_writeback(
                post_id=post_id,
                sink_type="tencent_docs",
                status="skipped",
                task_id=task_id,
                result_id=result_id,
                error="row not found",
            )

        result_with_post = dict(result)
        result_with_post.update(
            {"id": post_id, "url": url, "source_app": source_app, "row_index": row_index}
        )
        results.append(result_with_post)
        budget.sleep()

    if writebacks:
        try:
            sink.write_initial_check_results(writebacks)
            record_pending_writebacks(
                records=writeback_records,
                sink_type="tencent_docs",
                status="success",
            )
        except Exception as exc:
            send_alert("Tencent Docs initial writeback failed", str(exc), dedupe_key="docs_check_writeback")
            logger.warning("initial check batch writeback failed: %s", exc)
            record_pending_writebacks(
                records=writeback_records,
                sink_type="tencent_docs",
                status="error",
                error=str(exc),
            )

    success_count, not_found_count, error_count = _status_counts(results)
    duration = time.time() - start_time
    msg = (
        f"total={total}, success={success_count}, "
        f"not_found={not_found_count}, error={error_count}, duration={duration:.1f}s"
    )
    if stop_reason:
        msg = f"{msg}, stopped={stop_reason}"
    logger.info("initial check finished: %s", msg)
    log_task("check", "success", msg, duration)

    if error_count:
        send_alert("Initial check has errors", msg, level="warning", dedupe_key="check_errors")
    if stop_reason:
        send_alert("Initial check stopped by budget", stop_reason, level="warning", dedupe_key="check_budget")
    return results
