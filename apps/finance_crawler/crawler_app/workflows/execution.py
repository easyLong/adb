"""Generic crawler_app v2 task execution loop."""

from __future__ import annotations

import random
import time
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.crawler_app.documents.fields import REMARK
from apps.finance_crawler.crawler_app.storage.db import get_conn
from apps.finance_crawler.crawler_app.tasks.handlers import TaskHandler
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.utils.device_health import AdbDevice, DeviceUnavailable
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("crawler_app_execution")


def crawl_pending_tasks(handler: TaskHandler, *, limit: int | None = None) -> dict[str, Any]:
    conn = get_conn()
    results: list[dict[str, Any]] = []
    try:
        submissions = repository.get_pending_task_submissions(conn, task_type=handler.task_type, limit=limit)
        if not submissions:
            return execution_summary(handler.task_type, submissions, results)

        execution_device = handler.runtime.prepare()
        total = len(submissions)
        for index, submission in enumerate(submissions, start=1):
            _attach_capture_action_profile(conn, handler, submission)
            execution_id = repository.start_task_execution(
                conn,
                int(submission["id"]),
                worker_id=handler.worker_id,
            )
            conn.commit()
            result = _crawl_submission(handler, submission)
            status = str(result.get("status") or "error")
            error = None if status == "success" else str(result.get("error") or status)

            final_submission_status = repository.finish_task_execution(
                conn,
                submission_id=int(submission["id"]),
                execution_id=execution_id,
                status=status,
                result=result,
                metrics=handler.metrics(result),
                opened_url=str(result.get("opened_url") or submission["post_url"]),
                screenshot_path=result.get("screenshot_path"),
                error=error,
            )
            result_for_writeback = dict(result)
            result_for_writeback["final_submission_status"] = final_submission_status
            repository.create_writeback_plans(
                conn,
                submission_id=int(submission["id"]),
                execution_id=execution_id,
                document_id=int(submission["document_id"]),
                sheet_id=str(submission["sheet_id"]),
                row_index=int(submission["row_index"]),
                column_mapping_id=int(submission["source_locator"].get("column_mapping_id") or 0),
                values=_requested_writeback_values(submission, handler.writeback_values(result_for_writeback)),
            )
            conn.commit()
            result_with_task = dict(result)
            result_with_task["submission_id"] = int(submission["id"])
            result_with_task["execution_id"] = execution_id
            results.append(result_with_task)
            if index < total:
                _sleep_between_submissions(handler.task_type)
        return execution_summary(handler.task_type, submissions, results, device=execution_device)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execution_summary(
    task_type: str,
    submissions: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    device: AdbDevice | None = None,
) -> dict[str, Any]:
    return {
        "task_type": task_type,
        "device": _device_summary(device),
        "submissions": len(submissions),
        "success": sum(1 for item in results if item.get("status") == "success"),
        "not_found": sum(1 for item in results if item.get("status") == "not_found"),
        "failed": sum(1 for item in results if item.get("status") not in {"success", "not_found"}),
        "capture_profiles": _capture_profile_summary(submissions),
        "results": results,
    }


def _device_summary(device: AdbDevice | None) -> dict[str, Any] | None:
    if device is None:
        return None
    return {
        "serial": device.serial,
        "transport": device.transport,
        "model": device.model,
        "product": device.product,
        "device_name": device.device_name,
    }


def _crawl_submission(handler: TaskHandler, submission: dict[str, Any]) -> dict[str, Any]:
    try:
        return handler.crawl(submission)
    except DeviceUnavailable as exc:
        reset_device_session()
        logger.warning(
            "crawler_app device unavailable task_type=%s submission=%s: %s",
            handler.task_type,
            submission.get("id"),
            exc,
        )
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        logger.warning(
            "crawler_app task failed task_type=%s submission=%s: %s",
            handler.task_type,
            submission.get("id"),
            exc,
        )
        return {"status": "error", "error": str(exc)}


def _requested_writeback_values(submission: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    requested = submission.get("source_locator", {}).get("requested_fields")
    if not requested:
        return values
    allowed = {str(item) for item in requested if str(item)}
    if not allowed:
        return values
    allowed.add(REMARK)
    return {field_name: value for field_name, value in values.items() if field_name in allowed}


def _attach_capture_action_profile(conn, handler: TaskHandler, submission: dict[str, Any]) -> None:
    fields = _requested_fields(submission)
    if not fields:
        return
    profile = repository.get_capture_action_profile(
        conn,
        app_type=str(submission.get("app_type") or "unknown"),
        task_type=handler.task_type,
        field_names=fields,
    )
    if profile:
        submission["capture_action_profile"] = profile
        submission["capture_action_profile_id"] = profile.get("id")


def _requested_fields(submission: dict[str, Any]) -> tuple[str, ...]:
    requested = submission.get("source_locator", {}).get("requested_fields")
    if not requested:
        return ()
    return tuple(str(item) for item in requested if str(item))


def _capture_profile_summary(submissions: list[dict[str, Any]]) -> dict[str, int]:
    matched = sum(1 for item in submissions if item.get("capture_action_profile_id"))
    return {
        "matched": matched,
        "fallback": len(submissions) - matched,
    }


def _sleep_between_submissions(task_type: str) -> None:
    if task_type == "detail":
        delay_min = max(Config.DETAIL_POST_DELAY_MIN, 0)
        delay_max = max(Config.DETAIL_POST_DELAY_MAX, delay_min)
    else:
        delay_min = max(Config.POST_DELAY_MIN, 0)
        delay_max = max(Config.POST_DELAY_MAX, delay_min)
    delay = random.uniform(delay_min, delay_max)
    if delay <= 0:
        return
    logger.info("crawler_app pacing sleep task_type=%s delay=%.2fs", task_type, delay)
    time.sleep(delay)
