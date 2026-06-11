"""Generic crawler_app v2 task execution loop."""

from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.capture.core import CaptureBundle, FieldExtractionResult
from apps.finance_crawler.crawler_app.capture.observations import build_capture_bundle_observations
from apps.finance_crawler.crawler_app.errors import classify_crawl_error
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.crawler_app.documents.fields import REMARK
from apps.finance_crawler.crawler_app.storage.db import get_conn
from apps.finance_crawler.crawler_app.tasks.handlers import TaskHandler
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.storage.device_pool import release_device_lease, start_device_lease
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

        execution_device: AdbDevice | None = None
        total = len(submissions)
        for index, submission in enumerate(submissions, start=1):
            _attach_capture_action_profile(conn, handler, submission)
            lease = start_device_lease(
                app_type=str(submission.get("app_type") or "unknown"),
                task_scope=f"document:{handler.task_type}",
                task_id=int(submission["id"]),
                worker_id=handler.worker_id,
            )
            execution_device = AdbDevice(
                serial=lease.adb_serial,
                state="device",
                transport="unknown",
            )
            try:
                execution_id = repository.start_task_execution(
                    conn,
                    int(submission["id"]),
                    worker_id=handler.worker_id,
                )
                if execution_id is None:
                    conn.commit()
                    release_device_lease(lease, status="success")
                    logger.info(
                        "crawler_app skipped stale submission task_type=%s submission=%s status=%s attempts=%s/%s",
                        handler.task_type,
                        submission.get("id"),
                        submission.get("status"),
                        submission.get("attempts"),
                        submission.get("max_attempts"),
                    )
                    continue
                conn.commit()
            except Exception as exc:
                release_device_lease(lease, status="failed", error=str(exc), error_type=classify_crawl_error(exc).kind)
                raise
            result: dict[str, Any] = {}
            try:
                execution_device = handler.runtime.prepare()
                result = _crawl_submission(handler, submission)
            except Exception as exc:
                result = {"status": "error", "error": str(exc), "error_type": classify_crawl_error(exc).kind}
            status = str(result.get("status") or "error")
            error = None if status == "success" else str(result.get("error") or status)
            if error and not result.get("error_type"):
                result["error_type"] = classify_crawl_error(
                    error,
                    status=status,
                    page_state=str(result.get("page_state") or ""),
                ).kind
            release_device_lease(
                lease,
                status="success" if status in {"success", "not_found"} else "failed",
                error=error,
                error_type=str(result.get("error_type") or "") or None,
            )

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
            _record_field_capture_observations(
                conn,
                submission=submission,
                execution_id=execution_id,
                result=result,
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
        return {"status": "error", "error": str(exc), "error_type": classify_crawl_error(exc).kind}
    except Exception as exc:
        logger.warning(
            "crawler_app task failed task_type=%s submission=%s: %s",
            handler.task_type,
            submission.get("id"),
            exc,
        )
        return {"status": "error", "error": str(exc), "error_type": classify_crawl_error(exc).kind}


def _record_field_capture_observations(
    conn,
    *,
    submission: dict[str, Any],
    execution_id: int,
    result: dict[str, Any],
) -> int:
    bundle_payload = result.get("capture_bundle")
    result_payload = result.get("field_results")
    if not isinstance(bundle_payload, dict) or not isinstance(result_payload, list):
        return 0
    bundle = _capture_bundle_from_payload(bundle_payload, result)
    field_results = [_field_result_from_payload(item) for item in result_payload if isinstance(item, dict)]
    if not field_results:
        return 0
    source_row_id = submission.get("source_row_id")
    target_type = "source_row" if source_row_id else "task_submission"
    target_id = int(source_row_id or submission["id"])
    observations = build_capture_bundle_observations(
        subject_type="task_execution",
        subject_id=execution_id,
        target_type=target_type,
        target_id=target_id,
        bundle=bundle,
        field_results=field_results,
        observed_at=datetime.now(),
    )
    return repository.upsert_field_capture_observations(conn, observations)


def _capture_bundle_from_payload(payload: dict[str, Any], result: dict[str, Any]) -> CaptureBundle:
    return CaptureBundle(
        task_type=str(payload.get("task_type") or ""),
        app_type=str(payload.get("app_type") or "unknown"),
        requested_fields=tuple(str(item) for item in payload.get("requested_fields") or ()),
        action_template_key=str(payload.get("action_template_key") or "") or None,
        actions=tuple(str(item) for item in payload.get("actions") or ()),
        opened_url=str(payload.get("opened_url") or result.get("opened_url") or "") or None,
        status=str(payload.get("status") or result.get("status") or "unknown"),
        page_state=str(payload.get("page_state") or "unknown"),
        screenshot_path=str(payload.get("screenshot_path") or result.get("screenshot_path") or "") or None,
        raw_result=result,
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        error=str(payload.get("error") or result.get("error") or "") or None,
    )


def _field_result_from_payload(payload: dict[str, Any]) -> FieldExtractionResult:
    return FieldExtractionResult(
        field_name=str(payload.get("field_name") or ""),
        value=payload.get("value"),
        source=str(payload.get("source") or "") or None,
        accepted=bool(payload.get("accepted")),
        page_state=str(payload.get("page_state") or "unknown"),
        confidence=float(payload.get("confidence") or 0.0),
        evidence=payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
        quality_error=str(payload.get("quality_error") or "") or None,
    )


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
    elif task_type == "read_count":
        delay_min = max(Config.READ_COUNT_POST_DELAY_MIN, 0)
        delay_max = max(Config.READ_COUNT_POST_DELAY_MAX, delay_min)
    else:
        delay_min = max(Config.POST_DELAY_MIN, 0)
        delay_max = max(Config.POST_DELAY_MAX, delay_min)
    delay = random.uniform(delay_min, delay_max)
    if delay <= 0:
        return
    logger.info("crawler_app pacing sleep task_type=%s delay=%.2fs", task_type, delay)
    time.sleep(delay)
