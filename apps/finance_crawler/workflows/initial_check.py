"""Initial check workflow: crawl existence/account and write business results."""

from __future__ import annotations

import time

from apps.finance_crawler.config import Config
from apps.finance_crawler.mobile.device_session import restart_app_for_url
from apps.finance_crawler.mobile.crawler import (
    check_record_exists_and_account,
    is_transient_open_failure,
    open_url,
    reset_device_session,
    resolve_short_url,
)
from apps.finance_crawler.services.alerts import send_alert
from apps.finance_crawler.services.writeback import WritebackPlan, default_writeback_service
from apps.finance_crawler.storage.crawl_repository import (
    PendingWriteback,
    get_pending_initial_check_records,
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
from apps.finance_crawler.storage.device_pool import acquire_device
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.link_source import resolve_source_app
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.utils.rate_limiter import OperationBudget, TaskBudgetExceeded
from apps.finance_crawler.utils.record_identity import workflow_record_id, workflow_record_url
from apps.finance_crawler.utils.url_resolver import resolve_urls

logger = get_logger("initial_check_workflow")


def _status_counts(results: list[dict]) -> tuple[int, int, int]:
    success = sum(1 for item in results if item["status"] == "success")
    not_found = sum(1 for item in results if item["status"] == "not_found")
    error = sum(1 for item in results if item["status"] == "error")
    return success, not_found, error


def _open_and_check_with_app_recovery(
    *,
    opened_url: str,
    record_id: int,
    source_app: str,
) -> dict:
    attempts = max(1, Config.APP_OPEN_RECOVERY_RETRIES + 1)
    result: dict = {}
    restarts = 0

    for attempt in range(1, attempts + 1):
        open_url(opened_url)
        result = check_record_exists_and_account(record_id)
        if not is_transient_open_failure(result) or attempt >= attempts:
            if restarts:
                result["app_restart_attempts"] = restarts
            return result

        logger.warning(
            "transient initial-check page failure id=%s attempt=%s/%s error=%s; restarting app",
            record_id,
            attempt,
            attempts,
            result.get("error"),
        )
        if restart_app_for_url(opened_url, source_app=source_app):
            restarts += 1
        else:
            time.sleep(Config.APP_RESTART_WAIT)

    return result


def run_initial_check() -> list[dict]:
    start_time = time.time()
    budget = OperationBudget("check")
    writeback_service = default_writeback_service()
    records = budget.limit_items(get_pending_initial_check_records())
    total = len(records)
    logger.info("initial check started: total=%s", total)

    if not records:
        log_task("check", "success", "no pending records", 0)
        return []

    budget.check()

    writeback_service.load_snapshot()

    deep_links = resolve_urls(records, resolve_short_url, logger)
    results: list[dict] = []
    stop_reason: str | None = None

    with acquire_device(
        app_type="initial_check",
        task_scope="legacy:initial_check",
        task_id=f"batch:{int(start_time)}",
        worker_id="legacy_check",
    ):
        try:
            assert_device_ready()
        except DeviceUnavailable as exc:
            reset_device_session()
            send_alert("ADB device unavailable", str(exc), dedupe_key="device_unavailable")
            log_task("check", "error", str(exc), time.time() - start_time)
            raise

        for idx, record in enumerate(records, start=1):
            try:
                budget.check()
            except TaskBudgetExceeded as exc:
                stop_reason = str(exc)
                logger.warning("initial check stopped by runtime budget: %s", stop_reason)
                break

            record_id = workflow_record_id(record)
            url = workflow_record_url(record)
            source_app = resolve_source_app(record.get("source_app"), url)
            execution_id = _start_execution_for_record(record)
            if execution_id is None:
                logger.info("initial check skipped by task submission state id=%s", record_id)
                continue
            logger.info("[%s/%s] initial check source=%s id=%s", idx, total, source_app, record_id)

            try:
                result = _open_and_check_with_app_recovery(
                    opened_url=deep_links.get(record_id, url),
                    record_id=record_id,
                    source_app=source_app,
                )
            except DeviceUnavailable as exc:
                reset_device_session()
                _finish_execution(
                    execution_id,
                    result={
                        "status": "error",
                        "exists": False,
                        "account_name": None,
                        "error": str(exc),
                    },
                    metrics={"exists": False},
                    writeback_status="skipped",
                    writeback_error=str(exc),
                )
                raise
            except Exception as exc:
                logger.exception("initial check failed id=%s", record_id)
                result = {
                    "status": "error",
                    "exists": False,
                    "account_name": None,
                    "error": str(exc),
                }

            budget.record_status(result["status"])

            writeback_plan = writeback_service.prepare_initial_check(record=record, result=result)
            row_index = writeback_plan.row_index

            task_id, result_id = record_crawl_result(
                record=record,
                workflow="initial_check",
                status=result["status"],
                account_name=result.get("account_name"),
                metrics={
                    "exists": result.get("exists"),
                    "row_index": row_index,
                    "app_restart_attempts": result.get("app_restart_attempts"),
                },
                error=result.get("error"),
            )

            if writeback_plan.can_write and row_index:
                writeback_status, writeback_error = _write_single_initial_check_result(
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
                logger.warning(
                    "skipped %s writeback id=%s: %s",
                    writeback_plan.sink_type,
                    record_id,
                    writeback_plan.skip_reason,
                )
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
                metrics={
                    "exists": result.get("exists"),
                    "row_index": row_index,
                    "app_restart_attempts": result.get("app_restart_attempts"),
                },
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
            budget.sleep()

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


def _write_single_initial_check_result(
    *,
    writeback_service,
    writeback_plan: WritebackPlan,
    pending_writeback: PendingWriteback,
) -> tuple[str, str | None]:
    try:
        writeback_service.write_initial_check_results([writeback_plan])
        record_pending_writebacks(
            records=[pending_writeback],
            sink_type=writeback_service.sink_type,
            status="success",
        )
        return "success", None
    except Exception as exc:
        error = str(exc)
        logger.warning("initial check writeback failed row=%s: %s", pending_writeback.row_index, error)
        record_pending_writebacks(
            records=[pending_writeback],
            sink_type=writeback_service.sink_type,
            status="error",
            error=error,
        )
        send_alert(
            "Tencent Docs initial writeback failed",
            error,
            level="warning",
            dedupe_key="docs_check_writeback",
        )
        return "error", error


def _start_execution_for_record(record: dict) -> int | None:
    submission_id = record.get("submission_id")
    if not submission_id:
        logger.warning("initial check record has no submission_id id=%s", workflow_record_id(record))
        return None
    try:
        return start_task_execution(submission_id, worker_id="initial_check")
    except ValueError as exc:
        logger.warning("initial check submission not runnable record_id=%s: %s", workflow_record_id(record), exc)
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
            metrics=metrics,
            result=result,
            writeback_status=writeback_status,
            writeback_locator=writeback_locator,
            writeback_error=writeback_error,
            error=result.get("error"),
        )
    except Exception as exc:
        logger.warning("failed to finish initial check execution id=%s: %s", execution_id, exc)


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
        logger.warning("failed to update initial check writeback id=%s: %s", execution_id, exc)
